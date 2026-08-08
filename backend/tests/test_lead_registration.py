"""Testes do cadastro público e aprovação de leads."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from backend.admin_models import (
    AccountType,
    AdminActor,
    AdminPermission,
    ApprovalStatus,
    PaymentStatus,
)
from backend.admin_repository import InMemoryAdminRepository
from backend.admin_service import AdminManagementService, ValidationError
from backend.auth_session_service import PasswordPolicyError
from backend.registration_service import (
    RegisterLeadPayload,
    RegistrationError,
    RegistrationService,
)


class LeadRegistrationTests(unittest.IsolatedAsyncioTestCase):
    """Cadastro self-service fica pendente até o admin definir os dias."""

    def setUp(self) -> None:
        self.repository = InMemoryAdminRepository()
        self.registration = RegistrationService(self.repository, company_id="company-a")
        self.admin = AdminManagementService(self.repository)
        self.owner = AdminActor(
            user_id="admin-owner",
            company_id="company-a",
            permissions=frozenset(AdminPermission),
            manageable_role_ids=None,
        )

    async def test_register_lead_creates_pending_without_access(self) -> None:
        record = await self.registration.register_lead(
            RegisterLeadPayload(
                name="Lead Teste",
                email="lead@example.com",
                password="SenhaForte1",
                phone="+5511999999999",
            )
        )

        self.assertEqual(record.company_id, "company-a")
        self.assertEqual(record.approval_status, ApprovalStatus.PENDING)
        self.assertFalse(record.grant_access)
        self.assertEqual(record.payment_status, PaymentStatus.PENDING)
        self.assertIsNone(record.expires_at)
        self.assertTrue(record.trader_id.startswith("LEAD-"))

    async def test_weak_password_is_rejected(self) -> None:
        with self.assertRaises(PasswordPolicyError):
            await self.registration.register_lead(
                RegisterLeadPayload(
                    name="Lead Fraco",
                    email="fraco@example.com",
                    password="123456",
                )
            )

    async def test_invalid_name_is_rejected(self) -> None:
        with self.assertRaises(RegistrationError):
            await self.registration.register_lead(
                RegisterLeadPayload(
                    name="A",
                    email="ok@example.com",
                    password="SenhaForte1",
                )
            )

    async def test_admin_approve_sets_trial_days(self) -> None:
        lead = await self.registration.register_lead(
            RegisterLeadPayload(
                name="Lead Aprovar",
                email="aprovar@example.com",
                password="SenhaForte1",
            )
        )

        approved = await self.admin.approve_lead(
            self.owner,
            lead.user_id,
            access_days=10,
        )

        self.assertEqual(approved.approval_status, ApprovalStatus.APPROVED)
        self.assertTrue(approved.grant_access)
        self.assertEqual(approved.account_type, AccountType.TRIAL)
        self.assertEqual(approved.payment_status, PaymentStatus.NOT_REQUIRED)
        self.assertIsNotNone(approved.expires_at)
        assert approved.expires_at is not None
        delta = approved.expires_at - datetime.now(timezone.utc)
        self.assertGreater(delta, timedelta(days=9))
        self.assertLess(delta, timedelta(days=11))

    async def test_approve_rejects_invalid_days(self) -> None:
        lead = await self.registration.register_lead(
            RegisterLeadPayload(
                name="Lead Dias",
                email="dias@example.com",
                password="SenhaForte1",
            )
        )
        with self.assertRaises(ValidationError):
            await self.admin.approve_lead(self.owner, lead.user_id, access_days=0)

    async def test_approve_defer_side_effects_then_complete(self) -> None:
        lead = await self.registration.register_lead(
            RegisterLeadPayload(
                name="Lead Fast",
                email="fast@example.com",
                password="SenhaForte1",
            )
        )
        approved = await self.admin.approve_lead(
            self.owner,
            lead.user_id,
            access_days=5,
            defer_side_effects=True,
        )
        self.assertTrue(approved.grant_access)
        self.assertTrue(hasattr(approved, "_lead_approval_before"))
        await self.admin.complete_lead_approval_side_effects(self.owner, approved)
        stored = await self.repository.get_client("company-a", lead.user_id)
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.approval_status, ApprovalStatus.APPROVED)


if __name__ == "__main__":
    unittest.main()
