"""Contratos de persistência da administração."""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from backend.admin_models import (
    AdminRecord,
    AdminUpdate,
    ApprovalStatus,
    ClientCreate,
    ClientRecord,
    ClientUpdate,
    ImpersonationRecord,
)


class AdminRepository(ABC):
    """Interface assíncrona para persistência administrativa."""

    @abstractmethod
    async def create_client(self, company_id: str, payload: ClientCreate) -> ClientRecord:
        """Cria identidade e perfil de cliente na empresa autenticada."""

    @abstractmethod
    async def get_client(self, company_id: str, user_id: str) -> ClientRecord | None:
        """Busca um cliente pelo id e empresa."""

    @abstractmethod
    async def list_clients(
        self,
        company_id: str,
        *,
        account_type: str | None,
        include_deleted: bool,
        limit: int,
        offset: int,
        search: str | None = None,
        order_by: str = "created_at",
        order_direction: str = "desc",
        approval_status: str | None = None,
        grant_access: bool | None = None,
        exclude_approval_status: str | None = None,
    ) -> list[ClientRecord]:
        """Lista clientes com isolamento explícito por empresa."""

    @abstractmethod
    async def update_client(
        self,
        company_id: str,
        user_id: str,
        payload: ClientUpdate,
    ) -> ClientRecord | None:
        """Atualiza um cliente da empresa."""

    @abstractmethod
    async def save_client(self, record: ClientRecord) -> ClientRecord:
        """Persiste um registro de cliente já validado."""

    @abstractmethod
    async def create_admin(self, record: AdminRecord, password: str) -> AdminRecord:
        """Cria identidade e perfil administrativo."""

    @abstractmethod
    async def list_admins(self, company_id: str) -> list[AdminRecord]:
        """Lista administradores da empresa."""

    @abstractmethod
    async def get_admin(self, company_id: str, user_id: str) -> AdminRecord | None:
        """Busca administrador por empresa e id."""

    @abstractmethod
    async def update_admin(
        self,
        company_id: str,
        user_id: str,
        payload: AdminUpdate,
    ) -> AdminRecord | None:
        """Atualiza um administrador da empresa."""

    @abstractmethod
    async def save_admin(self, record: AdminRecord) -> AdminRecord:
        """Persiste um administrador validado."""

    @abstractmethod
    async def save_impersonation(self, record: ImpersonationRecord) -> ImpersonationRecord:
        """Persiste uma sessão temporária de impersonação."""

    @abstractmethod
    async def get_impersonation_by_hash(
        self,
        company_id: str,
        actor_user_id: str,
        token_hash: str,
    ) -> ImpersonationRecord | None:
        """Busca sessão ativa pela empresa, ator e hash."""

    @abstractmethod
    async def revoke_impersonation(
        self,
        company_id: str,
        actor_user_id: str,
        session_id: str,
    ) -> None:
        """Revoga uma sessão de impersonação no escopo do ator."""

    @abstractmethod
    async def append_audit_event(
        self,
        company_id: str,
        actor_user_id: str,
        action: str,
        target_user_id: str | None,
        context: dict[str, Any] | None = None,
        *,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> None:
        """Registra evento de auditoria sem segredos ou PII."""

    @abstractmethod
    async def list_audit_events(
        self,
        company_id: str,
        subject_user_id: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Lista o histórico imutável do usuário no mesmo tenant."""

    @abstractmethod
    async def save_simulated_trade(
        self,
        company_id: str,
        user_id: str,
        trade: dict[str, Any],
    ) -> dict[str, Any]:
        """Persiste operação exclusivamente sintética."""

    @abstractmethod
    async def list_simulated_trades(
        self,
        company_id: str,
        user_id: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Lista somente operações sintéticas do usuário e empresa."""

    @abstractmethod
    async def update_simulated_trade(
        self,
        company_id: str,
        user_id: str,
        trade_id: str,
        changes: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Atualiza operação sintética somente no escopo autenticado."""

    @abstractmethod
    async def delete_simulated_trade(
        self,
        company_id: str,
        user_id: str,
        trade_id: str,
    ) -> dict[str, Any] | None:
        """Exclui operação sintética somente no escopo autenticado.

        Returns:
            A operação excluída, ou None se não existia.
        """

    @abstractmethod
    async def clear_simulated_trades(
        self,
        company_id: str,
        user_id: str,
    ) -> int:
        """Remove todo o histórico sintético do usuário no tenant autenticado."""

    @abstractmethod
    async def list_revenue_events(
        self,
        company_id: str,
        start_at: datetime,
        end_at: datetime,
    ) -> list[dict[str, Any]]:
        """Lista vendas e renovações confirmadas no período."""

    @abstractmethod
    async def list_lifecycle_events(
        self,
        company_id: str,
        start_at: datetime,
        end_at: datetime,
    ) -> list[dict[str, Any]]:
        """Lista transições de acesso dos clientes no período."""

    @abstractmethod
    async def append_lifecycle_event(
        self,
        company_id: str,
        user_id: str,
        event_type: str,
        occurred_at: datetime,
    ) -> None:
        """Registra uma transição de acesso sem dados sensíveis."""


class InMemoryAdminRepository(AdminRepository):
    """Repositório determinístico para testes e desenvolvimento sem Supabase."""

    def __init__(self) -> None:
        self.clients: dict[tuple[str, str], ClientRecord] = {}
        self.admins: dict[tuple[str, str], AdminRecord] = {}
        self.impersonations: dict[str, ImpersonationRecord] = {}
        self.audit_events: list[dict[str, Any]] = []
        self.simulated_trades: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self.revenue_events: list[dict[str, Any]] = []
        self.lifecycle_events: list[dict[str, Any]] = []

    async def create_client(self, company_id: str, payload: ClientCreate) -> ClientRecord:
        """Cria um cliente em memória sem persistir a senha."""
        now = datetime.now(timezone.utc)
        record = ClientRecord(
            user_id=str(uuid.uuid4()),
            company_id=company_id,
            name=payload.name,
            email=payload.email,
            phone=payload.phone,
            trader_id=payload.trader_id,
            account_type=payload.account_type,
            payment_status=payload.payment_status,
            plan_name=None,
            plan_id=payload.plan_id,
            grant_access=True,
            created_at=now,
            updated_at=now,
            marketing_mode=payload.marketing_mode,
            marketing_win_rate=payload.marketing_win_rate,
            approval_status=ApprovalStatus.APPROVED,
        )
        self.clients[(company_id, record.user_id)] = record
        return record

    async def get_client(self, company_id: str, user_id: str) -> ClientRecord | None:
        """Busca um cliente em memória com chave composta por empresa."""
        return self.clients.get((company_id, user_id))

    async def list_clients(
        self,
        company_id: str,
        *,
        account_type: str | None,
        include_deleted: bool,
        limit: int,
        offset: int,
        search: str | None = None,
        order_by: str = "created_at",
        order_direction: str = "desc",
        approval_status: str | None = None,
        grant_access: bool | None = None,
        exclude_approval_status: str | None = None,
    ) -> list[ClientRecord]:
        """Lista clientes da empresa com paginação e filtros de segmento."""
        rows = [
            record
            for (record_company_id, _), record in self.clients.items()
            if record_company_id == company_id
            and (account_type is None or record.account_type.value == account_type)
            and (include_deleted or record.deleted_at is None)
            and (
                approval_status is None
                or record.approval_status.value == approval_status
            )
            and (
                exclude_approval_status is None
                or record.approval_status.value != exclude_approval_status
            )
            and (
                grant_access is None
                or (
                    include_deleted
                    and grant_access is False
                    and (record.grant_access is False or record.deleted_at is not None)
                )
                or (not (include_deleted and grant_access is False)
                    and record.grant_access is grant_access)
            )
            and (
                not search
                or search.casefold() in record.name.casefold()
                or search.casefold() in record.email.casefold()
                or search.casefold() in record.trader_id.casefold()
            )
        ]
        order_keys = {
            "created_at": lambda item: (item.created_at, item.user_id),
            "updated_at": lambda item: (item.updated_at, item.user_id),
            "name": lambda item: (item.name.casefold(), item.user_id),
            "email": lambda item: (item.email.casefold(), item.user_id),
        }
        rows.sort(
            key=order_keys.get(order_by, order_keys["created_at"]),
            reverse=order_direction == "desc",
        )
        return rows[offset : offset + limit]

    async def update_client(
        self,
        company_id: str,
        user_id: str,
        payload: ClientUpdate,
    ) -> ClientRecord | None:
        """Atualiza campos não nulos de um cliente em memória."""
        record = self.clients.get((company_id, user_id))
        if record is None:
            return None
        for field_name in (
            "name",
            "email",
            "phone",
            "trader_id",
            "account_type",
            "payment_status",
            "plan_id",
            "marketing_mode",
            "marketing_win_rate",
        ):
            value = getattr(payload, field_name)
            if value is not None:
                setattr(record, field_name, value)
        record.updated_at = datetime.now(timezone.utc)
        return record

    async def save_client(self, record: ClientRecord) -> ClientRecord:
        """Persiste o cliente usando sua chave composta."""
        self.clients[(record.company_id, record.user_id)] = record
        return record

    async def create_admin(self, record: AdminRecord, password: str) -> AdminRecord:
        """Cria um administrador sem armazenar sua senha."""
        _ = password
        self.admins[(record.company_id, record.user_id)] = record
        return record

    async def list_admins(self, company_id: str) -> list[AdminRecord]:
        """Lista administradores não removidos da empresa."""
        return [
            record
            for (record_company_id, _), record in self.admins.items()
            if record_company_id == company_id and record.deleted_at is None
        ]

    async def get_admin(self, company_id: str, user_id: str) -> AdminRecord | None:
        """Busca administrador usando chave composta."""
        return self.admins.get((company_id, user_id))

    async def update_admin(
        self,
        company_id: str,
        user_id: str,
        payload: AdminUpdate,
    ) -> AdminRecord | None:
        """Atualiza campos administrativos informados."""
        record = self.admins.get((company_id, user_id))
        if record is None:
            return None
        for field_name in ("name", "email", "job_title", "permissions"):
            value = getattr(payload, field_name)
            if value is not None:
                setattr(record, field_name, value)
        if payload.manageable_roles_supplied:
            record.manageable_role_ids = payload.manageable_role_ids
        return record

    async def save_admin(self, record: AdminRecord) -> AdminRecord:
        """Persiste administrador pela chave composta."""
        self.admins[(record.company_id, record.user_id)] = record
        return record

    async def save_impersonation(self, record: ImpersonationRecord) -> ImpersonationRecord:
        """Persiste uma sessão de impersonação em memória."""
        self.impersonations[record.session_id] = record
        return record

    async def get_impersonation_by_hash(
        self,
        company_id: str,
        actor_user_id: str,
        token_hash: str,
    ) -> ImpersonationRecord | None:
        """Resolve sessão ativa em memória sem expor token."""
        return next(
            (
                record
                for record in self.impersonations.values()
                if record.company_id == company_id
                and record.actor_user_id == actor_user_id
                and record.token_hash == token_hash
                and record.revoked_at is None
            ),
            None,
        )

    async def revoke_impersonation(
        self,
        company_id: str,
        actor_user_id: str,
        session_id: str,
    ) -> None:
        """Marca a sessão de impersonação como revogada."""
        record = self.impersonations.get(session_id)
        if (
            record is not None
            and record.company_id == company_id
            and record.actor_user_id == actor_user_id
        ):
            record.revoked_at = datetime.now(timezone.utc)

    async def append_audit_event(
        self,
        company_id: str,
        actor_user_id: str,
        action: str,
        target_user_id: str | None,
        context: dict[str, Any] | None = None,
        *,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> None:
        """Acrescenta evento imutável à lista de auditoria."""
        self.audit_events.append(
            {
                "company_id": company_id,
                "actor_user_id": actor_user_id,
                "action": action,
                "subject_user_id": target_user_id,
                "target_user_id": target_user_id,
                "context": dict(context or {}),
                "before": dict(before or {}),
                "after": dict(after or {}),
                "request_id": request_id or f"req-{uuid.uuid4()}",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    async def list_audit_events(
        self,
        company_id: str,
        subject_user_id: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Lista eventos imutáveis filtrados por empresa e sujeito."""
        rows = [
            dict(event)
            for event in self.audit_events
            if event["company_id"] == company_id
            and event.get("subject_user_id") == subject_user_id
        ]
        return rows[-limit:]

    async def save_simulated_trade(
        self,
        company_id: str,
        user_id: str,
        trade: dict[str, Any],
    ) -> dict[str, Any]:
        """Persiste cópia do trade sintético em memória."""
        stored = dict(trade)
        self.simulated_trades.setdefault((company_id, user_id), []).append(stored)
        return stored

    async def list_simulated_trades(
        self,
        company_id: str,
        user_id: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Lista histórico sintético isolado pela chave composta."""
        return list(self.simulated_trades.get((company_id, user_id), [])[-limit:])

    async def update_simulated_trade(
        self,
        company_id: str,
        user_id: str,
        trade_id: str,
        changes: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Atualiza somente um trade pertencente à chave composta informada."""
        rows = self.simulated_trades.get((company_id, user_id), [])
        for index, trade in enumerate(rows):
            if str(trade.get("id")) != trade_id:
                continue
            updated = {**trade, **changes}
            rows[index] = updated
            return dict(updated)
        return None

    async def delete_simulated_trade(
        self,
        company_id: str,
        user_id: str,
        trade_id: str,
    ) -> dict[str, Any] | None:
        """Exclui pelo id da linha ou pelo order_id espelhado da corretora."""
        rows = self.simulated_trades.get((company_id, user_id), [])
        for index, trade in enumerate(rows):
            identifiers = {
                str(trade.get("id")),
                str(trade.get("broker_order_id") or ""),
            }
            if trade_id in identifiers:
                removed = dict(trade)
                del rows[index]
                return removed
        return None

    async def clear_simulated_trades(
        self,
        company_id: str,
        user_id: str,
    ) -> int:
        """Remove todo o histórico sintético da chave composta informada."""
        rows = self.simulated_trades.get((company_id, user_id), [])
        count = len(rows)
        self.simulated_trades[(company_id, user_id)] = []
        return count

    async def list_revenue_events(
        self,
        company_id: str,
        start_at: datetime,
        end_at: datetime,
    ) -> list[dict[str, Any]]:
        """Lista eventos financeiros em memória pelo tenant e período."""
        return [
            dict(event)
            for event in self.revenue_events
            if event["company_id"] == company_id
            and start_at <= event["occurred_at"] <= end_at
        ]

    async def list_lifecycle_events(
        self,
        company_id: str,
        start_at: datetime,
        end_at: datetime,
    ) -> list[dict[str, Any]]:
        """Lista transições em memória pelo tenant e período."""
        return [
            dict(event)
            for event in self.lifecycle_events
            if event["company_id"] == company_id
            and start_at <= event["occurred_at"] <= end_at
        ]

    async def append_lifecycle_event(
        self,
        company_id: str,
        user_id: str,
        event_type: str,
        occurred_at: datetime,
    ) -> None:
        """Registra uma transição de acesso em memória."""
        self.lifecycle_events.append(
            {
                "company_id": company_id,
                "user_id": user_id,
                "event_type": event_type,
                "occurred_at": occurred_at,
            }
        )
