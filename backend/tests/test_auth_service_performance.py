"""
Gargalo real corrigido em 2026-08-07: `SupabaseAuthService.authenticate` roda
em TODA requisição autenticada (poll de `/robot/state` a cada poucos segundos
por usuário ativo, mais qualquer rota admin) e fazia até 6 chamadas HTTP
SEQUENCIAIS ao Supabase, cada uma abrindo um `httpx.AsyncClient` novo (sem
keep-alive). Sob carga real isso virou gargalo sistêmico — reproduzido com
Playwright: login e `GET /admin/dashboard` chegaram a levar 14-37s mesmo com
o payload do dashboard já em cache (o tempo não estava na lógica do endpoint,
e sim antes dela, na resolução de `require_headers`/`authenticate`).

Corrigido com:
1. Client HTTP compartilhado com keep-alive (`self._client`, criado uma vez
   no `__init__`, fechado em `aclose()` no shutdown do app).
2. As 3 consultas de `_load_authorization` que só dependem de `role_id`
   (role_permissions, security_roles, role_assignable_roles) passam a rodar
   em paralelo via `asyncio.gather` em vez de em série.

Ver docs/PERFORMANCE_SISTEMA.md.
"""

from __future__ import annotations

import unittest

import httpx

from backend.admin_models import AdminPermission
from backend.auth_service import AuthenticationError, SupabaseAuthService


def _json_response(payload: object, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=payload)


class _RecordingTransport(httpx.AsyncBaseTransport):
    """Transporte fake que roteia por path e registra a ordem das chamadas."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append(path)
        if path == "/auth/v1/user":
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
        raise AssertionError(f"rota inesperada no fake transport: {path}")


class SupabaseAuthServicePerformanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_authenticate_resolves_permissions_via_parallel_lookups(self) -> None:
        service = SupabaseAuthService("https://supabase.test", "service-role-key")
        transport = _RecordingTransport()
        service._client = httpx.AsyncClient(transport=transport)
        try:
            user = await service.authenticate("token-abc")
        finally:
            await service.aclose()

        self.assertEqual(user.user_id, "user-1")
        self.assertEqual(user.company_id, "company-1")
        self.assertFalse(user.is_admin)
        self.assertEqual(user.permissions, frozenset({AdminPermission.FINANCE_VIEW}))
        self.assertEqual(user.manageable_role_ids, frozenset({"role-2"}))

        # As 3 consultas dependentes só de role_id devem ter sido chamadas
        # (paralelas ou não, o resultado combinado tem que estar correto).
        for path in (
            "/rest/v1/role_permissions",
            "/rest/v1/security_roles",
            "/rest/v1/role_assignable_roles",
        ):
            self.assertIn(path, transport.calls)

    async def test_authenticate_reuses_shared_client_across_calls(self) -> None:
        """
        Antes da correção, cada chamada abria `httpx.AsyncClient` num
        `async with` novo. Agora o client é um único atributo de instância —
        chamar `authenticate` duas vezes não deve trocar `self._client`.
        """
        service = SupabaseAuthService("https://supabase.test", "service-role-key")
        transport = _RecordingTransport()
        service._client = httpx.AsyncClient(transport=transport)
        client_before = service._client
        try:
            await service.authenticate("token-abc", now_monotonic=1.0)
            # Força miss de cache para validar reuso do client, não do resultado.
            service.clear_auth_cache()
            await service.authenticate("token-abc", now_monotonic=2.0)
        finally:
            await service.aclose()

        self.assertIs(service._client, client_before)

    async def test_authenticate_rejects_invalid_token(self) -> None:
        class _RejectingTransport(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                return httpx.Response(401, json={"error": "invalid_token"})

        service = SupabaseAuthService("https://supabase.test", "service-role-key")
        service._client = httpx.AsyncClient(transport=_RejectingTransport())
        try:
            with self.assertRaises(AuthenticationError):
                await service.authenticate("bad-token")
        finally:
            await service.aclose()


if __name__ == "__main__":
    unittest.main()
