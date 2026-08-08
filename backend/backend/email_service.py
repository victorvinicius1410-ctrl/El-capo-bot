"""Regras de negócio e envio de emails nativos."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from backend.admin_models import AdminActor, AdminPermission
from backend.email_models import (
    EmailDelivery,
    EmailDeliveryStatus,
    EmailSettingsView,
    EmailTemplate,
    EmailTemplateUpdate,
    RenderedEmail,
)
from backend.email_repository import EmailRepository, new_template_id
from backend.webhook_models import DomainEvent, DomainEventType


logger = logging.getLogger("backend-emails")

VARIABLE_PATTERN = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")

TEMPLATE_VARIABLE_DESCRIPTIONS: dict[str, str] = {
    "customer_name": "Nome do cliente",
    "customer_email": "E-mail do cliente",
    "plan_name": "Nome da oferta/plano",
    "amount": "Valor cobrado",
    "currency": "Moeda (ex.: BRL)",
    "first_access_url": "Link para definir senha no primeiro acesso",
    "first_access_block": (
        "Bloco HTML com o link de 1º acesso (só aparece quando há link; "
        "vazio em renovações de clientes já cadastrados)"
    ),
    "recovery_url": "Link de recuperação de senha",
    "expires_at": "Data/hora de expiração (ISO)",
    "expires_in_seconds": "Segundos até expirar o link",
    "login_url": "URL de login do painel",
    "company_name": "Nome da empresa (ElCapo AutoBot)",
    "event_type": "Código do evento que disparou o e-mail",
    "checkout_url": "URL de checkout Cakto (quando aplicável)",
    "support_url": "URL de suporte (WhatsApp/site)",
}

AVAILABLE_TEMPLATE_VARIABLES: tuple[str, ...] = tuple(TEMPLATE_VARIABLE_DESCRIPTIONS.keys())

EVENT_LABELS: dict[DomainEventType, str] = {
    DomainEventType.PURCHASE_COMPLETED: "Compra / boas-vindas",
    DomainEventType.SUBSCRIPTION_RENEWED: "Assinatura renovada",
    DomainEventType.SUBSCRIPTION_CANCELED: "Assinatura cancelada",
    DomainEventType.PAYMENT_REFUNDED: "Reembolso",
    DomainEventType.PAYMENT_CHARGEBACK: "Chargeback",
    DomainEventType.SUBSCRIPTION_PAYMENT_FAILED: "Falha no pagamento",
    DomainEventType.TRIAL_STARTED: "Teste iniciado",
    DomainEventType.TRIAL_ENDED: "Teste encerrado",
    DomainEventType.PASSWORD_RECOVERY_REQUESTED: "Recuperação de senha",
}

DEFAULT_SUBJECTS: dict[DomainEventType, str] = {
    DomainEventType.PURCHASE_COMPLETED: "Bem-vindo ao ElCapo AutoBot",
    DomainEventType.SUBSCRIPTION_RENEWED: "Assinatura renovada — ElCapo",
    DomainEventType.SUBSCRIPTION_CANCELED: "Assinatura cancelada — ElCapo",
    DomainEventType.PAYMENT_REFUNDED: "Reembolso confirmado — ElCapo",
    DomainEventType.PAYMENT_CHARGEBACK: "Atualização de pagamento — ElCapo",
    DomainEventType.SUBSCRIPTION_PAYMENT_FAILED: "Falha no pagamento — ElCapo",
    DomainEventType.TRIAL_STARTED: "Seu teste grátis começou — ElCapo",
    DomainEventType.TRIAL_ENDED: "Seu teste grátis encerrou — ElCapo",
    DomainEventType.PASSWORD_RECOVERY_REQUESTED: "Redefinir senha — ElCapo",
}

DEFAULT_HTML_SHELL = """<!DOCTYPE html>
<html lang="pt-BR">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width"></head>
<body style="margin:0;padding:0;background:#0b1220;font-family:Arial,Helvetica,sans-serif;color:#e8f7f8;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#0b1220;padding:32px 16px;">
    <tr><td align="center">
      <table role="presentation" width="560" cellspacing="0" cellpadding="0" style="background:#121a2b;border:1px solid #1e3a4a;border-radius:12px;overflow:hidden;">
        <tr><td style="padding:28px 28px 8px;font-size:22px;font-weight:700;color:#7ef0f3;">ElCapo AutoBot</td></tr>
        <tr><td style="padding:8px 28px 24px;font-size:15px;line-height:1.55;color:#c9e4e6;">
          {body}
        </td></tr>
        <tr><td style="padding:16px 28px 28px;font-size:12px;color:#6f8b8e;">
          Este email foi enviado automaticamente. Não compartilhe links de acesso.
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""

DEFAULT_BODIES: dict[DomainEventType, str] = {
    DomainEventType.PURCHASE_COMPLETED: DEFAULT_HTML_SHELL.format(
        body=(
            "Olá <strong>{{customer_name}}</strong>,<br><br>"
            "Sua compra do plano <strong>{{plan_name}}</strong> foi confirmada"
            " ({{amount}} {{currency}}).<br><br>"
            "{{first_access_block}}"
            'Acesse o painel: <a href="{{login_url}}" style="color:#7ef0f3;">{{login_url}}</a>'
        )
    ),
    DomainEventType.SUBSCRIPTION_RENEWED: DEFAULT_HTML_SHELL.format(
        body=(
            "Olá <strong>{{customer_name}}</strong>,<br><br>"
            "Sua assinatura <strong>{{plan_name}}</strong> foi renovada"
            " ({{amount}} {{currency}})."
        )
    ),
    DomainEventType.SUBSCRIPTION_CANCELED: DEFAULT_HTML_SHELL.format(
        body=(
            "Olá <strong>{{customer_name}}</strong>,<br><br>"
            "Sua assinatura <strong>{{plan_name}}</strong> foi cancelada."
            " Você pode reativar em <a href=\"{{login_url}}\" style=\"color:#7ef0f3;\">{{login_url}}</a>."
        )
    ),
    DomainEventType.PAYMENT_REFUNDED: DEFAULT_HTML_SHELL.format(
        body=(
            "Olá <strong>{{customer_name}}</strong>,<br><br>"
            "Confirmamos o reembolso de {{amount}} {{currency}}."
        )
    ),
    DomainEventType.PAYMENT_CHARGEBACK: DEFAULT_HTML_SHELL.format(
        body=(
            "Olá <strong>{{customer_name}}</strong>,<br><br>"
            "Registramos uma contestação de {{amount}} {{currency}}."
            " Se precisar de suporte, responda este email."
        )
    ),
    DomainEventType.SUBSCRIPTION_PAYMENT_FAILED: DEFAULT_HTML_SHELL.format(
        body=(
            "Olá <strong>{{customer_name}}</strong>,<br><br>"
            "Não foi possível processar o pagamento de <strong>{{plan_name}}</strong>."
            " Atualize seus dados em <a href=\"{{login_url}}\" style=\"color:#7ef0f3;\">{{login_url}}</a>."
        )
    ),
    DomainEventType.TRIAL_STARTED: DEFAULT_HTML_SHELL.format(
        body=(
            "Olá <strong>{{customer_name}}</strong>,<br><br>"
            "Seu teste grátis está ativo até <strong>{{expires_at}}</strong>."
            " Entre em <a href=\"{{login_url}}\" style=\"color:#7ef0f3;\">{{login_url}}</a>."
        )
    ),
    DomainEventType.TRIAL_ENDED: DEFAULT_HTML_SHELL.format(
        body=(
            "Olá <strong>{{customer_name}}</strong>,<br><br>"
            "Seu teste grátis encerrou. Escolha uma oferta em"
            " <a href=\"{{login_url}}\" style=\"color:#7ef0f3;\">{{login_url}}</a>."
        )
    ),
    DomainEventType.PASSWORD_RECOVERY_REQUESTED: DEFAULT_HTML_SHELL.format(
        body=(
            "Olá <strong>{{customer_name}}</strong>,<br><br>"
            "Recebemos um pedido para redefinir sua senha.<br>"
            'Use este link (válido por {{expires_in_seconds}}s):'
            ' <a href="{{recovery_url}}" style="color:#7ef0f3;">{{recovery_url}}</a><br><br>'
            "Se não foi você, ignore este email."
        )
    ),
}


class EmailError(Exception):
    """Erro base do domínio de email."""


class EmailAuthorizationError(EmailError):
    """Ator sem permissão."""


class EmailValidationError(EmailError):
    """Entrada inválida."""


class EmailNotFoundError(EmailError):
    """Template inexistente no tenant."""


class EmailDeliveryError(EmailError):
    """Falha de envio."""


class EmailConfig:
    """Configuração segregada por ambiente (SMTP Hostinger ou Resend)."""

    def __init__(
        self,
        *,
        enabled: bool,
        from_address: str,
        frontend_url: str,
        provider: str | None = None,
        api_key: str = "",
        smtp_host: str = "",
        smtp_port: int = 465,
        smtp_username: str = "",
        smtp_password: str = "",
        smtp_use_ssl: bool = True,
    ) -> None:
        resolved_provider = (provider or "").strip().lower()
        if not resolved_provider:
            if smtp_host:
                resolved_provider = "smtp"
            elif api_key:
                resolved_provider = "resend"
            else:
                resolved_provider = "smtp"
        self.enabled = enabled
        self.provider = resolved_provider
        self.api_key = api_key
        self.from_address = from_address
        self.frontend_url = frontend_url.rstrip("/")
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.smtp_username = smtp_username
        self.smtp_password = smtp_password
        self.smtp_use_ssl = smtp_use_ssl

    @property
    def smtp_configured(self) -> bool:
        """Indica SMTP utilizável sem revelar a senha."""
        return bool(self.smtp_host and self.smtp_username and self.smtp_password)

    @property
    def resend_configured(self) -> bool:
        """Indica chave Resend presente."""
        return bool(self.api_key)

    @classmethod
    def from_environment(cls, frontend_url: str) -> EmailConfig:
        """
        Carrega flags e segredos com prefixo APP_ENV.

        Args:
            frontend_url: Base pública do painel para login_url.

        Returns:
            Configuração validada (credenciais obrigatórias só se enabled).

        Raises:
            ValueError: Integração ativa sem provedor completo.
        """
        from backend.env_prefix import environment_var_prefix

        app_env_raw = os.getenv("APP_ENV", "development").strip().lower()
        app_env = environment_var_prefix(app_env_raw)
        enabled = (
            os.getenv(f"{app_env}_EMAILS_ENABLED", os.getenv("EMAILS_ENABLED", "false"))
            .strip()
            .lower()
            == "true"
        )
        api_key = os.getenv(
            f"{app_env}_RESEND_API_KEY",
            os.getenv("RESEND_API_KEY", ""),
        ).strip()
        from_address = os.getenv(
            f"{app_env}_EMAIL_FROM",
            os.getenv("EMAIL_FROM", ""),
        ).strip()
        smtp_host = os.getenv(
            f"{app_env}_SMTP_HOST",
            os.getenv("SMTP_HOST", ""),
        ).strip()
        smtp_username = os.getenv(
            f"{app_env}_SMTP_USERNAME",
            os.getenv(
                f"{app_env}_SMTP_USER",
                os.getenv("SMTP_USERNAME", os.getenv("SMTP_USER", "")),
            ),
        ).strip()
        smtp_password = os.getenv(
            f"{app_env}_SMTP_PASSWORD",
            os.getenv("SMTP_PASSWORD", ""),
        ).strip()
        smtp_port_raw = os.getenv(
            f"{app_env}_SMTP_PORT",
            os.getenv("SMTP_PORT", "465"),
        ).strip() or "465"
        smtp_use_ssl = (
            os.getenv(
                f"{app_env}_SMTP_USE_SSL",
                os.getenv("SMTP_USE_SSL", "true"),
            )
            .strip()
            .lower()
            in {"1", "true", "yes"}
        )
        provider = (
            os.getenv(
                f"{app_env}_EMAIL_PROVIDER",
                os.getenv("EMAIL_PROVIDER", ""),
            )
            .strip()
            .lower()
        )
        if not provider:
            if smtp_host:
                provider = "smtp"
            elif api_key:
                provider = "resend"
            else:
                provider = "smtp"
        try:
            smtp_port = int(smtp_port_raw)
        except ValueError as exc:
            raise ValueError("SMTP_PORT inválido") from exc
        if enabled and not from_address:
            raise ValueError("EMAIL_FROM obrigatório quando EMAILS_ENABLED=true")
        if enabled and provider == "smtp":
            if not smtp_host or not smtp_username or not smtp_password:
                raise ValueError(
                    "SMTP_HOST, SMTP_USERNAME e SMTP_PASSWORD são obrigatórios para provider=smtp"
                )
        if enabled and provider == "resend" and not api_key:
            raise ValueError("RESEND_API_KEY obrigatória quando EMAIL_PROVIDER=resend")
        if enabled and "live" in api_key.lower() and app_env == "DEV":
            raise ValueError("Chave de produção detectada em ambiente development")
        return cls(
            enabled=enabled,
            provider=provider,
            api_key=api_key,
            from_address=from_address,
            frontend_url=frontend_url,
            smtp_host=smtp_host,
            smtp_port=smtp_port,
            smtp_username=smtp_username,
            smtp_password=smtp_password,
            smtp_use_ssl=smtp_use_ssl,
        )


class EmailService:
    """Gerencia templates, renderização e fila de envio."""

    MAX_ATTEMPTS = 6

    def __init__(
        self,
        repository: EmailRepository,
        config: EmailConfig,
    ) -> None:
        """
        Inicializa o domínio.

        Args:
            repository: Persistência multi-tenant.
            config: Provedor e flags de ambiente.
        """
        self.repository = repository
        self.config = config

    def settings(self) -> EmailSettingsView:
        """Retorna flags e catálogo de variáveis sem segredos."""
        return EmailSettingsView(
            enabled=self.config.enabled,
            from_configured=bool(self.config.from_address),
            provider=self.config.provider,
            smtp_configured=self.config.smtp_configured,
            available_variables=AVAILABLE_TEMPLATE_VARIABLES,
            variable_descriptions=dict(TEMPLATE_VARIABLE_DESCRIPTIONS),
            events=tuple(event.value for event in DomainEventType),
        )

    async def list_templates(self, actor: AdminActor) -> list[EmailTemplate]:
        """
        Lista templates e garante defaults persistidos.

        Args:
            actor: Administrador autenticado.

        Returns:
            Templates do tenant, um por evento canônico.
        """
        self._require(actor, AdminPermission.EMAILS_VIEW)
        existing = {
            item.event_type: item
            for item in await self.repository.list_templates(actor.company_id)
        }
        result: list[EmailTemplate] = []
        for event_type in DomainEventType:
            template = existing.get(event_type)
            if template is None:
                template = await self._seed_default(actor.company_id, event_type)
            result.append(template)
        return result

    async def get_template(
        self,
        actor: AdminActor,
        event_type: DomainEventType,
    ) -> EmailTemplate:
        """Obtém um template do tenant."""
        self._require(actor, AdminPermission.EMAILS_VIEW)
        template = await self.repository.get_template(actor.company_id, event_type)
        if template is None:
            return await self._seed_default(actor.company_id, event_type)
        return template

    async def update_template(
        self,
        actor: AdminActor,
        event_type: DomainEventType,
        payload: EmailTemplateUpdate,
    ) -> EmailTemplate:
        """
        Atualiza assunto, HTML e flag de envio.

        Args:
            actor: Administrador com emails.manage.
            event_type: Evento canônico.
            payload: Campos validados.

        Returns:
            Template persistido.

        Raises:
            EmailValidationError: Assunto/HTML inválidos.
        """
        self._require(actor, AdminPermission.EMAILS_MANAGE)
        subject = payload.subject.strip()
        html_body = payload.html_body.strip()
        if not subject or len(subject) > 200:
            raise EmailValidationError("SUBJECT_INVALID")
        if not html_body or len(html_body) > 200_000:
            raise EmailValidationError("HTML_INVALID")
        now = datetime.now(timezone.utc)
        current = await self.repository.get_template(actor.company_id, event_type)
        template = EmailTemplate(
            id=current.id if current else new_template_id(),
            company_id=actor.company_id,
            event_type=event_type,
            subject=subject,
            html_body=html_body,
            is_enabled=payload.is_enabled,
            created_at=current.created_at if current else now,
            updated_at=now,
        )
        return await self.repository.upsert_template(template)

    async def preview(
        self,
        actor: AdminActor,
        event_type: DomainEventType,
        *,
        subject: str | None = None,
        html_body: str | None = None,
        sample: dict[str, Any] | None = None,
    ) -> RenderedEmail:
        """Renderiza preview sem enviar."""
        self._require(actor, AdminPermission.EMAILS_VIEW)
        template = await self.get_template(actor, event_type)
        variables = self.build_variables(
            event_type,
            {
                "customer": {
                    "name": "Cliente Exemplo",
                    "email": "cliente@example.com",
                },
                "plan": {"name": "Mensal"},
                "data": {
                    "amount": 147.9,
                    "currency": "BRL",
                    "first_access_url": f"{self.config.frontend_url}/reset-password?token=exemplo",
                    "recovery_url": f"{self.config.frontend_url}/reset-password?token=exemplo",
                    "expires_at": "2026-08-01T12:00:00+00:00",
                    "expires_in_seconds": 3600,
                },
            },
            overrides=sample,
        )
        return self.render(
            subject or template.subject,
            html_body or template.html_body,
            variables,
        )

    async def send_test(
        self,
        actor: AdminActor,
        event_type: DomainEventType,
        recipient_email: str,
    ) -> EmailDelivery:
        """Envia email de teste ao administrador autenticado."""
        self._require(actor, AdminPermission.EMAILS_MANAGE)
        if not self.config.enabled:
            raise EmailValidationError("EMAILS_DISABLED")
        email = recipient_email.strip().lower()
        if "@" not in email:
            raise EmailValidationError("RECIPIENT_INVALID")
        template = await self.get_template(actor, event_type)
        rendered = await self.preview(actor, event_type)
        request_id = f"email_test_{uuid.uuid4()}"
        delivery = await self._create_delivery(
            company_id=actor.company_id,
            event_id=None,
            event_type=event_type,
            recipient_email=email,
            subject=rendered.subject,
            request_id=request_id,
        )
        return await self.deliver_now(
            delivery,
            recipient_email=email,
            html_body=rendered.html_body,
            subject=rendered.subject or template.subject,
        )

    async def queue_for_event(self, event: DomainEvent) -> EmailDelivery | None:
        """
        Enfileira email para um evento da outbox, se aplicável.

        Args:
            event: Evento canônico já persistido.

        Returns:
            Entrega criada ou None quando não há envio.
        """
        if not self.config.enabled:
            return None
        template = await self.repository.get_template(event.company_id, event.event_type)
        if template is None or not template.is_enabled:
            return None
        customer = event.payload.get("customer") or {}
        recipient = str(customer.get("email") or "").strip().lower()
        if not recipient or "@" not in recipient:
            logger.info(
                "email.skip_no_recipient",
                extra={"request_id": event.request_id, "event_type": event.event_type.value},
            )
            return None
        existing = await self.repository.get_delivery_by_event(
            event.company_id,
            event.id,
            event.event_type,
        )
        if existing is not None:
            return existing
        variables = self.build_variables(event.event_type, event.payload)
        rendered = self.render(template.subject, template.html_body, variables)
        delivery = await self._create_delivery(
            company_id=event.company_id,
            event_id=event.id,
            event_type=event.event_type,
            recipient_email=recipient,
            subject=rendered.subject,
            request_id=event.request_id,
        )
        try:
            from backend.workers.email_tasks import deliver_email

            await asyncio.to_thread(
                deliver_email.delay,
                event.company_id,
                delivery.id,
                recipient,
                rendered.subject,
                rendered.html_body,
            )
        except Exception as exc:
            logger.error(
                "email.queue_failed",
                extra={"request_id": event.request_id, "error_code": type(exc).__name__},
                exc_info=True,
            )
            # Fallback local: envia no processo se o broker falhar em development.
            if os.getenv("APP_ENV", "development").lower() != "production":
                return await self.deliver_now(
                    delivery,
                    recipient_email=recipient,
                    html_body=rendered.html_body,
                    subject=rendered.subject,
                )
            raise EmailDeliveryError("QUEUE_FAILED") from exc
        return delivery

    async def deliver_now(
        self,
        delivery: EmailDelivery,
        *,
        recipient_email: str,
        html_body: str,
        subject: str,
    ) -> EmailDelivery:
        """
        Envia pelo provedor configurado (SMTP ou Resend) e atualiza a entrega.

        Args:
            delivery: Registro persistido.
            recipient_email: Destinatário (não persistido em texto).
            html_body: HTML já renderizado.
            subject: Assunto renderizado.

        Returns:
            Entrega atualizada.
        """
        started = time.perf_counter()
        now = datetime.now(timezone.utc)
        delivery.attempt_count += 1
        delivery.updated_at = now
        try:
            message_id = await self._send_message(
                to_email=recipient_email,
                subject=subject,
                html_body=html_body,
            )
            delivery.status = EmailDeliveryStatus.DELIVERED
            delivery.provider_message_id = message_id
            delivery.latency_ms = int((time.perf_counter() - started) * 1000)
            delivery.last_error_code = None
            delivery.next_attempt_at = None
            logger.info(
                "email.delivered",
                extra={
                    "request_id": delivery.request_id,
                    "event_type": delivery.event_type.value,
                    "latency_ms": delivery.latency_ms,
                },
            )
        except EmailDeliveryError as exc:
            delivery.latency_ms = int((time.perf_counter() - started) * 1000)
            delivery.last_error_code = str(exc)[:80]
            if delivery.attempt_count >= self.MAX_ATTEMPTS:
                delivery.status = EmailDeliveryStatus.FAILED
                delivery.next_attempt_at = None
            else:
                delivery.status = EmailDeliveryStatus.RETRYING
                delivery.next_attempt_at = now + timedelta(seconds=2**delivery.attempt_count)
            logger.error(
                "email.delivery_failed",
                extra={
                    "request_id": delivery.request_id,
                    "error_code": delivery.last_error_code,
                    "attempt": delivery.attempt_count,
                },
                exc_info=True,
            )
        return await self.repository.save_delivery(delivery)

    async def list_deliveries(
        self,
        actor: AdminActor,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> list[EmailDelivery]:
        """Lista entregas sanitizadas do tenant."""
        self._require(actor, AdminPermission.EMAILS_VIEW)
        limit = max(1, min(limit, 100))
        offset = max(0, offset)
        return await self.repository.list_deliveries(
            actor.company_id,
            limit=limit,
            offset=offset,
        )

    @staticmethod
    def _build_first_access_block(first_access_url: Any) -> str:
        """
        Monta o trecho HTML do link de 1º acesso, ou vazio se não houver link.

        Clientes novos (1ª compra) recebem `first_access_url` do provisionamento
        de conta; clientes já cadastrados (renovação) não recebem esse link, e o
        e-mail não deve exibir um `<a href="">` vazio nesse caso.

        Args:
            first_access_url: URL de definição de senha, ou None/"" se ausente.

        Returns:
            HTML do bloco "Defina sua senha em: ..." ou string vazia.
        """
        url = str(first_access_url or "").strip()
        if not url:
            return ""
        return (
            f'Defina sua senha em: <a href="{url}" style="color:#7ef0f3;">{url}</a><br><br>'
        )

    def build_variables(
        self,
        event_type: DomainEventType,
        payload: dict[str, Any],
        overrides: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        """Extrai variáveis seguras do envelope do evento."""
        customer = payload.get("customer") or {}
        plan = payload.get("plan") or {}
        data = payload.get("data") or {}
        variables = {
            "customer_name": str(customer.get("name") or "Cliente"),
            "customer_email": str(customer.get("email") or ""),
            "plan_name": str(plan.get("name") or ""),
            "amount": str(data.get("amount") or ""),
            "currency": str(data.get("currency") or ""),
            "first_access_url": str(data.get("first_access_url") or ""),
            "first_access_block": self._build_first_access_block(data.get("first_access_url")),
            "recovery_url": str(data.get("recovery_url") or ""),
            "expires_at": str(data.get("expires_at") or ""),
            "expires_in_seconds": str(data.get("expires_in_seconds") or ""),
            "login_url": f"{self.config.frontend_url}/login",
            "company_name": "ElCapo AutoBot",
            "event_type": event_type.value,
            "checkout_url": str(data.get("checkout_url") or ""),
            "support_url": str(data.get("support_url") or ""),
        }
        # Aliases amigáveis usados no admin (mesmos valores).
        variables["name"] = variables["customer_name"]
        variables["email"] = variables["customer_email"]
        variables["reset_url"] = variables["recovery_url"] or variables["first_access_url"]
        if overrides:
            for key, value in overrides.items():
                if value is None:
                    continue
                if key in variables or key in TEMPLATE_VARIABLE_DESCRIPTIONS:
                    variables[key] = str(value)
        return variables

    @staticmethod
    def render(subject: str, html_body: str, variables: dict[str, str]) -> RenderedEmail:
        """
        Substitui {{variaveis}} conhecidas; remove placeholders desconhecidos.

        Args:
            subject: Assunto bruto.
            html_body: HTML bruto.
            variables: Mapa de substituição.

        Returns:
            Conteúdo renderizado.
        """

        def replace(match: re.Match[str]) -> str:
            key = match.group(1)
            return variables.get(key, "")

        return RenderedEmail(
            subject=VARIABLE_PATTERN.sub(replace, subject),
            html_body=VARIABLE_PATTERN.sub(replace, html_body),
        )

    async def _seed_default(
        self,
        company_id: str,
        event_type: DomainEventType,
    ) -> EmailTemplate:
        """Persiste template padrão desativado."""
        now = datetime.now(timezone.utc)
        template = EmailTemplate(
            id=new_template_id(),
            company_id=company_id,
            event_type=event_type,
            subject=DEFAULT_SUBJECTS[event_type],
            html_body=DEFAULT_BODIES[event_type],
            is_enabled=False,
            created_at=now,
            updated_at=now,
        )
        return await self.repository.upsert_template(template)

    async def _create_delivery(
        self,
        *,
        company_id: str,
        event_id: str | None,
        event_type: DomainEventType,
        recipient_email: str,
        subject: str,
        request_id: str,
    ) -> EmailDelivery:
        """Cria registro pending com hash do destinatário."""
        now = datetime.now(timezone.utc)
        delivery = EmailDelivery(
            id=str(uuid.uuid4()),
            company_id=company_id,
            event_id=event_id,
            event_type=event_type,
            recipient_email_hash=hashlib.sha256(recipient_email.encode()).hexdigest(),
            subject=subject[:200],
            status=EmailDeliveryStatus.PENDING,
            attempt_count=0,
            provider_message_id=None,
            latency_ms=None,
            next_attempt_at=None,
            last_error_code=None,
            request_id=request_id,
            created_at=now,
            updated_at=now,
        )
        return await self.repository.save_delivery(delivery)

    async def _send_message(
        self,
        *,
        to_email: str,
        subject: str,
        html_body: str,
    ) -> str:
        """Encaminha para SMTP ou Resend conforme ``EMAIL_PROVIDER``."""
        if self.config.provider == "smtp":
            return await self._send_smtp(
                to_email=to_email,
                subject=subject,
                html_body=html_body,
            )
        return await self._send_resend(
            to_email=to_email,
            subject=subject,
            html_body=html_body,
        )

    async def _send_smtp(
        self,
        *,
        to_email: str,
        subject: str,
        html_body: str,
    ) -> str:
        """
        Envia via SMTP (Hostinger) fora do event loop.

        Args:
            to_email: Destinatário.
            subject: Assunto renderizado.
            html_body: HTML renderizado.

        Returns:
            Identificador sintético da mensagem.

        Raises:
            EmailDeliveryError: Credenciais ausentes ou falha SMTP.
        """
        if not (
            self.config.from_address
            and self.config.smtp_host
            and self.config.smtp_username
            and self.config.smtp_password
        ):
            raise EmailDeliveryError("PROVIDER_NOT_CONFIGURED")

        def _send_blocking() -> str:
            from email.message import EmailMessage
            import smtplib

            message = EmailMessage()
            message["Subject"] = subject
            message["From"] = self.config.from_address
            message["To"] = to_email
            message.set_content("Abra este e-mail em um cliente compatível com HTML.")
            message.add_alternative(html_body, subtype="html")
            if self.config.smtp_use_ssl:
                with smtplib.SMTP_SSL(
                    self.config.smtp_host,
                    self.config.smtp_port,
                    timeout=30,
                ) as client:
                    client.login(self.config.smtp_username, self.config.smtp_password)
                    response = client.send_message(message)
            else:
                with smtplib.SMTP(
                    self.config.smtp_host,
                    self.config.smtp_port,
                    timeout=30,
                ) as client:
                    client.ehlo()
                    client.starttls()
                    client.ehlo()
                    client.login(self.config.smtp_username, self.config.smtp_password)
                    response = client.send_message(message)
            if response:
                raise EmailDeliveryError("PROVIDER_REJECTED")
            return f"smtp:{uuid.uuid4()}"

        try:
            return await asyncio.to_thread(_send_blocking)
        except EmailDeliveryError:
            raise
        except Exception as exc:
            import smtplib as smtp_lib

            if isinstance(exc, smtp_lib.SMTPAuthenticationError):
                logger.error("email.smtp_auth_failed", exc_info=True)
                raise EmailDeliveryError("PROVIDER_AUTH_FAILED") from exc
            logger.error("email.smtp_failed", exc_info=True)
            raise EmailDeliveryError("PROVIDER_UNAVAILABLE") from exc

    async def _send_resend(
        self,
        *,
        to_email: str,
        subject: str,
        html_body: str,
    ) -> str:
        """Chama a API Resend de forma assíncrona."""
        if not self.config.api_key or not self.config.from_address:
            raise EmailDeliveryError("PROVIDER_NOT_CONFIGURED")
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {self.config.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": self.config.from_address,
                    "to": [to_email],
                    "subject": subject,
                    "html": html_body,
                },
            )
        if response.status_code >= 500:
            raise EmailDeliveryError("PROVIDER_UNAVAILABLE")
        if response.status_code >= 400:
            raise EmailDeliveryError("PROVIDER_REJECTED")
        data = response.json()
        return str(data.get("id") or "")

    @staticmethod
    def _require(actor: AdminActor, permission: AdminPermission) -> None:
        """Exige permissão atômica."""
        if permission not in actor.permissions:
            raise EmailAuthorizationError("FORBIDDEN")
