"""Modelos do domínio de webhooks de saída."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class DomainEventType(StrEnum):
    """Eventos públicos e versionados disponíveis para assinatura."""

    PURCHASE_COMPLETED = "purchase.completed"
    PURCHASE_EXISTING_ACCOUNT = "purchase.existing_account"
    SUBSCRIPTION_CANCELED = "subscription.canceled"
    SUBSCRIPTION_RENEWED = "subscription.renewed"
    PAYMENT_REFUNDED = "payment.refunded"
    PAYMENT_CHARGEBACK = "payment.chargeback"
    SUBSCRIPTION_PAYMENT_FAILED = "subscription.payment_failed"
    TRIAL_STARTED = "trial.started"
    TRIAL_ENDED = "trial.ended"
    PASSWORD_RECOVERY_REQUESTED = "user.password_recovery_requested"


class DeliveryStatus(StrEnum):
    """Estados persistidos de uma tentativa de entrega."""

    PENDING = "pending"
    DELIVERED = "delivered"
    RETRYING = "retrying"
    FAILED = "failed"


@dataclass(frozen=True)
class WebhookEndpointCreate:
    """Dados aceitos ao cadastrar um destino."""

    name: str
    url: str
    subscribed_events: frozenset[DomainEventType]


@dataclass
class WebhookEndpoint:
    """Destino HTTPS isolado por empresa."""

    id: str
    company_id: str
    name: str
    url: str
    subscribed_events: frozenset[DomainEventType]
    encrypted_secret: str
    secret_version: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class CreatedWebhookEndpoint:
    """Retorno único de criação contendo o segredo em texto."""

    endpoint: WebhookEndpoint
    signing_secret: str


@dataclass
class DomainEvent:
    """Evento canônico persistido na outbox."""

    id: str
    company_id: str
    event_type: DomainEventType
    subject_user_id: str | None
    request_id: str
    payload: dict[str, Any]
    occurred_at: datetime
    created_at: datetime
    processed_at: datetime | None = None


@dataclass
class WebhookDelivery:
    """Resultado auditável e sanitizado de uma entrega."""

    id: str
    company_id: str
    endpoint_id: str
    event_id: str
    request_id: str
    status: DeliveryStatus
    attempt_count: int
    response_status: int | None
    latency_ms: int | None
    next_attempt_at: datetime | None
    last_error_code: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class EventCatalogItem:
    """Descrição segura de um contrato público."""

    event_type: DomainEventType
    description: str
    example_data: dict[str, Any] = field(default_factory=dict)
