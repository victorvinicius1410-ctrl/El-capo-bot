"""Testes do domínio financeiro, webhook Cakto e contratos HTTP."""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.account_link_service import AccountLink
from backend.admin_models import AdminActor, AdminPermission, PaymentStatus
from backend.cakto_service import CaktoConfig, CaktoOfferCreated, CaktoService
from backend.finance_models import BillingPlanCreate, BillingPlanUpdate
from backend.finance_repository import InMemoryFinanceRepository
from backend.finance_router import create_finance_router
from backend.finance_service import FinanceService, FinanceValidationError
from backend.webhook_models import DomainEventType


COMPANY_ID = "00000000-0000-0000-0000-000000000001"
OTHER_COMPANY_ID = "00000000-0000-0000-0000-000000000002"


def plan_payload(**overrides: object) -> BillingPlanCreate:
    """Cria um plano válido para os testes."""
    values: dict[str, object] = {
        "slug": "mensal",
        "name": "Mensal",
        "description": "Plano mensal",
        "price": Decimal("147.90"),
        "currency": "BRL",
        "billing_interval_months": 1,
        "features": ("Automação",),
        "is_featured": False,
        "is_active": True,
        "display_order": 1,
        "cakto_product_id": "product-monthly",
        "cakto_offer_id": "offer-monthly",
        "checkout_url": "https://pay.cakto.com.br/checkout/mensal",
    }
    values.update(overrides)
    return BillingPlanCreate(**values)


def cakto_payload(event: str, **overrides: object) -> dict[str, object]:
    """Monta payload Cakto sem usar company_id como identidade."""
    values: dict[str, object] = {
        "event": event,
        "secret": "webhook-secret-strong",
        "data": {
            "id": f"payment-{event}",
            "refId": f"ref-{event}",
            "product": {"id": "product-monthly"},
            "offer": {"id": "offer-monthly"},
            "customer": {"email": "client@example.com"},
            "amount": 147.90,
            "currency": "BRL",
            "createdAt": "2026-07-18T10:00:00Z",
        },
    }
    values.update(overrides)
    return values


class FinanceServiceTests(unittest.IsolatedAsyncioTestCase):
    """Valida tenant, planos, idempotência e regras conservadoras de acesso."""

    async def asyncSetUp(self) -> None:
        self.repository = InMemoryFinanceRepository()
        self.service = FinanceService(self.repository)
        self.owner = AdminActor(
            user_id="owner-a",
            company_id=COMPANY_ID,
            permissions=frozenset(AdminPermission),
            manageable_role_ids=None,
        )
        self.plan = await self.service.create_plan(self.owner, plan_payload())
        self.repository.add_customer(
            COMPANY_ID,
            user_id="client-a",
            email="client@example.com",
            payment_status=PaymentStatus.PENDING,
            grant_access=False,
        )

    async def test_checkout_requires_active_plan_from_same_tenant(self) -> None:
        """Checkout não atravessa tenants nem retorna plano inativo."""
        self.assertEqual(
            await self.service.get_checkout_url(COMPANY_ID, self.plan.id),
            "https://pay.cakto.com.br/checkout/mensal",
        )
        with self.assertRaises(FinanceValidationError):
            await self.service.get_checkout_url(OTHER_COMPANY_ID, self.plan.id)
        await self.service.update_plan(
            self.owner,
            self.plan.id,
            BillingPlanUpdate(is_active=False),
        )
        with self.assertRaises(FinanceValidationError):
            await self.service.get_checkout_url(COMPANY_ID, self.plan.id)

    async def test_checkout_url_accepts_only_cakto_https(self) -> None:
        """Bloqueia esquemas inseguros e hosts parecidos com o oficial."""
        for unsafe_url in (
            "http://pay.cakto.com.br/x",
            "https://pay.cakto.com.br.evil.example/x",
            "https://cakto.com.br/x",
            "",
        ):
            with self.subTest(url=unsafe_url), self.assertRaises(FinanceValidationError):
                await self.service.create_plan(
                    self.owner,
                    plan_payload(
                        slug=f"unsafe-{len(unsafe_url)}",
                        cakto_product_id=f"product-{len(unsafe_url)}",
                        cakto_offer_id=f"offer-{len(unsafe_url)}",
                        checkout_url=unsafe_url,
                    ),
                )

    async def test_webhook_is_idempotent_and_grants_access_on_approval(self) -> None:
        """Evento aprovado duplicado gera uma transação e uma mudança de acesso."""
        first = await self.service.process_cakto_event(cakto_payload("purchase_approved"))
        second = await self.service.process_cakto_event(cakto_payload("purchase_approved"))

        self.assertTrue(first.processed)
        self.assertFalse(second.processed)
        self.assertTrue(second.duplicate)
        customer = self.repository.customers[(COMPANY_ID, "client-a")]
        self.assertEqual(customer["payment_status"], PaymentStatus.PAID)
        self.assertTrue(customer["grant_access"])
        self.assertEqual(len(self.repository.events), 1)
        self.assertEqual(len(self.repository.revenue_events), 1)

    async def test_purchase_creates_profile_and_grants_access_for_new_buyer(self) -> None:
        """Compra sem cadastro prévio cria perfil e libera acesso com first_access_url."""
        links = AsyncMock()
        links.create_purchase_account = AsyncMock(
            return_value=AccountLink(
                user_id="buyer-new",
                company_id=COMPANY_ID,
                action_link="https://app.example.com/reset-password?token=one-time",
            )
        )
        service = FinanceService(self.repository, account_links=links)
        payload = cakto_payload("purchase_approved")
        payload["data"] = {
            **payload["data"],  # type: ignore[arg-type]
            "id": "payment-new-buyer",
            "customer": {"email": "newbuyer@example.com", "name": "Novo Comprador"},
        }

        result = await service.process_cakto_event(payload)

        self.assertTrue(result.processed)
        links.create_purchase_account.assert_awaited_once()
        customer = self.repository.customers[(COMPANY_ID, "buyer-new")]
        self.assertEqual(customer["email"], "newbuyer@example.com")
        self.assertTrue(customer["grant_access"])
        self.assertEqual(customer["payment_status"], PaymentStatus.PAID)
        self.assertEqual(self.repository.events[0].user_id, "buyer-new")

    async def test_purchase_grants_access_before_ledger_when_append_fails(self) -> None:
        """Regressão: compra aprovada libera acesso mesmo se o ledger falhar depois."""
        links = AsyncMock()
        links.create_purchase_account = AsyncMock(
            return_value=AccountLink(
                user_id="buyer-ledger-fail",
                company_id=COMPANY_ID,
                action_link="https://app.example.com/reset-password?token=one-time",
            )
        )
        repository = InMemoryFinanceRepository()
        repository.append_event = AsyncMock(side_effect=RuntimeError("ledger unavailable"))
        service = FinanceService(repository, account_links=links)
        await service.create_plan(self.owner, plan_payload())
        payload = cakto_payload("purchase_approved")
        payload["data"] = {
            **payload["data"],  # type: ignore[arg-type]
            "id": "payment-ledger-fail",
            "customer": {"email": "ledgerfail@example.com", "name": "Ledger Fail"},
        }

        with self.assertRaises(RuntimeError):
            await service.process_cakto_event(payload)

        customer = repository.customers[(COMPANY_ID, "buyer-ledger-fail")]
        self.assertTrue(customer["grant_access"])
        self.assertEqual(customer["payment_status"], PaymentStatus.PAID)

    async def test_duplicate_purchase_approved_heals_inactive_customer(self) -> None:
        """Reprocessamento idempotente reativa cliente deixado pendente na 1ª tentativa."""
        links = AsyncMock()
        links.create_purchase_account = AsyncMock(
            return_value=AccountLink(
                user_id="buyer-heal",
                company_id=COMPANY_ID,
                action_link="https://app.example.com/reset-password?token=heal",
            )
        )
        repository = InMemoryFinanceRepository()
        service = FinanceService(repository, account_links=links)
        await service.create_plan(self.owner, plan_payload())
        payload = cakto_payload("purchase_approved")
        payload["data"] = {
            **payload["data"],  # type: ignore[arg-type]
            "id": "payment-heal",
            "customer": {"email": "heal@example.com", "name": "Heal Me"},
        }

        await service.process_cakto_event(payload)
        repository.customers[(COMPANY_ID, "buyer-heal")]["grant_access"] = False
        repository.customers[(COMPANY_ID, "buyer-heal")]["payment_status"] = (
            PaymentStatus.PENDING
        )

        second = await service.process_cakto_event(payload)

        self.assertFalse(second.processed)
        self.assertTrue(second.duplicate)
        customer = repository.customers[(COMPANY_ID, "buyer-heal")]
        self.assertTrue(customer["grant_access"])
        self.assertEqual(customer["payment_status"], PaymentStatus.PAID)

    async def test_existing_account_purchase_emits_existing_account_email_event(self) -> None:
        """Quem já tem perfil recebe o evento de compra com conta existente, sem link de senha."""
        webhooks = AsyncMock()
        webhooks.enqueue_event = AsyncMock(return_value=AsyncMock(id="evt-existing"))
        webhooks.queue_event_deliveries = AsyncMock()
        service = FinanceService(self.repository, webhooks=webhooks)

        await service.process_cakto_event(cakto_payload("purchase_approved"))

        kwargs = webhooks.enqueue_event.await_args.kwargs
        self.assertEqual(
            kwargs["event_type"],
            DomainEventType.PURCHASE_EXISTING_ACCOUNT,
        )
        self.assertNotIn("first_access_url", kwargs["data"])

    async def test_new_buyer_emits_purchase_completed_email_event(self) -> None:
        """Primeira compra cria conta e dispara o e-mail de boas-vindas com first_access_url."""
        links = AsyncMock()
        links.create_purchase_account = AsyncMock(
            return_value=AccountLink(
                user_id="buyer-welcome",
                company_id=COMPANY_ID,
                action_link="https://app.example.com/reset-password?token=one-time",
            )
        )
        webhooks = AsyncMock()
        webhooks.enqueue_event = AsyncMock(return_value=AsyncMock(id="evt-new"))
        webhooks.queue_event_deliveries = AsyncMock()
        service = FinanceService(
            self.repository,
            webhooks=webhooks,
            account_links=links,
        )
        payload = cakto_payload("purchase_approved")
        payload["data"] = {
            **payload["data"],  # type: ignore[arg-type]
            "id": "payment-welcome-buyer",
            "customer": {"email": "welcome@example.com", "name": "Novo"},
        }

        await service.process_cakto_event(payload)

        kwargs = webhooks.enqueue_event.await_args.kwargs
        self.assertEqual(kwargs["event_type"], DomainEventType.PURCHASE_COMPLETED)
        self.assertEqual(
            kwargs["data"]["first_access_url"],
            "https://app.example.com/reset-password?token=one-time",
        )

    async def test_subscription_renewed_emits_renewed_email_event(self) -> None:
        """Renovação Cakto dispara o evento de assinatura renovada."""
        webhooks = AsyncMock()
        webhooks.enqueue_event = AsyncMock(return_value=AsyncMock(id="evt-renew"))
        webhooks.queue_event_deliveries = AsyncMock()
        service = FinanceService(self.repository, webhooks=webhooks)
        payload = cakto_payload("subscription_renewed")
        payload["data"] = {
            **payload["data"],  # type: ignore[arg-type]
            "id": "payment-renew-1",
        }

        await service.process_cakto_event(payload)

        kwargs = webhooks.enqueue_event.await_args.kwargs
        self.assertEqual(kwargs["event_type"], DomainEventType.SUBSCRIPTION_RENEWED)

    async def test_access_is_revoked_for_negative_events(self) -> None:
        """Reembolso, chargeback, cancelamento e recusa nunca mantêm acesso."""
        expected = {
            "refund": PaymentStatus.REFUNDED,
            "chargeback": PaymentStatus.CHARGEBACK,
            "subscription_canceled": PaymentStatus.CANCELED,
            "subscription_renewal_refused": PaymentStatus.REFUSED,
            "purchase_refused": PaymentStatus.REFUSED,
        }
        for index, (event_name, status) in enumerate(expected.items()):
            payload = cakto_payload(event_name)
            payload["data"] = {
                **payload["data"],  # type: ignore[arg-type]
                "id": f"negative-{index}",
                "createdAt": f"2026-07-18T10:0{index}:00Z",
            }
            await self.service.process_cakto_event(payload)
            customer = self.repository.customers[(COMPANY_ID, "client-a")]
            self.assertEqual(customer["payment_status"], status)
            self.assertFalse(customer["grant_access"])

    async def test_negative_event_uses_previous_subscription_when_email_is_absent(self) -> None:
        """Chargeback sem PII ainda encontra o cliente pela assinatura já vinculada."""
        approved = cakto_payload("purchase_approved")
        approved["data"] = {
            **approved["data"],  # type: ignore[arg-type]
            "subscription": {"id": "subscription-1"},
        }
        await self.service.process_cakto_event(approved)

        chargeback = cakto_payload("chargeback")
        chargeback["data"] = {
            **chargeback["data"],  # type: ignore[arg-type]
            "id": "chargeback-without-email",
            "subscription": {"id": "subscription-1"},
            "customer": {},
            "chargedbackAt": "2026-07-18T10:05:00Z",
        }
        await self.service.process_cakto_event(chargeback)

        customer = self.repository.customers[(COMPANY_ID, "client-a")]
        self.assertEqual(customer["payment_status"], PaymentStatus.CHARGEBACK)
        self.assertFalse(customer["grant_access"])

    async def test_pending_and_informative_events_do_not_grant_access(self) -> None:
        """Criação de assinatura e pagamento gerado permanecem pendentes."""
        for event_name in (
            "subscription_created",
            "pix_gerado",
            "boleto_gerado",
            "picpay_gerado",
            "openfinance_nubank_gerado",
            "initiate_checkout",
            "checkout_abandonment",
        ):
            await self.service.process_cakto_event(cakto_payload(event_name))
        customer = self.repository.customers[(COMPANY_ID, "client-a")]
        self.assertEqual(customer["payment_status"], PaymentStatus.PENDING)
        self.assertFalse(customer["grant_access"])

    async def test_subscription_created_after_purchase_keeps_paid(self) -> None:
        """Regressão: subscription_created pós-venda não rebaixa para 'Ainda não pagou'."""
        await self.service.process_cakto_event(cakto_payload("purchase_approved"))
        customer = self.repository.customers[(COMPANY_ID, "client-a")]
        self.assertEqual(customer["payment_status"], PaymentStatus.PAID)
        self.assertTrue(customer["grant_access"])

        created = cakto_payload("subscription_created")
        created["data"] = {
            **created["data"],  # type: ignore[arg-type]
            "id": "payment-purchase_approved",
            "subscription": {"id": "subscription-after-sale"},
            "createdAt": "2026-07-18T10:00:01Z",
        }
        await self.service.process_cakto_event(created)

        customer = self.repository.customers[(COMPANY_ID, "client-a")]
        self.assertEqual(customer["payment_status"], PaymentStatus.PAID)
        self.assertTrue(customer["grant_access"])

    async def test_pix_gerado_after_purchase_does_not_downgrade_paid(self) -> None:
        """Novo PIX gerado não pode apagar status pago de compra já aprovada."""
        await self.service.process_cakto_event(cakto_payload("purchase_approved"))
        pix = cakto_payload("pix_gerado")
        pix["data"] = {
            **pix["data"],  # type: ignore[arg-type]
            "id": "pix-after-paid",
            "createdAt": "2026-07-18T10:05:00Z",
        }
        await self.service.process_cakto_event(pix)

        customer = self.repository.customers[(COMPANY_ID, "client-a")]
        self.assertEqual(customer["payment_status"], PaymentStatus.PAID)
        self.assertTrue(customer["grant_access"])

    async def test_subscription_created_before_purchase_then_paid(self) -> None:
        """Ordem Cakto inversa: created primeiro, approved depois → termina pago."""
        created = cakto_payload("subscription_created")
        created["data"] = {
            **created["data"],  # type: ignore[arg-type]
            "id": "order-race-1",
            "subscription": {"id": "sub-race-1"},
        }
        await self.service.process_cakto_event(created)
        customer = self.repository.customers[(COMPANY_ID, "client-a")]
        self.assertEqual(customer["payment_status"], PaymentStatus.PENDING)
        self.assertFalse(customer["grant_access"])

        approved = cakto_payload("purchase_approved")
        approved["data"] = {
            **approved["data"],  # type: ignore[arg-type]
            "id": "order-race-1",
            "subscription": {"id": "sub-race-1"},
            "createdAt": "2026-07-18T10:00:02Z",
        }
        await self.service.process_cakto_event(approved)
        customer = self.repository.customers[(COMPANY_ID, "client-a")]
        self.assertEqual(customer["payment_status"], PaymentStatus.PAID)
        self.assertTrue(customer["grant_access"])

    async def test_marketing_or_not_required_account_is_not_changed(self) -> None:
        """Webhook financeiro não altera perfis especiais."""
        self.repository.add_customer(
            COMPANY_ID,
            user_id="marketing-a",
            email="marketing@example.com",
            payment_status=PaymentStatus.NOT_REQUIRED,
            grant_access=True,
            account_type="marketing",
        )
        payload = cakto_payload("chargeback")
        payload["data"] = {
            **payload["data"],  # type: ignore[arg-type]
            "id": "marketing-chargeback",
            "customer": {"email": "marketing@example.com"},
        }
        await self.service.process_cakto_event(payload)
        customer = self.repository.customers[(COMPANY_ID, "marketing-a")]
        self.assertEqual(customer["payment_status"], PaymentStatus.NOT_REQUIRED)
        self.assertTrue(customer["grant_access"])

    async def test_trial_customer_is_upgraded_to_paid_on_purchase_approved(self) -> None:
        """Lead com trial aprovado pelo admin é promovido a cliente pagante ao comprar."""
        self.repository.add_customer(
            COMPANY_ID,
            user_id="trial-a",
            email="trial@example.com",
            payment_status=PaymentStatus.NOT_REQUIRED,
            grant_access=True,
            account_type="trial",
        )
        payload = cakto_payload("purchase_approved")
        payload["data"] = {
            **payload["data"],  # type: ignore[arg-type]
            "id": "trial-conversion",
            "customer": {"email": "trial@example.com"},
        }
        await self.service.process_cakto_event(payload)

        customer = self.repository.customers[(COMPANY_ID, "trial-a")]
        self.assertEqual(customer["payment_status"], PaymentStatus.PAID)
        self.assertTrue(customer["grant_access"])
        self.assertEqual(customer["account_type"], "client")
        self.assertEqual(customer["plan_id"], self.plan.id)

    async def test_trial_customer_negative_event_without_purchase_keeps_protected(self) -> None:
        """Chargeback isolado (sem compra real) não derruba trial não obrigatório."""
        self.repository.add_customer(
            COMPANY_ID,
            user_id="trial-b",
            email="trial-b@example.com",
            payment_status=PaymentStatus.NOT_REQUIRED,
            grant_access=True,
            account_type="trial",
        )
        payload = cakto_payload("chargeback")
        payload["data"] = {
            **payload["data"],  # type: ignore[arg-type]
            "id": "trial-noise-chargeback",
            "customer": {"email": "trial-b@example.com"},
        }
        await self.service.process_cakto_event(payload)

        customer = self.repository.customers[(COMPANY_ID, "trial-b")]
        self.assertEqual(customer["payment_status"], PaymentStatus.NOT_REQUIRED)
        self.assertTrue(customer["grant_access"])
        self.assertEqual(customer["account_type"], "trial")

    async def test_payload_company_id_is_ignored_and_external_plan_maps_tenant(self) -> None:
        """O tenant sempre é derivado do produto/oferta cadastrados."""
        payload = cakto_payload("purchase_approved", company_id=OTHER_COMPANY_ID)
        result = await self.service.process_cakto_event(payload)
        self.assertEqual(result.company_id, COMPANY_ID)
        self.assertEqual(self.repository.events[0].company_id, COMPANY_ID)

    async def test_same_product_supports_multiple_offers_and_routes_by_offer(self) -> None:
        """Um produto da empresa aceita ofertas distintas sem ambiguidade no webhook."""
        annual_offer = await self.service.create_plan(
            self.owner,
            plan_payload(
                slug="anual",
                name="Anual",
                description="Oferta anual",
                price=Decimal("1447.90"),
                billing_interval_months=12,
                cakto_product_id="product-monthly",
                cakto_offer_id="offer-annual",
                checkout_url="https://pay.cakto.com.br/checkout/anual",
            ),
        )
        payload = cakto_payload("purchase_approved")
        payload["data"] = {
            **payload["data"],  # type: ignore[arg-type]
            "id": "payment-annual",
            "offer": {"id": "offer-annual"},
            "amount": 1447.90,
        }

        await self.service.process_cakto_event(payload)

        self.assertEqual(self.repository.events[0].plan_id, annual_offer.id)
        self.assertEqual(
            self.repository.events[0].metadata["billing_interval_months"],
            12,
        )

    async def test_product_without_offer_does_not_choose_arbitrary_offer(self) -> None:
        """Produto compartilhado não basta para selecionar uma oferta no webhook."""
        await self.service.create_plan(
            self.owner,
            plan_payload(
                slug="semestral",
                name="Semestral",
                description="Oferta semestral",
                price=Decimal("667.00"),
                billing_interval_months=6,
                cakto_product_id="product-monthly",
                cakto_offer_id="offer-semiannual",
                checkout_url="https://pay.cakto.com.br/checkout/semestral",
            ),
        )
        payload = cakto_payload("purchase_approved")
        payload["data"] = {
            key: value
            for key, value in payload["data"].items()  # type: ignore[union-attr]
            if key != "offer"
        }

        with self.assertRaises(FinanceValidationError):
            await self.service.process_cakto_event(payload)

    async def test_product_cannot_be_shared_between_companies(self) -> None:
        """O mesmo produto externo não pode mapear duas empresas."""
        other_owner = AdminActor(
            user_id="owner-b",
            company_id=OTHER_COMPANY_ID,
            permissions=frozenset(AdminPermission),
            manageable_role_ids=None,
        )

        with self.assertRaises(FinanceValidationError):
            await self.service.create_plan(
                other_owner,
                plan_payload(
                    slug="mensal-b",
                    cakto_product_id="product-monthly",
                    cakto_offer_id="offer-company-b",
                ),
            )

    async def test_metrics_are_tenant_scoped_and_use_financial_ledger(self) -> None:
        """Métricas não misturam tenants nem resultados do robô."""
        recent = datetime.now(timezone.utc) - timedelta(days=2)
        payload = cakto_payload("purchase_approved")
        payload["data"]["createdAt"] = recent.isoformat()  # type: ignore[index]
        await self.service.process_cakto_event(payload)
        refund_payload = cakto_payload("refund")
        refund_payload["data"] = {
            **refund_payload["data"],  # type: ignore[arg-type]
            "id": "refund-1",
            "amount": 20,
            "refundedAt": (recent + timedelta(minutes=1)).isoformat(),
        }
        await self.service.process_cakto_event(refund_payload)
        metrics = await self.service.get_metrics(self.owner, days=30)
        self.assertEqual(metrics["gross_revenue"], 147.9)
        self.assertEqual(metrics["refunds"], 20.0)
        self.assertEqual(metrics["net_revenue"], 127.9)
        self.assertEqual(metrics["approved_payments"], 1)
        self.assertEqual(metrics["currency"], "BRL")
        self.assertEqual(metrics["period_days"], 30)
        self.assertTrue(metrics["period_start"])
        self.assertTrue(metrics["period_end"])
        self.assertEqual(metrics["breakdowns"]["status"][0]["label"], "approved")


class CaktoConfigAndClientTests(unittest.IsolatedAsyncioTestCase):
    """Valida configuração server-only e reconciliação HTTP assíncrona."""

    def test_disabled_configuration_does_not_require_secrets(self) -> None:
        """Integração desabilitada não impede o startup."""
        with patch.dict(os.environ, {"CAKTO_ENABLED": "false"}, clear=True):
            config = CaktoConfig.from_environment()
        self.assertFalse(config.enabled)
        self.assertFalse(config.webhook_configured)
        self.assertFalse(config.api_configured)

    def test_enabled_configuration_requires_valid_secret(self) -> None:
        """Integração ativa falha cedo sem segredo forte."""
        with patch.dict(os.environ, {"CAKTO_ENABLED": "true", "CAKTO_WEBHOOK_SECRET": "short"}, clear=True):
            with self.assertRaises(ValueError):
                CaktoConfig.from_environment()

    async def test_reconciliation_uses_async_httpx_and_bounded_page(self) -> None:
        """Cliente consulta somente uma página razoável do histórico."""
        config = CaktoConfig(
            enabled=True,
            webhook_secret="webhook-secret-strong",
            oauth_token="oauth-token-long-enough",
            api_base_url="https://api.cakto.com.br",
        )
        response = AsyncMock()
        response.raise_for_status = lambda: None
        response.json = lambda: {
            "results": [
                {
                    "id": 28127,
                    "event_id": "purchase_approved",
                    "payload": {
                        "event": "purchase_approved",
                        "data": {"id": "order-1"},
                    },
                }
            ]
        }
        client = AsyncMock()
        client.get.return_value = response
        client.__aenter__.return_value = client
        client.__aexit__.return_value = None
        with patch("backend.cakto_service.httpx.AsyncClient", return_value=client):
            events = await CaktoService(config).fetch_event_history(page=1, per_page=50)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "purchase_approved")
        self.assertNotIn("payload", events[0])
        client.get.assert_awaited_once()
        _, kwargs = client.get.await_args
        self.assertEqual(kwargs["params"]["limit"], 50)
        self.assertNotIn(config.oauth_token, str(events))

    async def test_reconciliation_obtains_oauth_token_from_client_credentials(self) -> None:
        """Credenciais server-only geram um token temporário sem chegar ao frontend."""
        config = CaktoConfig(
            enabled=True,
            webhook_secret="webhook-secret-strong",
            oauth_token=None,
            oauth_client_id="client-id-long-enough",
            oauth_client_secret="client-secret-long-enough",
            api_base_url="https://api.cakto.com.br",
        )
        token_response = AsyncMock()
        token_response.raise_for_status = lambda: None
        token_response.json = lambda: {"access_token": "temporary-access-token"}
        history_response = AsyncMock()
        history_response.raise_for_status = lambda: None
        history_response.json = lambda: {"results": []}
        client = AsyncMock()
        client.post.return_value = token_response
        client.get.return_value = history_response
        client.__aenter__.return_value = client
        client.__aexit__.return_value = None

        with patch("backend.cakto_service.httpx.AsyncClient", return_value=client):
            await CaktoService(config).fetch_event_history(page=1, per_page=10)

        client.post.assert_awaited_once()
        _, token_kwargs = client.post.await_args
        self.assertEqual(
            token_kwargs["data"],
            {
                "client_id": "client-id-long-enough",
                "client_secret": "client-secret-long-enough",
            },
        )
        _, history_kwargs = client.get.await_args
        self.assertEqual(
            history_kwargs["headers"]["Authorization"],
            "Bearer temporary-access-token",
        )


    async def test_create_offer_posts_to_public_api_with_default_product(self) -> None:
        """Provisionamento remoto usa OAuth e o produto padrão do ambiente."""
        config = CaktoConfig(
            enabled=True,
            webhook_secret="webhook-secret-strong",
            oauth_token="oauth-token-long-enough",
            default_product_id="product-default-uuid",
            api_base_url="https://api.cakto.com.br",
        )
        response = AsyncMock()
        response.raise_for_status = lambda: None
        response.json = lambda: {
            "id": "5Hrb526",
            "name": "Mensal",
            "price": 147.9,
            "product": "product-default-uuid",
            "status": "active",
        }
        client = AsyncMock()
        client.post.return_value = response
        client.__aenter__.return_value = client
        client.__aexit__.return_value = None
        with patch("backend.cakto_service.httpx.AsyncClient", return_value=client):
            created = await CaktoService(config).create_offer(
                name="Mensal",
                price=147.9,
                billing_interval_months=1,
            )
        self.assertEqual(created.offer_id, "5Hrb526")
        self.assertEqual(created.checkout_url, "https://pay.cakto.com.br/5Hrb526")
        self.assertEqual(created.product_id, "product-default-uuid")
        _, kwargs = client.post.await_args
        self.assertTrue(str(kwargs["headers"]["Authorization"]).startswith("Bearer "))
        self.assertEqual(kwargs["json"]["product"], "product-default-uuid")
        self.assertEqual(kwargs["json"]["type"], "subscription")
        self.assertEqual(kwargs["json"]["interval"], 1)
        self.assertNotIn(config.oauth_token, str(created))

    def test_offers_provisioning_requires_default_product(self) -> None:
        """Sem produto padrão a flag de provisionamento permanece falsa."""
        config = CaktoConfig(
            enabled=True,
            webhook_secret="webhook-secret-strong",
            oauth_token="oauth-token-long-enough",
            default_product_id=None,
        )
        self.assertTrue(config.api_configured)
        self.assertFalse(config.offers_provisioning_configured)


class FinanceOfferProvisioningTests(unittest.IsolatedAsyncioTestCase):
    """Garante criação local com oferta remota automática."""

    async def asyncSetUp(self) -> None:
        self.repository = InMemoryFinanceRepository()
        self.service = FinanceService(self.repository)
        self.owner = AdminActor(
            user_id="owner-a",
            company_id=COMPANY_ID,
            permissions=frozenset(AdminPermission),
            manageable_role_ids=None,
        )

    async def test_create_plan_provisions_cakto_offer_from_default_product(self) -> None:
        """Nova oferta sem IDs Cakto cria remota e preenche checkout oficial."""
        cakto = CaktoService(
            CaktoConfig(
                enabled=True,
                webhook_secret="webhook-secret-strong",
                oauth_token="oauth-token-long-enough",
                default_product_id="product-default-uuid",
            )
        )
        cakto.create_offer = AsyncMock(
            return_value=CaktoOfferCreated(
                offer_id="offer-auto-1",
                product_id="product-default-uuid",
                checkout_url="https://pay.cakto.com.br/offer-auto-1",
                name="Mensal",
                price=147.9,
            )
        )
        plan = await self.service.create_plan(
            self.owner,
            plan_payload(
                cakto_product_id=None,
                cakto_offer_id=None,
                checkout_url=None,
            ),
            cakto,
        )
        self.assertEqual(plan.cakto_product_id, "product-default-uuid")
        self.assertEqual(plan.cakto_offer_id, "offer-auto-1")
        self.assertEqual(plan.checkout_url, "https://pay.cakto.com.br/offer-auto-1")
        cakto.create_offer.assert_awaited_once()


class FinanceApiTests(unittest.TestCase):
    """Exercita catálogo, webhook e RBAC sem depender do app global."""

    def setUp(self) -> None:
        self.repository = InMemoryFinanceRepository()
        self.service = FinanceService(self.repository)
        self.config = CaktoConfig(
            enabled=True,
            webhook_secret="webhook-secret-strong",
            oauth_token=None,
            api_base_url="https://api.cakto.com.br",
        )

        async def authenticated() -> dict[str, str]:
            return {
                "user_id": "client-a",
                "company_id": COMPANY_ID,
                "is_admin": "false",
                "permissions": "",
            }

        async def admin() -> dict[str, str]:
            return {
                "user_id": "owner-a",
                "company_id": COMPANY_ID,
                "is_admin": "true",
                "permissions": ",".join(permission.value for permission in AdminPermission),
                "manageable_role_ids": "*",
            }

        app = FastAPI()
        app.include_router(
            create_finance_router(
                self.service,
                CaktoService(self.config),
                authenticated,
                admin,
                public_webhook_url="https://api.example.com/webhooks/cakto",
            )
        )
        self.client = TestClient(app)

    def test_plan_crud_and_checkout_never_accept_company_id(self) -> None:
        """CRUD usa tenant autenticado e rejeita company_id livre."""
        body = {
            "slug": "mensal",
            "name": "Mensal",
            "description": "Plano mensal",
            "price": 147.9,
            "currency": "BRL",
            "billing_interval_months": 1,
            "features": ["Automação"],
            "is_featured": False,
            "is_active": True,
            "display_order": 1,
            "cakto_product_id": "product-monthly",
            "cakto_offer_id": "offer-monthly",
            "checkout_url": "https://pay.cakto.com.br/checkout/mensal",
        }
        forged = self.client.post(
            "/admin/finance/plans",
            json={**body, "company_id": OTHER_COMPANY_ID},
        )
        self.assertEqual(forged.status_code, 422)
        created = self.client.post("/admin/finance/plans", json=body)
        self.assertEqual(created.status_code, 201)
        plan_id = created.json()["data"]["id"]
        self.assertEqual(self.client.get("/billing/plans").status_code, 200)
        checkout = self.client.get(f"/billing/checkout/{plan_id}")
        self.assertEqual(
            checkout.json()["data"],
            {"checkout_url": "https://pay.cakto.com.br/checkout/mensal"},
        )
        deleted = self.client.delete(f"/admin/finance/plans/{plan_id}")
        self.assertEqual(deleted.status_code, 204)

    def test_webhook_validates_secret_with_public_contract(self) -> None:
        """Webhook recusa segredo incorreto e devolve correlation ID."""
        invalid = self.client.post(
            "/webhooks/cakto",
            headers={"X-Request-ID": "req-webhook"},
            json=cakto_payload("purchase_approved", secret="wrong-secret-value"),
        )
        self.assertEqual(invalid.status_code, 401)
        self.assertEqual(invalid.headers["x-request-id"], "req-webhook")
        self.assertEqual(invalid.json()["error"]["request_id"], "req-webhook")


if __name__ == "__main__":
    unittest.main()
