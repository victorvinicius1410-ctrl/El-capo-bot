"""Validação server-side da sessão Supabase e carregamento de acesso."""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from backend.admin_models import AdminPermission

# TTL curto: cobre vários polls de /robot/state sem atrasar revoke de acesso
# por mais de ~1 min. Invalidação explícita em approve/update/delete/logout.
AUTH_CACHE_TTL_SECONDS = 45.0
AUTH_CACHE_MAX_ENTRIES = 2_000


class AuthenticationError(Exception):
    """Token ausente, inválido ou expirado."""


@dataclass(frozen=True)
class AuthenticatedUser:
    """Identidade e acesso derivados exclusivamente do servidor."""

    user_id: str
    email: str
    company_id: str
    is_admin: bool
    permissions: frozenset[AdminPermission]
    manageable_role_ids: frozenset[str] | None
    grant_access: bool
    account_type: str
    payment_status: str
    expires_at: str | None
    marketing_mode: str | None
    marketing_win_rate: int | None
    approval_status: str = "approved"


@dataclass
class _AuthCacheEntry:
    """Entrada do cache de authenticate (chave = hash do token)."""

    user: AuthenticatedUser
    expires_at: float
    user_id: str


class SupabaseAuthService:
    """Valida bearer token no Supabase Auth e resolve o perfil multi-tenant."""

    def __init__(self, supabase_url: str, service_role_key: str) -> None:
        self.supabase_url = supabase_url.rstrip("/")
        self.service_role_key = service_role_key
        # Client HTTP com keep-alive reaproveitado entre chamadas (evita
        # handshake TLS por request). Cache curto do resultado de
        # `authenticate` por token: ver docs/PERFORMANCE_SISTEMA.md.
        self._client = httpx.AsyncClient(
            timeout=10.0,
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
        )
        self._auth_cache: dict[str, _AuthCacheEntry] = {}

    async def aclose(self) -> None:
        """Fecha o client HTTP compartilhado (shutdown do processo)."""
        self._auth_cache.clear()
        await self._client.aclose()

    def auth_cache_size(self) -> int:
        """Quantidade de entradas no cache (testes / métricas)."""
        return len(self._auth_cache)

    def invalidate_token(self, access_token: str) -> None:
        """
        Remove o cache de um access token (logout / refresh).

        Args:
            access_token: Bearer token em texto (nunca logado).
        """
        if not access_token:
            return
        self._auth_cache.pop(_token_cache_key(access_token), None)

    def invalidate_user(self, user_id: str) -> None:
        """
        Invalida todas as entradas de um usuário (approve/revoke/role change).

        Args:
            user_id: UUID do usuário afetado.
        """
        target = str(user_id or "").strip()
        if not target:
            return
        stale = [key for key, entry in self._auth_cache.items() if entry.user_id == target]
        for key in stale:
            self._auth_cache.pop(key, None)

    def clear_auth_cache(self) -> None:
        """Limpa todo o cache de autenticação."""
        self._auth_cache.clear()

    async def authenticate(
        self,
        access_token: str,
        *,
        now_monotonic: float | None = None,
        ttl_seconds: float = AUTH_CACHE_TTL_SECONDS,
    ) -> AuthenticatedUser:
        """
        Valida o token e carrega perfil/permissões da mesma empresa.

        Resultado é cacheado por hash do token durante ``ttl_seconds``.
        Mutações de acesso devem chamar ``invalidate_user``.

        Args:
            access_token: JWT/access token do Supabase Auth.
            now_monotonic: Relógio injetável (testes).
            ttl_seconds: Validade do cache para esta chamada.

        Returns:
            Identidade autenticada com permissões do tenant.

        Raises:
            AuthenticationError: Se token, metadata ou perfil forem inválidos.
        """
        if not access_token:
            raise AuthenticationError("Token ausente")

        current = time.monotonic() if now_monotonic is None else now_monotonic
        cache_key = _token_cache_key(access_token)
        cached = self._auth_cache.get(cache_key)
        if cached is not None and cached.expires_at > current:
            return cached.user

        user = await self._authenticate_uncached(access_token)
        self._auth_cache[cache_key] = _AuthCacheEntry(
            user=user,
            expires_at=current + max(ttl_seconds, 0.0),
            user_id=user.user_id,
        )
        self._prune_auth_cache(current)
        return user

    async def _authenticate_uncached(self, access_token: str) -> AuthenticatedUser:
        """Resolve identidade no Supabase sem ler/escrever o cache."""
        client = self._client
        user_response = await client.get(
            f"{self.supabase_url}/auth/v1/user",
            headers={
                "apikey": self.service_role_key,
                "Authorization": f"Bearer {access_token}",
            },
        )
        if user_response.status_code != 200:
            raise AuthenticationError("Sessão inválida ou expirada")
        user_payload = user_response.json()
        user_id = str(user_payload.get("id") or "").strip()
        email = str(user_payload.get("email") or "").strip().lower()
        app_metadata = _as_dict(user_payload.get("app_metadata"))
        company_id = str(app_metadata.get("company_id") or "").strip()
        if not user_id or not email or not company_id:
            raise AuthenticationError("Sessão sem identidade empresarial")

        rest_headers = {
            "apikey": self.service_role_key,
            "Authorization": f"Bearer {self.service_role_key}",
        }
        profile = await self._load_access_profile(client, rest_headers, company_id, user_id)
        if profile is None:
            raise AuthenticationError("Perfil de acesso inexistente ou inativo")

        permissions, manageable_role_ids = await self._load_authorization(
            client,
            rest_headers,
            company_id,
            user_id,
        )
        is_admin = bool(
            profile.get("is_admin")
            or app_metadata.get("role") == "admin"
            or app_metadata.get("is_admin") is True
        )
        # Admin dono do painel recebe o catálogo completo (inclui emails.*).
        if is_admin:
            permissions = frozenset(AdminPermission)
            manageable_role_ids = None
        return AuthenticatedUser(
            user_id=user_id,
            email=email,
            company_id=company_id,
            is_admin=is_admin,
            permissions=permissions,
            manageable_role_ids=manageable_role_ids,
            grant_access=bool(profile.get("grant_access")),
            account_type=str(profile.get("account_type") or "client"),
            payment_status=str(profile.get("payment_status") or "pending"),
            expires_at=profile.get("expires_at"),
            marketing_mode=profile.get("marketing_mode"),
            marketing_win_rate=profile.get("marketing_win_rate"),
            approval_status=_resolve_approval_status(profile),
        )

    def _prune_auth_cache(self, now: float) -> None:
        """Remove entradas expiradas e limita o tamanho do cache."""
        expired = [key for key, entry in self._auth_cache.items() if entry.expires_at <= now]
        for key in expired:
            self._auth_cache.pop(key, None)
        overflow = len(self._auth_cache) - AUTH_CACHE_MAX_ENTRIES
        if overflow <= 0:
            return
        # Remove as que expiram primeiro (aproximação de LRU por TTL).
        ordered = sorted(self._auth_cache.items(), key=lambda item: item[1].expires_at)
        for key, _ in ordered[:overflow]:
            self._auth_cache.pop(key, None)

    async def _load_access_profile(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
        company_id: str,
        user_id: str,
    ) -> dict[str, Any] | None:
        """Carrega o perfil; tolera bancos sem a coluna approval_status."""
        base = (
            f"{self.supabase_url}/rest/v1/user_access_profiles"
            f"?company_id=eq.{quote(company_id, safe='')}"
            f"&user_id=eq.{quote(user_id, safe='')}"
            "&limit=1"
        )
        select_with = (
            "&select=user_id,email,grant_access,is_admin,account_type,"
            "payment_status,expires_at,deleted_at,marketing_mode,"
            "marketing_win_rate,plan_name,approval_status"
        )
        select_without = (
            "&select=user_id,email,grant_access,is_admin,account_type,"
            "payment_status,expires_at,deleted_at,marketing_mode,"
            "marketing_win_rate,plan_name"
        )
        for select in (select_with, select_without):
            response = await client.get(f"{base}{select}", headers=headers)
            if response.status_code == 200:
                profiles = response.json()
                if not profiles or profiles[0].get("deleted_at"):
                    return None
                return profiles[0]
            message = (response.text or "").lower()
            if "approval_status" in message and (
                "42703" in message or "pgrst204" in message or "does not exist" in message
            ):
                continue
            raise AuthenticationError("Falha ao validar perfil de acesso")
        raise AuthenticationError("Falha ao validar perfil de acesso")

    async def _load_authorization(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
        company_id: str,
        user_id: str,
    ) -> tuple[frozenset[AdminPermission], frozenset[str] | None]:
        assignment_response = await client.get(
            (
                f"{self.supabase_url}/rest/v1/admin_role_assignments"
                f"?company_id=eq.{quote(company_id, safe='')}"
                f"&user_id=eq.{quote(user_id, safe='')}"
                "&deleted_at=is.null&select=role_id"
            ),
            headers=headers,
        )
        if assignment_response.status_code != 200 or not assignment_response.json():
            return frozenset(), frozenset()
        role_id = str(assignment_response.json()[0]["role_id"])
        # As 3 consultas abaixo só dependem de `role_id` — em paralelo.
        permissions_response, role_response, scope_response = await asyncio.gather(
            client.get(
                (
                    f"{self.supabase_url}/rest/v1/role_permissions"
                    f"?company_id=eq.{quote(company_id, safe='')}"
                    f"&role_id=eq.{quote(role_id, safe='')}"
                    "&select=permission_key"
                ),
                headers=headers,
            ),
            client.get(
                (
                    f"{self.supabase_url}/rest/v1/security_roles"
                    f"?company_id=eq.{quote(company_id, safe='')}"
                    f"&id=eq.{quote(role_id, safe='')}"
                    "&select=can_manage_all_roles&limit=1"
                ),
                headers=headers,
            ),
            client.get(
                (
                    f"{self.supabase_url}/rest/v1/role_assignable_roles"
                    f"?company_id=eq.{quote(company_id, safe='')}"
                    f"&actor_role_id=eq.{quote(role_id, safe='')}"
                    "&select=target_role_id"
                ),
                headers=headers,
            ),
        )
        if permissions_response.status_code != 200:
            return frozenset(), frozenset()
        resolved: set[AdminPermission] = set()
        for row in permissions_response.json():
            try:
                resolved.add(AdminPermission(str(row.get("permission_key"))))
            except ValueError:
                continue
        can_manage_all = bool(
            role_response.status_code == 200
            and role_response.json()
            and role_response.json()[0].get("can_manage_all_roles")
        )
        if can_manage_all:
            return frozenset(resolved), None
        manageable = (
            frozenset(str(row["target_role_id"]) for row in scope_response.json())
            if scope_response.status_code == 200
            else frozenset()
        )
        return frozenset(resolved), manageable


def _token_cache_key(access_token: str) -> str:
    """Hash estável do token (não guarda o segredo como chave crua)."""
    return hashlib.sha256(access_token.encode("utf-8")).hexdigest()


def _as_dict(value: Any) -> dict[str, Any]:
    """Normaliza payload desconhecido para dicionário."""
    return value if isinstance(value, dict) else {}


def _resolve_approval_status(profile: dict[str, Any]) -> str:
    """
    Resolve approval_status com fallback legado (sem coluna no banco).

    Args:
        profile: Linha de user_access_profiles.

    Returns:
        pending | approved | rejected.
    """
    raw = str(profile.get("approval_status") or "").strip().lower()
    if raw in {"pending", "approved", "rejected"}:
        return raw
    plan_name = str(profile.get("plan_name") or "").strip().casefold()
    if not profile.get("grant_access") and plan_name.startswith("aguardando"):
        return "pending"
    return "approved"
