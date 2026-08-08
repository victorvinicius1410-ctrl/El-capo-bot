"""Persistência assíncrona e multi-tenant do domínio financeiro."""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import quote

import httpx

from backend.admin_models import AccountType, PaymentStatus
from backend.finance_models import (
    BillingPlan,
    BillingPlanCreate,
    BillingPlanUpdate,
    FinanceConfigState,
    FinanceEvent,
)


class FinanceRepository(ABC):
    """Contrato assíncrono de persistência financeira."""

    @abstractmethod
    async def list_plans(self, company_id: str, *, include_inactive: bool) -> list[BillingPlan]:
        """Lista planos de uma empresa."""

    @abstractmethod
    async def get_plan(self, company_id: str, plan_id: str) -> BillingPlan | None:
        """Busca plano por empresa e id."""

    @abstractmethod
    async def resolve_plan(
        self,
        cakto_product_id: str | None,
        cakto_offer_id: str | None,
    ) -> BillingPlan | None:
        """Resolve a oferta pela identificação externa globalmente única."""

    @abstractmethod
    async def product_is_available_for_company(
        self,
        company_id: str,
        cakto_product_id: str,
    ) -> bool:
        """Confirma que o produto está livre ou já pertence à mesma empresa."""

    @abstractmethod
    async def create_plan(self, company_id: str, payload: BillingPlanCreate) -> BillingPlan:
        """Cria plano no tenant autenticado."""

    @abstractmethod
    async def update_plan(
        self,
        company_id: str,
        plan_id: str,
        payload: BillingPlanUpdate,
    ) -> BillingPlan | None:
        """Atualiza plano no tenant autenticado."""

    @abstractmethod
    async def deactivate_plan(self, company_id: str, plan_id: str) -> bool:
        """Desativa plano sem apagar histórico."""

    @abstractmethod
    async def find_customer_by_email(
        self,
        company_id: str,
        email: str,
    ) -> dict[str, Any] | None:
        """Resolve cliente pelo email somente dentro da empresa mapeada."""

    @abstractmethod
    async def ensure_purchase_customer(
        self,
        company_id: str,
        *,
        user_id: str,
        email: str,
        name: str,
    ) -> dict[str, Any]:
        """
        Garante perfil de acesso após criar usuário Auth na compra.

        Sem esta linha em ``user_access_profiles``, o webhook cria a conta Auth
        mas não consegue liberar ``grant_access`` (PATCH não encontra o cliente).
        """

    @abstractmethod
    async def find_customer_by_billing_reference(
        self,
        company_id: str,
        *,
        provider_reference: str,
        subscription_reference: str | None,
    ) -> dict[str, Any] | None:
        """Resolve cliente por evento financeiro anterior dentro do mesmo tenant."""

    @abstractmethod
    async def apply_customer_access(
        self,
        company_id: str,
        user_id: str,
        *,
        payment_status: PaymentStatus,
        grant_access: bool,
        plan: BillingPlan,
    ) -> None:
        """Atualiza pagamento e acesso com filtros explícitos de tenant e usuário."""

    @abstractmethod
    async def append_event(self, event: FinanceEvent) -> bool:
        """Insere evento uma vez e informa se a chave era nova."""

    @abstractmethod
    async def append_revenue_event(self, event: FinanceEvent) -> None:
        """Mantém o ledger administrativo legado compatível."""

    @abstractmethod
    async def list_events(
        self,
        company_id: str,
        *,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        user_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[FinanceEvent]:
        """Lista ledger por tenant, período e cliente."""

    @abstractmethod
    async def get_config_state(self, company_id: str) -> FinanceConfigState:
        """Obtém estado não secreto da integração."""

    @abstractmethod
    async def touch_config_state(
        self,
        company_id: str,
        *,
        last_event_at: datetime | None = None,
        last_reconciled_at: datetime | None = None,
    ) -> FinanceConfigState:
        """Atualiza timestamps operacionais no tenant."""


class InMemoryFinanceRepository(FinanceRepository):
    """Repositório financeiro determinístico para testes."""

    def __init__(self) -> None:
        self.plans: dict[tuple[str, str], BillingPlan] = {}
        self.events: list[FinanceEvent] = []
        self.event_keys: set[tuple[str, str, str]] = set()
        self.customers: dict[tuple[str, str], dict[str, Any]] = {}
        self.revenue_events: list[dict[str, Any]] = []
        self.config_states: dict[str, FinanceConfigState] = {}

    def add_customer(
        self,
        company_id: str,
        *,
        user_id: str,
        email: str,
        payment_status: PaymentStatus,
        grant_access: bool,
        account_type: str = "client",
    ) -> None:
        """Adiciona cliente de teste sem persistir credenciais."""
        self.customers[(company_id, user_id)] = {
            "company_id": company_id,
            "user_id": user_id,
            "email": email.casefold(),
            "payment_status": payment_status,
            "grant_access": grant_access,
            "account_type": account_type,
            "plan_id": None,
        }

    async def list_plans(self, company_id: str, *, include_inactive: bool) -> list[BillingPlan]:
        """Lista planos do tenant ordenados para exibição."""
        rows = [
            plan
            for (tenant, _), plan in self.plans.items()
            if tenant == company_id
            and plan.deleted_at is None
            and (include_inactive or plan.is_active)
        ]
        return sorted(rows, key=lambda plan: (plan.display_order, plan.name.casefold()))

    async def get_plan(self, company_id: str, plan_id: str) -> BillingPlan | None:
        """Busca plano pela chave composta."""
        plan = self.plans.get((company_id, plan_id))
        return plan if plan is not None and plan.deleted_at is None else None

    async def resolve_plan(
        self,
        cakto_product_id: str | None,
        cakto_offer_id: str | None,
    ) -> BillingPlan | None:
        """Resolve oferta pelo ID único e valida seu produto compartilhado."""
        if not cakto_offer_id:
            return None
        matches = [
            plan
            for plan in self.plans.values()
            if plan.deleted_at is None
            and plan.cakto_offer_id == cakto_offer_id
            and (not cakto_product_id or plan.cakto_product_id == cakto_product_id)
        ]
        return matches[0] if len(matches) == 1 else None

    async def product_is_available_for_company(
        self,
        company_id: str,
        cakto_product_id: str,
    ) -> bool:
        """Impede que o mesmo produto externo seja associado a outro tenant."""
        owners = {
            plan.company_id
            for plan in self.plans.values()
            if plan.deleted_at is None and plan.cakto_product_id == cakto_product_id
        }
        return not owners or owners == {company_id}

    async def create_plan(self, company_id: str, payload: BillingPlanCreate) -> BillingPlan:
        """Cria plano em memória."""
        now = datetime.now(timezone.utc)
        plan = BillingPlan(
            id=str(uuid.uuid4()),
            company_id=company_id,
            created_at=now,
            updated_at=now,
            **payload.__dict__,
        )
        self.plans[(company_id, plan.id)] = plan
        return plan

    async def update_plan(
        self,
        company_id: str,
        plan_id: str,
        payload: BillingPlanUpdate,
    ) -> BillingPlan | None:
        """Atualiza somente campos fornecidos no mesmo tenant."""
        plan = await self.get_plan(company_id, plan_id)
        if plan is None:
            return None
        for field_name, value in payload.__dict__.items():
            if value is not None:
                setattr(plan, field_name, value)
        plan.updated_at = datetime.now(timezone.utc)
        return plan

    async def deactivate_plan(self, company_id: str, plan_id: str) -> bool:
        """Desativa plano preservando referências históricas."""
        plan = await self.get_plan(company_id, plan_id)
        if plan is None:
            return False
        plan.is_active = False
        plan.deleted_at = datetime.now(timezone.utc)
        plan.updated_at = plan.deleted_at
        return True

    async def find_customer_by_email(
        self,
        company_id: str,
        email: str,
    ) -> dict[str, Any] | None:
        """Busca cliente por email normalizado e tenant."""
        return next(
            (
                customer
                for (tenant, _), customer in self.customers.items()
                if tenant == company_id and customer["email"] == email.casefold()
            ),
            None,
        )

    async def ensure_purchase_customer(
        self,
        company_id: str,
        *,
        user_id: str,
        email: str,
        name: str,
    ) -> dict[str, Any]:
        """Cria perfil mínimo em memória para o fluxo de compra."""
        existing = self.customers.get((company_id, user_id))
        if existing is not None:
            return existing
        by_email = await self.find_customer_by_email(company_id, email)
        if by_email is not None:
            return by_email
        customer = {
            "company_id": company_id,
            "user_id": user_id,
            "email": email.casefold(),
            "name": name,
            "payment_status": PaymentStatus.PENDING,
            "grant_access": False,
            "account_type": AccountType.CLIENT.value,
            "plan_id": None,
        }
        self.customers[(company_id, user_id)] = customer
        return customer

    async def find_customer_by_billing_reference(
        self,
        company_id: str,
        *,
        provider_reference: str,
        subscription_reference: str | None,
    ) -> dict[str, Any] | None:
        """Reutiliza o vínculo sem PII gravado em eventos anteriores."""
        for event in reversed(self.events):
            reference_matches = event.provider_reference == provider_reference
            subscription_matches = bool(
                subscription_reference
                and event.subscription_reference == subscription_reference
            )
            if (
                event.company_id == company_id
                and event.user_id
                and (reference_matches or subscription_matches)
            ):
                return self.customers.get((company_id, event.user_id))
        return None

    async def apply_customer_access(
        self,
        company_id: str,
        user_id: str,
        *,
        payment_status: PaymentStatus,
        grant_access: bool,
        plan: BillingPlan,
    ) -> None:
        """Aplica acesso somente à chave composta existente."""
        customer = self.customers.get((company_id, user_id))
        if customer is None:
            return
        customer.update(
            payment_status=payment_status,
            grant_access=grant_access,
            plan_id=plan.id,
        )
        if grant_access:
            # Compra aprovada converte definitivamente trial/pendente em cliente
            # pagante, para não ser alvo do expirador agendado de trial.
            customer["account_type"] = AccountType.CLIENT.value

    async def append_event(self, event: FinanceEvent) -> bool:
        """Insere evento apenas quando a chave composta é inédita."""
        key = (event.provider, event.company_id, event.provider_event_key)
        if key in self.event_keys:
            return False
        self.event_keys.add(key)
        self.events.append(event)
        return True

    async def append_revenue_event(self, event: FinanceEvent) -> None:
        """Espelha venda/renovação confirmada para o dashboard legado."""
        if event.user_id is None:
            return
        self.revenue_events.append(
            {
                "company_id": event.company_id,
                "user_id": event.user_id,
                "event_type": (
                    "renewal" if event.event_name == "subscription_renewed" else "sale"
                ),
                "amount": event.amount,
                "currency": event.currency,
                "status": "confirmed",
                "source_reference": event.provider_event_key,
                "occurred_at": event.occurred_at,
            }
        )

    async def list_events(
        self,
        company_id: str,
        *,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        user_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[FinanceEvent]:
        """Lista eventos filtrados explicitamente por tenant."""
        rows = [
            event
            for event in self.events
            if event.company_id == company_id
            and (start_at is None or event.occurred_at >= start_at)
            and (end_at is None or event.occurred_at <= end_at)
            and (user_id is None or event.user_id == user_id)
        ]
        rows.sort(key=lambda event: event.occurred_at, reverse=True)
        return rows[offset : offset + limit]

    async def get_config_state(self, company_id: str) -> FinanceConfigState:
        """Obtém ou inicializa estado operacional do tenant."""
        return self.config_states.setdefault(company_id, FinanceConfigState(company_id=company_id))

    async def touch_config_state(
        self,
        company_id: str,
        *,
        last_event_at: datetime | None = None,
        last_reconciled_at: datetime | None = None,
    ) -> FinanceConfigState:
        """Atualiza estado operacional em memória."""
        state = await self.get_config_state(company_id)
        state.last_event_at = last_event_at or state.last_event_at
        state.last_reconciled_at = last_reconciled_at or state.last_reconciled_at
        return state


class SupabaseFinanceRepository(FinanceRepository):
    """Persistência financeira via PostgREST usando credenciais server-only."""

    def __init__(self, supabase_url: str, service_role_key: str) -> None:
        self.supabase_url = supabase_url.rstrip("/")
        self.headers = {
            "apikey": service_role_key,
            "Authorization": f"Bearer {service_role_key}",
            "Content-Type": "application/json",
        }

    async def list_plans(self, company_id: str, *, include_inactive: bool) -> list[BillingPlan]:
        """Lista planos com filtro explícito de empresa."""
        path = (
            "/rest/v1/billing_plans"
            f"?company_id=eq.{quote(company_id, safe='')}"
            "&deleted_at=is.null"
            "&select=*"
            "&order=display_order.asc,name.asc"
        )
        if not include_inactive:
            path += "&is_active=eq.true"
        return [_plan_from_row(row) for row in await self._request("GET", path)]

    async def get_plan(self, company_id: str, plan_id: str) -> BillingPlan | None:
        """Busca plano com filtros de empresa e id."""
        rows = await self._request(
            "GET",
            (
                "/rest/v1/billing_plans"
                f"?company_id=eq.{quote(company_id, safe='')}"
                f"&id=eq.{quote(plan_id, safe='')}"
                "&deleted_at=is.null&select=*&limit=1"
            ),
        )
        return _plan_from_row(rows[0]) if rows else None

    async def resolve_plan(
        self,
        cakto_product_id: str | None,
        cakto_offer_id: str | None,
    ) -> BillingPlan | None:
        """Chama função restrita que resolve a oferta e valida seu produto."""
        rows = await self._request(
            "POST",
            "/rest/v1/rpc/resolve_billing_plan_external",
            json={
                "target_product_id": cakto_product_id,
                "target_offer_id": cakto_offer_id,
            },
        )
        return _plan_from_row(rows[0]) if rows else None

    async def product_is_available_for_company(
        self,
        company_id: str,
        cakto_product_id: str,
    ) -> bool:
        """Valida por RPC restrita o vínculo global do produto com a empresa."""
        result = await self._request(
            "POST",
            "/rest/v1/rpc/billing_product_is_available_for_company",
            json={
                "target_company_id": company_id,
                "target_product_id": cakto_product_id,
            },
        )
        return bool(result)

    async def create_plan(self, company_id: str, payload: BillingPlanCreate) -> BillingPlan:
        """Cria plano usando company_id da sessão."""
        row = {"company_id": company_id, **_plan_payload(payload)}
        rows = await self._request(
            "POST",
            "/rest/v1/billing_plans",
            json=row,
            headers={"Prefer": "return=representation"},
        )
        return _plan_from_row(rows[0])

    async def update_plan(
        self,
        company_id: str,
        plan_id: str,
        payload: BillingPlanUpdate,
    ) -> BillingPlan | None:
        """Atualiza plano com company_id e id explícitos."""
        body = _plan_payload(payload, omit_none=True)
        body["updated_at"] = datetime.now(timezone.utc).isoformat()
        rows = await self._request(
            "PATCH",
            (
                "/rest/v1/billing_plans"
                f"?company_id=eq.{quote(company_id, safe='')}"
                f"&id=eq.{quote(plan_id, safe='')}"
            ),
            json=body,
            headers={"Prefer": "return=representation"},
        )
        return _plan_from_row(rows[0]) if rows else None

    async def deactivate_plan(self, company_id: str, plan_id: str) -> bool:
        """Executa exclusão lógica dentro do tenant."""
        now = datetime.now(timezone.utc).isoformat()
        rows = await self._request(
            "PATCH",
            (
                "/rest/v1/billing_plans"
                f"?company_id=eq.{quote(company_id, safe='')}"
                f"&id=eq.{quote(plan_id, safe='')}"
            ),
            json={"is_active": False, "deleted_at": now, "updated_at": now},
            headers={"Prefer": "return=representation"},
        )
        return bool(rows)

    async def find_customer_by_email(
        self,
        company_id: str,
        email: str,
    ) -> dict[str, Any] | None:
        """Busca perfil por empresa e email sem registrar PII."""
        rows = await self._request(
            "GET",
            (
                "/rest/v1/user_access_profiles"
                f"?company_id=eq.{quote(company_id, safe='')}"
                f"&email=ilike.{quote(email, safe='@.')}"
                "&deleted_at=is.null"
                "&select=user_id,company_id,account_type,payment_status,grant_access"
                "&limit=1"
            ),
        )
        return rows[0] if rows else None

    async def ensure_purchase_customer(
        self,
        company_id: str,
        *,
        user_id: str,
        email: str,
        name: str,
    ) -> dict[str, Any]:
        """
        Upsert do perfil mínimo após Auth Admin criar o usuário da compra.

        Args:
            company_id: Tenant resolvido pela oferta Cakto.
            user_id: ID Auth já provisionado.
            email: E-mail do comprador.
            name: Nome do comprador.

        Returns:
            Perfil legível para liberação de acesso.
        """
        existing = await self.find_customer_by_email(company_id, email)
        if existing is not None:
            return existing
        now = datetime.now(timezone.utc).isoformat()
        rows = await self._request(
            "POST",
            "/rest/v1/user_access_profiles?on_conflict=user_id",
            json={
                "user_id": user_id,
                "company_id": company_id,
                "name": name.strip() or email.split("@", 1)[0],
                "email": email.strip().lower(),
                "account_type": AccountType.CLIENT.value,
                "payment_status": PaymentStatus.PENDING.value,
                "plan_name": "Aguardando liberação",
                "plan_status": "pending",
                "grant_access": False,
                "approval_status": "pending",
                "is_admin": False,
                "created_at": now,
                "updated_at": now,
            },
            headers={"Prefer": "resolution=merge-duplicates,return=representation"},
        )
        if rows:
            return {
                "user_id": str(rows[0]["user_id"]),
                "company_id": str(rows[0]["company_id"]),
                "account_type": rows[0].get("account_type"),
                "payment_status": rows[0].get("payment_status"),
                "grant_access": rows[0].get("grant_access"),
            }
        found = await self.find_customer_by_email(company_id, email)
        if found is None:
            raise RuntimeError("PURCHASE_CUSTOMER_PROFILE_MISSING")
        return found

    async def find_customer_by_billing_reference(
        self,
        company_id: str,
        *,
        provider_reference: str,
        subscription_reference: str | None,
    ) -> dict[str, Any] | None:
        """Resolve usuário por pedido/assinatura já vinculado, sempre no tenant."""
        filters = [
            f"provider_reference.eq.{quote(provider_reference, safe='')}",
        ]
        if subscription_reference:
            filters.append(
                f"subscription_reference.eq.{quote(subscription_reference, safe='')}"
            )
        events = await self._request(
            "GET",
            (
                "/rest/v1/billing_events"
                f"?company_id=eq.{quote(company_id, safe='')}"
                f"&or=({','.join(filters)})"
                "&user_id=not.is.null"
                "&select=user_id&order=occurred_at.desc&limit=1"
            ),
        )
        if not events:
            return None
        user_id = str(events[0]["user_id"])
        customers = await self._request(
            "GET",
            (
                "/rest/v1/user_access_profiles"
                f"?company_id=eq.{quote(company_id, safe='')}"
                f"&user_id=eq.{quote(user_id, safe='')}"
                "&deleted_at=is.null"
                "&select=user_id,company_id,account_type,payment_status,grant_access"
                "&limit=1"
            ),
        )
        return customers[0] if customers else None

    async def apply_customer_access(
        self,
        company_id: str,
        user_id: str,
        *,
        payment_status: PaymentStatus,
        grant_access: bool,
        plan: BillingPlan,
    ) -> None:
        """Atualiza acesso com filtros de empresa e usuário."""
        body: dict[str, object] = {
            "payment_status": payment_status.value,
            "grant_access": grant_access,
            "plan_id": plan.id,
            "plan_name": plan.name,
            "plan_status": "active" if grant_access else "expired",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if grant_access:
            # Compra/renovação libera lead pendente imediatamente e converte
            # definitivamente trial -> cliente pagante (evita que o expirador
            # agendado de trial revogue o acesso de quem já pagou).
            body["approval_status"] = "approved"
            body["account_type"] = AccountType.CLIENT.value
        await self._request(
            "PATCH",
            (
                "/rest/v1/user_access_profiles"
                f"?company_id=eq.{quote(company_id, safe='')}"
                f"&user_id=eq.{quote(user_id, safe='')}"
            ),
            json=body,
        )

    async def append_event(self, event: FinanceEvent) -> bool:
        """Insere ledger com resolução idempotente por chave composta."""
        rows = await self._request(
            "POST",
            "/rest/v1/billing_events?on_conflict=provider,company_id,provider_event_key",
            json=_event_row(event),
            headers={"Prefer": "resolution=ignore-duplicates,return=representation"},
        )
        return bool(rows)

    async def append_revenue_event(self, event: FinanceEvent) -> None:
        """Espelha evento aprovado no ledger consumido pelo dashboard atual."""
        if event.user_id is None:
            return
        await self._request(
            "POST",
            "/rest/v1/subscription_revenue_events",
            json={
                "company_id": event.company_id,
                "user_id": event.user_id,
                "event_type": (
                    "renewal" if event.event_name == "subscription_renewed" else "sale"
                ),
                "amount": str(event.amount),
                "currency": event.currency,
                "status": "confirmed",
                "source": "cakto",
                "source_reference": event.provider_event_key,
                "occurred_at": event.occurred_at.isoformat(),
            },
        )

    async def list_events(
        self,
        company_id: str,
        *,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        user_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[FinanceEvent]:
        """Lista ledger sempre filtrado por empresa."""
        path = (
            "/rest/v1/billing_events"
            f"?company_id=eq.{quote(company_id, safe='')}"
            "&select=*"
            "&order=occurred_at.desc"
            f"&limit={limit}&offset={offset}"
        )
        if start_at is not None:
            path += f"&occurred_at=gte.{quote(start_at.isoformat(), safe='')}"
        if end_at is not None:
            path += f"&occurred_at=lte.{quote(end_at.isoformat(), safe='')}"
        if user_id is not None:
            path += f"&user_id=eq.{quote(user_id, safe='')}"
        return [_event_from_row(row) for row in await self._request("GET", path)]

    async def get_config_state(self, company_id: str) -> FinanceConfigState:
        """Obtém configuração operacional pelo tenant."""
        rows = await self._request(
            "GET",
            (
                "/rest/v1/billing_provider_state"
                f"?company_id=eq.{quote(company_id, safe='')}"
                "&provider=eq.cakto&select=*&limit=1"
            ),
        )
        if not rows:
            return FinanceConfigState(company_id=company_id)
        return _state_from_row(rows[0])

    async def touch_config_state(
        self,
        company_id: str,
        *,
        last_event_at: datetime | None = None,
        last_reconciled_at: datetime | None = None,
    ) -> FinanceConfigState:
        """Faz upsert de timestamps operacionais sem armazenar segredo."""
        row: dict[str, Any] = {"company_id": company_id, "provider": "cakto"}
        if last_event_at is not None:
            row["last_event_at"] = last_event_at.isoformat()
        if last_reconciled_at is not None:
            row["last_reconciled_at"] = last_reconciled_at.isoformat()
        rows = await self._request(
            "POST",
            "/rest/v1/billing_provider_state?on_conflict=company_id,provider",
            json=row,
            headers={"Prefer": "resolution=merge-duplicates,return=representation"},
        )
        return _state_from_row(rows[0])

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """Executa I/O HTTP assíncrono sem expor credenciais."""
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.request(
                method,
                f"{self.supabase_url}{path}",
                headers={**self.headers, **(headers or {})},
                json=json,
            )
        response.raise_for_status()
        return response.json() if response.content else []


def _plan_payload(
    payload: BillingPlanCreate | BillingPlanUpdate,
    *,
    omit_none: bool = False,
) -> dict[str, Any]:
    """Serializa payload do plano para PostgREST."""
    row = dict(payload.__dict__)
    if omit_none:
        row = {key: value for key, value in row.items() if value is not None}
    if "price" in row and row["price"] is not None:
        row["price"] = str(row["price"])
    if "features" in row and row["features"] is not None:
        row["features"] = list(row["features"])
    return row


def _plan_from_row(row: dict[str, Any]) -> BillingPlan:
    """Converte linha do catálogo em modelo."""
    return BillingPlan(
        id=str(row["id"]),
        company_id=str(row["company_id"]),
        slug=str(row["slug"]),
        name=str(row["name"]),
        description=str(row.get("description") or ""),
        price=Decimal(str(row["price"])),
        currency=str(row.get("currency") or "BRL"),
        billing_interval_months=int(row["billing_interval_months"]),
        features=tuple(str(value) for value in row.get("features") or []),
        is_featured=bool(row.get("is_featured")),
        is_active=bool(row.get("is_active")),
        display_order=int(row.get("display_order") or 0),
        cakto_product_id=row.get("cakto_product_id"),
        cakto_offer_id=row.get("cakto_offer_id"),
        checkout_url=row.get("checkout_url"),
        created_at=_parse_datetime(row.get("created_at")) or datetime.now(timezone.utc),
        updated_at=_parse_datetime(row.get("updated_at")) or datetime.now(timezone.utc),
        deleted_at=_parse_datetime(row.get("deleted_at")),
    )


def _event_row(event: FinanceEvent) -> dict[str, Any]:
    """Serializa evento sem PII ou segredo."""
    return {
        "id": event.id,
        "company_id": event.company_id,
        "provider": event.provider,
        "provider_event_key": event.provider_event_key,
        "event_name": event.event_name,
        "status": event.status.value,
        "plan_id": event.plan_id,
        "user_id": event.user_id,
        "provider_reference": event.provider_reference,
        "subscription_reference": event.subscription_reference,
        "amount": str(event.amount),
        "currency": event.currency,
        "occurred_at": event.occurred_at.isoformat(),
        "received_at": event.received_at.isoformat(),
        "metadata": event.metadata,
    }


def _event_from_row(row: dict[str, Any]) -> FinanceEvent:
    """Converte linha do ledger em evento de domínio."""
    from backend.finance_models import FinanceEventStatus

    return FinanceEvent(
        id=str(row["id"]),
        company_id=str(row["company_id"]),
        provider=str(row["provider"]),
        provider_event_key=str(row["provider_event_key"]),
        event_name=str(row["event_name"]),
        status=FinanceEventStatus(str(row["status"])),
        plan_id=str(row["plan_id"]),
        user_id=str(row["user_id"]) if row.get("user_id") else None,
        provider_reference=str(row["provider_reference"]),
        subscription_reference=row.get("subscription_reference"),
        amount=Decimal(str(row.get("amount") or 0)),
        currency=str(row.get("currency") or "BRL"),
        occurred_at=_parse_datetime(row["occurred_at"]) or datetime.now(timezone.utc),
        received_at=_parse_datetime(row["received_at"]) or datetime.now(timezone.utc),
        metadata=dict(row.get("metadata") or {}),
    )


def _state_from_row(row: dict[str, Any]) -> FinanceConfigState:
    """Converte estado não secreto da integração."""
    return FinanceConfigState(
        company_id=str(row["company_id"]),
        provider=str(row.get("provider") or "cakto"),
        last_event_at=_parse_datetime(row.get("last_event_at")),
        last_reconciled_at=_parse_datetime(row.get("last_reconciled_at")),
    )


def _parse_datetime(value: Any) -> datetime | None:
    """Converte timestamp ISO opcional."""
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
