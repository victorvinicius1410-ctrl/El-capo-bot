"""Testes de listagem rápida por segmento (Pedidos / Ativos / Inativos)."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from backend.admin_models import (
    AccountType,
    AdminActor,
    AdminPermission,
    ApprovalStatus,
    ClientRecord,
    PaymentStatus,
)
from backend.admin_repository import InMemoryAdminRepository
from backend.admin_service import AdminManagementService


def _client(
    user_id: str,
    *,
    account_type: AccountType = AccountType.CLIENT,
    grant_access: bool = True,
    approval_status: ApprovalStatus = ApprovalStatus.APPROVED,
    deleted: bool = False,
) -> ClientRecord:
    now = datetime.now(timezone.utc)
    return ClientRecord(
        user_id=user_id,
        company_id="company-a",
        name=user_id,
        email=f"{user_id}@example.com",
        phone=None,
        trader_id=user_id.upper(),
        account_type=account_type,
        payment_status=PaymentStatus.PAID
        if grant_access
        else PaymentStatus.PENDING,
        plan_name="Plano",
        grant_access=grant_access,
        created_at=now,
        updated_at=now,
        deleted_at=now if deleted else None,
        approval_status=approval_status,
    )


class ClientSegmentFilterTests(unittest.IsolatedAsyncioTestCase):
    """Filtros de segmento devem acontecer na query, não só em memória."""

    def setUp(self) -> None:
        self.repository = InMemoryAdminRepository()
        self.service = AdminManagementService(self.repository)
        self.actor = AdminActor(
            user_id="admin-1",
            company_id="company-a",
            permissions=frozenset(AdminPermission),
            manageable_role_ids=None,
        )

    async def asyncSetUp(self) -> None:
        for record in (
            _client("pending-1", grant_access=False, approval_status=ApprovalStatus.PENDING),
            _client("pending-2", grant_access=False, approval_status=ApprovalStatus.PENDING),
            _client("active-1", grant_access=True, account_type=AccountType.CLIENT),
            _client("active-2", grant_access=True, account_type=AccountType.CLIENT),
            _client("trial-1", grant_access=True, account_type=AccountType.TRIAL),
            _client(
                "inactive-1",
                grant_access=False,
                approval_status=ApprovalStatus.APPROVED,
            ),
            _client("deleted-1", grant_access=False, deleted=True),
        ):
            await self.repository.save_client(record)

    async def test_pending_filter_returns_only_pending_leads(self) -> None:
        rows = await self.service.list_clients(
            self.actor,
            approval_status="pending",
            grant_access=False,
            include_deleted=False,
            limit=50,
            offset=0,
        )
        ids = {row.user_id for row in rows}
        self.assertEqual(ids, {"pending-1", "pending-2"})

    async def test_active_filter_returns_only_granted_clients(self) -> None:
        rows = await self.service.list_clients(
            self.actor,
            account_type=AccountType.CLIENT,
            grant_access=True,
            include_deleted=False,
            limit=50,
            offset=0,
        )
        ids = {row.user_id for row in rows}
        self.assertEqual(ids, {"active-1", "active-2"})

    async def test_inactive_excludes_pending_and_includes_revoked(self) -> None:
        rows = await self.service.list_clients(
            self.actor,
            include_deleted=True,
            grant_access=False,
            exclude_approval_status="pending",
            limit=50,
            offset=0,
        )
        ids = {row.user_id for row in rows}
        self.assertIn("inactive-1", ids)
        self.assertIn("deleted-1", ids)
        self.assertNotIn("pending-1", ids)
        self.assertNotIn("pending-2", ids)

    async def test_search_filters_by_name_email_or_trader_id(self) -> None:
        rows = await self.service.list_clients(
            self.actor,
            account_type=AccountType.CLIENT,
            grant_access=True,
            include_deleted=False,
            search="active-1",
            limit=50,
            offset=0,
        )
        ids = {row.user_id for row in rows}
        self.assertEqual(ids, {"active-1"})

    async def test_search_matches_email_case_insensitively(self) -> None:
        rows = await self.service.list_clients(
            self.actor,
            include_deleted=False,
            search="PENDING-2@EXAMPLE.COM",
            limit=50,
            offset=0,
        )
        ids = {row.user_id for row in rows}
        self.assertEqual(ids, {"pending-2"})

    async def test_search_without_match_returns_empty(self) -> None:
        rows = await self.service.list_clients(
            self.actor,
            include_deleted=False,
            search="nao-existe-nenhum-lead-com-esse-nome",
            limit=50,
            offset=0,
        )
        self.assertEqual(rows, [])

    async def test_pending_pagination_uses_offset(self) -> None:
        page1 = await self.service.list_clients(
            self.actor,
            approval_status="pending",
            grant_access=False,
            limit=1,
            offset=0,
        )
        page2 = await self.service.list_clients(
            self.actor,
            approval_status="pending",
            grant_access=False,
            limit=1,
            offset=1,
        )
        self.assertEqual(len(page1), 1)
        self.assertEqual(len(page2), 1)
        self.assertNotEqual(page1[0].user_id, page2[0].user_id)


if __name__ == "__main__":
    unittest.main()
