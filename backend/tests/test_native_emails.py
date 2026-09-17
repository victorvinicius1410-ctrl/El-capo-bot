"""Testes do domínio de emails nativos."""

from __future__ import annotations

import base64
import json
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.admin_models import AdminActor, AdminPermission
from backend.email_models import EmailTemplateUpdate
from backend.email_repository import (
    EMAIL_STORAGE_BUCKET,
    InMemoryEmailRepository,
    SupabaseEmailRepository,
)
from backend.email_router import create_email_router
from backend.email_service import (
    DEFAULT_BODIES,
    DEFAULT_SUBJECTS,
    FACTORY_RENEWAL_SNIPPET,
    EmailAuthorizationError,
    EmailConfig,
    EmailService,
    EmailValidationError,
)
from backend.services.encryption_service import EncryptionService
from backend.webhook_models import DomainEvent, DomainEventType
from backend.webhook_repository import InMemoryWebhookRepository
from backend.webhook_service import WebhookService


COMPANY_ID = "00000000-0000-0000-0000-000000000001"
OTHER_COMPANY_ID = "00000000-0000-0000-0000-000000000002"


def owner(company_id: str = COMPANY_ID) -> AdminActor:
    """Cria ator com permissões de email."""
    return AdminActor(
        user_id="owner",
        company_id=company_id,
        permissions=frozenset(AdminPermission),
        manageable_role_ids=None,
    )


def viewer(company_id: str = COMPANY_ID) -> AdminActor:
    """Ator somente leitura."""
    return AdminActor(
        user_id="viewer",
        company_id=company_id,
        permissions=frozenset({AdminPermission.EMAILS_VIEW}),
        manageable_role_ids=None,
    )


class EmailServiceTests(unittest.IsolatedAsyncioTestCase):
    """Valida templates, render, isolamento e fila."""

    async def asyncSetUp(self) -> None:
        self.repository = InMemoryEmailRepository()
        self.service = EmailService(
            self.repository,
            EmailConfig(
                enabled=True,
                api_key="re_test",
                from_address="ElCapo <noreply@example.com>",
                frontend_url="https://app.example.com",
            ),
        )

    async def test_list_seeds_every_event_enabled(self) -> None:
        """Lista garante um template por evento e todos já nascem ativos."""
        templates = await self.service.list_templates(owner())
        self.assertEqual(len(templates), len(DomainEventType))
        enabled = {item.event_type for item in templates if item.is_enabled}
        self.assertEqual(enabled, set(DomainEventType))
        self.assertEqual(set(DEFAULT_BODIES), set(DomainEventType))

    async def test_list_upgrades_factory_renewal_template(self) -> None:
        """HTML curto e desligado da renovação é substituído e ativado uma vez."""
        from backend.email_models import EmailTemplate
        from backend.email_repository import new_template_id

        now = datetime.now(timezone.utc)
        old_html = (
            "<p>Olá <strong>{{customer_name}}</strong>, "
            "Sua assinatura <strong>{{plan_name}}</strong> foi renovada "
            "({{amount}} {{currency}}).</p>"
        )
        self.repository.templates[(COMPANY_ID, DomainEventType.SUBSCRIPTION_RENEWED.value)] = (
            EmailTemplate(
                id=new_template_id(),
                company_id=COMPANY_ID,
                event_type=DomainEventType.SUBSCRIPTION_RENEWED,
                subject="Assinatura renovada — ElCapo",
                html_body=old_html,
                is_enabled=False,
                created_at=now,
                updated_at=now,
            )
        )
        templates = await self.service.list_templates(owner())
        renewed = next(
            item for item in templates if item.event_type == DomainEventType.SUBSCRIPTION_RENEWED
        )
        self.assertTrue(renewed.is_enabled)
        self.assertIn("ACESSAR O PAINEL", renewed.html_body)
        self.assertNotIn(FACTORY_RENEWAL_SNIPPET, renewed.html_body)

    async def test_update_requires_manage_permission(self) -> None:
        """Viewer não edita HTML."""
        with self.assertRaises(EmailAuthorizationError):
            await self.service.update_template(
                viewer(),
                DomainEventType.TRIAL_STARTED,
                EmailTemplateUpdate(
                    subject="Trial",
                    html_body="<p>oi</p>",
                    is_enabled=True,
                ),
            )

    async def test_render_replaces_known_variables(self) -> None:
        """Placeholders conhecidos são substituídos."""
        rendered = EmailService.render(
            "Olá {{customer_name}}",
            "<p>{{plan_name}} — {{login_url}}</p>",
            {
                "customer_name": "Ana",
                "plan_name": "Mensal",
                "login_url": "https://app.example.com/login",
            },
        )
        self.assertEqual(rendered.subject, "Olá Ana")
        self.assertIn("Mensal", rendered.html_body)
        self.assertIn("https://app.example.com/login", rendered.html_body)

    async def test_purchase_email_includes_first_access_link_for_new_customer(self) -> None:
        """Cliente novo (1ª compra) recebe o link para definir senha."""
        variables = self.service.build_variables(
            DomainEventType.PURCHASE_COMPLETED,
            {
                "customer": {"name": "Ana", "email": "ana@example.com"},
                "plan": {"name": "Mensal"},
                "data": {
                    "amount": 147.9,
                    "currency": "BRL",
                    "first_access_url": "https://app.example.com/reset-password?token=abc",
                },
            },
        )
        rendered = EmailService.render(
            "Bem-vindo",
            DEFAULT_BODIES[DomainEventType.PURCHASE_COMPLETED],
            variables,
        )
        self.assertIn(
            'href="https://app.example.com/reset-password?token=abc"',
            rendered.html_body,
        )
        self.assertNotIn('href=""', rendered.html_body)

    async def test_purchase_cta_falls_back_to_login_without_first_access(self) -> None:
        """Sem link de senha, o botão aponta para o login em vez de href vazio."""
        variables = self.service.build_variables(
            DomainEventType.PURCHASE_COMPLETED,
            {
                "customer": {"name": "Ana", "email": "ana@example.com"},
                "plan": {"name": "Mensal"},
                "data": {"amount": 147.9, "currency": "BRL"},
            },
        )
        rendered = EmailService.render(
            "Bem-vindo",
            DEFAULT_BODIES[DomainEventType.PURCHASE_COMPLETED],
            variables,
        )
        self.assertNotIn('href=""', rendered.html_body)
        self.assertIn('href="https://app.example.com/login"', rendered.html_body)

    async def test_purchase_email_omits_first_access_link_for_existing_customer(self) -> None:
        """Template de quem já tinha conta não pede senha nova."""
        variables = self.service.build_variables(
            DomainEventType.PURCHASE_EXISTING_ACCOUNT,
            {
                "customer": {"name": "Ana", "email": "ana@example.com"},
                "plan": {"name": "Mensal"},
                "data": {"amount": 147.9, "currency": "BRL"},
            },
        )
        rendered = EmailService.render(
            "Compra confirmada",
            DEFAULT_BODIES[DomainEventType.PURCHASE_EXISTING_ACCOUNT],
            variables,
        )
        self.assertNotIn("Defina sua senha", rendered.html_body)
        self.assertNotIn("{{first_access", rendered.html_body)
        self.assertNotIn('href=""', rendered.html_body)
        self.assertIn("https://app.example.com/login", rendered.html_body)
        self.assertIn("mesma senha", rendered.html_body)

    async def test_renewal_email_confirms_plan_and_login(self) -> None:
        """Renovação confirma valor e aponta para o painel, sem link de 1º acesso."""
        variables = self.service.build_variables(
            DomainEventType.SUBSCRIPTION_RENEWED,
            {
                "customer": {"name": "Ana", "email": "ana@example.com"},
                "plan": {"name": "Mensal"},
                "data": {"amount": 147.9, "currency": "BRL"},
            },
        )
        rendered = EmailService.render(
            "Assinatura renovada",
            DEFAULT_BODIES[DomainEventType.SUBSCRIPTION_RENEWED],
            variables,
        )
        self.assertIn("renovada", rendered.html_body.lower())
        self.assertIn("R$ 147,90", rendered.html_body)
        self.assertIn("https://app.example.com/login", rendered.html_body)
        self.assertNotIn("Defina sua senha", rendered.html_body)

    async def test_queue_skips_disabled_template(self) -> None:
        """Template desativado pelo admin não gera entrega."""
        await self.service.list_templates(owner())
        await self.service.update_template(
            owner(),
            DomainEventType.TRIAL_STARTED,
            EmailTemplateUpdate(
                subject="Trial",
                html_body="<p>oi</p>",
                is_enabled=False,
            ),
        )
        event = DomainEvent(
            id="evt-1",
            company_id=COMPANY_ID,
            event_type=DomainEventType.TRIAL_STARTED,
            subject_user_id="user-1",
            request_id="req-1",
            payload={
                "customer": {"name": "Ana", "email": "ana@example.com"},
                "plan": None,
                "data": {"expires_at": "2026-08-01T12:00:00+00:00"},
            },
            occurred_at=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
        )
        result = await self.service.queue_for_event(event)
        self.assertIsNone(result)

    async def test_queue_seeds_existing_account_template_when_missing(self) -> None:
        """Compra de conta existente envia mesmo sem o admin abrir a lista de templates."""
        event = DomainEvent(
            id="evt-existing-seed",
            company_id=COMPANY_ID,
            event_type=DomainEventType.PURCHASE_EXISTING_ACCOUNT,
            subject_user_id="user-3",
            request_id="req-existing",
            payload={
                "customer": {"name": "Ana", "email": "ana@example.com"},
                "plan": {"name": "Mensal"},
                "data": {"amount": 147.9, "currency": "BRL"},
            },
            occurred_at=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
        )
        with patch("backend.workers.email_tasks.deliver_email") as task:
            task.delay = lambda *args, **kwargs: None
            delivery = await self.service.queue_for_event(event)
        self.assertIsNotNone(delivery)
        assert delivery is not None
        self.assertEqual(delivery.event_type, DomainEventType.PURCHASE_EXISTING_ACCOUNT)

    async def test_queue_and_deliver_enabled_template(self) -> None:
        """Template ativo enfileira e envia via provedor mockado."""
        await self.service.update_template(
            owner(),
            DomainEventType.PURCHASE_COMPLETED,
            EmailTemplateUpdate(
                subject="Bem-vindo {{customer_name}}",
                html_body="<p>{{plan_name}}</p>",
                is_enabled=True,
            ),
        )
        event = DomainEvent(
            id="evt-2",
            company_id=COMPANY_ID,
            event_type=DomainEventType.PURCHASE_COMPLETED,
            subject_user_id="user-2",
            request_id="req-2",
            payload={
                "customer": {"name": "Ana", "email": "ana@example.com"},
                "plan": {"name": "Mensal"},
                "data": {"amount": 147.9, "currency": "BRL"},
            },
            occurred_at=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
        )

        with patch("backend.workers.email_tasks.deliver_email") as task:
            task.delay = lambda *args, **kwargs: None
            delivery = await self.service.queue_for_event(event)

        self.assertIsNotNone(delivery)
        assert delivery is not None
        self.assertEqual(delivery.event_type, DomainEventType.PURCHASE_COMPLETED)

        with patch.object(
            self.service,
            "_send_message",
            new=AsyncMock(return_value="msg_123"),
        ):
            updated = await self.service.deliver_now(
                delivery,
                recipient_email="ana@example.com",
                html_body="<p>Mensal</p>",
                subject="Bem-vindo Ana",
            )
        self.assertEqual(updated.status.value, "delivered")
        self.assertEqual(updated.provider_message_id, "msg_123")

    def test_smtp_environment_loads_hostinger_settings(self) -> None:
        """Provider SMTP exige host/usuário/senha e não exige Resend."""
        with patch.dict(
            os.environ,
            {
                "APP_ENV": "production",
                "PROD_EMAILS_ENABLED": "true",
                "PROD_EMAIL_PROVIDER": "smtp",
                "PROD_SMTP_HOST": "smtp.hostinger.com",
                "PROD_SMTP_PORT": "465",
                "PROD_SMTP_USERNAME": "noreply@elcapobot.online",
                "PROD_SMTP_PASSWORD": "secret-password-hostinger",
                "PROD_EMAIL_FROM": "ElCapo <noreply@elcapobot.online>",
            },
            clear=False,
        ):
            config = EmailConfig.from_environment("https://app.elcapobot.online")
        self.assertEqual(config.provider, "smtp")
        self.assertTrue(config.smtp_configured)
        self.assertEqual(config.smtp_host, "smtp.hostinger.com")

    def test_settings_expose_template_variables(self) -> None:
        """Admin recebe catálogo de variáveis sem segredos."""
        view = self.service.settings()
        self.assertIn("customer_name", view.available_variables)
        self.assertEqual(view.variable_descriptions["login_url"], "URL de login do painel")
        self.assertIn("purchase.completed", view.events)

    async def test_tenant_isolation(self) -> None:
        """Empresa B não vê template da empresa A."""
        await self.service.update_template(
            owner(COMPANY_ID),
            DomainEventType.TRIAL_ENDED,
            EmailTemplateUpdate(
                subject="Fim A",
                html_body="<p>A</p>",
                is_enabled=True,
            ),
        )
        other = await self.service.list_templates(owner(OTHER_COMPANY_ID))
        trial = next(item for item in other if item.event_type == DomainEventType.TRIAL_ENDED)
        self.assertEqual(trial.subject, DEFAULT_SUBJECTS[DomainEventType.TRIAL_ENDED])
        self.assertNotEqual(trial.subject, "Fim A")

    async def test_webhook_queue_also_triggers_email(self) -> None:
        """queue_event_deliveries notifica o EmailService."""
        emails = EmailService(
            InMemoryEmailRepository(),
            EmailConfig(
                enabled=True,
                api_key="re_test",
                from_address="ElCapo <noreply@example.com>",
                frontend_url="https://app.example.com",
            ),
        )
        await emails.update_template(
            owner(),
            DomainEventType.TRIAL_STARTED,
            EmailTemplateUpdate(
                subject="Trial",
                html_body="<p>oi</p>",
                is_enabled=True,
            ),
        )
        key = base64.urlsafe_b64encode(os.urandom(32)).decode()
        webhooks = WebhookService(
            InMemoryWebhookRepository(),
            EncryptionService(key),
            emails=emails,
        )
        event = await webhooks.enqueue_event(
            company_id=COMPANY_ID,
            event_type=DomainEventType.TRIAL_STARTED,
            subject_user_id="user-3",
            request_id="req-3",
            customer={"name": "Ana", "email": "ana@example.com"},
            plan=None,
            data={"expires_at": "2026-08-01T12:00:00+00:00"},
        )
        calls: list[tuple] = []

        def _capture(*args: object, **kwargs: object) -> None:
            calls.append((args, kwargs))

        with patch("backend.workers.webhook_tasks.deliver_webhook") as webhook_task:
            webhook_task.delay = lambda *args, **kwargs: None
            with patch("backend.workers.email_tasks.deliver_email") as email_task:
                email_task.delay = _capture
                await webhooks.queue_event_deliveries(event)
        self.assertEqual(len(calls), 1)
        deliveries = await emails.list_deliveries(owner())
        self.assertEqual(len(deliveries), 1)


class StoragePruneTests(unittest.IsolatedAsyncioTestCase):
    """Regressão: o DELETE unitário era recusado com 400 e nada era apagado."""

    async def test_prune_sends_batch_delete_with_prefixes(self) -> None:
        """A remoção vai em lote, no bucket, com os obsoletos no corpo."""
        prefix = "tenant-1/deliveries/"
        listed = [{"name": f"2026090{index}_abc.json"} for index in range(1, 6)]
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            if request.url.path.startswith("/storage/v1/object/list/"):
                return httpx.Response(200, json=listed)
            return httpx.Response(200, json=[])

        repository = SupabaseEmailRepository(
            "https://project.supabase.co",
            "server-only-key",
        )
        # `backend.email_repository.httpx` é o módulo global: sem guardar a
        # classe real antes, o lambda chamaria a si mesmo.
        real_client = httpx.AsyncClient
        transport = httpx.MockTransport(handler)
        with patch(
            "backend.email_repository.httpx.AsyncClient",
            lambda **kwargs: real_client(transport=transport),
        ):
            await repository._storage_prune_prefix(prefix, keep=3)

        removal = captured[-1]
        self.assertEqual(removal.method, "DELETE")
        self.assertEqual(
            removal.url.path, f"/storage/v1/object/{EMAIL_STORAGE_BUCKET}"
        )
        body = json.loads(removal.content)
        # Os 3 mais recentes ficam; os 2 mais antigos saem, com o prefixo junto.
        self.assertEqual(
            body["prefixes"],
            [f"{prefix}20260902_abc.json", f"{prefix}20260901_abc.json"],
        )

    async def test_prune_logs_when_storage_rejects(self) -> None:
        """Falha da remoção precisa aparecer no log, não sumir em silêncio."""
        listed = [{"name": f"2026090{index}_abc.json"} for index in range(1, 6)]

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.startswith("/storage/v1/object/list/"):
                return httpx.Response(200, json=listed)
            return httpx.Response(400, json={"error": "invalid"})

        repository = SupabaseEmailRepository(
            "https://project.supabase.co",
            "server-only-key",
        )
        # `backend.email_repository.httpx` é o módulo global: sem guardar a
        # classe real antes, o lambda chamaria a si mesmo.
        real_client = httpx.AsyncClient
        transport = httpx.MockTransport(handler)
        with patch(
            "backend.email_repository.httpx.AsyncClient",
            lambda **kwargs: real_client(transport=transport),
        ):
            with self.assertLogs("backend-email-repository", level="WARNING") as logs:
                await repository._storage_prune_prefix("tenant-1/deliveries/", keep=3)

        self.assertTrue(
            any("email.storage_prune_failed" in line for line in logs.output)
        )


class EmailRouterTests(unittest.TestCase):
    """Contratos REST e autorização."""

    def setUp(self) -> None:
        self.repository = InMemoryEmailRepository()
        self.service = EmailService(
            self.repository,
            EmailConfig(
                enabled=False,
                api_key="",
                from_address="",
                frontend_url="http://localhost:5173",
            ),
        )
        app = FastAPI()

        async def require_admin() -> dict[str, str]:
            return {
                "user_id": "owner",
                "company_id": COMPANY_ID,
                "email": "admin@example.com",
                "permissions": ",".join(p.value for p in AdminPermission),
                "manageable_role_ids": "*",
                "is_admin": "true",
            }

        app.include_router(create_email_router(self.service, require_admin))
        self.client = TestClient(app)

    def test_list_templates_and_settings(self) -> None:
        """Lista templates e settings sem expor chave."""
        templates = self.client.get("/admin/emails/templates")
        self.assertEqual(templates.status_code, 200)
        body = templates.json()
        self.assertTrue(body["ok"])
        self.assertEqual(len(body["data"]), len(DomainEventType))

        settings = self.client.get("/admin/emails/settings")
        self.assertEqual(settings.status_code, 200)
        data = settings.json()["data"]
        self.assertFalse(data["enabled"])
        self.assertNotIn("api_key", data)
        self.assertNotIn("smtp_password", data)
        self.assertIn(data["provider"], {"smtp", "resend"})
        self.assertIn("customer_name", data["available_variables"])
        self.assertIn("purchase.completed", data["events"])

    def test_update_and_preview(self) -> None:
        """Salva HTML e preview substitui variáveis."""
        updated = self.client.put(
            "/admin/emails/templates/trial.started",
            json={
                "subject": "Olá {{customer_name}}",
                "html_body": "<p>{{login_url}}</p>",
                "is_enabled": True,
            },
        )
        self.assertEqual(updated.status_code, 200)
        self.assertTrue(updated.json()["data"]["is_enabled"])

        preview = self.client.post(
            "/admin/emails/templates/trial.started/preview",
            json={},
        )
        self.assertEqual(preview.status_code, 200)
        rendered = preview.json()["data"]
        self.assertIn("Cliente Exemplo", rendered["subject"])
        self.assertIn("/login", rendered["html_body"])

    def test_patch_update_template(self) -> None:
        """PATCH atualiza o template (contrato preferido no admin)."""
        updated = self.client.patch(
            "/admin/emails/templates/trial.started",
            json={
                "subject": "Patch {{customer_name}}",
                "html_body": "<p>patch</p>",
                "is_enabled": False,
            },
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["data"]["subject"], "Patch {{customer_name}}")

    def test_test_send_rejects_when_disabled(self) -> None:
        """Teste falha com EMAILS_DISABLED quando provedor off."""
        response = self.client.post("/admin/emails/templates/trial.started/test")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "VALIDATION_ERROR")

    def test_test_send_accepts_recipient_in_body(self) -> None:
        """Admin pode informar o destinatário do e-mail de teste no body."""
        self.service.config = EmailConfig(
            enabled=True,
            api_key="re_test",
            from_address="ElCapo <noreply@example.com>",
            frontend_url="http://localhost:5173",
            provider="resend",
        )
        captured: dict[str, str] = {}

        async def _fake_send(*, to_email: str, subject: str, html_body: str) -> str:
            captured["to_email"] = to_email
            return "msg_test_1"

        with patch.object(self.service, "_send_message", new=_fake_send):
            response = self.client.post(
                "/admin/emails/templates/trial.started/test",
                json={"recipient_email": "teste@destino.com"},
            )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(captured.get("to_email"), "teste@destino.com")
        self.assertEqual(response.json()["data"]["status"], "delivered")

    def test_test_send_rejects_invalid_recipient(self) -> None:
        """Destinatário inválido no body retorna VALIDATION_ERROR."""
        self.service.config = EmailConfig(
            enabled=True,
            api_key="re_test",
            from_address="ElCapo <noreply@example.com>",
            frontend_url="http://localhost:5173",
            provider="resend",
        )
        response = self.client.post(
            "/admin/emails/templates/trial.started/test",
            json={"recipient_email": "sem-arroba"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "VALIDATION_ERROR")


class EmailWorkerRegistrationTests(unittest.TestCase):
    """Garante que o worker Celery conhece emails.deliver."""

    def test_webhook_celery_app_registers_email_deliver_task(self) -> None:
        """Task emails.deliver deve existir no mesmo app do webhook-worker."""
        from backend.workers.webhook_tasks import celery_app

        self.assertIn("emails.deliver", celery_app.tasks)


class EmailOrphanPendingTests(unittest.IsolatedAsyncioTestCase):
    """Pendentes nunca despachados passam a falhar de forma explícita."""

    async def asyncSetUp(self) -> None:
        self.repository = InMemoryEmailRepository()
        self.service = EmailService(
            self.repository,
            EmailConfig(
                enabled=True,
                api_key="re_test",
                from_address="ElCapo <noreply@example.com>",
                frontend_url="https://app.example.com",
            ),
        )

    async def test_list_deliveries_marks_stale_pending_as_failed(self) -> None:
        """Pending com 0 tentativas e idade alta vira failed (nunca enviou)."""
        from datetime import timedelta

        from backend.email_models import EmailDelivery, EmailDeliveryStatus

        stale = EmailDelivery(
            id="del-stale",
            company_id=COMPANY_ID,
            event_id="evt-stale",
            event_type=DomainEventType.PURCHASE_COMPLETED,
            recipient_email_hash="abc",
            subject="Bem-vindo",
            status=EmailDeliveryStatus.PENDING,
            attempt_count=0,
            provider_message_id=None,
            latency_ms=None,
            next_attempt_at=None,
            last_error_code=None,
            request_id="req-stale",
            created_at=datetime.now(timezone.utc) - timedelta(minutes=10),
            updated_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        )
        await self.repository.save_delivery(stale)
        items = await self.service.list_deliveries(owner())
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].status, EmailDeliveryStatus.FAILED)
        self.assertEqual(items[0].last_error_code, "DISPATCH_TIMEOUT")


if __name__ == "__main__":
    unittest.main()
