"""Contratos de segurança da nova fundação administrativa."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from backend import main
from backend.admin_models import (
    AccountType,
    AdminActor,
    AdminPermission,
    ClientCreate,
    PaymentStatus,
)
from backend.admin_repository import InMemoryAdminRepository
from backend.admin_service import (
    AdminManagementService,
    AuthorizationError,
    ValidationError,
    authorize_support_action,
)
from backend.auth_service import AuthenticatedUser, AuthenticationError


class FakeSupabaseAuth:
    """Validador determinístico que simula a identidade já verificada."""

    async def authenticate(self, token: str) -> AuthenticatedUser:
        """Retorna um administrador somente para o token de teste."""
        if token != "valid-jwt":
            raise AuthenticationError("token inválido")
        return AuthenticatedUser(
            user_id="owner-a",
            email="owner@example.com",
            company_id="company-a",
            is_admin=True,
            permissions=frozenset(AdminPermission),
            manageable_role_ids=None,
            grant_access=True,
            account_type="client",
            payment_status="paid",
            expires_at=None,
            marketing_mode=None,
            marketing_win_rate=None,
        )


class FakeLocalAdminWithoutNewPermissions:
    """Simula o owner local antes da migration de novas permissões."""

    async def authenticate(self, token: str) -> AuthenticatedUser:
        """Retorna um admin válido cujo cargo ainda não conhece webhooks."""
        if token != "valid-jwt":
            raise AuthenticationError("token inválido")
        return AuthenticatedUser(
            user_id="local-owner",
            email="admin@elcapobot.local",
            company_id="00000000-0000-0000-0000-000000000001",
            is_admin=True,
            permissions=frozenset({AdminPermission.FINANCE_VIEW}),
            manageable_role_ids=None,
            grant_access=True,
            account_type="client",
            payment_status="paid",
            expires_at=None,
            marketing_mode=None,
            marketing_win_rate=None,
        )


class SecureAdminApiTests(unittest.TestCase):
    """Exercita autenticação, CRUD, histórico e headers das novas rotas."""

    def setUp(self) -> None:
        self.old_auth = main.supabase_auth_service
        self.old_repository = main.admin_management_service.repository
        main.supabase_auth_service = FakeSupabaseAuth()
        main.admin_management_service.repository = InMemoryAdminRepository()
        self.client = TestClient(main.app)
        self.headers = {"Authorization": "Bearer valid-jwt", "X-Request-ID": "req-test-1"}

    def tearDown(self) -> None:
        main.supabase_auth_service = self.old_auth
        main.admin_management_service.repository = self.old_repository

    def test_invalid_jwt_and_forged_admin_headers_are_rejected(self) -> None:
        invalid = self.client.get(
            "/admin/users",
            headers={
                "Authorization": "Bearer invalid",
                "x-user-id": "owner-a",
                "x-user-email": "owner@example.com",
                "x-api-key": "forged",
            },
        )
        forged = self.client.get(
            "/admin/users",
            headers={"x-user-id": "owner-a", "x-user-email": "owner@example.com"},
        )

        self.assertEqual(invalid.status_code, 401)
        self.assertEqual(forged.status_code, 401)

    def test_development_without_supabase_accepts_explicit_local_admin(self) -> None:
        """Fallback local exige ambiente dev, API key e allowlist administrativa."""
        main.supabase_auth_service = None
        old_env = main.config.app_env
        old_legacy = main.config.allow_legacy_auth
        old_api_key = main.config.panel_api_key
        old_admin_emails = main.config.admin_emails
        try:
            main.config.app_env = "development"
            main.config.allow_legacy_auth = True
            main.config.panel_api_key = "local-key"
            main.config.admin_emails = {"admin@elcapobot.local"}
            response = self.client.get(
                "/admin/users",
                headers={
                    "x-api-key": "local-key",
                    "x-user-id": "local-admin",
                    "x-user-email": "admin@elcapobot.local",
                },
            )
            self.assertEqual(response.status_code, 200)
        finally:
            main.config.app_env = old_env
            main.config.allow_legacy_auth = old_legacy
            main.config.panel_api_key = old_api_key
            main.config.admin_emails = old_admin_emails

    def test_local_allowlisted_admin_receives_current_permissions(self) -> None:
        """O localhost não oculta funções novas enquanto a migration está pendente."""
        old_env = main.config.app_env
        old_legacy = main.config.allow_legacy_auth
        old_api_key = main.config.panel_api_key
        old_admin_emails = main.config.admin_emails
        main.supabase_auth_service = FakeLocalAdminWithoutNewPermissions()
        try:
            main.config.app_env = "development"
            main.config.allow_legacy_auth = True
            main.config.panel_api_key = "local-key"
            main.config.admin_emails = {"admin@elcapobot.local"}
            headers = {
                "Authorization": "Bearer valid-jwt",
                "x-api-key": "local-key",
            }

            access = self.client.get("/me/access", headers=headers)
            catalog = self.client.get("/admin/webhooks/catalog", headers=headers)

            self.assertEqual(access.status_code, 200)
            self.assertIn("webhooks.view", access.json()["data"]["permissions"])
            self.assertEqual(catalog.status_code, 200)
        finally:
            main.config.app_env = old_env
            main.config.allow_legacy_auth = old_legacy
            main.config.panel_api_key = old_api_key
            main.config.admin_emails = old_admin_emails

    def test_user_crud_history_overview_and_security_headers(self) -> None:
        created_response = self.client.post(
            "/admin/users",
            headers=self.headers,
            json={
                "name": "Cliente Seguro",
                "email": "cliente-seguro@example.com",
                "phone": "+5511999999999",
                "trader_id": "SAFE-1",
                "password": "SenhaForte1",
                "customer_type": "trial",
                "trial_days": 7,
                "plan_id": "starter",
                "payment_status": "not_applicable",
            },
        )
        self.assertEqual(created_response.status_code, 201)
        user_id = created_response.json()["data"]["id"]

        listing = self.client.get(
            "/admin/users",
            headers=self.headers,
            params={"stage": "trial", "search": "Seguro", "limit": 10, "offset": 0},
        )
        detail = self.client.get(f"/admin/users/{user_id}", headers=self.headers)
        updated = self.client.patch(
            f"/admin/users/{user_id}",
            headers=self.headers,
            json={
                "customer_type": "client",
                "payment_status": "paid",
                "plan_id": "pro",
            },
        )
        history = self.client.get(f"/admin/users/{user_id}/history", headers=self.headers)
        overview = self.client.get("/admin/overview", headers=self.headers)

        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()["data"]["items"][0]["id"], user_id)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["data"]["plan_id"], "pro")
        self.assertEqual(history.status_code, 200)
        self.assertGreaterEqual(len(history.json()["data"]), 2)
        self.assertEqual(history.json()["data"][0]["request_id"], "req-test-1")
        self.assertEqual(overview.status_code, 200)
        self.assertEqual(overview.headers["x-frame-options"], "DENY")
        self.assertEqual(overview.headers["x-content-type-options"], "nosniff")
        self.assertIn("frame-ancestors 'none'", overview.headers["content-security-policy"])

        deleted = self.client.delete(f"/admin/users/{user_id}", headers=self.headers)
        self.assertEqual(deleted.status_code, 204)

    def test_validation_boundaries(self) -> None:
        base = {
            "name": "Cliente",
            "email": "valid@example.com",
            "trader_id": "BOUNDARY-1",
            "password": "SenhaForte1",
            "customer_type": "trial",
            "payment_status": "not_applicable",
        }
        weak = self.client.post(
            "/admin/users",
            headers=self.headers,
            json={**base, "password": "fraca", "trial_days": 7},
        )
        trial = self.client.post(
            "/admin/users",
            headers=self.headers,
            json={**base, "trial_days": 366},
        )
        marketing = self.client.post(
            "/admin/users",
            headers=self.headers,
            json={
                **base,
                "customer_type": "marketing",
                "payment_status": "not_applicable",
                "marketing_target_win_rate": 101,
            },
        )

        self.assertIn(weak.status_code, {400, 422})
        self.assertEqual(trial.status_code, 422)
        self.assertEqual(marketing.status_code, 422)

    def test_company_isolation_ignores_body_company_id(self) -> None:
        response = self.client.post(
            "/admin/users",
            headers=self.headers,
            json={
                "name": "Tenant A",
                "email": "tenant-a@example.com",
                "trader_id": "TENANT-A",
                "password": "SenhaForte1",
                "customer_type": "client",
                "plan_id": "pro",
                "payment_status": "paid",
                "company_id": "company-b",
            },
        )
        self.assertEqual(response.status_code, 422)


class AdminFoundationDomainTests(unittest.IsolatedAsyncioTestCase):
    """Valida deny-by-default e sessões temporárias de suporte."""

    async def asyncSetUp(self) -> None:
        self.repository = InMemoryAdminRepository()
        self.service = AdminManagementService(self.repository)
        self.owner = AdminActor(
            user_id="owner-a",
            company_id="company-a",
            permissions=frozenset(AdminPermission),
            manageable_role_ids=None,
        )
        self.client_record = await self.service.create_client(
            self.owner,
            ClientCreate(
                name="Cliente",
                email="cliente@example.com",
                phone=None,
                trader_id="SUPPORT-1",
                password="SenhaForte1",
                account_type=AccountType.CLIENT,
                trial_days=None,
                payment_status=PaymentStatus.PAID,
                plan_id="pro",
            ),
            request_id="req-create",
        )

    async def test_permission_is_deny_by_default(self) -> None:
        actor = AdminActor(
            user_id="limited",
            company_id="company-a",
            permissions=frozenset(),
            manageable_role_ids=frozenset(),
        )
        with self.assertRaises(AuthorizationError):
            await self.service.delete_client(actor, self.client_record.user_id)

    async def test_support_session_expired_revoked_and_dangerous_actions_denied(self) -> None:
        session, token = await self.service.start_support_session(
            self.owner,
            self.client_record.user_id,
            "Diagnóstico autorizado pelo cliente",
            request_id="req-support",
        )
        resolved = await self.service.resolve_support_session(self.owner, token)
        self.assertIsNotNone(resolved)
        authorize_support_action(session, "account.view")
        authorize_support_action(session, "broker_account.edit")
        for action in (
            "robot.start",
            "robot.stop",
            "robot.config",
            "robot.reset",
            "robot.tick",
            "broker.buy-real",
            "chart.stream",
            "websocket.connect",
        ):
            with self.assertRaises(AuthorizationError):
                authorize_support_action(session, action)

        session.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        self.assertIsNone(await self.service.resolve_support_session(self.owner, token))
        session.expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)
        await self.service.end_support_session(
            self.owner,
            session.session_id,
            request_id="req-revoke",
        )
        self.assertIsNone(await self.service.resolve_support_session(self.owner, token))


if __name__ == "__main__":
    unittest.main()
