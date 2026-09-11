"""Regras de negócio para clientes, administradores e impersonação."""

from __future__ import annotations

import hashlib
import asyncio
import logging
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.admin_models import (
    AccountType,
    AdminActor,
    AdminCreate,
    AdminPermission,
    AdminRecord,
    AdminUpdate,
    ApprovalStatus,
    ClientCreate,
    ClientRecord,
    ClientUpdate,
    ImpersonationRecord,
    MarketingMode,
    PaymentStatus,
)
from backend.admin_repository import AdminRepository
from backend.webhook_models import DomainEventType
from backend.webhook_service import WebhookService

_PASSWORD_PATTERN = re.compile(r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d).+$")
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
logger = logging.getLogger("backend-admin")


class AdminServiceError(Exception):
    """Erro base conhecido do domínio administrativo."""


class AuthorizationError(AdminServiceError):
    """A identidade não possui permissão ou escopo para a ação."""


class ValidationError(AdminServiceError):
    """Dados recebidos não atendem às regras do domínio."""


class NotFoundError(AdminServiceError):
    """O recurso não existe no escopo autenticado."""


class AdminManagementService:
    """Centraliza regras administrativas sem depender de FastAPI."""

    def __init__(
        self,
        repository: AdminRepository,
        *,
        webhooks: WebhookService | None = None,
    ) -> None:
        """
        Inicializa regras administrativas.

        Args:
            repository: Persistência multi-tenant de clientes e acessos.
            webhooks: Outbox opcional para eventos de ciclo de vida.
        """
        self.repository = repository
        self.webhooks = webhooks

    async def create_client(
        self,
        actor: AdminActor,
        payload: ClientCreate,
        *,
        request_id: str | None = None,
    ) -> ClientRecord:
        """
        Cria um cliente na empresa do administrador.

        Raises:
            AuthorizationError: Se faltar permissão.
            ValidationError: Se os dados forem inválidos.
        """
        self._require_permission(actor, AdminPermission.CLIENTS_CREATE)
        self._validate_password(payload.password)
        self._validate_identity_fields(payload.name, payload.email, payload.trader_id)
        self._validate_account_configuration(
            payload.account_type,
            payload.payment_status,
            payload.trial_days,
            payload.marketing_mode,
            payload.marketing_win_rate,
        )

        record = await self.repository.create_client(actor.company_id, payload)
        self._apply_access_configuration(
            record,
            account_type=payload.account_type,
            payment_status=payload.payment_status,
            trial_days=payload.trial_days,
            marketing_mode=payload.marketing_mode,
            marketing_win_rate=payload.marketing_win_rate,
        )
        await self.repository.save_client(record)
        lifecycle_event = (
            "trial_started"
            if record.account_type == AccountType.TRIAL
            else (
                "client_activated"
                if record.account_type == AccountType.CLIENT and record.grant_access
                else None
            )
        )
        if lifecycle_event:
            await self.repository.append_lifecycle_event(
                actor.company_id,
                record.user_id,
                lifecycle_event,
                record.updated_at,
            )
        if lifecycle_event == "trial_started":
            await self._emit_trial_started(record, request_id=request_id)
        await self.repository.append_audit_event(
            actor.company_id,
            actor.user_id,
            "client.created",
            record.user_id,
            {"account_type": record.account_type.value},
            after=_client_audit_snapshot(record),
            request_id=request_id,
        )
        return record

    async def list_clients(
        self,
        actor: AdminActor,
        *,
        account_type: AccountType | None = None,
        include_deleted: bool = False,
        limit: int = 10,
        offset: int = 0,
        search: str | None = None,
        order_by: str = "created_at",
        order_direction: str = "desc",
        approval_status: str | None = None,
        grant_access: bool | None = None,
        exclude_approval_status: str | None = None,
    ) -> list[ClientRecord]:
        """
        Lista clientes da empresa do administrador.

        Raises:
            ValidationError: Se a paginação for inválida.
        """
        if limit < 1 or limit > 100 or offset < 0:
            raise ValidationError("Paginação inválida")
        return await self.repository.list_clients(
            actor.company_id,
            account_type=account_type.value if account_type else None,
            include_deleted=include_deleted,
            limit=limit,
            offset=offset,
            search=search,
            order_by=order_by,
            order_direction=order_direction,
            approval_status=approval_status,
            grant_access=grant_access,
            exclude_approval_status=exclude_approval_status,
        )

    async def update_client(
        self,
        actor: AdminActor,
        user_id: str,
        payload: ClientUpdate,
        *,
        request_id: str | None = None,
    ) -> ClientRecord:
        """
        Atualiza um cliente sem permitir acesso entre empresas.

        Raises:
            AuthorizationError: Se faltar permissão ou o alvo estiver fora da empresa.
            ValidationError: Se a nova configuração for inconsistente.
        """
        self._require_any_permission(
            actor,
            AdminPermission.CLIENTS_EDIT,
            AdminPermission.CLIENTS_UPDATE,
        )
        current = await self.repository.get_client(actor.company_id, user_id)
        if current is None:
            raise AuthorizationError("Cliente fora do escopo permitido")
        before = _client_audit_snapshot(current)
        was_active_client = (
            current.account_type == AccountType.CLIENT
            and current.grant_access
            and current.deleted_at is None
        )
        was_trial = current.account_type == AccountType.TRIAL
        if payload.password is not None:
            self._validate_password(payload.password)

        account_type = payload.account_type or current.account_type
        payment_status = payload.payment_status or current.payment_status
        marketing_mode = payload.marketing_mode or current.marketing_mode
        marketing_win_rate = (
            payload.marketing_win_rate
            if payload.marketing_win_rate is not None
            else current.marketing_win_rate
        )
        self._validate_account_configuration(
            account_type,
            payment_status,
            payload.trial_days,
            marketing_mode,
            marketing_win_rate,
            allow_existing_trial_expiration=current.expires_at is not None,
        )

        updated = await self.repository.update_client(actor.company_id, user_id, payload)
        if updated is None:
            raise NotFoundError("Cliente não encontrado")
        self._apply_access_configuration(
            updated,
            account_type=account_type,
            payment_status=payment_status,
            trial_days=payload.trial_days,
            marketing_mode=marketing_mode,
            marketing_win_rate=marketing_win_rate,
        )
        await self.repository.save_client(updated)
        is_active_client = (
            updated.account_type == AccountType.CLIENT
            and updated.grant_access
            and updated.deleted_at is None
        )
        if not was_active_client and is_active_client:
            await self.repository.append_lifecycle_event(
                actor.company_id,
                updated.user_id,
                "client_activated",
                updated.updated_at,
            )
        elif was_active_client and not is_active_client:
            await self.repository.append_lifecycle_event(
                actor.company_id,
                updated.user_id,
                "client_inactivated",
                updated.updated_at,
            )
        if not was_trial and updated.account_type == AccountType.TRIAL:
            await self.repository.append_lifecycle_event(
                actor.company_id,
                updated.user_id,
                "trial_started",
                updated.updated_at,
            )
            await self._emit_trial_started(updated, request_id=request_id)
        await self.repository.append_audit_event(
            actor.company_id,
            actor.user_id,
            "client.updated",
            updated.user_id,
            {"account_type": updated.account_type.value},
            before=before,
            after=_client_audit_snapshot(updated),
            request_id=request_id,
        )
        return updated

    async def approve_lead(
        self,
        actor: AdminActor,
        user_id: str,
        access_days: int,
        *,
        request_id: str | None = None,
        emit_webhooks: bool = True,
        defer_side_effects: bool = False,
    ) -> ClientRecord:
        """
        Aprova um lead pendente liberando N dias de trial.

        Args:
            actor: Administrador autenticado da mesma empresa.
            user_id: Identificador do lead.
            access_days: Dias de acesso (1..365).
            request_id: Correlação para auditoria e webhooks.
            emit_webhooks: Se False (e sem defer), agenda só webhooks no caller.
            defer_side_effects: Se True, responde após o save; audit/lifecycle/
                webhooks ficam para ``complete_lead_approval_side_effects``.

        Returns:
            Cliente atualizado com acesso liberado.

        Raises:
            AuthorizationError: Sem permissão ou fora do tenant.
            ValidationError: ``access_days`` inválido.
            NotFoundError: Cliente inexistente no tenant.
        """
        self._require_any_permission(
            actor,
            AdminPermission.CLIENTS_EDIT,
            AdminPermission.CLIENTS_UPDATE,
        )
        if not isinstance(access_days, int) or access_days < 1 or access_days > 365:
            raise ValidationError("access_days deve estar entre 1 e 365")

        record = await self.repository.get_client(actor.company_id, user_id)
        if record is None or record.deleted_at is not None:
            raise NotFoundError("Cliente não encontrado")

        before = _client_audit_snapshot(record)
        now = datetime.now(timezone.utc)
        record.approval_status = ApprovalStatus.APPROVED
        record.account_type = AccountType.TRIAL
        record.payment_status = PaymentStatus.NOT_REQUIRED
        record.expires_at = now + timedelta(days=access_days)
        record.grant_access = True
        record.plan_name = f"Acesso liberado ({access_days} dias)"
        record.updated_at = now
        await self.repository.save_client(record)

        if defer_side_effects:
            record._lead_approval_before = before  # type: ignore[attr-defined]
            record._lead_approval_access_days = access_days  # type: ignore[attr-defined]
            return record

        await self._persist_lead_approval_side_effects(
            actor,
            record,
            access_days=access_days,
            before=before,
            request_id=request_id,
            emit_webhooks=emit_webhooks,
        )
        return record

    async def _persist_lead_approval_side_effects(
        self,
        actor: AdminActor,
        record: ClientRecord,
        *,
        access_days: int,
        before: dict[str, Any],
        request_id: str | None = None,
        emit_webhooks: bool = True,
    ) -> None:
        """Grava auditoria/lifecycle e, opcionalmente, dispara webhooks de trial."""
        await asyncio.gather(
            self.repository.append_lifecycle_event(
                actor.company_id,
                record.user_id,
                "trial_started",
                record.updated_at,
            ),
            self.repository.append_audit_event(
                actor.company_id,
                actor.user_id,
                "client.lead_approved",
                record.user_id,
                {"access_days": access_days},
                before=before,
                after=_client_audit_snapshot(record),
                request_id=request_id,
            ),
        )
        if emit_webhooks:
            await self._emit_trial_started(record, request_id=request_id)

    async def complete_lead_approval_side_effects(
        self,
        actor: AdminActor,
        record: ClientRecord,
        *,
        request_id: str | None = None,
    ) -> None:
        """
        Finaliza audit/lifecycle/webhooks fora do caminho crítico do HTTP.

        Args:
            actor: Administrador que aprovou o lead.
            record: Cliente já persistido com acesso liberado (pode carregar
                metadados ``_lead_approval_*`` preenchidos por ``approve_lead``).
            request_id: Correlação opcional.

        Raises:
            ValidationError: Se faltar metadado de auditoria no record.
        """
        before = getattr(record, "_lead_approval_before", None)
        access_days = getattr(record, "_lead_approval_access_days", None)
        if before is None or not isinstance(access_days, int):
            raise ValidationError("Metadados de aprovação ausentes no record")
        await self._persist_lead_approval_side_effects(
            actor,
            record,
            access_days=access_days,
            before=before,
            request_id=request_id,
            emit_webhooks=True,
        )

    async def emit_trial_started_after_approval(
        self,
        record: ClientRecord,
        *,
        request_id: str | None = None,
    ) -> None:
        """
        Dispara webhooks/agendamento de trial fora do caminho crítico do HTTP.

        Args:
            record: Cliente já aprovado e persistido.
            request_id: Correlação opcional.
        """
        await self._emit_trial_started(record, request_id=request_id)

    async def _emit_trial_started(
        self,
        record: ClientRecord,
        *,
        request_id: str | None,
    ) -> None:
        """Registra início de trial e agenda seu encerramento autoritativo."""
        if self.webhooks is None:
            return
        resolved_request_id = request_id or str(uuid.uuid4())
        event = await self.webhooks.enqueue_event(
            company_id=record.company_id,
            event_type=DomainEventType.TRIAL_STARTED,
            subject_user_id=record.user_id,
            request_id=resolved_request_id,
            customer={
                "id": record.user_id,
                "name": record.name,
                "email": record.email,
                "phone": record.phone,
            },
            plan=(
                {"id": record.plan_id, "name": record.plan_name}
                if record.plan_id or record.plan_name
                else None
            ),
            data={
                "expires_at": record.expires_at.isoformat() if record.expires_at else None,
            },
            event_id=str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"trial-started:{record.company_id}:{record.user_id}:{record.updated_at.isoformat()}",
                )
            ),
            occurred_at=record.updated_at,
        )
        try:
            await self.webhooks.queue_event_deliveries(event)
            if record.expires_at is not None:
                from backend.workers.webhook_tasks import end_trial

                await asyncio.to_thread(
                    end_trial.apply_async,
                    args=[record.company_id, record.user_id, resolved_request_id],
                    eta=record.expires_at,
                )
        except Exception:
            logger.error(
                "admin.trial.queue_failed company_id=%s user_id=%s request_id=%s",
                record.company_id,
                record.user_id,
                resolved_request_id,
                exc_info=True,
            )

    async def delete_client(
        self,
        actor: AdminActor,
        user_id: str,
        *,
        request_id: str | None = None,
    ) -> ClientRecord:
        """
        Desativa logicamente um cliente preservando histórico e auditoria.

        Raises:
            AuthorizationError: Se faltar permissão ou o alvo estiver fora da empresa.
        """
        self._require_permission(actor, AdminPermission.CLIENTS_DELETE)
        record = await self.repository.get_client(actor.company_id, user_id)
        if record is None:
            raise AuthorizationError("Cliente fora do escopo permitido")
        before = _client_audit_snapshot(record)
        was_accessible = record.grant_access and record.deleted_at is None
        now = datetime.now(timezone.utc)
        record.deleted_at = now
        record.updated_at = now
        record.grant_access = False
        await self.repository.save_client(record)
        if was_accessible:
            await self.repository.append_lifecycle_event(
                actor.company_id,
                user_id,
                "client_inactivated",
                now,
            )
        await self.repository.append_audit_event(
            actor.company_id,
            actor.user_id,
            "client.deleted",
            user_id,
            before=before,
            after=_client_audit_snapshot(record),
            request_id=request_id,
        )
        return record

    async def create_admin(
        self,
        actor: AdminActor,
        payload: AdminCreate,
        *,
        request_id: str | None = None,
    ) -> AdminRecord:
        """
        Cria um administrador sem permitir delegação indevida.

        Raises:
            AuthorizationError: Se faltar permissão ou houver escalada.
            ValidationError: Se identidade, cargo ou senha forem inválidos.
        """
        self._require_permission(actor, AdminPermission.ADMINS_CREATE)
        self._validate_password(payload.password)
        self._validate_identity_fields(payload.name, payload.email, payload.job_title)
        if not payload.permissions.issubset(actor.permissions):
            raise AuthorizationError("Não é permitido delegar permissões não possuídas")
        if actor.manageable_role_ids is not None:
            if payload.manageable_role_ids is None:
                raise AuthorizationError("Não é permitido conceder gestão de todos os cargos")
            if not payload.manageable_role_ids.issubset(actor.manageable_role_ids):
                raise AuthorizationError("Não é permitido delegar cargos fora do próprio escopo")

        role_id = _role_id(payload.job_title)
        if actor.manageable_role_ids is not None and role_id not in actor.manageable_role_ids:
            raise AuthorizationError("Cargo fora do escopo gerenciável")
        record = AdminRecord(
            user_id=str(uuid.uuid4()),
            company_id=actor.company_id,
            name=payload.name.strip(),
            email=payload.email.strip().lower(),
            job_title=payload.job_title.strip(),
            role_id=role_id,
            permissions=payload.permissions,
            manageable_role_ids=payload.manageable_role_ids,
        )
        created = await self.repository.create_admin(record, payload.password)
        await self.repository.append_audit_event(
            actor.company_id,
            actor.user_id,
            "admin.created",
            created.user_id,
            {"role_id": role_id},
            request_id=request_id,
        )
        return created

    async def list_admins(self, actor: AdminActor) -> list[AdminRecord]:
        """Lista administradores da mesma empresa."""
        return await self.repository.list_admins(actor.company_id)

    async def update_admin(
        self,
        actor: AdminActor,
        user_id: str,
        payload: AdminUpdate,
        *,
        request_id: str | None = None,
    ) -> AdminRecord:
        """
        Atualiza administrador respeitando permissões e escopo de cargos.

        Raises:
            AuthorizationError: Se houver tentativa de escalada ou acesso cross-tenant.
        """
        self._require_any_permission(
            actor,
            AdminPermission.ADMINS_EDIT,
            AdminPermission.ADMINS_UPDATE,
        )
        current = await self.repository.get_admin(actor.company_id, user_id)
        if current is None:
            raise AuthorizationError("Administrador fora do escopo permitido")
        if payload.password is not None:
            self._validate_password(payload.password)
        if payload.permissions is not None and not payload.permissions.issubset(actor.permissions):
            raise AuthorizationError("Não é permitido delegar permissões não possuídas")
        if payload.manageable_roles_supplied and actor.manageable_role_ids is not None:
            if payload.manageable_role_ids is None:
                raise AuthorizationError("Não é permitido conceder gestão de todos os cargos")
            if not payload.manageable_role_ids.issubset(actor.manageable_role_ids):
                raise AuthorizationError("Não é permitido delegar cargos fora do próprio escopo")
        if actor.manageable_role_ids is not None and current.role_id not in actor.manageable_role_ids:
            raise AuthorizationError("Cargo do administrador fora do escopo gerenciável")
        updated = await self.repository.update_admin(actor.company_id, user_id, payload)
        if updated is None:
            raise NotFoundError("Administrador não encontrado")
        await self.repository.append_audit_event(
            actor.company_id,
            actor.user_id,
            "admin.updated",
            user_id,
            {"role_id": updated.role_id},
            request_id=request_id,
        )
        return updated

    async def delete_admin(
        self,
        actor: AdminActor,
        user_id: str,
        *,
        request_id: str | None = None,
    ) -> AdminRecord:
        """
        Desativa administrador sem permitir autoexclusão ou remover o último admin.

        Raises:
            AuthorizationError: Se a ação for insegura ou estiver fora do escopo.
        """
        self._require_permission(actor, AdminPermission.ADMINS_DELETE)
        if actor.user_id == user_id:
            raise AuthorizationError("Não é permitido excluir o próprio acesso")
        admins = await self.repository.list_admins(actor.company_id)
        if len(admins) <= 1:
            raise AuthorizationError("Não é permitido remover o último administrador")
        target = next((admin for admin in admins if admin.user_id == user_id), None)
        if target is None:
            raise AuthorizationError("Administrador fora do escopo permitido")
        if actor.manageable_role_ids is not None and target.role_id not in actor.manageable_role_ids:
            raise AuthorizationError("Cargo do administrador fora do escopo gerenciável")
        target.deleted_at = datetime.now(timezone.utc)
        await self.repository.save_admin(target)
        await self.repository.append_audit_event(
            actor.company_id,
            actor.user_id,
            "admin.deleted",
            user_id,
            request_id=request_id,
        )
        return target

    async def start_impersonation(
        self,
        actor: AdminActor,
        target_user_id: str,
        reason: str,
        *,
        duration_minutes: int = 15,
        request_id: str | None = None,
    ) -> tuple[ImpersonationRecord, str]:
        """
        Inicia impersonação temporária somente leitura.

        Returns:
            Registro persistido e token opaco, exibido uma única vez.

        Raises:
            AuthorizationError: Se faltar permissão ou o alvo estiver fora da empresa.
            ValidationError: Se motivo ou duração forem inválidos.
        """
        self._require_any_permission(
            actor,
            AdminPermission.CLIENTS_ACCESS_ACCOUNT,
            AdminPermission.CLIENTS_IMPERSONATE,
        )
        target = await self.repository.get_client(actor.company_id, target_user_id)
        if target is None or target.deleted_at is not None:
            raise AuthorizationError("Cliente fora do escopo permitido")
        normalized_reason = reason.strip()
        if len(normalized_reason) < 10:
            raise ValidationError("Informe um motivo com pelo menos 10 caracteres")
        if duration_minutes < 1 or duration_minutes > 15:
            raise ValidationError("Impersonação deve durar entre 1 e 15 minutos")

        token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        record = ImpersonationRecord(
            session_id=str(uuid.uuid4()),
            company_id=actor.company_id,
            actor_user_id=actor.user_id,
            target_user_id=target_user_id,
            reason=normalized_reason,
            expires_at=now + timedelta(minutes=duration_minutes),
            token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
        )
        await self.repository.save_impersonation(record)
        await self.repository.append_audit_event(
            actor.company_id,
            actor.user_id,
            "impersonation.started",
            target_user_id,
            {"session_id": record.session_id},
            request_id=request_id,
        )
        return record, token

    async def start_support_session(
        self,
        actor: AdminActor,
        target_user_id: str,
        reason: str,
        *,
        request_id: str | None = None,
    ) -> tuple[ImpersonationRecord, str]:
        """
        Emite uma sessão de suporte com duração fixa de quinze minutos.

        Args:
            actor: Administrador autenticado.
            target_user_id: Cliente da mesma empresa.
            reason: Justificativa obrigatória da sessão.
            request_id: Identificador de correlação.

        Returns:
            Registro persistido e token opaco.

        Raises:
            AuthorizationError: Se faltar permissão ou escopo.
            ValidationError: Se o motivo for inválido.
        """
        return await self.start_impersonation(
            actor,
            target_user_id,
            reason,
            duration_minutes=15,
            request_id=request_id,
        )

    async def resolve_impersonation(
        self,
        actor: AdminActor,
        token: str,
    ) -> tuple[ImpersonationRecord, ClientRecord] | None:
        """Valida cookie opaco e resolve o cliente efetivo somente leitura."""
        if not {
            AdminPermission.CLIENTS_ACCESS_ACCOUNT,
            AdminPermission.CLIENTS_IMPERSONATE,
        }.intersection(actor.permissions) or not token:
            return None
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        record = await self.repository.get_impersonation_by_hash(
            actor.company_id,
            actor.user_id,
            token_hash,
        )
        now = datetime.now(timezone.utc)
        if record is None or record.revoked_at is not None or record.expires_at <= now:
            return None
        target = await self.repository.get_client(actor.company_id, record.target_user_id)
        if target is None or target.deleted_at is not None:
            return None
        return record, target

    async def resolve_support_session(
        self,
        actor: AdminActor,
        token: str,
    ) -> tuple[ImpersonationRecord, ClientRecord] | None:
        """Resolve uma sessão de suporte ativa, não expirada e não revogada."""
        return await self.resolve_impersonation(actor, token)

    async def end_impersonation(
        self,
        actor: AdminActor,
        session_id: str,
        *,
        request_id: str | None = None,
    ) -> None:
        """Revoga a sessão e registra o encerramento na auditoria."""
        await self.repository.revoke_impersonation(
            actor.company_id,
            actor.user_id,
            session_id,
        )
        await self.repository.append_audit_event(
            actor.company_id,
            actor.user_id,
            "impersonation.ended",
            None,
            {"session_id": session_id},
            request_id=request_id,
        )

    async def end_support_session(
        self,
        actor: AdminActor,
        session_id: str,
        *,
        request_id: str | None = None,
    ) -> None:
        """Revoga uma sessão de suporte pertencente ao ator e tenant."""
        await self.end_impersonation(actor, session_id, request_id=request_id)

    @staticmethod
    def _require_permission(actor: AdminActor, permission: AdminPermission) -> None:
        if permission not in actor.permissions:
            raise AuthorizationError(f"Permissão obrigatória: {permission.value}")

    @staticmethod
    def _require_any_permission(
        actor: AdminActor,
        *permissions: AdminPermission,
    ) -> None:
        """Exige ao menos uma permissão equivalente, mantendo migração segura."""
        if not set(permissions).intersection(actor.permissions):
            expected = " ou ".join(permission.value for permission in permissions)
            raise AuthorizationError(f"Permissão obrigatória: {expected}")

    @staticmethod
    def _validate_password(password: str) -> None:
        if len(password) < 8 or not _PASSWORD_PATTERN.match(password):
            raise ValidationError("Senha deve ter 8+ caracteres, maiúscula, minúscula e número")

    @staticmethod
    def _validate_identity_fields(name: str, email: str, reference: str) -> None:
        if len(name.strip()) < 2:
            raise ValidationError("Nome deve ter pelo menos 2 caracteres")
        if not _EMAIL_PATTERN.match(email.strip()):
            raise ValidationError("Email inválido")
        if not reference.strip():
            raise ValidationError("Identificador/cargo é obrigatório")

    @staticmethod
    def _validate_account_configuration(
        account_type: AccountType,
        payment_status: PaymentStatus,
        trial_days: int | None,
        marketing_mode: MarketingMode | None,
        marketing_win_rate: int | None,
        *,
        allow_existing_trial_expiration: bool = False,
    ) -> None:
        if account_type == AccountType.TRIAL:
            if trial_days is None and not allow_existing_trial_expiration:
                raise ValidationError("Informe o tempo de teste grátis")
            if trial_days is not None and (trial_days < 1 or trial_days > 365):
                raise ValidationError("Tempo de teste deve ficar entre 1 e 365 dias")
            if payment_status not in {
                PaymentStatus.NOT_APPLICABLE,
                PaymentStatus.NOT_REQUIRED,
            }:
                raise ValidationError("Teste grátis não deve possuir cobrança")
        elif account_type == AccountType.CLIENT:
            if payment_status not in {
                PaymentStatus.PAID,
                PaymentStatus.PENDING,
                PaymentStatus.OVERDUE,
                PaymentStatus.CANCELED,
                PaymentStatus.REFUNDED,
                PaymentStatus.CHARGEBACK,
                PaymentStatus.REFUSED,
            }:
                raise ValidationError("Cliente deve informar situação de pagamento")
        elif account_type == AccountType.MARKETING:
            if marketing_mode != MarketingMode.SIMULATION:
                raise ValidationError("Conta marketing deve ser exclusivamente de simulação")
            if marketing_win_rate is None or not 0 <= marketing_win_rate <= 100:
                raise ValidationError("Taxa simulada deve ficar entre 0 e 100")

    @staticmethod
    def _apply_access_configuration(
        record: ClientRecord,
        *,
        account_type: AccountType,
        payment_status: PaymentStatus,
        trial_days: int | None,
        marketing_mode: MarketingMode | None,
        marketing_win_rate: int | None,
    ) -> None:
        now = datetime.now(timezone.utc)
        record.account_type = account_type
        record.payment_status = payment_status
        record.marketing_mode = marketing_mode if account_type == AccountType.MARKETING else None
        record.marketing_win_rate = (
            marketing_win_rate
            if account_type == AccountType.MARKETING
            and marketing_mode == MarketingMode.SIMULATION
            else None
        )
        if account_type == AccountType.TRIAL:
            if trial_days is not None:
                record.expires_at = now + timedelta(days=trial_days)
                record.plan_name = f"Teste grátis ({trial_days} dias)"
            record.grant_access = bool(record.expires_at and record.expires_at > now)
        elif account_type == AccountType.CLIENT:
            record.expires_at = None
            record.grant_access = payment_status == PaymentStatus.PAID
        else:
            record.expires_at = None
            record.grant_access = True
        record.updated_at = now


SUPPORT_ALLOWED_ACTIONS = frozenset({"account.view", "broker_account.edit"})
SUPPORT_DENIED_PREFIXES = (
    "robot.",
    "broker.buy-real",
    "chart.",
    "websocket.",
)


def authorize_support_action(record: ImpersonationRecord, action: str) -> None:
    """
    Autoriza somente as duas capacidades explícitas do contexto de suporte.

    Args:
        record: Sessão de suporte já autenticada.
        action: Capacidade solicitada.

    Returns:
        Nada quando a ação é permitida.

    Raises:
        AuthorizationError: Para sessão expirada/revogada ou ação perigosa.
    """
    now = datetime.now(timezone.utc)
    if record.revoked_at is not None or record.expires_at <= now:
        raise AuthorizationError("Sessão de suporte expirada ou revogada")
    if action not in SUPPORT_ALLOWED_ACTIONS or action.startswith(SUPPORT_DENIED_PREFIXES):
        raise AuthorizationError("Ação bloqueada no contexto de suporte")


def _client_audit_snapshot(record: ClientRecord) -> dict[str, object]:
    """Produz snapshot de auditoria sem senha, email, telefone ou trader id."""
    return {
        "customer_type": record.account_type.value,
        "payment_status": record.payment_status.value,
        "plan_id": record.plan_id,
        "grant_access": record.grant_access,
        "approval_status": record.approval_status.value,
        "expires_at": record.expires_at.isoformat() if record.expires_at else None,
        "deleted_at": record.deleted_at.isoformat() if record.deleted_at else None,
        "marketing_target_win_rate": record.marketing_win_rate,
    }


def _role_id(job_title: str) -> str:
    """Gera chave estável de cargo sem usar o texto como autorização."""
    normalized = re.sub(r"[^a-z0-9]+", "-", job_title.strip().lower()).strip("-")
    if not normalized:
        raise ValidationError("Cargo inválido")
    return normalized
