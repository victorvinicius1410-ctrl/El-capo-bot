"""Cache curto de `SupabaseAuthService.authenticate` por access token.

Sob carga, `/robot/state` (e demais rotas autenticadas) chamavam o Supabase
em toda request. O cache por token (TTL curto + invalidação por user_id)
corta a maior parte dessas idas sem manter permissões eternas.
"""

from __future__ import annotations

import unittest

import httpx

from backend.admin_models import AdminPermission
from backend.auth_service import (
    AUTH_CACHE_TTL_SECONDS,
    AuthenticationError,
    SupabaseAuthService,
)


def _json_response(payload: object, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=payload)


class _CountingTransport(httpx.AsyncBaseTransport):
    """Conta quantas vezes cada path foi atingido."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.auth_user_hits = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append(path)
        if path == "/auth/v1/user":
            self.auth_user_hits += 1
            return _json_response(
                {
                    "id": "user-1",
                    "email": "user@example.com",
                    "app_metadata": {"company_id": "company-1"},
                }
            )
        if path == "/rest/v1/user_access_profiles":
            return _json_response(
                [
                    {
                        "user_id": "user-1",
                        "email": "user@example.com",
                        "grant_access": True,
                        "is_admin": False,
                        "account_type": "client",
                        "payment_status": "paid",
                        "expires_at": None,
                        "deleted_at": None,
                        "marketing_mode": None,
                        "marketing_win_rate": None,
                        "plan_name": "pro",
                        "approval_status": "approved",
                    }
                ]
            )
        if path == "/rest/v1/admin_role_assignments":
            return _json_response([{"role_id": "role-1"}])
        if path == "/rest/v1/role_permissions":
            return _json_response([{"permission_key": "finance.view"}])
        if path == "/rest/v1/security_roles":
            return _json_response([{"can_manage_all_roles": False}])
        if path == "/rest/v1/role_assignable_roles":
            return _json_response([{"target_role_id": "role-2"}])
        raise AssertionError(f"rota inesperada: {path}")


class AuthCacheTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.service = SupabaseAuthService("https://supabase.test", "service-role-key")
        self.transport = _CountingTransport()
        self.service._client = httpx.AsyncClient(transport=self.transport)

    async def asyncTearDown(self) -> None:
        await self.service.aclose()

    async def test_second_authenticate_hits_cache(self) -> None:
        first = await self.service.authenticate("token-abc", now_monotonic=100.0)
        second = await self.service.authenticate("token-abc", now_monotonic=110.0)
        self.assertEqual(first.user_id, second.user_id)
        self.assertEqual(first.permissions, frozenset({AdminPermission.FINANCE_VIEW}))
        self.assertEqual(self.transport.auth_user_hits, 1)

    async def test_cache_expires_after_ttl(self) -> None:
        await self.service.authenticate("token-abc", now_monotonic=100.0)
        await self.service.authenticate(
            "token-abc",
            now_monotonic=100.0 + AUTH_CACHE_TTL_SECONDS + 0.1,
        )
        self.assertEqual(self.transport.auth_user_hits, 2)

    async def test_invalidate_user_forces_refresh(self) -> None:
        await self.service.authenticate("token-abc", now_monotonic=100.0)
        self.service.invalidate_user("user-1")
        await self.service.authenticate("token-abc", now_monotonic=101.0)
        self.assertEqual(self.transport.auth_user_hits, 2)

    async def test_invalidate_token_forces_refresh(self) -> None:
        await self.service.authenticate("token-abc", now_monotonic=100.0)
        self.service.invalidate_token("token-abc")
        await self.service.authenticate("token-abc", now_monotonic=101.0)
        self.assertEqual(self.transport.auth_user_hits, 2)

    async def test_different_tokens_are_isolated(self) -> None:
        await self.service.authenticate("token-a", now_monotonic=100.0)
        await self.service.authenticate("token-b", now_monotonic=100.0)
        self.assertEqual(self.transport.auth_user_hits, 2)

    async def test_failed_auth_is_not_cached(self) -> None:
        class _Reject(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                return httpx.Response(401, json={"error": "nope"})

        self.service._client = httpx.AsyncClient(transport=_Reject())
        with self.assertRaises(AuthenticationError):
            await self.service.authenticate("bad", now_monotonic=1.0)
        self.assertEqual(self.service.auth_cache_size(), 0)


if __name__ == "__main__":
    unittest.main()
