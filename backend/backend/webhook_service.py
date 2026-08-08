"""Regras de negócio e entrega segura dos webhooks de saída."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import secrets
import socket
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import httpx

from backend.admin_models import AdminActor, AdminPermission
from backend.services.encryption_service import EncryptionService
from backend.webhook_models import (
    CreatedWebhookEndpoint,
    DeliveryStatus,
    DomainEvent,
    DomainEventType,
    EventCatalogItem,
    WebhookDelivery,
    WebhookEndpoint,
    WebhookEndpointCreate,
)
from backend.webhook_repository import WebhookRepository


logger = logging.getLogger("backend-webhooks")


class WebhookError(Exception):
    """Erro base seguro do domínio."""


class WebhookAuthorizationError(WebhookError):
    """Ator sem permissão ou recurso fora do tenant."""


class WebhookValidationError(WebhookError):
    """Entrada inválida para o domínio."""


class UnsafeWebhookUrlError(WebhookValidationError):
    """URL insegura ou com risco de SSRF."""


class WebhookNotFoundError(WebhookError):
    """Recurso inexistente no tenant autenticado."""


class WebhookDeliveryError(WebhookError):
    """Falha transitória ou definitiva de entrega."""


CATALOG = (
    EventCatalogItem(
        DomainEventType.PURCHASE_COMPLETED,
        "Compra aprovada e acesso liberado.",
        {"first_access_url": "https://app.example.com/primeiro-acesso?token=uso-unico"},
    ),
    EventCatalogItem(
        DomainEventType.SUBSCRIPTION_CANCELED,
        "Assinatura cancelada e acesso removido.",
        {"reason": "customer_request"},
    ),
    EventCatalogItem(
        DomainEventType.SUBSCRIPTION_RENEWED,
        "Assinatura renovada com sucesso.",
        {"amount": 147.9, "currency": "BRL"},
    ),
    EventCatalogItem(
        DomainEventType.PAYMENT_REFUNDED,
        "Pagamento reembolsado.",
        {"amount": 147.9, "currency": "BRL"},
    ),
    EventCatalogItem(
        DomainEventType.PAYMENT_CHARGEBACK,
        "Contestação ou chargeback confirmado.",
        {"amount": 147.9, "currency": "BRL"},
    ),
    EventCatalogItem(
        DomainEventType.SUBSCRIPTION_PAYMENT_FAILED,
        "Compra ou renovação recusada.",
        {"reason": "payment_refused"},
    ),
    EventCatalogItem(
        DomainEventType.TRIAL_STARTED,
        "Teste grátis iniciado.",
        {"expires_at": "2026-08-01T12:00:00+00:00"},
    ),
    EventCatalogItem(
        DomainEventType.TRIAL_ENDED,
        "Teste grátis encerrado.",
        {"ended_at": "2026-08-01T12:00:00+00:00"},
    ),
    EventCatalogItem(
        DomainEventType.PASSWORD_RECOVERY_REQUESTED,
        "Recuperação de acesso solicitada para uma conta existente.",
        {
            "recovery_url": "https://app.example.com/redefinir-senha?token=uso-unico",
            "expires_in_seconds": 3600,
        },
    ),
)


class WebhookService:
    """Gerencia destinos, outbox e entregas assinadas."""

    MAX_ATTEMPTS = 6
    RESPONSE_LIMIT_BYTES = 4096

    def __init__(
        self,
        repository: WebhookRepository,
        encryption: EncryptionService,
        emails: Any | None = None,
    ) -> None:
        """
        Inicializa o domínio.

        Args:
            repository: Persistência multi-tenant assíncrona.
            encryption: Cofre AES-256-GCM para segredos de assinatura.
            emails: Serviço opcional de emails nativos (mesmo evento).
        """
        self.repository = repository
        self.encryption = encryption
        self.emails = emails

    async def list_endpoints(self, actor: AdminActor) -> list[WebhookEndpoint]:
        """
        Lista destinos do tenant do ator.

        Args:
            actor: Administrador autenticado.

        Returns:
            Destinos sem segredo em texto.

        Raises:
            WebhookAuthorizationError: Sem permissão de leitura.
        """
        self._require(actor, AdminPermission.WEBHOOKS_VIEW)
        return await self.repository.list_endpoints(actor.company_id)

    async def create_endpoint(
        self,
        actor: AdminActor,
        payload: WebhookEndpointCreate,
    ) -> CreatedWebhookEndpoint:
        """
        Cria destino e revela o segredo uma única vez.

        Args:
            actor: Administrador autenticado.
            payload: Nome, URL e eventos inscritos.

        Returns:
            Destino criado e segredo inicial.

        Raises:
            WebhookAuthorizationError: Sem permissão de gestão.
            WebhookValidationError: Campos inválidos.
            UnsafeWebhookUrlError: URL sem HTTPS ou endereço privado.
        """
        self._require(actor, AdminPermission.WEBHOOKS_MANAGE)
        name = payload.name.strip()
        if not name or len(name) > 120:
            raise WebhookValidationError("WEBHOOK_NAME_INVALID")
        if not payload.subscribed_events:
            raise WebhookValidationError("WEBHOOK_EVENTS_REQUIRED")
        normalized_url = self._validate_url_syntax(payload.url)
        signing_secret = f"whsec_{secrets.token_urlsafe(32)}"
        endpoint = await self.repository.create_endpoint(
            actor.company_id,
            WebhookEndpointCreate(
                name=name,
                url=normalized_url,
                subscribed_events=payload.subscribed_events,
            ),
            encrypted_secret=self.encryption.encrypt(signing_secret),
        )
        return CreatedWebhookEndpoint(endpoint=endpoint, signing_secret=signing_secret)

    async def update_endpoint(
        self,
        actor: AdminActor,
        endpoint_id: str,
        *,
        name: str | None = None,
        url: str | None = None,
        subscribed_events: frozenset[DomainEventType] | None = None,
        is_active: bool | None = None,
    ) -> WebhookEndpoint:
        """
        Atualiza um destino do mesmo tenant.

        Args:
            actor: Administrador autenticado.
            endpoint_id: Identificador do destino.
            name: Novo nome opcional.
            url: Nova URL opcional.
            subscribed_events: Nova assinatura opcional.
            is_active: Novo estado opcional.

        Returns:
            Destino atualizado.

        Raises:
            WebhookAuthorizationError: Sem permissão ou recurso fora do tenant.
            WebhookValidationError: Campos inválidos.
        """
        self._require(actor, AdminPermission.WEBHOOKS_MANAGE)
        if name is not None:
            name = name.strip()
            if not name or len(name) > 120:
                raise WebhookValidationError("WEBHOOK_NAME_INVALID")
        if url is not None:
            url = self._validate_url_syntax(url)
        if subscribed_events is not None and not subscribed_events:
            raise WebhookValidationError("WEBHOOK_EVENTS_REQUIRED")
        updated = await self.repository.update_endpoint(
            actor.company_id,
            endpoint_id,
            name=name,
            url=url,
            subscribed_events=subscribed_events,
            is_active=is_active,
        )
        if updated is None:
            raise WebhookAuthorizationError("WEBHOOK_NOT_FOUND")
        return updated

    async def deactivate_endpoint(self, actor: AdminActor, endpoint_id: str) -> None:
        """
        Desativa um destino preservando auditoria.

        Args:
            actor: Administrador autenticado.
            endpoint_id: Identificador do destino.

        Raises:
            WebhookAuthorizationError: Sem permissão ou fora do tenant.
        """
        await self.update_endpoint(actor, endpoint_id, is_active=False)

    async def enqueue_event(
        self,
        *,
        company_id: str,
        event_type: DomainEventType,
        subject_user_id: str | None,
        request_id: str,
        customer: dict[str, Any],
        plan: dict[str, Any] | None,
        data: dict[str, Any],
        event_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> DomainEvent:
        """
        Registra evento canônico e prepara entregas idempotentes.

        Args:
            company_id: Tenant obtido da identidade ou do plano confiável.
            event_type: Tipo canônico.
            subject_user_id: Usuário relacionado.
            request_id: Correlation ID propagado.
            customer: Nome, email e telefone necessários ao destino.
            plan: Identificação segura do plano.
            data: Campos específicos do evento.
            event_id: Id estável opcional da origem.
            occurred_at: Horário do fato.

        Returns:
            Evento persistido na outbox.

        Raises:
            WebhookValidationError: Identidade confiável ausente.
        """
        if not company_id or not request_id:
            raise WebhookValidationError("EVENT_IDENTITY_REQUIRED")
        now = datetime.now(timezone.utc)
        resolved_id = event_id or str(uuid.uuid4())
        occurred = occurred_at or now
        payload = {
            "event_id": resolved_id,
            "event_type": event_type.value,
            "api_version": "2026-07-18",
            "occurred_at": occurred.isoformat(),
            "request_id": request_id,
            "customer": {
                key: value
                for key, value in customer.items()
                if key in {"id", "name", "email", "phone"} and value is not None
            },
            "plan": plan,
            "data": data,
        }
        event = DomainEvent(
            id=resolved_id,
            company_id=company_id,
            event_type=event_type,
            subject_user_id=subject_user_id,
            request_id=request_id,
            payload=payload,
            occurred_at=occurred,
            created_at=now,
        )
        inserted = await self.repository.append_event(event)
        if not inserted:
            existing = await self.repository.get_event(company_id, resolved_id)
            if existing is not None:
                return existing
        return event

    async def queue_event_deliveries(self, event: DomainEvent) -> None:
        """
        Publica entregas no Celery sem bloquear o event loop.

        Args:
            event: Evento já persistido na outbox.

        Raises:
            WebhookDeliveryError: Quando o broker rejeitar a publicação.
        """
        from backend.workers.webhook_tasks import deliver_webhook

        # Emails nativos não dependem de destinos HTTP externos.
        if self.emails is not None:
            try:
                await self.emails.queue_for_event(event)
            except Exception:
                logger.error(
                    "email.queue_for_event.failed request_id=%s event_id=%s",
                    event.request_id,
                    event.id,
                    exc_info=True,
                )

        try:
            endpoints = await self.repository.list_subscribed_endpoints(
                event.company_id,
                event.event_type,
            )
        except Exception:
            logger.error(
                "webhook.list_endpoints.failed company_id=%s event_id=%s",
                event.company_id,
                event.id,
                exc_info=True,
            )
            endpoints = []

        if not endpoints:
            return
        try:
            await asyncio.gather(
                *(
                    asyncio.to_thread(
                        deliver_webhook.delay,
                        event.company_id,
                        endpoint.id,
                        event.id,
                    )
                    for endpoint in endpoints
                )
            )
        except Exception as exc:
            logger.error(
                "webhook.queue.failed company_id=%s event_id=%s request_id=%s",
                event.company_id,
                event.id,
                event.request_id,
                exc_info=True,
            )
            raise WebhookDeliveryError("WEBHOOK_QUEUE_UNAVAILABLE") from exc

    async def list_deliveries(
        self,
        actor: AdminActor,
        *,
        endpoint_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[WebhookDelivery]:
        """
        Lista histórico sanitizado do tenant.

        Args:
            actor: Administrador autenticado.
            endpoint_id: Filtro opcional de destino.
            limit: Tamanho da página.
            offset: Início da página.

        Returns:
            Entregas sem payload integral.
        """
        self._require(actor, AdminPermission.WEBHOOKS_VIEW)
        return await self.repository.list_deliveries(
            actor.company_id,
            endpoint_id=endpoint_id,
            limit=max(1, min(limit, 100)),
            offset=max(0, offset),
        )

    async def replay_delivery(self, actor: AdminActor, delivery_id: str) -> WebhookDelivery:
        """
        Reenvia entrega do tenant com auditoria.

        Args:
            actor: Administrador autenticado.
            delivery_id: Identificador da entrega.

        Returns:
            Estado após nova tentativa.

        Raises:
            WebhookAuthorizationError: Sem permissão ou fora do tenant.
        """
        self._require(actor, AdminPermission.WEBHOOKS_REPLAY)
        delivery = await self.repository.get_delivery(actor.company_id, delivery_id)
        if delivery is None:
            raise WebhookAuthorizationError("WEBHOOK_DELIVERY_NOT_FOUND")
        return await self.deliver_event(
            delivery.endpoint_id,
            delivery.event_id,
            company_id=actor.company_id,
        )

    async def deliver_event(
        self,
        endpoint_id: str,
        event_id: str,
        *,
        company_id: str | None = None,
    ) -> WebhookDelivery:
        """
        Entrega um evento e persiste somente metadados sanitizados.

        Args:
            endpoint_id: Destino previamente validado.
            event_id: Evento da outbox.

        Returns:
            Estado auditável da tentativa.

        Raises:
            WebhookNotFoundError: Quando endpoint/evento não formam o mesmo tenant.
        """
        endpoint, event = await self._resolve_delivery_resources(
            endpoint_id,
            event_id,
            company_id=company_id,
        )
        await self._validate_resolved_host(endpoint.url)
        existing = await self.repository.get_delivery_for_event(
            endpoint.company_id,
            endpoint.id,
            event.id,
        )
        delivery = existing or self._new_delivery(endpoint, event)
        if delivery.status == DeliveryStatus.DELIVERED:
            return delivery

        raw_body = json.dumps(
            event.payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        timestamp = str(int(time.time()))
        secret = self.encryption.decrypt(endpoint.encrypted_secret)
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "ElCapo-Webhooks/1.0",
            "Idempotency-Key": event.id,
            "X-Webhook-ID": event.id,
            "X-Webhook-Timestamp": timestamp,
            "X-Webhook-Signature": self.sign_payload(
                secret,
                timestamp=timestamp,
                raw_body=raw_body,
            ),
            "X-Request-ID": event.request_id,
        }
        started = time.monotonic()
        delivery.attempt_count += 1
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(10.0, connect=5.0),
                follow_redirects=False,
            ) as client:
                response = await client.post(endpoint.url, content=raw_body, headers=headers)
            latency_ms = int((time.monotonic() - started) * 1000)
            delivery.response_status = response.status_code
            delivery.latency_ms = latency_ms
            if 200 <= response.status_code < 300:
                delivery.status = DeliveryStatus.DELIVERED
                delivery.next_attempt_at = None
                delivery.last_error_code = None
            else:
                self._schedule_retry(delivery, f"HTTP_{response.status_code}")
        except (httpx.HTTPError, OSError, asyncio.TimeoutError) as exc:
            self._schedule_retry(delivery, exc.__class__.__name__.upper())
        delivery.updated_at = datetime.now(timezone.utc)
        return await self.repository.save_delivery(delivery)

    @staticmethod
    def sign_payload(secret: str, *, timestamp: str, raw_body: bytes) -> str:
        """
        Assina timestamp e corpo bruto com HMAC-SHA256.

        Args:
            secret: Segredo conhecido pelo destinatário.
            timestamp: Epoch enviado no header.
            raw_body: JSON exato transmitido.

        Returns:
            Assinatura no formato ``v1=<hex>``.
        """
        signed = timestamp.encode() + b"." + raw_body
        digest = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
        return f"v1={digest}"

    @staticmethod
    def catalog() -> tuple[EventCatalogItem, ...]:
        """Retorna contratos públicos sem segredos reais."""
        return CATALOG

    async def _resolve_delivery_resources(
        self,
        endpoint_id: str,
        event_id: str,
        *,
        company_id: str | None,
    ) -> tuple[WebhookEndpoint, DomainEvent]:
        """Resolve endpoint e evento sem permitir combinação entre tenants."""
        endpoint: WebhookEndpoint | None = None
        if company_id is not None:
            endpoint = await self.repository.get_endpoint(company_id, endpoint_id)
        elif hasattr(self.repository, "endpoints"):
            candidates = [
                stored
                for (_, stored_id), stored in self.repository.endpoints.items()  # type: ignore[attr-defined]
                if stored_id == endpoint_id
            ]
            endpoint = candidates[0] if len(candidates) == 1 else None
        if endpoint is None:
            raise WebhookNotFoundError("WEBHOOK_ENDPOINT_NOT_FOUND")
        event = await self.repository.get_event(endpoint.company_id, event_id)
        if event is None or not endpoint.is_active:
            raise WebhookNotFoundError("WEBHOOK_EVENT_NOT_FOUND")
        return endpoint, event

    def _new_delivery(
        self,
        endpoint: WebhookEndpoint,
        event: DomainEvent,
    ) -> WebhookDelivery:
        """Cria entrega determinística para endpoint e evento."""
        now = datetime.now(timezone.utc)
        return WebhookDelivery(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{endpoint.id}:{event.id}")),
            company_id=event.company_id,
            endpoint_id=endpoint.id,
            event_id=event.id,
            request_id=event.request_id,
            status=DeliveryStatus.PENDING,
            attempt_count=0,
            response_status=None,
            latency_ms=None,
            next_attempt_at=now,
            last_error_code=None,
            created_at=now,
            updated_at=now,
        )

    def _schedule_retry(self, delivery: WebhookDelivery, error_code: str) -> None:
        """Aplica backoff exponencial limitado sem registrar resposta externa."""
        delivery.last_error_code = error_code[:80]
        if delivery.attempt_count >= self.MAX_ATTEMPTS:
            delivery.status = DeliveryStatus.FAILED
            delivery.next_attempt_at = None
            return
        delay = min(3600, (2 ** delivery.attempt_count) * 30)
        delivery.status = DeliveryStatus.RETRYING
        delivery.next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=delay)

    @staticmethod
    def _validate_url_syntax(url: str) -> str:
        """Valida HTTPS e bloqueia destinos locais/literais privados."""
        normalized = url.strip()
        parsed = urlparse(normalized)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
        ):
            raise UnsafeWebhookUrlError("WEBHOOK_URL_UNSAFE")
        hostname = parsed.hostname.casefold().rstrip(".")
        if hostname == "localhost" or hostname.endswith(".localhost"):
            raise UnsafeWebhookUrlError("WEBHOOK_URL_UNSAFE")
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise UnsafeWebhookUrlError("WEBHOOK_URL_UNSAFE")
        return normalized

    async def _validate_resolved_host(self, url: str) -> None:
        """Resolve DNS antes da entrega e recusa qualquer endereço não global."""
        hostname = urlparse(self._validate_url_syntax(url)).hostname
        if hostname is None:
            raise UnsafeWebhookUrlError("WEBHOOK_URL_UNSAFE")
        loop = asyncio.get_running_loop()
        try:
            results = await loop.getaddrinfo(
                hostname,
                443,
                type=socket.SOCK_STREAM,
            )
        except socket.gaierror as exc:
            raise UnsafeWebhookUrlError("WEBHOOK_DNS_UNAVAILABLE") from exc
        addresses = {item[4][0] for item in results}
        if not addresses or any(not ipaddress.ip_address(value).is_global for value in addresses):
            raise UnsafeWebhookUrlError("WEBHOOK_URL_UNSAFE")

    @staticmethod
    def _require(actor: AdminActor, permission: AdminPermission) -> None:
        """Aplica RBAC deny-by-default."""
        if permission not in actor.permissions:
            raise WebhookAuthorizationError("WEBHOOK_FORBIDDEN")
