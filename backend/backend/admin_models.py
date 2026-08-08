"""Modelos de domínio para clientes, administradores e controle de acesso."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class AccountType(StrEnum):
    """Tipos de conta gerenciados pelo painel administrativo."""

    TRIAL = "trial"
    CLIENT = "client"
    MARKETING = "marketing"


class PaymentStatus(StrEnum):
    """Estados de pagamento independentes do estado de acesso."""

    NOT_APPLICABLE = "not_applicable"
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    PAID = "paid"
    OVERDUE = "overdue"
    CANCELED = "canceled"
    REFUNDED = "refunded"
    CHARGEBACK = "chargeback"
    REFUSED = "refused"


class ApprovalStatus(StrEnum):
    """Estado de aprovação administrativa do cadastro (lead)."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class MarketingMode(StrEnum):
    """Modos permitidos para uma conta de marketing."""

    REAL = "real"
    SIMULATION = "simulation"


class AdminPermission(StrEnum):
    """Permissões atômicas verificadas no backend."""

    CLIENTS_CREATE = "clients.create"
    CLIENTS_EDIT = "clients.edit"
    CLIENTS_UPDATE = "clients.update"
    CLIENTS_DELETE = "clients.delete"
    CLIENTS_VIEW_HISTORY = "clients.view_history"
    CLIENTS_HISTORY_READ = "clients.history.read"
    CLIENTS_ACCESS_ACCOUNT = "clients.access_account"
    CLIENTS_IMPERSONATE = "clients.impersonate"
    ADMINS_CREATE = "admins.create"
    ADMINS_EDIT = "admins.edit"
    ADMINS_UPDATE = "admins.update"
    ADMINS_DELETE = "admins.delete"
    FINANCE_VIEW = "finance.view"
    FINANCE_PLANS_MANAGE = "finance.plans.manage"
    FINANCE_SETTINGS_MANAGE = "finance.settings.manage"
    FINANCE_RECONCILE = "finance.reconcile"
    WEBHOOKS_VIEW = "webhooks.view"
    WEBHOOKS_MANAGE = "webhooks.manage"
    WEBHOOKS_REPLAY = "webhooks.replay"
    EMAILS_VIEW = "emails.view"
    EMAILS_MANAGE = "emails.manage"


@dataclass(frozen=True)
class AdminActor:
    """Identidade administrativa validada no servidor."""

    user_id: str
    company_id: str
    permissions: frozenset[AdminPermission]
    manageable_role_ids: frozenset[str] | None


@dataclass(frozen=True)
class ClientCreate:
    """Dados aceitos para criar um cliente."""

    name: str
    email: str
    phone: str | None
    trader_id: str
    password: str
    account_type: AccountType
    trial_days: int | None
    payment_status: PaymentStatus
    plan_id: str | None = None
    marketing_mode: MarketingMode | None = None
    marketing_win_rate: int | None = None


@dataclass(frozen=True)
class ClientUpdate:
    """Campos alteráveis de um cliente."""

    name: str | None = None
    email: str | None = None
    phone: str | None = None
    trader_id: str | None = None
    password: str | None = None
    account_type: AccountType | None = None
    trial_days: int | None = None
    payment_status: PaymentStatus | None = None
    plan_id: str | None = None
    marketing_mode: MarketingMode | None = None
    marketing_win_rate: int | None = None


@dataclass
class ClientRecord:
    """Cliente persistido e sempre vinculado a uma empresa."""

    user_id: str
    company_id: str
    name: str
    email: str
    phone: str | None
    trader_id: str
    account_type: AccountType
    payment_status: PaymentStatus
    plan_name: str | None
    grant_access: bool
    created_at: datetime
    updated_at: datetime
    plan_id: str | None = None
    expires_at: datetime | None = None
    deleted_at: datetime | None = None
    marketing_mode: MarketingMode | None = None
    marketing_win_rate: int | None = None
    approval_status: ApprovalStatus = ApprovalStatus.APPROVED


@dataclass(frozen=True)
class AdminCreate:
    """Dados necessários para criar um administrador."""

    name: str
    email: str
    job_title: str
    password: str
    permissions: frozenset[AdminPermission]
    manageable_role_ids: frozenset[str] | None


@dataclass(frozen=True)
class AdminUpdate:
    """Campos alteráveis de um administrador."""

    name: str | None = None
    email: str | None = None
    job_title: str | None = None
    password: str | None = None
    permissions: frozenset[AdminPermission] | None = None
    manageable_role_ids: frozenset[str] | None = None
    manageable_roles_supplied: bool = False


@dataclass
class AdminRecord:
    """Administrador persistido com permissões e escopo."""

    user_id: str
    company_id: str
    name: str
    email: str
    job_title: str
    role_id: str
    permissions: frozenset[AdminPermission] = field(default_factory=frozenset)
    manageable_role_ids: frozenset[str] | None = field(default_factory=frozenset)
    deleted_at: datetime | None = None


@dataclass
class ImpersonationRecord:
    """Sessão temporária e auditável de visualização como cliente."""

    session_id: str
    company_id: str
    actor_user_id: str
    target_user_id: str
    reason: str
    expires_at: datetime
    token_hash: str
    scopes: frozenset[str] = field(
        default_factory=lambda: frozenset({"account.view", "broker_account.edit"})
    )
    revoked_at: datetime | None = None
