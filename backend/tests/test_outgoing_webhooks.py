"""Testes do domínio multi-tenant de webhooks de saída."""

from __future__ import annotations

import base64
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.account_link_service import SupabaseAccountLinkService
from backend.admin_models import AdminActor, AdminPermission
from backend.services.encryption_service import EncryptionService
from backend.webhook_models import DomainEventType, WebhookEndpointCreate
from backend.webhook_repository import InMemoryWebhookRepository
from backend.webhook_router import create_webhook_router
from backend.webhook_service import (
    UnsafeWebhookUrlError,
    WebhookAuthorizationError,
    WebhookService,
)


COMPANY_ID = "00000000-0000-0000-0000-000000000001"
OTHER_COMPANY_ID = "00000000-0000-0000-0000-000000000002"


def owner(company_id: str = COMPANY_ID) -> AdminActor:
    """Cria ator proprietário com todas as permissões."""
    return AdminActor(
        user_id="owner",
        company_id=company_id,
        permissions=frozenset(AdminPermission),
        manageable_role_ids=None,
    )


class WebhookServiceTests(unittest.IsolatedAsyncioTestCase):
    """Valida configuração, assinatura, isolamento e entrega."""

    async def asyncSetUp(self) -> None:
        key = base64.urlsafe_b64encode(os.urandom(32)).decode()
        self.repository = InMemoryWebhookRepository()
        self.service = WebhookService(
            self.repository,
            EncryptionService(key),
        )

    async def test_create_returns_secret_once_and_persists_only_ciphertext(self) -> None:
        """O segredo nasce forte e nunca é persistido em texto."""
        created = await self.service.create_endpoint(
            owner(),
            WebhookEndpointCreate(
                name="Automação de email",
                url="https://hooks.example.com/elcapo",
                subscribed_events=frozenset({DomainEventType.PURCHASE_COMPLETED}),
            ),
        )

        self.assertTrue(created.signing_secret.startswith("whsec_"))
        stored = self.repository.endpoints[(COMPANY_ID, created.endpoint.id)]
        self.assertNotIn(created.signing_secret, stored.encrypted_secret)
        listed = await self.service.list_endpoints(owner())
        self.assertEqual(len(listed), 1)
        self.assertFalse(hasattr(listed[0], "signing_secret"))

    async def test_queries_and_mutations_are_tenant_scoped(self) -> None:
        """Um administrador não acessa endpoint de outra empresa."""
        created = await self.service.create_endpoint(
            owner(),
            WebhookEndpointCreate(
                name="CRM",
                url="https://events.example.com/webhook",
                subscribed_events=frozenset(DomainEventType),
            ),
        )

        self.assertEqual(await self.service.list_endpoints(owner(OTHER_COMPANY_ID)), [])
        with self.assertRaises(WebhookAuthorizationError):
            await self.service.deactivate_endpoint(
                owner(OTHER_COMPANY_ID),
                created.endpoint.id,
            )

    async def test_rejects_private_local_and_insecure_destinations(self) -> None:
        """Protege o worker contra SSRF e URLs sem TLS."""
        unsafe_urls = (
            "",
            "http://hooks.example.com/event",
            "https://localhost/event",
            "https://127.0.0.1/event",
            "https://169.254.169.254/latest/meta-data",
            "https://10.0.0.8/event",
        )
        for index, url in enumerate(unsafe_urls):
            with self.subTest(url=url), self.assertRaises(UnsafeWebhookUrlError):
                await self.service.create_endpoint(
                    owner(),
                    WebhookEndpointCreate(
                        name=f"Inválido {index}",
                        url=url,
                        subscribed_events=frozenset({DomainEventType.TRIAL_STARTED}),
                    ),
                )

    async def test_event_payload_contains_required_customer_and_plan_fields(self) -> None:
        """O contrato canônico contém os dados necessários à automação."""
        event = await self.service.enqueue_event(
            company_id=COMPANY_ID,
            event_type=DomainEventType.PURCHASE_COMPLETED,
            subject_user_id="user-1",
            request_id="req-1",
            customer={
                "name": "Cliente",
                "email": "cliente@example.com",
                "phone": "+5511999999999",
            },
            plan={"id": "plan-1", "name": "Mensal"},
            data={"first_access_url": "https://app.example.com/primeiro-acesso?token=one-time"},
        )

        self.assertEqual(event.payload["event_id"], event.id)
        self.assertEqual(event.payload["event_type"], "purchase.completed")
        self.assertEqual(event.payload["customer"]["phone"], "+5511999999999")
        self.assertEqual(event.payload["plan"]["name"], "Mensal")
        self.assertIn("first_access_url", event.payload["data"])

    async def test_hmac_signature_is_stable_for_raw_body_and_timestamp(self) -> None:
        """A plataforma destinatária consegue reproduzir a assinatura."""
        signature = self.service.sign_payload(
            "whsec_test-secret",
            timestamp="1721304000",
            raw_body=b'{"event_id":"evt_1"}',
        )
        repeated = self.service.sign_payload(
            "whsec_test-secret",
            timestamp="1721304000",
            raw_body=b'{"event_id":"evt_1"}',
        )
        changed = self.service.sign_payload(
            "whsec_test-secret",
            timestamp="1721304001",
            raw_body=b'{"event_id":"evt_1"}',
        )
        self.assertEqual(signature, repeated)
        self.assertNotEqual(signature, changed)
        self.assertTrue(signature.startswith("v1="))

    async def test_delivery_disables_redirects_and_records_success(self) -> None:
        """A entrega usa HTTP assíncrono endurecido e mantém auditoria."""
        created = await self.service.create_endpoint(
            owner(),
            WebhookEndpointCreate(
                name="CRM",
                url="https://hooks.example.com/events",
                subscribed_events=frozenset({DomainEventType.TRIAL_ENDED}),
            ),
        )
        event = await self.service.enqueue_event(
            company_id=COMPANY_ID,
            event_type=DomainEventType.TRIAL_ENDED,
            subject_user_id="user-1",
            request_id="req-delivery",
            customer={"email": "client@example.com"},
            plan=None,
            data={},
        )
        pending = next(iter(self.repository.deliveries.values()))
        pending.id = "rpc-generated-delivery-id"
        self.repository.deliveries = {(COMPANY_ID, pending.id): pending}
        response = AsyncMock()
        response.status_code = 204
        response.content = b""
        client = AsyncMock()
        client.post.return_value = response
        client.__aenter__.return_value = client
        client.__aexit__.return_value = None

        with (
            patch("backend.webhook_service.httpx.AsyncClient", return_value=client) as factory,
            patch.object(self.service, "_validate_resolved_host", new=AsyncMock()),
        ):
            delivery = await self.service.deliver_event(created.endpoint.id, event.id)

        self.assertEqual(delivery.status.value, "delivered")
        self.assertEqual(delivery.id, "rpc-generated-delivery-id")
        self.assertEqual(delivery.request_id, "req-delivery")
        self.assertFalse(factory.call_args.kwargs["follow_redirects"])
        sent_headers = client.post.await_args.kwargs["headers"]
        self.assertEqual(sent_headers["X-Webhook-ID"], event.id)
        self.assertEqual(sent_headers["X-Request-ID"], "req-delivery")
        self.assertIn("X-Webhook-Signature", sent_headers)


class WebhookApiTests(unittest.TestCase):
    """Valida os contratos REST administrativos."""

    def setUp(self) -> None:
        key = base64.urlsafe_b64encode(os.urandom(32)).decode()
        self.repository = InMemoryWebhookRepository()
        service = WebhookService(self.repository, EncryptionService(key))

        async def admin() -> dict[str, str]:
            return {
                "user_id": "owner",
                "company_id": COMPANY_ID,
                "permissions": ",".join(permission.value for permission in AdminPermission),
                "manageable_role_ids": "*",
            }

        app = FastAPI()
        app.include_router(create_webhook_router(service, admin))
        self.client = TestClient(app)

    def test_create_rejects_company_id_and_returns_secret_once(self) -> None:
        """O body não escolhe tenant e a criação segue status REST 201."""
        body = {
            "name": "Automação",
            "url": "https://hooks.example.com/events",
            "subscribed_events": ["purchase.completed"],
        }
        forged = self.client.post(
            "/admin/webhooks",
            json={**body, "company_id": OTHER_COMPANY_ID},
        )
        self.assertEqual(forged.status_code, 422)

        created = self.client.post(
            "/admin/webhooks",
            headers={"X-Request-ID": "req-create"},
            json=body,
        )
        self.assertEqual(created.status_code, 201)
        self.assertTrue(created.json()["data"]["signing_secret"].startswith("whsec_"))
        listed = self.client.get("/admin/webhooks")
        self.assertNotIn("signing_secret", listed.text)

    def test_catalog_documents_password_recovery_without_plain_password(self) -> None:
        """O catálogo expõe link temporário e nunca senha em texto."""
        response = self.client.get("/admin/webhooks/catalog")
        self.assertEqual(response.status_code, 200)
        recovery = next(
            item
            for item in response.json()["data"]
            if item["event_type"] == "user.password_recovery_requested"
        )
        data = recovery["example"]["data"]
        self.assertIn("recovery_url", data)
        self.assertNotIn("temporary_password", data)
        self.assertNotIn("password", data)


class QueueDeliveriesResilienceTests(unittest.IsolatedAsyncioTestCase):
    """Garante que falha em destinos HTTP não bloqueia e-mail nem compra."""

    async def test_email_is_queued_even_when_endpoint_listing_fails(self) -> None:
        """list_subscribed_endpoints com erro não impede queue_for_event."""
        key = base64.urlsafe_b64encode(os.urandom(32)).decode()
        repository = InMemoryWebhookRepository()
        emails = AsyncMock()
        emails.queue_for_event = AsyncMock()
        service = WebhookService(
            repository,
            EncryptionService(key),
            emails=emails,
        )
        event = await service.enqueue_event(
            company_id=COMPANY_ID,
            event_type=DomainEventType.PURCHASE_COMPLETED,
            subject_user_id="user-1",
            request_id="req-1",
            customer={"email": "client@example.com", "name": "Cliente"},
            plan={"id": "plan-1", "name": "Mensal"},
            data={"amount": 99.9, "currency": "BRL"},
        )
        repository.list_subscribed_endpoints = AsyncMock(
            side_effect=RuntimeError("postgrest 400")
        )

        await service.queue_event_deliveries(event)

        emails.queue_for_event.assert_awaited_once_with(event)


class AccountLinkAndEncryptionTests(unittest.IsolatedAsyncioTestCase):
    """Valida links de uso único e criptografia autenticada."""

    def test_encryption_roundtrip_and_invalid_key(self) -> None:
        """AES-GCM recupera o segredo e rejeita chave com tamanho incorreto."""
        key = base64.urlsafe_b64encode(os.urandom(32)).decode()
        encryption = EncryptionService(key)
        encrypted = encryption.encrypt("whsec_sensitive")
        self.assertNotIn("whsec_sensitive", encrypted)
        self.assertEqual(encryption.decrypt(encrypted), "whsec_sensitive")
        with self.assertRaises(ValueError):
            EncryptionService(base64.urlsafe_b64encode(b"short").decode())

    async def test_recovery_uses_company_from_verified_auth_metadata(self) -> None:
        """O tenant vem do usuário retornado pela Auth Admin."""
        response = AsyncMock()
        response.status_code = 200
        response.json = lambda: {
            "action_link": "https://auth.example.com/verify?token=one-time",
            "user": {
                "id": "user-1",
                "app_metadata": {"company_id": COMPANY_ID},
            },
        }
        client = AsyncMock()
        client.post.return_value = response
        client.__aenter__.return_value = client
        client.__aexit__.return_value = None
        service = SupabaseAccountLinkService(
            "https://project.supabase.co",
            "server-only-key",
            redirect_url="https://app.example.com/reset-password",
        )

        with patch("backend.account_link_service.httpx.AsyncClient", return_value=client):
            link = await service.create_recovery_link("client@example.com")

        self.assertIsNotNone(link)
        self.assertEqual(link.company_id, COMPANY_ID)
        self.assertEqual(link.user_id, "user-1")
        sent_body = client.post.await_args.kwargs["json"]
        self.assertNotIn("company_id", sent_body)
        self.assertNotIn("password", sent_body)

    async def test_recovery_does_not_reveal_missing_account(self) -> None:
        """Conta inexistente produz resultado neutro."""
        response = AsyncMock()
        response.status_code = 404
        client = AsyncMock()
        client.post.return_value = response
        client.__aenter__.return_value = client
        client.__aexit__.return_value = None
        service = SupabaseAccountLinkService(
            "https://project.supabase.co",
            "server-only-key",
            redirect_url="https://app.example.com/reset-password",
        )

        with patch("backend.account_link_service.httpx.AsyncClient", return_value=client):
            link = await service.create_recovery_link("missing@example.com")

        self.assertIsNone(link)
