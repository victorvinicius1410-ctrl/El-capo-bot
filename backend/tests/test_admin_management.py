"""Contratos de gestão administrativa, acesso e simulação de marketing."""

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
    ClientRecord,
    ClientUpdate,
    PaymentStatus,
)
from backend.admin_repository import InMemoryAdminRepository
from backend.admin_dashboard_service import calculate_admin_dashboard
from backend.admin_service import (
    AdminManagementService,
    AuthorizationError,
    ValidationError,
)
from backend.marketing_simulation_service import MarketingSimulationService


class AdminManagementServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.repository = InMemoryAdminRepository()
        self.service = AdminManagementService(self.repository)
        self.owner = AdminActor(
            user_id="admin-owner",
            company_id="company-a",
            permissions=frozenset(AdminPermission),
            manageable_role_ids=None,
        )

    async def test_create_client_uses_actor_company_and_never_payload_company(self) -> None:
        created = await self.service.create_client(
            self.owner,
            ClientCreate(
                name="Cliente Teste",
                email="cliente@example.com",
                phone="+5511999999999",
                trader_id="TRADER-123",
                password="SenhaForte1",
                account_type=AccountType.TRIAL,
                trial_days=7,
                payment_status=PaymentStatus.NOT_REQUIRED,
            ),
        )

        self.assertEqual(created.company_id, "company-a")
        self.assertEqual(created.account_type, AccountType.TRIAL)
        self.assertIsNotNone(created.expires_at)

    async def test_create_client_requires_permission(self) -> None:
        actor = AdminActor(
            user_id="support",
            company_id="company-a",
            permissions=frozenset({AdminPermission.CLIENTS_HISTORY_READ}),
            manageable_role_ids=frozenset(),
        )

        with self.assertRaises(AuthorizationError):
            await self.service.create_client(
                actor,
                ClientCreate(
                    name="Sem Permissão",
                    email="blocked@example.com",
                    phone=None,
                    trader_id="TRADER-999",
                    password="SenhaForte1",
                    account_type=AccountType.CLIENT,
                    trial_days=None,
                    payment_status=PaymentStatus.PAID,
                ),
            )

    async def test_weak_password_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            await self.service.create_client(
                self.owner,
                ClientCreate(
                    name="Senha Fraca",
                    email="weak@example.com",
                    phone=None,
                    trader_id="TRADER-456",
                    password="123456",
                    account_type=AccountType.CLIENT,
                    trial_days=None,
                    payment_status=PaymentStatus.PAID,
                ),
            )

    async def test_client_conversion_requires_payment_state(self) -> None:
        created = await self.service.create_client(
            self.owner,
            ClientCreate(
                name="Trial",
                email="trial@example.com",
                phone=None,
                trader_id="TRIAL-1",
                password="SenhaForte1",
                account_type=AccountType.TRIAL,
                trial_days=3,
                payment_status=PaymentStatus.NOT_REQUIRED,
            ),
        )

        with self.assertRaises(ValidationError):
            await self.service.update_client(
                self.owner,
                created.user_id,
                ClientUpdate(
                    account_type=AccountType.CLIENT,
                    payment_status=PaymentStatus.NOT_REQUIRED,
                ),
            )

    async def test_soft_delete_preserves_record_and_marks_inactive(self) -> None:
        created = await self.service.create_client(
            self.owner,
            ClientCreate(
                name="Excluir",
                email="delete@example.com",
                phone=None,
                trader_id="DELETE-1",
                password="SenhaForte1",
                account_type=AccountType.CLIENT,
                trial_days=None,
                payment_status=PaymentStatus.PAID,
            ),
        )

        await self.service.delete_client(self.owner, created.user_id)
        stored = await self.repository.get_client("company-a", created.user_id)

        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertIsNotNone(stored.deleted_at)
        self.assertFalse(stored.grant_access)

    async def test_cross_company_client_cannot_be_edited(self) -> None:
        other_owner = AdminActor(
            user_id="other-owner",
            company_id="company-b",
            permissions=frozenset(AdminPermission),
            manageable_role_ids=None,
        )
        created = await self.service.create_client(
            other_owner,
            ClientCreate(
                name="Outra Empresa",
                email="other@example.com",
                phone=None,
                trader_id="OTHER-1",
                password="SenhaForte1",
                account_type=AccountType.CLIENT,
                trial_days=None,
                payment_status=PaymentStatus.PAID,
            ),
        )

        with self.assertRaises(AuthorizationError):
            await self.service.update_client(
                self.owner,
                created.user_id,
                ClientUpdate(name="Tentativa cross-tenant"),
            )


class MarketingSimulationServiceTests(unittest.TestCase):
    def test_target_rate_controls_only_synthetic_results(self) -> None:
        service = MarketingSimulationService(seed="marketing-user", target_win_rate=80)

        results = [service.next_trade()["result"] for _ in range(10)]

        self.assertEqual(results.count("WIN"), 8)
        self.assertEqual(results.count("LOSS"), 2)
        self.assertTrue(all(service_item["is_simulated"] for service_item in service.history))
        self.assertTrue(
            all(service_item["account_mode"] == "SIMULATED_MARKETING" for service_item in service.history)
        )

    def test_invalid_target_rate_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            MarketingSimulationService(seed="invalid", target_win_rate=101)

    def test_next_trade_accepts_custom_created_at(self) -> None:
        service = MarketingSimulationService(seed="timing-user", target_win_rate=50)

        trade = service.next_trade(created_at="2026-07-20T14:30:00-03:00")

        self.assertTrue(str(trade["created_at"]).startswith("2026-07-20T17:30:00"))

    def test_next_trade_rejects_invalid_created_at(self) -> None:
        service = MarketingSimulationService(seed="timing-user", target_win_rate=50)

        with self.assertRaises(ValueError):
            service.next_trade(created_at="invalido")


class AdminDashboardServiceTests(unittest.TestCase):
    def test_dashboard_aggregates_clients_profit_users_and_assets(self) -> None:
        now = datetime.now(timezone.utc)
        clients = [
            ClientRecord(
                user_id="winner",
                company_id="company-a",
                name="Maior Ganhador",
                email="winner@example.com",
                phone=None,
                trader_id="WIN-1",
                account_type=AccountType.CLIENT,
                payment_status=PaymentStatus.PAID,
                plan_name=None,
                grant_access=True,
                created_at=now,
                updated_at=now,
            ),
            ClientRecord(
                user_id="loser",
                company_id="company-a",
                name="Maior Perdedor",
                email="loser@example.com",
                phone=None,
                trader_id="LOSS-1",
                account_type=AccountType.CLIENT,
                payment_status=PaymentStatus.OVERDUE,
                plan_name=None,
                grant_access=False,
                created_at=now,
                updated_at=now,
            ),
            ClientRecord(
                user_id="trial",
                company_id="company-a",
                name="Trial",
                email="trial-dashboard@example.com",
                phone=None,
                trader_id="TRIAL-DASH",
                account_type=AccountType.TRIAL,
                payment_status=PaymentStatus.NOT_REQUIRED,
                plan_name=None,
                grant_access=True,
                created_at=now,
                updated_at=now,
                expires_at=now + timedelta(days=7),
            ),
        ]
        histories = {
            "winner": [
                {"order_id": "1", "result": "WIN", "profit": 12, "active": "EURUSD-OTC"},
                {"order_id": "2", "result": "WIN", "profit": 8, "active": "EURUSD-OTC"},
                {"order_id": "3", "result": "LOSS", "profit": -2, "active": "GBPUSD-OTC"},
                {
                    "order_id": "synthetic",
                    "result": "WIN",
                    "profit": 999,
                    "active": "FAKE",
                    "is_simulated": True,
                },
            ],
            "loser": [
                {"order_id": "4", "result": "LOSS", "profit": -10, "active": "GBPUSD-OTC"}
            ],
        }

        dashboard = calculate_admin_dashboard(
            clients,
            histories,
            days=30,
            revenue_events=[
                {
                    "event_type": "sale",
                    "amount": 100,
                    "currency": "BRL",
                    "user_id": "winner",
                },
                {
                    "event_type": "renewal",
                    "amount": 50,
                    "currency": "BRL",
                    "user_id": "winner",
                },
            ],
            lifecycle_events=[],
            now=now,
        )

        self.assertEqual(dashboard["active_clients"], 1)
        self.assertEqual(dashboard["inactive_clients"], 1)
        self.assertEqual(dashboard["trial_clients"], 1)
        self.assertEqual(dashboard["new_clients"], 1)
        self.assertEqual(dashboard["revenue"], 150.0)
        self.assertEqual(dashboard["new_client_revenue"], 100.0)
        self.assertEqual(dashboard["top_winners"][0]["user_id"], "winner")
        self.assertEqual(dashboard["top_losers"][0]["user_id"], "loser")
        self.assertEqual(dashboard["most_accurate_assets"][0]["asset"], "EURUSD-OTC")
        self.assertEqual(dashboard["least_accurate_assets"][0]["asset"], "GBPUSD-OTC")


class AdminManagementApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.old_repository = main.admin_management_service.repository
        self.old_api_key = main.config.panel_api_key
        self.old_admin_emails = main.config.admin_emails
        self.old_legacy_auth = main.config.allow_legacy_auth
        main.admin_management_service.repository = InMemoryAdminRepository()
        main.config.panel_api_key = "test-key"
        main.config.admin_emails = {"admin@elcapobot.local"}
        main.config.allow_legacy_auth = True
        self.client = TestClient(main.app)
        self.headers = {
            "x-api-key": "test-key",
            "x-user-id": "admin-owner",
            "x-user-email": "admin@elcapobot.local",
        }

    def tearDown(self) -> None:
        main.admin_management_service.repository = self.old_repository
        main.config.panel_api_key = self.old_api_key
        main.config.admin_emails = self.old_admin_emails
        main.config.allow_legacy_auth = self.old_legacy_auth

    def test_admin_client_crud_is_paged_and_soft_deleted(self) -> None:
        created_response = self.client.post(
            "/admin/clients",
            headers=self.headers,
            json={
                "name": "Cliente API",
                "email": "api@example.com",
                "phone": "+5511999999999",
                "trader_id": "API-001",
                "password": "SenhaForte1",
                "account_type": "trial",
                "trial_days": 7,
                "payment_status": "not_required",
            },
        )
        self.assertEqual(created_response.status_code, 201)
        created = created_response.json()["data"]

        listing = self.client.get(
            "/admin/clients",
            headers=self.headers,
            params={"segment": "trial", "limit": 10, "offset": 0},
        )
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()["data"]["items"][0]["id"], created["id"])
        dashboard = self.client.get(
            "/admin/dashboard",
            headers=self.headers,
            params={"days": 30},
        )
        self.assertEqual(dashboard.status_code, 200)
        self.assertEqual(dashboard.json()["data"]["trial_clients"], 1)

        update = self.client.patch(
            f"/admin/clients/{created['id']}",
            headers=self.headers,
            json={
                "account_type": "client",
                "payment_status": "pending",
                "plan_id": "plan-semestral",
            },
        )
        self.assertEqual(update.status_code, 200)
        self.assertFalse(update.json()["data"]["grant_access"])
        self.assertEqual(update.json()["data"]["plan_id"], "plan-semestral")

        delete = self.client.delete(
            f"/admin/clients/{created['id']}",
            headers=self.headers,
        )
        self.assertEqual(delete.status_code, 204)
        inactive = self.client.get(
            "/admin/clients",
            headers=self.headers,
            params={"segment": "inactive"},
        )
        self.assertEqual(len(inactive.json()["data"]["items"]), 1)

    def test_non_admin_cannot_manage_clients(self) -> None:
        response = self.client.get(
            "/admin/clients",
            headers={
                "x-api-key": "test-key",
                "x-user-id": "regular-user",
                "x-user-email": "regular@example.com",
            },
        )
        self.assertEqual(response.status_code, 403)

    def test_admin_access_and_impersonation_are_audited(self) -> None:
        client = self.client.post(
            "/admin/clients",
            headers=self.headers,
            json={
                "name": "Cliente Suporte",
                "email": "support-client@example.com",
                "trader_id": "SUPPORT-001",
                "password": "SenhaForte1",
                "account_type": "client",
                "payment_status": "paid",
            },
        ).json()["data"]
        admin_response = self.client.post(
            "/admin/admins",
            headers=self.headers,
            json={
                "name": "Suporte",
                "email": "support-admin@example.com",
                "job_title": "Suporte",
                "password": "SenhaForte1",
                "permissions": ["clients.history.read", "clients.impersonate"],
                "manageable_role_ids": [],
            },
        )
        self.assertEqual(admin_response.status_code, 201)
        self.assertEqual(
            set(admin_response.json()["data"]["permissions"]),
            {"clients.history.read", "clients.impersonate"},
        )

        impersonation = self.client.post(
            "/admin/impersonations",
            headers=self.headers,
            json={
                "target_user_id": client["id"],
                "reason": "Análise solicitada pelo cliente",
                "duration_minutes": 10,
            },
        )
        self.assertEqual(impersonation.status_code, 201)
        self.assertTrue(impersonation.json()["data"]["read_only"])
        self.assertIn("elcapo-impersonation", impersonation.cookies)
        effective_access = self.client.get("/me/access", headers=self.headers)
        self.assertEqual(effective_access.status_code, 200)
        self.assertTrue(effective_access.json()["data"]["impersonating"])
        self.assertEqual(effective_access.json()["data"]["user_id"], client["id"])
        blocked_write = self.client.post("/robot/start", headers=self.headers)
        self.assertEqual(blocked_write.status_code, 403)
        self.assertEqual(blocked_write.json()["error"], "IMPERSONATION_READ_ONLY")

        ended = self.client.delete("/admin/impersonations/current", headers=self.headers)
        self.assertEqual(ended.status_code, 204)
        repository = main.admin_management_service.repository
        assert isinstance(repository, InMemoryAdminRepository)
        self.assertTrue(
            any(event["action"] == "impersonation.started" for event in repository.audit_events)
        )
        self.assertTrue(
            any(event["action"] == "impersonation.ended" for event in repository.audit_events)
        )


if __name__ == "__main__":
    unittest.main()
