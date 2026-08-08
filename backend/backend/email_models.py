"""Modelos do domínio de emails nativos."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from backend.webhook_models import DomainEventType


class EmailDeliveryStatus(StrEnum):
    """Estados persistidos de uma tentativa de envio."""

    PENDING = "pending"
    DELIVERED = "delivered"
    RETRYING = "retrying"
    FAILED = "failed"


@dataclass
class EmailTemplate:
    """Layout HTML configurável por empresa e evento."""

    id: str
    company_id: str
    event_type: DomainEventType
    subject: str
    html_body: str
    is_enabled: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class EmailTemplateUpdate:
    """Campos aceitos na atualização administrativa."""

    subject: str
    html_body: str
    is_enabled: bool


@dataclass
class EmailDelivery:
    """Resultado auditável e sanitizado de um envio."""

    id: str
    company_id: str
    event_id: str | None
    event_type: DomainEventType
    recipient_email_hash: str
    subject: str
    status: EmailDeliveryStatus
    attempt_count: int
    provider_message_id: str | None
    latency_ms: int | None
    next_attempt_at: datetime | None
    last_error_code: str | None
    request_id: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class RenderedEmail:
    """Assunto e HTML após substituição de variáveis."""

    subject: str
    html_body: str


@dataclass(frozen=True)
class EmailSettingsView:
    """Flags públicas do provedor sem segredos."""

    enabled: bool
    from_configured: bool
    provider: str
    smtp_configured: bool = False
    available_variables: tuple[str, ...] = ()
    variable_descriptions: dict[str, str] | None = None
    events: tuple[str, ...] = ()
