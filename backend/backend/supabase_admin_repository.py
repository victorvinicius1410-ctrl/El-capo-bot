"""Persistência administrativa assíncrona no Supabase."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import httpx

from backend.admin_models import (
    AccountType,
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

# Coluna `marketing_simulated_trades.id` é UUID; IDs da Bullex (numéricos) ou
# rótulos sintéticos geram 400 no PostgREST se usados em `id=eq.…`.
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_POSTGRES_INT_MAX = 2_147_483_647


def _is_uuid(value: str) -> bool:
    """Retorna True quando o valor é um UUID canônico de 36 caracteres."""
    return bool(_UUID_RE.match(str(value or "").strip()))


def resolve_synthetic_sequence_from_trade(trade: dict[str, Any]) -> int | None:
    """
    Extrai ``synthetic_sequence`` de um trade quando for seguro para INTEGER.

    Args:
        trade: Payload com ``id`` estilo ``synthetic-000012`` ou
            ``_synthetic_sequence`` explícito.

    Returns:
        Sequência positiva cabível em INTEGER do Postgres, ou None para o
        chamador alocar o próximo valor (ex.: order_id Bullex > 2^31-1).
    """
    explicit = trade.get("_synthetic_sequence")
    if explicit is not None:
        try:
            parsed = int(explicit)
        except (TypeError, ValueError):
            return None
        if 0 < parsed <= _POSTGRES_INT_MAX:
            return parsed
        return None

    raw_id = str(trade.get("id") or "").strip()
    if not raw_id.startswith("synthetic-"):
        return None
    suffix = raw_id.rsplit("-", 1)[-1]
    if not suffix.isdigit():
        return None
    parsed = int(suffix)
    if 0 < parsed <= _POSTGRES_INT_MAX:
        return parsed
    return None


class SupabaseAdminRepository(AdminRepository):
    """Implementa gestão de Auth e perfis usando apenas credenciais do servidor."""

    def __init__(self, supabase_url: str, service_role_key: str) -> None:
        self.supabase_url = supabase_url.rstrip("/")
        self.service_role_key = service_role_key
        self.headers = {
            "apikey": service_role_key,
            "Authorization": f"Bearer {service_role_key}",
            "Content-Type": "application/json",
        }
        self._client = httpx.AsyncClient(timeout=20.0)

    async def create_client(self, company_id: str, payload: ClientCreate) -> ClientRecord:
        """Cria usuário pelo Auth Admin API e perfil na mesma empresa."""
        auth_user = await self._request(
            "POST",
            "/auth/v1/admin/users",
            json={
                "email": payload.email.strip().lower(),
                "password": payload.password,
                "email_confirm": True,
                "user_metadata": {"name": payload.name.strip()},
                "app_metadata": {
                    "company_id": company_id,
                    "role": "authenticated",
                    "is_admin": False,
                },
            },
        )
        user_id = str(auth_user["id"])
        now = datetime.now(timezone.utc)
        row = {
            "user_id": user_id,
            "company_id": company_id,
            "name": payload.name.strip(),
            "email": payload.email.strip().lower(),
            "phone": payload.phone,
            "trader_id": payload.trader_id.strip(),
            "account_type": payload.account_type.value,
            "payment_status": payload.payment_status.value,
            "plan_id": payload.plan_id,
            "plan_name": "A definir",
            "plan_status": _plan_status(payload.account_type, payload.payment_status),
            "grant_access": _initial_access(payload.account_type, payload.payment_status),
            "approval_status": ApprovalStatus.APPROVED.value,
            "is_admin": False,
            "marketing_mode": payload.marketing_mode.value if payload.marketing_mode else None,
            "marketing_win_rate": payload.marketing_win_rate,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
        try:
            rows = await self._request(
                "POST",
                "/rest/v1/user_access_profiles?on_conflict=user_id",
                json=row,
                headers={"Prefer": "resolution=merge-duplicates,return=representation"},
            )
        except Exception as exc:
            if not _is_missing_approval_status_column(exc):
                raise
            row.pop("approval_status", None)
            rows = await self._request(
                "POST",
                "/rest/v1/user_access_profiles?on_conflict=user_id",
                json=row,
                headers={"Prefer": "resolution=merge-duplicates,return=representation"},
            )
        return _client_from_row(rows[0] if rows else row)

    async def get_client(self, company_id: str, user_id: str) -> ClientRecord | None:
        """Busca perfil usando filtros explícitos de empresa e usuário."""
        rows = await self._fetch_client_rows(
            (
                "/rest/v1/user_access_profiles"
                f"?company_id=eq.{quote(company_id, safe='')}"
                f"&user_id=eq.{quote(user_id, safe='')}"
                "&limit=1"
            )
        )
        return _client_from_row(rows[0]) if rows else None

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
        """Lista perfis paginados e isolados pela empresa com filtros de segmento."""
        path = (
            "/rest/v1/user_access_profiles"
            f"?company_id=eq.{quote(company_id, safe='')}"
            "&is_admin=eq.false"
            f"&order={order_by if order_by in {'created_at', 'updated_at', 'name', 'email'} else 'created_at'}."
            f"{order_direction if order_direction in {'asc', 'desc'} else 'desc'}"
            f"&limit={limit}&offset={offset}"
        )
        if account_type:
            path += f"&account_type=eq.{quote(account_type, safe='')}"
        if not include_deleted:
            path += "&deleted_at=is.null"
        if approval_status:
            path += f"&approval_status=eq.{quote(approval_status, safe='')}"
        if exclude_approval_status:
            path += f"&approval_status=neq.{quote(exclude_approval_status, safe='')}"
        if include_deleted and grant_access is False:
            # Inativos: sem acesso OU soft-deleted (exclui pending via filtro acima).
            path += "&or=(grant_access.eq.false,deleted_at.not.is.null)"
        elif grant_access is not None:
            path += f"&grant_access=eq.{str(grant_access).lower()}"
        if search:
            normalized = quote(f"*{search.strip()}*", safe="*")
            path += f"&or=(name.ilike.{normalized},email.ilike.{normalized},trader_id.ilike.{normalized})"
        try:
            rows = await self._fetch_client_rows(path)
        except Exception as exc:
            # Ambientes sem coluna approval_status: cai no filtro em memória.
            if approval_status or exclude_approval_status:
                if not _is_missing_approval_status_column(exc):
                    raise
                legacy_path = (
                    "/rest/v1/user_access_profiles"
                    f"?company_id=eq.{quote(company_id, safe='')}"
                    "&is_admin=eq.false"
                    f"&order={order_by if order_by in {'created_at', 'updated_at', 'name', 'email'} else 'created_at'}."
                    f"{order_direction if order_direction in {'asc', 'desc'} else 'desc'}"
                    f"&limit={min(100, max(limit + offset, limit))}&offset=0"
                )
                if not include_deleted:
                    legacy_path += "&deleted_at=is.null"
                if include_deleted and grant_access is False:
                    legacy_path += "&or=(grant_access.eq.false,deleted_at.not.is.null)"
                elif grant_access is not None:
                    legacy_path += f"&grant_access=eq.{str(grant_access).lower()}"
                rows = await self._fetch_client_rows(legacy_path)
                clients = [_client_from_row(row) for row in rows]
                if approval_status == "pending":
                    clients = [
                        item
                        for item in clients
                        if item.approval_status == ApprovalStatus.PENDING
                        and not item.grant_access
                        and item.deleted_at is None
                    ]
                if exclude_approval_status == "pending":
                    clients = [
                        item
                        for item in clients
                        if item.approval_status != ApprovalStatus.PENDING
                    ]
                return clients[offset : offset + limit]
            raise
        return [_client_from_row(row) for row in rows]

    async def update_client(
        self,
        company_id: str,
        user_id: str,
        payload: ClientUpdate,
    ) -> ClientRecord | None:
        """Atualiza Auth quando necessário e os campos de perfil informados."""
        auth_body: dict[str, Any] = {}
        if payload.email is not None:
            auth_body["email"] = payload.email.strip().lower()
        if payload.password is not None:
            auth_body["password"] = payload.password
        if payload.name is not None:
            auth_body["user_metadata"] = {"name": payload.name.strip()}
        if auth_body:
            await self._request("PUT", f"/auth/v1/admin/users/{quote(user_id, safe='')}", json=auth_body)

        profile_body: dict[str, Any] = {}
        for field_name in ("name", "phone", "trader_id", "plan_id", "marketing_win_rate"):
            value = getattr(payload, field_name)
            if value is not None:
                profile_body[field_name] = value.strip() if isinstance(value, str) else value
        if payload.email is not None:
            profile_body["email"] = payload.email.strip().lower()
        if payload.account_type is not None:
            profile_body["account_type"] = payload.account_type.value
        if payload.payment_status is not None:
            profile_body["payment_status"] = payload.payment_status.value
        if payload.marketing_mode is not None:
            profile_body["marketing_mode"] = payload.marketing_mode.value
        if not profile_body:
            return await self.get_client(company_id, user_id)
        profile_body["updated_at"] = datetime.now(timezone.utc).isoformat()
        rows = await self._request(
            "PATCH",
            (
                "/rest/v1/user_access_profiles"
                f"?company_id=eq.{quote(company_id, safe='')}"
                f"&user_id=eq.{quote(user_id, safe='')}"
            ),
            json=profile_body,
            headers={"Prefer": "return=representation"},
        )
        return _client_from_row(rows[0]) if rows else None

    async def save_client(self, record: ClientRecord) -> ClientRecord:
        """Persiste o estado de acesso calculado pelo service."""
        body = {
            "account_type": record.account_type.value,
            "payment_status": record.payment_status.value,
            "plan_id": record.plan_id,
            "plan_name": record.plan_name,
            "plan_status": _plan_status(record.account_type, record.payment_status),
            "grant_access": record.grant_access,
            "approval_status": record.approval_status.value,
            "expires_at": record.expires_at.isoformat() if record.expires_at else None,
            "deleted_at": record.deleted_at.isoformat() if record.deleted_at else None,
            "marketing_mode": record.marketing_mode.value if record.marketing_mode else None,
            "marketing_win_rate": record.marketing_win_rate,
            "updated_at": record.updated_at.isoformat(),
        }
        path = (
            "/rest/v1/user_access_profiles"
            f"?company_id=eq.{quote(record.company_id, safe='')}"
            f"&user_id=eq.{quote(record.user_id, safe='')}"
        )
        try:
            rows = await self._request(
                "PATCH",
                path,
                json=body,
                headers={"Prefer": "return=representation"},
            )
        except Exception as exc:
            if not _is_missing_approval_status_column(exc):
                raise
            body.pop("approval_status", None)
            rows = await self._request(
                "PATCH",
                path,
                json=body,
                headers={"Prefer": "return=representation"},
            )
        if record.deleted_at is not None:
            await self._request(
                "PUT",
                f"/auth/v1/admin/users/{quote(record.user_id, safe='')}",
                json={"ban_duration": "876000h"},
            )
        return _client_from_row(rows[0]) if rows else record

    async def create_admin(self, record: AdminRecord, password: str) -> AdminRecord:
        """Cria administrador, cargo e permissões dentro da empresa."""
        auth_user = await self._request(
            "POST",
            "/auth/v1/admin/users",
            json={
                "email": record.email,
                "password": password,
                "email_confirm": True,
                "user_metadata": {"name": record.name},
                "app_metadata": {
                    "company_id": record.company_id,
                    "role": "admin",
                    "is_admin": True,
                },
            },
        )
        record.user_id = str(auth_user["id"])
        await self._request(
            "POST",
            "/rest/v1/user_access_profiles?on_conflict=user_id",
            json={
                "user_id": record.user_id,
                "company_id": record.company_id,
                "name": record.name,
                "email": record.email,
                "account_type": "client",
                "payment_status": "not_required",
                "plan_name": "Administrativo",
                "plan_status": "active",
                "grant_access": True,
                "is_admin": True,
            },
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
        )
        role_rows = await self._request(
            "POST",
            "/rest/v1/security_roles?on_conflict=company_id,role_key",
            json={
                "company_id": record.company_id,
                "role_key": record.role_id,
                "name": record.job_title,
                "can_manage_all_roles": record.manageable_role_ids is None,
            },
            headers={"Prefer": "resolution=merge-duplicates,return=representation"},
        )
        role_id = str(role_rows[0]["id"])
        record.role_id = role_id
        await self._request(
            "POST",
            "/rest/v1/admin_role_assignments",
            json={
                "company_id": record.company_id,
                "user_id": record.user_id,
                "role_id": role_id,
                "job_title": record.job_title,
            },
        )
        permission_rows = [
            {
                "company_id": record.company_id,
                "role_id": role_id,
                "permission_key": permission.value,
            }
            for permission in record.permissions
        ]
        if permission_rows:
            await self._request(
                "POST",
                "/rest/v1/role_permissions?on_conflict=company_id,role_id,permission_key",
                json=permission_rows,
                headers={"Prefer": "resolution=ignore-duplicates,return=minimal"},
            )
        if record.manageable_role_ids:
            await self._request(
                "POST",
                "/rest/v1/role_assignable_roles?on_conflict=company_id,actor_role_id,target_role_id",
                json=[
                    {
                        "company_id": record.company_id,
                        "actor_role_id": role_id,
                        "target_role_id": target_role_id,
                    }
                    for target_role_id in record.manageable_role_ids
                ],
                headers={"Prefer": "resolution=ignore-duplicates,return=minimal"},
            )
        return record

    async def list_admins(self, company_id: str) -> list[AdminRecord]:
        """Lista administradores ativos da empresa."""
        profiles = await self._request(
            "GET",
            (
                "/rest/v1/user_access_profiles"
                f"?company_id=eq.{quote(company_id, safe='')}"
                "&is_admin=eq.true&deleted_at=is.null"
                "&select=user_id,name,email"
            ),
        )
        assignments = await self._request(
            "GET",
            (
                "/rest/v1/admin_role_assignments"
                f"?company_id=eq.{quote(company_id, safe='')}"
                "&deleted_at=is.null&select=user_id,role_id,job_title"
            ),
        )
        role_permissions = await self._request(
            "GET",
            (
                "/rest/v1/role_permissions"
                f"?company_id=eq.{quote(company_id, safe='')}"
                "&select=role_id,permission_key"
            ),
        )
        role_scopes = await self._request(
            "GET",
            (
                "/rest/v1/role_assignable_roles"
                f"?company_id=eq.{quote(company_id, safe='')}"
                "&select=actor_role_id,target_role_id"
            ),
        )
        roles = await self._request(
            "GET",
            (
                "/rest/v1/security_roles"
                f"?company_id=eq.{quote(company_id, safe='')}"
                "&select=id,is_owner,can_manage_all_roles"
            ),
        )
        assignment_by_user = {str(row["user_id"]): row for row in assignments}
        all_scope_roles = {
            str(row["id"])
            for row in roles
            if row.get("is_owner") or row.get("can_manage_all_roles")
        }
        permissions_by_role: dict[str, set[AdminPermission]] = {}
        for row in role_permissions:
            try:
                permission = AdminPermission(str(row["permission_key"]))
            except ValueError:
                continue
            permissions_by_role.setdefault(str(row["role_id"]), set()).add(permission)
        scopes_by_role: dict[str, set[str]] = {}
        for row in role_scopes:
            scopes_by_role.setdefault(str(row["actor_role_id"]), set()).add(
                str(row["target_role_id"])
            )
        return [
            AdminRecord(
                user_id=str(profile["user_id"]),
                company_id=company_id,
                name=str(profile.get("name") or ""),
                email=str(profile.get("email") or ""),
                job_title=str(assignment_by_user.get(str(profile["user_id"]), {}).get("job_title") or ""),
                role_id=str(assignment_by_user.get(str(profile["user_id"]), {}).get("role_id") or ""),
                permissions=frozenset(
                    permissions_by_role.get(
                        str(assignment_by_user.get(str(profile["user_id"]), {}).get("role_id") or ""),
                        set(),
                    )
                ),
                manageable_role_ids=(
                    None
                    if str(
                        assignment_by_user.get(str(profile["user_id"]), {}).get("role_id") or ""
                    )
                    in all_scope_roles
                    else frozenset(
                        scopes_by_role.get(
                            str(
                                assignment_by_user.get(str(profile["user_id"]), {}).get("role_id")
                                or ""
                            ),
                            set(),
                        )
                    )
                ),
            )
            for profile in profiles
        ]

    async def get_admin(self, company_id: str, user_id: str) -> AdminRecord | None:
        """Busca administrador na empresa sem permitir consulta cross-tenant."""
        admins = await self.list_admins(company_id)
        return next((admin for admin in admins if admin.user_id == user_id), None)

    async def update_admin(
        self,
        company_id: str,
        user_id: str,
        payload: AdminUpdate,
    ) -> AdminRecord | None:
        """Atualiza identidade, cargo e permissões administrativas."""
        record = await self.get_admin(company_id, user_id)
        if record is None:
            return None
        auth_body: dict[str, Any] = {}
        if payload.email is not None:
            auth_body["email"] = payload.email.strip().lower()
            record.email = auth_body["email"]
        if payload.password is not None:
            auth_body["password"] = payload.password
        if payload.name is not None:
            auth_body["user_metadata"] = {"name": payload.name.strip()}
            record.name = payload.name.strip()
        if auth_body:
            await self._request("PUT", f"/auth/v1/admin/users/{quote(user_id, safe='')}", json=auth_body)
        profile_body = {
            key: value
            for key, value in {
                "name": record.name if payload.name is not None else None,
                "email": record.email if payload.email is not None else None,
            }.items()
            if value is not None
        }
        if profile_body:
            await self._request(
                "PATCH",
                (
                    "/rest/v1/user_access_profiles"
                    f"?company_id=eq.{quote(company_id, safe='')}"
                    f"&user_id=eq.{quote(user_id, safe='')}"
                ),
                json=profile_body,
            )
        if payload.job_title is not None:
            role_rows = await self._request(
                "POST",
                "/rest/v1/security_roles?on_conflict=company_id,role_key",
                json={
                    "company_id": company_id,
                    "role_key": _role_key(payload.job_title),
                    "name": payload.job_title.strip(),
                },
                headers={"Prefer": "resolution=merge-duplicates,return=representation"},
            )
            record.role_id = str(role_rows[0]["id"])
            record.job_title = payload.job_title.strip()
            await self._request(
                "PATCH",
                (
                    "/rest/v1/admin_role_assignments"
                    f"?company_id=eq.{quote(company_id, safe='')}"
                    f"&user_id=eq.{quote(user_id, safe='')}"
                ),
                json={"role_id": record.role_id, "job_title": record.job_title},
            )
        if payload.permissions is not None:
            record.permissions = payload.permissions
            await self._request(
                "DELETE",
                (
                    "/rest/v1/role_permissions"
                    f"?company_id=eq.{quote(company_id, safe='')}"
                    f"&role_id=eq.{quote(record.role_id, safe='')}"
                ),
            )
            if payload.permissions:
                await self._request(
                    "POST",
                    "/rest/v1/role_permissions",
                    json=[
                        {
                            "company_id": company_id,
                            "role_id": record.role_id,
                            "permission_key": permission.value,
                        }
                        for permission in payload.permissions
                    ],
                )
        if payload.manageable_roles_supplied:
            record.manageable_role_ids = payload.manageable_role_ids
            await self._request(
                "PATCH",
                (
                    "/rest/v1/security_roles"
                    f"?company_id=eq.{quote(company_id, safe='')}"
                    f"&id=eq.{quote(record.role_id, safe='')}"
                ),
                json={"can_manage_all_roles": payload.manageable_role_ids is None},
            )
            await self._request(
                "DELETE",
                (
                    "/rest/v1/role_assignable_roles"
                    f"?company_id=eq.{quote(company_id, safe='')}"
                    f"&actor_role_id=eq.{quote(record.role_id, safe='')}"
                ),
            )
            if payload.manageable_role_ids:
                await self._request(
                    "POST",
                    "/rest/v1/role_assignable_roles",
                    json=[
                        {
                            "company_id": company_id,
                            "actor_role_id": record.role_id,
                            "target_role_id": target_role_id,
                        }
                        for target_role_id in payload.manageable_role_ids
                    ],
                )
        return record

    async def save_admin(self, record: AdminRecord) -> AdminRecord:
        """Persiste desativação administrativa sem apagar auditoria."""
        if record.deleted_at is not None:
            deleted_at = record.deleted_at.isoformat()
            await self._request(
                "PATCH",
                (
                    "/rest/v1/user_access_profiles"
                    f"?company_id=eq.{quote(record.company_id, safe='')}"
                    f"&user_id=eq.{quote(record.user_id, safe='')}"
                ),
                json={"deleted_at": deleted_at, "grant_access": False, "is_admin": False},
            )
            await self._request(
                "PATCH",
                (
                    "/rest/v1/admin_role_assignments"
                    f"?company_id=eq.{quote(record.company_id, safe='')}"
                    f"&user_id=eq.{quote(record.user_id, safe='')}"
                ),
                json={"deleted_at": deleted_at},
            )
            await self._request(
                "PUT",
                f"/auth/v1/admin/users/{quote(record.user_id, safe='')}",
                json={
                    "ban_duration": "876000h",
                    "app_metadata": {
                        "company_id": record.company_id,
                        "role": "authenticated",
                        "is_admin": False,
                    },
                },
            )
        return record

    async def save_impersonation(self, record: ImpersonationRecord) -> ImpersonationRecord:
        """Persiste token somente como hash."""
        await self._request(
            "POST",
            "/rest/v1/support_access_sessions",
            json={
                "id": record.session_id,
                "company_id": record.company_id,
                "actor_user_id": record.actor_user_id,
                "subject_user_id": record.target_user_id,
                "reason": record.reason,
                "token_hash": record.token_hash,
                "scopes": sorted(record.scopes),
                "expires_at": record.expires_at.isoformat(),
            },
        )
        return record

    async def get_impersonation_by_hash(
        self,
        company_id: str,
        actor_user_id: str,
        token_hash: str,
    ) -> ImpersonationRecord | None:
        """Busca sessão pelo hash com filtros explícitos de tenant e ator."""
        rows = await self._request(
            "GET",
            (
                "/rest/v1/support_access_sessions"
                f"?company_id=eq.{quote(company_id, safe='')}"
                f"&actor_user_id=eq.{quote(actor_user_id, safe='')}"
                f"&token_hash=eq.{quote(token_hash, safe='')}"
                "&revoked_at=is.null"
                "&select=id,company_id,actor_user_id,subject_user_id,reason,"
                "expires_at,token_hash,scopes,revoked_at&limit=1"
            ),
        )
        if not rows:
            return None
        row = rows[0]
        return ImpersonationRecord(
            session_id=str(row["id"]),
            company_id=str(row["company_id"]),
            actor_user_id=str(row["actor_user_id"]),
            target_user_id=str(row["subject_user_id"]),
            reason=str(row["reason"]),
            expires_at=_parse_datetime(row["expires_at"]) or datetime.now(timezone.utc),
            token_hash=str(row["token_hash"]),
            scopes=frozenset(str(scope) for scope in row.get("scopes") or []),
            revoked_at=_parse_datetime(row.get("revoked_at")),
        )

    async def revoke_impersonation(
        self,
        company_id: str,
        actor_user_id: str,
        session_id: str,
    ) -> None:
        """Revoga somente a sessão pertencente ao ator e à empresa."""
        await self._request(
            "PATCH",
            (
                "/rest/v1/support_access_sessions"
                f"?company_id=eq.{quote(company_id, safe='')}"
                f"&actor_user_id=eq.{quote(actor_user_id, safe='')}"
                f"&id=eq.{quote(session_id, safe='')}"
            ),
            json={"revoked_at": datetime.now(timezone.utc).isoformat()},
        )

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
        """Registra auditoria append-only sem dados sensíveis."""
        await self._request(
            "POST",
            "/rest/v1/admin_customer_events",
            json={
                "company_id": company_id,
                "actor_user_id": actor_user_id,
                "subject_user_id": target_user_id,
                "action": action,
                "context": context or {},
                "before": before or {},
                "after": after or {},
                "request_id": request_id or f"req-{uuid4()}",
            },
        )

    async def list_audit_events(
        self,
        company_id: str,
        subject_user_id: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Lista histórico administrativo imutável e isolado pelo tenant."""
        return await self._request(
            "GET",
            (
                "/rest/v1/admin_customer_events"
                f"?company_id=eq.{quote(company_id, safe='')}"
                f"&subject_user_id=eq.{quote(subject_user_id, safe='')}"
                "&select=actor_user_id,subject_user_id,action,before,after,request_id,created_at"
                f"&order=created_at.desc&limit={limit}"
            ),
        )

    async def save_simulated_trade(
        self,
        company_id: str,
        user_id: str,
        trade: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Persiste operação sintética na tabela isolada.

        A sequência INTEGER não pode ser derivada de ``order_id`` Bullex
        (valores > 2^31-1). Nesses casos aloca ``max(sequence)+1``.
        Em conflito de sequência (corrida), tenta o próximo valor uma vez.
        """
        if not _is_uuid(str(company_id or "")) or not _is_uuid(str(user_id or "")):
            raise ValueError(
                f"company_id/user_id inválidos para marketing_simulated_trades "
                f"(company_id={company_id!r} user_id={user_id!r})"
            )
        sequence = resolve_synthetic_sequence_from_trade(trade)
        if sequence is None:
            sequence = await self._next_synthetic_sequence(company_id, user_id)
        payload = {
            "company_id": company_id,
            "user_id": user_id,
            "synthetic_sequence": sequence,
            "result": str(trade["result"]).strip().upper(),
            "asset": str(trade["asset"]),
            "direction": str(trade["direction"]).strip().upper(),
            "amount": float(trade["amount"]),
            "payout": int(round(float(trade["payout"]))),
            "profit": float(trade["profit"]),
            "is_simulated": True,
            "source": "marketing_demo",
            "created_at": trade["created_at"],
        }
        broker_order_id = str(trade.get("broker_order_id") or "").strip()
        if broker_order_id:
            payload["broker_order_id"] = broker_order_id
        try:
            rows = await self._insert_simulated_trade(payload)
        except RuntimeError as exc:
            message = str(exc)
            if "23505" in message or "duplicate" in message.lower() or "409" in message:
                payload["synthetic_sequence"] = await self._next_synthetic_sequence(
                    company_id, user_id
                )
                rows = await self._insert_simulated_trade(payload)
            else:
                raise
        stored = dict(trade)
        stored["_synthetic_sequence"] = int(payload["synthetic_sequence"])
        if rows:
            return _simulated_trade_from_row(rows[0])
        return stored

    async def _insert_simulated_trade(
        self,
        payload: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """
        Insere o trade sintético tolerando ambiente sem ``broker_order_id``.

        A coluna chega por ``migration_marketing_broker_order_id.sql``. Enquanto
        ela não estiver aplicada, o espelho é gravado sem o vínculo em vez de
        derrubar a operação ao vivo.

        Args:
            payload: Corpo já validado da linha em ``marketing_simulated_trades``.

        Returns:
            Linhas retornadas pelo PostgREST.

        Raises:
            RuntimeError: Erros que não sejam a ausência da coluna.
        """
        try:
            return await self._request(
                "POST",
                "/rest/v1/marketing_simulated_trades",
                json=payload,
                headers={"Prefer": "return=representation"},
            )
        except RuntimeError as exc:
            if "broker_order_id" not in payload or not _is_missing_broker_column(exc):
                raise
            payload.pop("broker_order_id")
            return await self._request(
                "POST",
                "/rest/v1/marketing_simulated_trades",
                json=payload,
                headers={"Prefer": "return=representation"},
            )

    async def _next_synthetic_sequence(self, company_id: str, user_id: str) -> int:
        """
        Aloca o próximo ``synthetic_sequence`` do usuário no tenant.

        Args:
            company_id: Empresa da sessão autenticada.
            user_id: Usuário dono do histórico sintético.

        Returns:
            Próximo inteiro positivo disponível (1 se a lista estiver vazia).
        """
        rows = await self._request(
            "GET",
            (
                "/rest/v1/marketing_simulated_trades"
                f"?company_id=eq.{quote(company_id, safe='')}"
                f"&user_id=eq.{quote(user_id, safe='')}"
                "&select=synthetic_sequence"
                "&order=synthetic_sequence.desc&limit=1"
            ),
        )
        if not rows:
            return 1
        try:
            current = int(rows[0].get("synthetic_sequence") or 0)
        except (TypeError, ValueError):
            current = 0
        nxt = current + 1
        if nxt > _POSTGRES_INT_MAX:
            raise RuntimeError("Limite de synthetic_sequence atingido para o usuário")
        return nxt

    async def list_simulated_trades(
        self,
        company_id: str,
        user_id: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Lista histórico sintético sem consultar operações reais."""
        # `select=*` mantém a leitura compatível com bancos que ainda não têm
        # a coluna `broker_order_id`; o mapeamento descarta o que não é usado.
        rows = await self._request(
            "GET",
            (
                "/rest/v1/marketing_simulated_trades"
                f"?company_id=eq.{quote(company_id, safe='')}"
                f"&user_id=eq.{quote(user_id, safe='')}"
                "&select=*"
                f"&order=synthetic_sequence.asc&limit={limit}"
            ),
        )
        return [_simulated_trade_from_row(row) for row in rows]

    async def update_simulated_trade(
        self,
        company_id: str,
        user_id: str,
        trade_id: str,
        changes: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Atualiza trade sintético com filtros obrigatórios de tenant e usuário."""
        if not _is_uuid(trade_id):
            return None
        rows = await self._request(
            "PATCH",
            (
                "/rest/v1/marketing_simulated_trades"
                f"?company_id=eq.{quote(company_id, safe='')}"
                f"&user_id=eq.{quote(user_id, safe='')}"
                f"&id=eq.{quote(trade_id, safe='')}"
            ),
            json={
                field_name: value
                for field_name, value in changes.items()
                if field_name
                in {"result", "asset", "direction", "amount", "payout", "profit"}
            },
            headers={"Prefer": "return=representation"},
        )
        return _simulated_trade_from_row(rows[0]) if rows else None

    async def delete_simulated_trade(
        self,
        company_id: str,
        user_id: str,
        trade_id: str,
    ) -> bool:
        """
        Exclui trade sintético com filtros obrigatórios de tenant e usuário.

        Aceita o UUID da linha ou o ``order_id`` da Bullex; neste caso a busca
        usa ``broker_order_id``, porque filtrar a coluna UUID com um id
        numérico devolve HTTP 400.

        Args:
            company_id: Empresa da sessão autenticada.
            user_id: Usuário dono do histórico sintético.
            trade_id: UUID da linha ou order_id da corretora.

        Returns:
            True quando uma linha foi excluída.
        """
        if _is_uuid(trade_id):
            column, value = "id", trade_id
        else:
            column, value = "broker_order_id", str(trade_id or "").strip()
        if not value:
            return False
        try:
            rows = await self._request(
                "DELETE",
                (
                    "/rest/v1/marketing_simulated_trades"
                    f"?company_id=eq.{quote(company_id, safe='')}"
                    f"&user_id=eq.{quote(user_id, safe='')}"
                    f"&{column}=eq.{quote(value, safe='')}"
                ),
                headers={"Prefer": "return=representation"},
            )
        except RuntimeError as exc:
            if column == "broker_order_id" and _is_missing_broker_column(exc):
                # Migration pendente: o chamador usa o fallback do robô.
                return False
            raise
        return bool(rows)

    async def clear_simulated_trades(
        self,
        company_id: str,
        user_id: str,
    ) -> int:
        """Remove todo o histórico sintético do usuário no tenant autenticado."""
        rows = await self._request(
            "DELETE",
            (
                "/rest/v1/marketing_simulated_trades"
                f"?company_id=eq.{quote(company_id, safe='')}"
                f"&user_id=eq.{quote(user_id, safe='')}"
            ),
            headers={"Prefer": "return=representation"},
        )
        return len(rows)

    async def list_revenue_events(
        self,
        company_id: str,
        start_at: datetime,
        end_at: datetime,
    ) -> list[dict[str, Any]]:
        """Lista vendas e renovações do tenant dentro do período."""
        rows = await self._request(
            "GET",
            (
                "/rest/v1/subscription_revenue_events"
                f"?company_id=eq.{quote(company_id, safe='')}"
                f"&occurred_at=gte.{quote(start_at.isoformat(), safe='')}"
                f"&occurred_at=lte.{quote(end_at.isoformat(), safe='')}"
                "&status=eq.confirmed"
                "&select=user_id,event_type,amount,currency,occurred_at"
                "&order=occurred_at.asc"
            ),
        )
        return [
            {
                **row,
                "amount": float(row["amount"]),
            }
            for row in rows
        ]

    async def list_lifecycle_events(
        self,
        company_id: str,
        start_at: datetime,
        end_at: datetime,
    ) -> list[dict[str, Any]]:
        """Lista transições de clientes do tenant dentro do período."""
        return await self._request(
            "GET",
            (
                "/rest/v1/client_lifecycle_events"
                f"?company_id=eq.{quote(company_id, safe='')}"
                f"&occurred_at=gte.{quote(start_at.isoformat(), safe='')}"
                f"&occurred_at=lte.{quote(end_at.isoformat(), safe='')}"
                "&select=user_id,event_type,occurred_at"
                "&order=occurred_at.asc"
            ),
        )

    async def append_lifecycle_event(
        self,
        company_id: str,
        user_id: str,
        event_type: str,
        occurred_at: datetime,
    ) -> None:
        """Persiste transição de acesso sem PII."""
        await self._request(
            "POST",
            "/rest/v1/client_lifecycle_events",
            json={
                "company_id": company_id,
                "user_id": user_id,
                "event_type": event_type,
                "occurred_at": occurred_at.isoformat(),
            },
        )

    async def _fetch_client_rows(self, path_without_select: str) -> list[dict[str, Any]]:
        """
        Lista/busca perfis tolerando bancos sem a coluna ``approval_status``.

        Args:
            path_without_select: Path PostgREST sem o parâmetro ``select``.

        Returns:
            Linhas brutas do PostgREST.
        """
        separator = "&" if "?" in path_without_select else "?"
        try:
            rows = await self._request(
                "GET",
                f"{path_without_select}{separator}select={_CLIENT_SELECT}",
            )
        except Exception as exc:
            if not _is_missing_approval_status_column(exc):
                raise
            rows = await self._request(
                "GET",
                f"{path_without_select}{separator}select={_CLIENT_SELECT_LEGACY}",
            )
        return rows if isinstance(rows, list) else []

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        merged_headers = {**self.headers, **(headers or {})}
        response = await self._client.request(
            method,
            f"{self.supabase_url}{path}",
            headers=merged_headers,
            json=json,
        )
        if response.is_error:
            detail = (response.text or "")[:800]
            raise RuntimeError(
                f"Supabase {method} {path} -> {response.status_code}: {detail}"
            )
        return response.json() if response.content else []

    async def aclose(self) -> None:
        """Fecha o client HTTP compartilhado."""
        await self._client.aclose()


_CLIENT_SELECT = (
    "user_id,company_id,name,email,phone,trader_id,account_type,payment_status,"
    "plan_name,plan_id,grant_access,approval_status,created_at,updated_at,expires_at,deleted_at,"
    "marketing_mode,marketing_win_rate"
)
_CLIENT_SELECT_LEGACY = (
    "user_id,company_id,name,email,phone,trader_id,account_type,payment_status,"
    "plan_name,plan_id,grant_access,created_at,updated_at,expires_at,deleted_at,"
    "marketing_mode,marketing_win_rate"
)


def _parse_datetime(value: Any) -> datetime | None:
    """Converte timestamps ISO do PostgREST."""
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _resolve_client_approval_status(row: dict[str, Any]) -> ApprovalStatus:
    """
    Resolve ``approval_status`` com fallback legado via ``plan_name``.

    Args:
        row: Linha de ``user_access_profiles``.

    Returns:
        Enum de aprovação do domínio administrativo.
    """
    raw = str(row.get("approval_status") or "").strip().lower()
    if raw in {status.value for status in ApprovalStatus}:
        return ApprovalStatus(raw)
    plan_name = str(row.get("plan_name") or "").strip().casefold()
    if not row.get("grant_access") and plan_name.startswith("aguardando"):
        return ApprovalStatus.PENDING
    return ApprovalStatus.APPROVED


def _client_from_row(row: dict[str, Any]) -> ClientRecord:
    """Converte uma linha do PostgREST em modelo de domínio."""
    return ClientRecord(
        user_id=str(row["user_id"]),
        company_id=str(row["company_id"]),
        name=str(row.get("name") or ""),
        email=str(row.get("email") or ""),
        phone=row.get("phone"),
        trader_id=str(row.get("trader_id") or ""),
        account_type=AccountType(str(row.get("account_type") or "client")),
        payment_status=PaymentStatus(str(row.get("payment_status") or "pending")),
        plan_name=row.get("plan_name"),
        plan_id=row.get("plan_id"),
        grant_access=bool(row.get("grant_access")),
        created_at=_parse_datetime(row.get("created_at")) or datetime.now(timezone.utc),
        updated_at=_parse_datetime(row.get("updated_at")) or datetime.now(timezone.utc),
        expires_at=_parse_datetime(row.get("expires_at")),
        deleted_at=_parse_datetime(row.get("deleted_at")),
        marketing_mode=(
            MarketingMode(str(row["marketing_mode"])) if row.get("marketing_mode") else None
        ),
        marketing_win_rate=row.get("marketing_win_rate"),
        approval_status=_resolve_client_approval_status(row),
    )


def _is_missing_broker_column(error: Exception) -> bool:
    """
    Detecta ambiente sem a coluna ``broker_order_id`` no espelho marketing.

    Args:
        error: Exceção devolvida pelo PostgREST.

    Returns:
        True quando o erro indica coluna inexistente.
    """
    message = str(error).lower()
    return "broker_order_id" in message and (
        # 42703 = undefined_column (Postgres); PGRST204 = coluna ausente no cache.
        "42703" in message
        or "pgrst204" in message
        or "pgrst100" in message
        or "does not exist" in message
        or "could not find" in message
    )


def _is_missing_approval_status_column(error: Exception) -> bool:
    """
    Detecta PostgREST/Postgres sem a coluna ``approval_status``.

    Args:
        error: Exceção devolvida pela chamada HTTP.

    Returns:
        True quando o erro indica coluna inexistente.
    """
    message = str(error).lower()
    return "approval_status" in message and (
        "42703" in message
        or "pgrst204" in message
        or "pgrst100" in message
        or "does not exist" in message
        or "could not find" in message
    )


def _simulated_trade_from_row(row: dict[str, Any]) -> dict[str, Any]:
    """Converte uma linha isolada em contrato explicitamente sintético."""
    broker_order_id = str(row.get("broker_order_id") or "").strip()
    return {
        "id": str(row["id"]),
        "result": str(row["result"]),
        "asset": str(row["asset"]),
        "direction": str(row["direction"]),
        "amount": float(row["amount"]),
        "payout": int(row["payout"]),
        "profit": float(row["profit"]),
        "created_at": str(row["created_at"]),
        "broker_order_id": broker_order_id or None,
        "_synthetic_sequence": int(row.get("synthetic_sequence") or 0),
        "is_simulated": True,
        "source": "marketing_demo",
        "account_mode": "SIMULATED_MARKETING",
        "disclaimer": "Resultados simulados — não representam operações reais",
    }


def _plan_status(account_type: AccountType, payment_status: PaymentStatus) -> str:
    """Mantém compatibilidade com o status de plano legado."""
    if account_type == AccountType.TRIAL:
        return "trial"
    if account_type == AccountType.MARKETING:
        return "active"
    return "active" if payment_status == PaymentStatus.PAID else "expired"


def _initial_access(account_type: AccountType, payment_status: PaymentStatus) -> bool:
    """Calcula acesso inicial antes das regras de expiração do service."""
    return account_type != AccountType.CLIENT or payment_status == PaymentStatus.PAID


def _role_key(job_title: str) -> str:
    """Normaliza nome de cargo para chave estável."""
    return "-".join(part for part in job_title.strip().lower().replace("_", " ").split() if part)
