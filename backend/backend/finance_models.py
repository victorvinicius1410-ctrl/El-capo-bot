"""Modelos de domínio do catálogo e ledger financeiro."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any


class FinanceEventStatus(StrEnum):
    """Estados normalizados persistidos no ledger financeiro."""

    APPROVED = "approved"
    PENDING = "pending"
    REFUSED = "refused"
    REFUNDED = "refunded"
    CHARGEBACK = "chargeback"
    CANCELED = "canceled"
    INFORMATIVE = "informative"


@dataclass(frozen=True)
class BillingPlanCreate:
    """Campos aceitos na criação administrativa de um plano."""

    slug: str
    name: str
    description: str
    price: Decimal
    currency: str
    billing_interval_months: int
    features: tuple[str, ...]
    is_featured: bool
    is_active: bool
    display_order: int
    cakto_product_id: str | None = None
    cakto_offer_id: str | None = None
    checkout_url: str | None = None


@dataclass(frozen=True)
class BillingPlanUpdate:
    """Campos alteráveis de um plano existente."""

    slug: str | None = None
    name: str | None = None
    description: str | None = None
    price: Decimal | None = None
    currency: str | None = None
    billing_interval_months: int | None = None
    features: tuple[str, ...] | None = None
    is_featured: bool | None = None
    is_active: bool | None = None
    display_order: int | None = None
    cakto_product_id: str | None = None
    cakto_offer_id: str | None = None
    checkout_url: str | None = None


@dataclass
class BillingPlan:
    """Plano persistido e isolado por empresa."""

    id: str
    company_id: str
    slug: str
    name: str
    description: str
    price: Decimal
    currency: str
    billing_interval_months: int
    features: tuple[str, ...]
    is_featured: bool
    is_active: bool
    display_order: int
    cakto_product_id: str | None
    cakto_offer_id: str | None
    checkout_url: str | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None


@dataclass(frozen=True)
class FinanceEvent:
    """Evento Cakto sanitizado antes da persistência."""

    id: str
    company_id: str
    provider: str
    provider_event_key: str
    event_name: str
    status: FinanceEventStatus
    plan_id: str
    user_id: str | None
    provider_reference: str
    subscription_reference: str | None
    amount: Decimal
    currency: str
    occurred_at: datetime
    received_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProcessEventResult:
    """Resultado seguro do processamento de um evento externo."""

    processed: bool
    duplicate: bool
    company_id: str
    event_name: str


@dataclass
class FinanceConfigState:
    """Estado operacional sem segredos da integração de cobrança."""

    company_id: str
    provider: str = "cakto"
    last_event_at: datetime | None = None
    last_reconciled_at: datetime | None = None
