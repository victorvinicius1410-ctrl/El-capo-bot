"""Regras de negócio do catálogo, ledger e integração financeira."""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse

from backend.account_link_service import AccountLinkError, SupabaseAccountLinkService
from backend.admin_models import (
    AccountType,
    AdminActor,
    AdminPermission,
    PaymentStatus,
)
from backend.brasilia_time import brasilia_today, history_cutoff, to_brasilia_date
from backend.cakto_service import CaktoService
from backend.finance_models import (
    BillingPlan,
    BillingPlanCreate,
    BillingPlanUpdate,
    FinanceEvent,
    FinanceEventStatus,
    ProcessEventResult,
)
from backend.finance_repository import FinanceRepository
from backend.webhook_models import DomainEventType
from backend.webhook_service import WebhookService


logger = logging.getLogger("backend-finance")


ESSENTIAL_EVENTS = frozenset(
    {
        "purchase_approved",
        "purchase_refused",
        "refund",
        "chargeback",
        "subscription_created",
        "subscription_canceled",
        "subscription_renewed",
        "subscription_renewal_refused",
    }
)
GENERATED_EVENTS = frozenset(
    {
        "pix_gerado",
        "boleto_gerado",
        "picpay_gerado",
        "openfinance_nubank_gerado",
    }
)
INFORMATIVE_EVENTS = frozenset({"initiate_checkout", "checkout_abandonment"})
ENABLED_EVENTS = tuple(sorted(ESSENTIAL_EVENTS | GENERATED_EVENTS | INFORMATIVE_EVENTS))
APPROVED_EVENTS = frozenset({"purchase_approved", "subscription_renewed"})
# Sentinela de "o payload não trouxe data": nunca pode virar last_event_at.
UNKNOWN_EVENT_AT = datetime(1970, 1, 1, tzinfo=timezone.utc)
OUTGOING_EVENT_MAP = {
    "purchase_approved": DomainEventType.PURCHASE_COMPLETED,
    "purchase_refused": DomainEventType.SUBSCRIPTION_PAYMENT_FAILED,
    "refund": DomainEventType.PAYMENT_REFUNDED,
    "chargeback": DomainEventType.PAYMENT_CHARGEBACK,
    "subscription_canceled": DomainEventType.SUBSCRIPTION_CANCELED,
    "subscription_renewed": DomainEventType.SUBSCRIPTION_RENEWED,
    "subscription_renewal_refused": DomainEventType.SUBSCRIPTION_PAYMENT_FAILED,
}


def resolve_outgoing_event_type(
    event_name: str,
    *,
    first_access_url: str | None,
) -> DomainEventType | None:
    """
    Escolhe o evento de e-mail/webhook a partir do fato Cakto.

    Compra aprovada com link de 1º acesso (conta nova) usa
    ``purchase.completed``. Compra de quem já tinha perfil usa
    ``purchase.existing_account``. Renovação continua em
    ``subscription.renewed``.

    Args:
        event_name: Nome normalizado do evento Cakto.
        first_access_url: Link de definição de senha, se a conta foi criada agora.

    Returns:
        Tipo canônico ou None quando o evento não gera e-mail/webhook.
    """
    if event_name == "purchase_approved":
        if str(first_access_url or "").strip():
            return DomainEventType.PURCHASE_COMPLETED
        return DomainEventType.PURCHASE_EXISTING_ACCOUNT
    return OUTGOING_EVENT_MAP.get(event_name)


class FinanceError(Exception):
    """Erro base seguro do domínio financeiro."""


class FinanceValidationError(FinanceError):
    """Entrada ou estado financeiro inválido."""


class FinanceAuthorizationError(FinanceError):
    """Permissão financeira obrigatória ausente."""


class FinanceNotFoundError(FinanceError):
    """Recurso financeiro não encontrado no tenant."""


class FinanceService:
    """Orquestra regras financeiras sem acoplar a interface HTTP."""

    def __init__(
        self,
        repository: FinanceRepository,
        *,
        webhooks: WebhookService | None = None,
        account_links: SupabaseAccountLinkService | None = None,
    ) -> None:
        """
        Inicializa o domínio financeiro e integrações opcionais.

        Args:
            repository: Persistência financeira multi-tenant.
            webhooks: Outbox de eventos de saída.
            account_links: Provisionamento de primeiro acesso.
        """
        self.repository = repository
        self.webhooks = webhooks
        self.account_links = account_links

    async def list_client_plans(self, company_id: str) -> list[BillingPlan]:
        """Lista somente planos ativos do tenant autenticado."""
        return await self.repository.list_plans(company_id, include_inactive=False)

    async def list_admin_plans(self, actor: AdminActor) -> list[BillingPlan]:
        """Lista catálogo completo mediante permissão de visualização."""
        self._require_permission(actor, AdminPermission.FINANCE_VIEW)
        return await self.repository.list_plans(actor.company_id, include_inactive=True)

    async def create_plan(
        self,
        actor: AdminActor,
        payload: BillingPlanCreate,
        cakto: CaktoService | None = None,
    ) -> BillingPlan:
        """
        Valida, provisiona oferta na Cakto quando necessário e cria o plano.

        Args:
            actor: Administrador autenticado do tenant.
            payload: Dados comerciais da oferta local.
            cakto: Cliente server-only; quando configurado, cria a oferta remota.

        Returns:
            Plano persistido com IDs e checkout Cakto preenchidos.

        Raises:
            FinanceAuthorizationError: Sem permissão ``finance.plans.manage``.
            FinanceValidationError: Dados inválidos ou provisionamento incompleto.
            CaktoConfigurationError: Integração Cakto incompleta.
            httpx.HTTPError: Falha HTTP ao chamar a Cakto.
        """
        self._require_permission(actor, AdminPermission.FINANCE_PLANS_MANAGE)
        self._validate_plan(payload)
        provisioned = await self._provision_cakto_offer(payload, cakto)
        self._validate_active_plan_configuration(provisioned)
        await self._ensure_plan_uniqueness(actor.company_id, provisioned)
        return await self.repository.create_plan(actor.company_id, provisioned)

    async def update_plan(
        self,
        actor: AdminActor,
        plan_id: str,
        payload: BillingPlanUpdate,
    ) -> BillingPlan:
        """Valida e atualiza um plano do mesmo tenant."""
        self._require_permission(actor, AdminPermission.FINANCE_PLANS_MANAGE)
        existing = await self.repository.get_plan(actor.company_id, plan_id)
        if existing is None:
            raise FinanceNotFoundError("PLAN_NOT_FOUND")
        self._validate_plan(payload, partial=True)
        self._validate_active_plan_configuration(
            BillingPlanCreate(
                slug=payload.slug if payload.slug is not None else existing.slug,
                name=payload.name if payload.name is not None else existing.name,
                description=(
                    payload.description
                    if payload.description is not None
                    else existing.description
                ),
                price=payload.price if payload.price is not None else existing.price,
                currency=(
                    payload.currency if payload.currency is not None else existing.currency
                ),
                billing_interval_months=(
                    payload.billing_interval_months
                    if payload.billing_interval_months is not None
                    else existing.billing_interval_months
                ),
                features=(
                    payload.features if payload.features is not None else existing.features
                ),
                is_featured=(
                    payload.is_featured
                    if payload.is_featured is not None
                    else existing.is_featured
                ),
                is_active=(
                    payload.is_active
                    if payload.is_active is not None
                    else existing.is_active
                ),
                display_order=(
                    payload.display_order
                    if payload.display_order is not None
                    else existing.display_order
                ),
                cakto_product_id=(
                    payload.cakto_product_id
                    if payload.cakto_product_id is not None
                    else existing.cakto_product_id
                ),
                cakto_offer_id=(
                    payload.cakto_offer_id
                    if payload.cakto_offer_id is not None
                    else existing.cakto_offer_id
                ),
                checkout_url=(
                    payload.checkout_url
                    if payload.checkout_url is not None
                    else existing.checkout_url
                ),
            )
        )
        await self._ensure_plan_uniqueness(actor.company_id, payload, exclude_id=plan_id)
        updated = await self.repository.update_plan(actor.company_id, plan_id, payload)
        if updated is None:
            raise FinanceNotFoundError("PLAN_NOT_FOUND")
        return updated

    async def deactivate_plan(self, actor: AdminActor, plan_id: str) -> None:
        """Desativa plano preservando eventos e transações."""
        self._require_permission(actor, AdminPermission.FINANCE_PLANS_MANAGE)
        if not await self.repository.deactivate_plan(actor.company_id, plan_id):
            raise FinanceNotFoundError("PLAN_NOT_FOUND")

    async def get_checkout_url(self, company_id: str, plan_id: str) -> str:
        """Retorna apenas checkout seguro de plano ativo do tenant."""
        plan = await self.repository.get_plan(company_id, plan_id)
        if plan is None or not plan.is_active or not plan.checkout_url:
            raise FinanceValidationError("CHECKOUT_NOT_AVAILABLE")
        self._validate_checkout_url(plan.checkout_url)
        return plan.checkout_url

    async def get_history(
        self,
        company_id: str,
        user_id: str,
        *,
        limit: int,
        offset: int,
    ) -> list[FinanceEvent]:
        """Lista histórico paginado do cliente autenticado."""
        return await self.repository.list_events(
            company_id,
            user_id=user_id,
            limit=limit,
            offset=offset,
        )

    async def process_cakto_event(
        self,
        payload: dict[str, Any],
        *,
        expected_company_id: str | None = None,
        request_id: str | None = None,
    ) -> ProcessEventResult:
        """
        Normaliza, persiste e aplica um evento Cakto de forma idempotente.

        O tenant vem exclusivamente da oferta, validada contra o produto.
        """
        event_name = _normalize_event_name(payload)
        if event_name not in set(ENABLED_EVENTS):
            raise FinanceValidationError("UNSUPPORTED_CAKTO_EVENT")
        data = payload.get("data")
        if not isinstance(data, dict):
            data = payload
        product_id = _external_id(
            data.get("product_id")
            or data.get("product")
            or payload.get("product_id")
            or payload.get("product")
        )
        offer_id = _external_id(
            data.get("offer_id")
            or data.get("offer")
            or payload.get("offer_id")
            or payload.get("offer")
        )
        if not offer_id:
            raise FinanceValidationError("CAKTO_OFFER_ID_REQUIRED")
        plan = await self.repository.resolve_plan(product_id, offer_id)
        if plan is None:
            raise FinanceValidationError("BILLING_PLAN_MAPPING_NOT_FOUND")
        if (
            (product_id and plan.cakto_product_id != product_id)
            or (offer_id and plan.cakto_offer_id != offer_id)
        ):
            raise FinanceValidationError("BILLING_PLAN_MAPPING_MISMATCH")
        if expected_company_id is not None and plan.company_id != expected_company_id:
            raise FinanceAuthorizationError("EVENT_OUTSIDE_TENANT")

        if event_name in INFORMATIVE_EVENTS:
            # Abandono/início de checkout não têm transação, logo não têm
            # referência estável para o ledger: a Cakto reenvia o mesmo abandono
            # com timestamp novo e cada retentativa viraria uma linha nova.
            # Estes eventos também não estão em OUTGOING_EVENT_MAP, então a única
            # coisa que perdemos ao não gravar é ruído no painel.
            informative_at = _event_datetime(data, payload)
            if informative_at != UNKNOWN_EVENT_AT:
                # touch_config_state sobrescreve sem comparar: gravar a sentinela
                # rebobinaria o "último evento" do painel para 1970 e faria a
                # integração parecer morta.
                await self.repository.touch_config_state(
                    plan.company_id,
                    last_event_at=informative_at,
                )
            return ProcessEventResult(
                processed=False,
                duplicate=False,
                company_id=plan.company_id,
                event_name=event_name,
            )

        occurred_at = _event_datetime(data, payload)
        provider_reference = _provider_reference(data)
        subscription_reference = _subscription_reference(data)
        currency, amount = _validated_event_monetary(data)
        customer_email = _customer_email(data)
        customer = (
            await self.repository.find_customer_by_email(plan.company_id, customer_email)
            if customer_email
            else None
        )
        first_access_url: str | None = None
        customer_data = data.get("customer") if isinstance(data.get("customer"), dict) else {}
        if (
            customer is None
            and event_name == "purchase_approved"
            and customer_email
            and self.account_links is not None
        ):
            try:
                account_link = await self.account_links.create_purchase_account(
                    company_id=plan.company_id,
                    email=customer_email,
                    name=str(customer_data.get("name") or customer_email.split("@", 1)[0]),
                )
                customer = await self.repository.ensure_purchase_customer(
                    plan.company_id,
                    user_id=account_link.user_id,
                    email=customer_email,
                    name=str(
                        customer_data.get("name") or customer_email.split("@", 1)[0]
                    ),
                )
            except AccountLinkError as exc:
                logger.error(
                    "finance.purchase.account_provisioning_failed company_id=%s offer_id=%s",
                    plan.company_id,
                    offer_id,
                    exc_info=True,
                )
                raise FinanceValidationError("PURCHASE_ACCOUNT_PROVISIONING_FAILED") from exc
            except Exception as exc:
                logger.error(
                    "finance.purchase.profile_provisioning_failed company_id=%s offer_id=%s",
                    plan.company_id,
                    offer_id,
                    exc_info=True,
                )
                raise FinanceValidationError("PURCHASE_ACCOUNT_PROVISIONING_FAILED") from exc
            first_access_url = account_link.action_link
            await self._ensure_customer_access_from_event(
                event_name=event_name,
                company_id=plan.company_id,
                customer=customer,
                plan=plan,
            )
        if customer is None:
            customer = await self.repository.find_customer_by_billing_reference(
                plan.company_id,
                provider_reference=provider_reference,
                subscription_reference=subscription_reference,
            )
        user_id = str(customer["user_id"]) if customer else None
        event_key = _provider_event_key(
            event_name,
            provider_reference,
            subscription_reference,
            occurred_at,
        )
        event = FinanceEvent(
            id=str(uuid.uuid4()),
            company_id=plan.company_id,
            provider="cakto",
            provider_event_key=event_key,
            event_name=event_name,
            status=_event_status(event_name),
            plan_id=plan.id,
            user_id=user_id,
            provider_reference=provider_reference,
            subscription_reference=subscription_reference,
            amount=amount,
            currency=currency,
            occurred_at=occurred_at,
            received_at=datetime.now(timezone.utc),
            metadata={
                "cakto_product_id": product_id,
                "cakto_offer_id": offer_id,
                "billing_interval_months": plan.billing_interval_months,
                "source": "webhook_or_reconciliation",
                "request_id": request_id,
            },
        )
        inserted = await self.repository.append_event(event)
        if not inserted:
            await self._ensure_customer_access_from_event(
                event_name=event_name,
                company_id=plan.company_id,
                customer=customer,
                plan=plan,
            )
            await self._emit_outgoing_event(
                event,
                plan,
                customer_data=customer_data,
                customer_email=customer_email,
                first_access_url=first_access_url,
            )
            return ProcessEventResult(
                processed=False,
                duplicate=True,
                company_id=plan.company_id,
                event_name=event_name,
            )

        await self._ensure_customer_access_from_event(
            event_name=event_name,
            company_id=plan.company_id,
            customer=customer,
            plan=plan,
        )
        if event_name in APPROVED_EVENTS:
            await self.repository.append_revenue_event(event)
        await self.repository.touch_config_state(
            plan.company_id,
            last_event_at=event.occurred_at,
        )
        await self._emit_outgoing_event(
            event,
            plan,
            customer_data=customer_data,
            customer_email=customer_email,
            first_access_url=first_access_url,
        )
        return ProcessEventResult(
            processed=True,
            duplicate=False,
            company_id=plan.company_id,
            event_name=event_name,
        )

    async def _ensure_customer_access_from_event(
        self,
        *,
        event_name: str,
        company_id: str,
        customer: dict[str, Any] | None,
        plan: BillingPlan,
    ) -> None:
        """
        Aplica regra de acesso do evento quando permitido.

        Chamado logo após provisionar conta nova (antes do ledger) para evitar
        perfil inativo quando ``append_event`` ou passos posteriores falham, e
        também em reprocessamentos idempotentes que chegam com o cliente ainda
        pendente.
        """
        if customer is None:
            return
        access_rule = _access_rule(event_name)
        if access_rule is None:
            return
        payment_status, grant_access = access_rule
        user_id = str(customer.get("user_id") or "")
        if not user_id:
            return
        if not _customer_can_be_changed(customer, grants_access=grant_access):
            return
        if _is_regressive_pending_transition(
            customer,
            payment_status=payment_status,
            grant_access=grant_access,
        ):
            return
        await self.repository.apply_customer_access(
            company_id,
            user_id,
            payment_status=payment_status,
            grant_access=grant_access,
            plan=plan,
        )
        customer["payment_status"] = payment_status
        customer["grant_access"] = grant_access

    async def _emit_outgoing_event(
        self,
        event: FinanceEvent,
        plan: BillingPlan,
        *,
        customer_data: dict[str, Any],
        customer_email: str | None,
        first_access_url: str | None,
    ) -> None:
        """Registra e agenda o evento público sem expor segredos em logs."""
        event_type = resolve_outgoing_event_type(
            event.event_name,
            first_access_url=first_access_url,
        )
        if self.webhooks is None or event_type is None:
            return
        data: dict[str, Any] = {
            "amount": float(event.amount),
            "currency": event.currency,
            "provider_reference": event.provider_reference,
            "subscription_reference": event.subscription_reference,
        }
        if first_access_url:
            data["first_access_url"] = first_access_url
        outgoing_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"cakto:{event.company_id}:{event.provider_event_key}",
            )
        )
        outgoing = await self.webhooks.enqueue_event(
            company_id=event.company_id,
            event_type=event_type,
            subject_user_id=event.user_id,
            request_id=str(event.metadata.get("request_id") or event.provider_event_key),
            customer={
                "id": event.user_id,
                "name": customer_data.get("name"),
                "email": customer_email,
                "phone": customer_data.get("phone") or customer_data.get("phone_number"),
            },
            plan={"id": plan.id, "name": plan.name},
            data=data,
            event_id=outgoing_id,
            occurred_at=event.occurred_at,
        )
        try:
            await self.webhooks.queue_event_deliveries(outgoing)
        except Exception:
            # Compra/liberação já persistiram; falha de e-mail/webhook externo
            # não pode reverter o 202 do webhook Cakto.
            logger.error(
                "finance.outgoing_webhook.queue_failed company_id=%s event_id=%s",
                event.company_id,
                outgoing.id,
                exc_info=True,
            )

    async def get_metrics(self, actor: AdminActor, *, days: int) -> dict[str, Any]:
        """Calcula métricas BRL do ledger financeiro do tenant e período."""
        self._require_permission(actor, AdminPermission.FINANCE_VIEW)
        if not 1 <= days <= 3660:
            raise FinanceValidationError("INVALID_METRICS_PERIOD")
        end_at = datetime.now(timezone.utc)
        start_at = history_cutoff(days, end_at)
        events = await self.repository.list_events(
            actor.company_id,
            start_at=start_at,
            end_at=end_at,
            limit=10_000,
        )
        return _calculate_metrics(events, start_at, end_at)

    async def get_settings(
        self,
        actor: AdminActor,
        cakto: CaktoService,
        *,
        webhook_url: str,
    ) -> dict[str, Any]:
        """Retorna estado operacional sem revelar segredo ou OAuth."""
        self._require_permission(actor, AdminPermission.FINANCE_SETTINGS_MANAGE)
        state = await self.repository.get_config_state(actor.company_id)
        return {
            "provider": "cakto",
            "webhook_url": webhook_url,
            "webhook_configured": cakto.config.webhook_configured,
            "api_configured": cakto.config.api_configured,
            "offers_provisioning_configured": cakto.config.offers_provisioning_configured,
            "default_product_id": cakto.config.default_product_id,
            "last_event_at": _iso(state.last_event_at),
            "last_reconciled_at": _iso(state.last_reconciled_at),
            "enabled_events": list(ENABLED_EVENTS),
        }

    async def reconcile(
        self,
        actor: AdminActor,
        cakto: CaktoService,
        *,
        page_size: int = 50,
    ) -> dict[str, int]:
        """Reconcilia uma página limitada da API Cakto de modo idempotente."""
        self._require_permission(actor, AdminPermission.FINANCE_RECONCILE)
        events = await cakto.fetch_event_history(page=1, per_page=min(page_size, 100))
        processed = 0
        duplicates = 0
        skipped = 0
        for payload in events:
            try:
                result = await self.process_cakto_event(
                    payload,
                    expected_company_id=actor.company_id,
                )
            except FinanceError as exc:
                logger.warning(
                    "finance.reconciliation.event_skipped event=%s error=%s",
                    _normalize_event_name(payload),
                    str(exc),
                    exc_info=True,
                )
                skipped += 1
                continue
            processed += int(result.processed)
            duplicates += int(result.duplicate)
        await self.repository.touch_config_state(
            actor.company_id,
            last_reconciled_at=datetime.now(timezone.utc),
        )
        return {
            "fetched": len(events),
            "processed": processed,
            "duplicates": duplicates,
            "skipped": skipped,
        }

    async def _ensure_plan_uniqueness(
        self,
        company_id: str,
        payload: BillingPlanCreate | BillingPlanUpdate,
        *,
        exclude_id: str | None = None,
    ) -> None:
        """Valida slug, produto compartilhado da empresa e oferta global."""
        existing = await self.repository.list_plans(company_id, include_inactive=True)
        slug = payload.slug.strip().lower() if payload.slug else None
        if slug and any(plan.slug == slug and plan.id != exclude_id for plan in existing):
            raise FinanceValidationError("PLAN_SLUG_ALREADY_EXISTS")
        product_id = payload.cakto_product_id
        if product_id:
            configured_products = {
                plan.cakto_product_id
                for plan in existing
                if plan.id != exclude_id and plan.cakto_product_id
            }
            if configured_products and configured_products != {product_id}:
                raise FinanceValidationError("CAKTO_PRODUCT_MUST_MATCH_COMPANY_PRODUCT")
            if not await self.repository.product_is_available_for_company(
                company_id,
                product_id,
            ):
                raise FinanceValidationError("CAKTO_PRODUCT_ID_ALREADY_EXISTS")
        offer_id = payload.cakto_offer_id
        if offer_id:
            resolved = await self.repository.resolve_plan(None, offer_id)
            if resolved is not None and resolved.id != exclude_id:
                raise FinanceValidationError("CAKTO_OFFER_ID_ALREADY_EXISTS")

    async def _provision_cakto_offer(
        self,
        payload: BillingPlanCreate,
        cakto: CaktoService | None,
    ) -> BillingPlanCreate:
        """
        Garante produto/oferta/checkout usando o produto padrão do ambiente.

        Quando a API Cakto está pronta e a oferta ainda não foi informada,
        cria a oferta remota no produto padrão e preenche checkout oficial.
        Se a oferta já veio preenchida (migração manual), apenas completa o
        produto padrão ausente.

        Args:
            payload: Dados da oferta local.
            cakto: Cliente Cakto opcional.

        Returns:
            Payload enriquecido pronto para persistência.

        Raises:
            FinanceValidationError: Preço abaixo do mínimo ou produto ausente.
            CaktoConfigurationError: Integração incompleta para provisionar.
        """
        product_id = (payload.cakto_product_id or "").strip() or None
        offer_id = (payload.cakto_offer_id or "").strip() or None
        checkout_url = (payload.checkout_url or "").strip() or None
        default_product = (
            cakto.config.default_product_id.strip()
            if cakto and cakto.config.default_product_id
            else None
        )
        if not product_id and default_product:
            product_id = default_product

        should_create_remote = (
            cakto is not None
            and cakto.config.enabled
            and cakto.config.offers_provisioning_configured
            and not offer_id
        )
        if should_create_remote:
            assert cakto is not None
            if not product_id:
                raise FinanceValidationError("CAKTO_DEFAULT_PRODUCT_REQUIRED")
            try:
                created = await cakto.create_offer(
                    name=payload.name,
                    price=float(payload.price),
                    product_id=product_id,
                    billing_interval_months=payload.billing_interval_months,
                    status="active" if payload.is_active else "disabled",
                )
            except ValueError as exc:
                raise FinanceValidationError(str(exc)) from exc
            product_id = created.product_id
            offer_id = created.offer_id
            checkout_url = created.checkout_url
            logger.info(
                "finance.offer.cakto_provisioned product_id=%s offer_id=%s",
                product_id,
                offer_id,
            )
        elif not checkout_url and offer_id:
            checkout_url = f"https://pay.cakto.com.br/{offer_id}"

        return BillingPlanCreate(
            slug=payload.slug,
            name=payload.name,
            description=payload.description,
            price=payload.price,
            currency=payload.currency,
            billing_interval_months=payload.billing_interval_months,
            features=payload.features,
            is_featured=payload.is_featured,
            is_active=payload.is_active,
            display_order=payload.display_order,
            cakto_product_id=product_id,
            cakto_offer_id=offer_id,
            checkout_url=checkout_url,
        )

    @staticmethod
    def _validate_plan(
        payload: BillingPlanCreate | BillingPlanUpdate,
        *,
        partial: bool = False,
    ) -> None:
        """Valida limites e formato do plano."""
        if not partial or payload.slug is not None:
            slug = (payload.slug or "").strip().lower()
            if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
                raise FinanceValidationError("INVALID_PLAN_SLUG")
        if not partial or payload.name is not None:
            if not (payload.name or "").strip():
                raise FinanceValidationError("PLAN_NAME_REQUIRED")
        if payload.price is not None and payload.price < 0:
            raise FinanceValidationError("INVALID_PLAN_PRICE")
        if payload.currency is not None and payload.currency.upper() != "BRL":
            raise FinanceValidationError("ONLY_BRL_SUPPORTED")
        if (
            payload.billing_interval_months is not None
            and payload.billing_interval_months < 1
        ):
            raise FinanceValidationError("INVALID_BILLING_INTERVAL")
        if payload.checkout_url is not None:
            FinanceService._validate_checkout_url(payload.checkout_url)

    @staticmethod
    def _validate_checkout_url(value: str) -> None:
        """Aceita somente checkout HTTPS no host oficial exato."""
        parsed = urlparse(value)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "pay.cakto.com.br"
            or not parsed.path
        ):
            raise FinanceValidationError("INVALID_CHECKOUT_URL")

    @staticmethod
    def _validate_active_plan_configuration(payload: BillingPlanCreate) -> None:
        """Exige mapeamento e checkout completos antes de ativar contratação."""
        if payload.is_active and not (
            payload.cakto_product_id
            and payload.cakto_offer_id
            and payload.checkout_url
        ):
            raise FinanceValidationError("ACTIVE_PLAN_REQUIRES_CAKTO_CONFIGURATION")

    @staticmethod
    def _require_permission(actor: AdminActor, permission: AdminPermission) -> None:
        """Aplica RBAC deny-by-default."""
        if permission not in actor.permissions:
            raise FinanceAuthorizationError(f"Permissão obrigatória: {permission.value}")


def _normalize_event_name(payload: dict[str, Any]) -> str:
    """Obtém nome canônico do evento."""
    raw = payload.get("event") or payload.get("event_name") or payload.get("type")
    return str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")


def _external_id(value: Any) -> str | None:
    """Extrai identificador externo de valor simples ou objeto."""
    if isinstance(value, dict):
        value = value.get("id") or value.get("refId") or value.get("ref_id")
    normalized = str(value or "").strip()
    return normalized or None


def _customer_email(data: dict[str, Any]) -> str | None:
    """Extrai email somente para resolução interna, sem persistência no ledger."""
    customer = data.get("customer") or data.get("buyer")
    value = customer.get("email") if isinstance(customer, dict) else data.get("email")
    normalized = str(value or "").strip().casefold()
    return normalized or None


def _provider_reference(data: dict[str, Any]) -> str:
    """Obtém referência estável do pagamento."""
    value = (
        data.get("id")
        or data.get("refId")
        or data.get("ref_id")
        or _subscription_reference(data)
    )
    normalized = str(value or "").strip()
    if not normalized:
        raise FinanceValidationError("CAKTO_EVENT_REFERENCE_REQUIRED")
    return normalized


def _subscription_reference(data: dict[str, Any]) -> str | None:
    """Obtém referência estável da assinatura quando presente."""
    subscription = data.get("subscription")
    value = (
        subscription.get("id")
        if isinstance(subscription, dict)
        else data.get("subscription_id") or subscription
    )
    normalized = str(value or "").strip()
    return normalized or None


def _event_datetime(data: dict[str, Any], payload: dict[str, Any]) -> datetime:
    """Converte o melhor timestamp estável disponível."""
    value = (
        data.get("occurred_at")
        or data.get("refundedAt")
        or data.get("chargedbackAt")
        or data.get("canceledAt")
        or data.get("paidAt")
        or data.get("updatedAt")
        or data.get("createdAt")
        or data.get("created_at")
        or data.get("updated_at")
        or payload.get("dispatchedAt")
        or payload.get("created_at")
        or payload.get("timestamp")
    )
    if not value:
        return UNKNOWN_EVENT_AT
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise FinanceValidationError("INVALID_CAKTO_EVENT_TIMESTAMP") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _provider_event_key(
    event_name: str,
    provider_reference: str,
    subscription_reference: str | None,
    occurred_at: datetime,
) -> str:
    """Deriva chave idempotente sem PII."""
    stable = "|".join(
        (
            event_name,
            provider_reference,
            subscription_reference or "",
            occurred_at.astimezone(timezone.utc).isoformat(),
        )
    )
    return hashlib.sha256(stable.encode()).hexdigest()


def _validated_event_monetary(data: dict[str, Any]) -> tuple[str, Decimal]:
    """
    Valida moeda e valor antes de efeitos colaterais irreversíveis (Auth/perfil).

    Returns:
        Tupla ``(currency, amount)`` normalizada.

    Raises:
        FinanceValidationError: Moeda não suportada ou valor inválido.
    """
    currency = str(data.get("currency") or "BRL").upper()
    if currency != "BRL":
        raise FinanceValidationError("ONLY_BRL_SUPPORTED")
    return currency, _event_amount(data)


def _event_amount(data: dict[str, Any]) -> Decimal:
    """Normaliza valor monetário para Decimal."""
    value = data.get("amount") or data.get("price") or 0
    if isinstance(value, dict):
        value = value.get("value") or value.get("amount") or 0
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError) as exc:
        raise FinanceValidationError("INVALID_CAKTO_AMOUNT") from exc
    if amount < 0:
        raise FinanceValidationError("INVALID_CAKTO_AMOUNT")
    return amount


def _event_status(event_name: str) -> FinanceEventStatus:
    """Mapeia evento Cakto para status do ledger."""
    if event_name in APPROVED_EVENTS:
        return FinanceEventStatus.APPROVED
    if event_name == "refund":
        return FinanceEventStatus.REFUNDED
    if event_name == "chargeback":
        return FinanceEventStatus.CHARGEBACK
    if event_name == "subscription_canceled":
        return FinanceEventStatus.CANCELED
    if event_name in {"purchase_refused", "subscription_renewal_refused"}:
        return FinanceEventStatus.REFUSED
    if event_name in GENERATED_EVENTS or event_name == "subscription_created":
        return FinanceEventStatus.PENDING
    return FinanceEventStatus.INFORMATIVE


def _access_rule(event_name: str) -> tuple[PaymentStatus, bool] | None:
    """
    Retorna transição conservadora de pagamento e acesso.

    ``subscription_created`` NÃO altera acesso: na Cakto ele costuma chegar
    milissegundos depois de ``purchase_approved``. Tratar como ``pending``
    rebaixava clientes já pagos para \"Ainda não pagou\" e revogava
    ``grant_access`` — enquanto a notificação de venda (ledger/outbox) já
    tinha sido emitida pelo evento aprovado.
    """
    if event_name in APPROVED_EVENTS:
        return PaymentStatus.PAID, True
    if event_name == "refund":
        return PaymentStatus.REFUNDED, False
    if event_name == "chargeback":
        return PaymentStatus.CHARGEBACK, False
    if event_name == "subscription_canceled":
        return PaymentStatus.CANCELED, False
    if event_name in {"purchase_refused", "subscription_renewal_refused"}:
        return PaymentStatus.REFUSED, False
    if event_name in GENERATED_EVENTS:
        return PaymentStatus.PENDING, False
    return None


def _payment_status_value(customer: dict[str, Any]) -> str:
    """Normaliza ``payment_status`` do perfil para string comparável."""
    status = customer.get("payment_status")
    if isinstance(status, PaymentStatus):
        return status.value
    return str(status or "")


def _is_regressive_pending_transition(
    customer: dict[str, Any],
    *,
    payment_status: PaymentStatus,
    grant_access: bool,
) -> bool:
    """
    Bloqueia rebaixamento de cliente já pago por eventos soft (ex.: pix_gerado).

    Args:
        customer: Perfil atual.
        payment_status: Status proposto pela regra do evento.
        grant_access: Acesso proposto pela regra do evento.

    Returns:
        True se a transição deve ser ignorada (regressiva).
    """
    if grant_access or payment_status != PaymentStatus.PENDING:
        return False
    return _payment_status_value(customer) == PaymentStatus.PAID.value


def _customer_can_be_changed(customer: dict[str, Any], *, grants_access: bool) -> bool:
    """
    Protege contas de marketing e cobrança não obrigatória de ruído financeiro.

    Contas de marketing (simulação interna) nunca são alteradas por webhook.
    Contas com cobrança não obrigatória (ex.: trial aprovado pelo admin,
    `payment_status=not_required`) só ficam protegidas de eventos que NÃO
    concedem acesso (ex.: chargeback/estorno de ruído). Um evento que
    **concede** acesso (compra aprovada, assinatura criada/renovada) sempre
    deve converter o lead em cliente pagante — senão o trial nunca é
    promovido e o expirador agendado (`end_trial`) revoga o acesso de quem
    já pagou.

    Args:
        customer: Registro atual do cliente (payment_status, account_type).
        grants_access: True quando o evento concede acesso (`_access_rule`
            retornou `grant_access=True`).

    Returns:
        True se o webhook pode atualizar este cliente.
    """
    if str(customer.get("account_type") or AccountType.CLIENT.value) == AccountType.MARKETING.value:
        return False
    if grants_access:
        return True
    status_value = _payment_status_value(customer)
    return status_value not in {
        PaymentStatus.NOT_REQUIRED.value,
        PaymentStatus.NOT_APPLICABLE.value,
    }


def _calculate_metrics(
    events: list[FinanceEvent],
    start_at: datetime,
    end_at: datetime,
) -> dict[str, Any]:
    """Agrega métricas financeiras exclusivamente do ledger Cakto."""
    approved = [event for event in events if event.status == FinanceEventStatus.APPROVED]
    refunds = [event for event in events if event.status == FinanceEventStatus.REFUNDED]
    chargebacks = [event for event in events if event.status == FinanceEventStatus.CHARGEBACK]
    refused = [event for event in events if event.status == FinanceEventStatus.REFUSED]
    gross = sum((event.amount for event in approved), Decimal("0"))
    refund_total = sum((event.amount for event in refunds), Decimal("0"))
    chargeback_total = sum((event.amount for event in chargebacks), Decimal("0"))
    net = gross - refund_total - chargeback_total
    subscriptions: dict[str, FinanceEvent] = {}
    for event in sorted(events, key=lambda item: item.occurred_at):
        key = event.subscription_reference or event.provider_reference
        if event.event_name.startswith("subscription_"):
            subscriptions[key] = event
    active_subscriptions = sum(
        1
        for event in subscriptions.values()
        if event.event_name in {"subscription_created", "subscription_renewed"}
    )
    canceled_subscriptions = sum(
        1
        for event in subscriptions.values()
        if event.event_name
        in {"subscription_canceled", "subscription_renewal_refused"}
    )
    monthly_values = []
    plan_intervals: dict[str, int] = {}
    for event in approved:
        interval = max(1, int(event.metadata.get("billing_interval_months") or 1))
        plan_intervals[event.plan_id] = interval
        monthly_values.append(event.amount / interval)
    mrr = sum(monthly_values, Decimal("0"))
    approval_denominator = len(approved) + len(refused)
    churn_denominator = active_subscriptions + canceled_subscriptions
    status_breakdown = Counter(event.status.value for event in events)
    event_breakdown = Counter(event.event_name for event in events)
    daily: dict[str, dict[str, Decimal | int]] = defaultdict(
        lambda: {
            "gross_revenue": Decimal("0"),
            "refunds": Decimal("0"),
            "chargebacks": Decimal("0"),
            "approved_payments": 0,
            "refused_payments": 0,
        }
    )
    for event in events:
        day = to_brasilia_date(event.occurred_at).isoformat()
        if event.status == FinanceEventStatus.APPROVED:
            daily[day]["gross_revenue"] += event.amount  # type: ignore[operator]
            daily[day]["approved_payments"] += 1  # type: ignore[operator]
        elif event.status == FinanceEventStatus.REFUNDED:
            daily[day]["refunds"] += event.amount  # type: ignore[operator]
        elif event.status == FinanceEventStatus.CHARGEBACK:
            daily[day]["chargebacks"] += event.amount  # type: ignore[operator]
        elif event.status == FinanceEventStatus.REFUSED:
            daily[day]["refused_payments"] += 1  # type: ignore[operator]
    return {
        "currency": "BRL",
        "period_days": max(1, (brasilia_today(end_at) - to_brasilia_date(start_at)).days + 1),
        "period_start": _iso(start_at),
        "period_end": _iso(end_at),
        "period": {"start_at": _iso(start_at), "end_at": _iso(end_at)},
        "gross_revenue": float(gross),
        "net_revenue": float(net),
        "refunds": float(refund_total),
        "chargebacks": float(chargeback_total),
        "approved_payments": len(approved),
        "refused_payments": len(refused),
        "active_subscriptions": active_subscriptions,
        "canceled_subscriptions": canceled_subscriptions,
        "mrr": float(mrr),
        "arr": float(mrr * 12),
        "average_ticket": float(gross / len(approved)) if approved else 0.0,
        "approval_rate": (
            round(len(approved) / approval_denominator * 100, 2)
            if approval_denominator
            else 0.0
        ),
        "churn_rate": (
            round(canceled_subscriptions / churn_denominator * 100, 2)
            if churn_denominator
            else 0.0
        ),
        "status_breakdown": dict(status_breakdown),
        "event_breakdown": dict(event_breakdown),
        "breakdowns": {
            "status": [
                {"label": label, "count": count}
                for label, count in sorted(status_breakdown.items())
            ],
            "events": [
                {"label": label, "count": count}
                for label, count in sorted(event_breakdown.items())
            ],
        },
        "daily_series": [
            {
                "date": day,
                **{
                    key: float(value) if isinstance(value, Decimal) else value
                    for key, value in values.items()
                },
            }
            for day, values in sorted(daily.items())
        ],
    }


def _iso(value: datetime | None) -> str | None:
    """Serializa timestamp UTC opcional."""
    return value.isoformat() if value is not None else None
