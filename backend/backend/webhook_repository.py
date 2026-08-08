"""Persistência assíncrona e multi-tenant de webhooks de saída."""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx

from backend.webhook_models import (
    DeliveryStatus,
    DomainEvent,
    DomainEventType,
    WebhookDelivery,
    WebhookEndpoint,
    WebhookEndpointCreate,
)


class WebhookRepository(ABC):
    """Contrato de persistência do domínio."""

    @abstractmethod
    async def list_endpoints(self, company_id: str) -> list[WebhookEndpoint]:
        """Lista destinos de uma empresa."""

    @abstractmethod
    async def get_endpoint(self, company_id: str, endpoint_id: str) -> WebhookEndpoint | None:
        """Busca destino pela chave composta."""

    @abstractmethod
    async def create_endpoint(
        self,
        company_id: str,
        payload: WebhookEndpointCreate,
        *,
        encrypted_secret: str,
    ) -> WebhookEndpoint:
        """Cria um destino dentro do tenant autenticado."""

    @abstractmethod
    async def update_endpoint(
        self,
        company_id: str,
        endpoint_id: str,
        *,
        name: str | None = None,
        url: str | None = None,
        subscribed_events: frozenset[DomainEventType] | None = None,
        is_active: bool | None = None,
    ) -> WebhookEndpoint | None:
        """Atualiza um destino com filtro explícito de empresa."""

    @abstractmethod
    async def append_event(self, event: DomainEvent) -> bool:
        """Insere evento idempotente na outbox."""

    @abstractmethod
    async def get_event(self, company_id: str, event_id: str) -> DomainEvent | None:
        """Busca evento por empresa e id."""

    @abstractmethod
    async def list_subscribed_endpoints(
        self,
        company_id: str,
        event_type: DomainEventType,
    ) -> list[WebhookEndpoint]:
        """Lista destinos ativos inscritos no evento."""

    @abstractmethod
    async def save_delivery(self, delivery: WebhookDelivery) -> WebhookDelivery:
        """Insere ou atualiza estado sanitizado de uma entrega."""

    @abstractmethod
    async def list_deliveries(
        self,
        company_id: str,
        *,
        endpoint_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[WebhookDelivery]:
        """Lista entregas paginadas do tenant."""

    @abstractmethod
    async def get_delivery(self, company_id: str, delivery_id: str) -> WebhookDelivery | None:
        """Busca entrega por chave composta."""

    @abstractmethod
    async def get_delivery_for_event(
        self,
        company_id: str,
        endpoint_id: str,
        event_id: str,
    ) -> WebhookDelivery | None:
        """Busca tentativa pela combinação idempotente do mesmo tenant."""


class InMemoryWebhookRepository(WebhookRepository):
    """Repositório determinístico para testes e desenvolvimento isolado."""

    def __init__(self) -> None:
        self.endpoints: dict[tuple[str, str], WebhookEndpoint] = {}
        self.events: dict[tuple[str, str], DomainEvent] = {}
        self.deliveries: dict[tuple[str, str], WebhookDelivery] = {}

    async def list_endpoints(self, company_id: str) -> list[WebhookEndpoint]:
        """Lista destinos do tenant por criação decrescente."""
        rows = [
            endpoint
            for (tenant, _), endpoint in self.endpoints.items()
            if tenant == company_id
        ]
        return sorted(rows, key=lambda item: item.created_at, reverse=True)

    async def get_endpoint(self, company_id: str, endpoint_id: str) -> WebhookEndpoint | None:
        """Busca destino pela chave composta."""
        return self.endpoints.get((company_id, endpoint_id))

    async def create_endpoint(
        self,
        company_id: str,
        payload: WebhookEndpointCreate,
        *,
        encrypted_secret: str,
    ) -> WebhookEndpoint:
        """Cria destino sem persistir o segredo em texto."""
        now = datetime.now(timezone.utc)
        endpoint = WebhookEndpoint(
            id=str(uuid.uuid4()),
            company_id=company_id,
            name=payload.name,
            url=payload.url,
            subscribed_events=payload.subscribed_events,
            encrypted_secret=encrypted_secret,
            secret_version=1,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        self.endpoints[(company_id, endpoint.id)] = endpoint
        return endpoint

    async def update_endpoint(
        self,
        company_id: str,
        endpoint_id: str,
        *,
        name: str | None = None,
        url: str | None = None,
        subscribed_events: frozenset[DomainEventType] | None = None,
        is_active: bool | None = None,
    ) -> WebhookEndpoint | None:
        """Atualiza campos fornecidos do mesmo tenant."""
        endpoint = await self.get_endpoint(company_id, endpoint_id)
        if endpoint is None:
            return None
        if name is not None:
            endpoint.name = name
        if url is not None:
            endpoint.url = url
        if subscribed_events is not None:
            endpoint.subscribed_events = subscribed_events
        if is_active is not None:
            endpoint.is_active = is_active
        endpoint.updated_at = datetime.now(timezone.utc)
        return endpoint

    async def append_event(self, event: DomainEvent) -> bool:
        """Insere evento e entregas pendentes somente uma vez."""
        key = (event.company_id, event.id)
        if key in self.events:
            return False
        self.events[key] = event
        now = datetime.now(timezone.utc)
        for endpoint in await self.list_subscribed_endpoints(
            event.company_id,
            event.event_type,
        ):
            delivery = WebhookDelivery(
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
            self.deliveries[(event.company_id, delivery.id)] = delivery
        return True

    async def get_event(self, company_id: str, event_id: str) -> DomainEvent | None:
        """Busca evento pela chave composta."""
        return self.events.get((company_id, event_id))

    async def list_subscribed_endpoints(
        self,
        company_id: str,
        event_type: DomainEventType,
    ) -> list[WebhookEndpoint]:
        """Lista somente destinos ativos do tenant."""
        return [
            endpoint
            for endpoint in await self.list_endpoints(company_id)
            if endpoint.is_active and event_type in endpoint.subscribed_events
        ]

    async def save_delivery(self, delivery: WebhookDelivery) -> WebhookDelivery:
        """Persiste o estado mais recente da entrega."""
        self.deliveries[(delivery.company_id, delivery.id)] = delivery
        return delivery

    async def list_deliveries(
        self,
        company_id: str,
        *,
        endpoint_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[WebhookDelivery]:
        """Lista entregas sem atravessar tenants."""
        rows = [
            delivery
            for (tenant, _), delivery in self.deliveries.items()
            if tenant == company_id
            and (endpoint_id is None or delivery.endpoint_id == endpoint_id)
        ]
        rows.sort(key=lambda item: item.created_at, reverse=True)
        return rows[offset : offset + limit]

    async def get_delivery(self, company_id: str, delivery_id: str) -> WebhookDelivery | None:
        """Busca entrega pela chave composta."""
        return self.deliveries.get((company_id, delivery_id))

    async def get_delivery_for_event(
        self,
        company_id: str,
        endpoint_id: str,
        event_id: str,
    ) -> WebhookDelivery | None:
        """Busca tentativa pela combinação idempotente."""
        return next(
            (
                delivery
                for (tenant, _), delivery in self.deliveries.items()
                if tenant == company_id
                and delivery.endpoint_id == endpoint_id
                and delivery.event_id == event_id
            ),
            None,
        )


class SupabaseWebhookRepository(WebhookRepository):
    """Persistência PostgREST usando service role somente no backend."""

    def __init__(self, supabase_url: str, service_role_key: str) -> None:
        self.supabase_url = supabase_url.rstrip("/")
        self.headers = {
            "apikey": service_role_key,
            "Authorization": f"Bearer {service_role_key}",
            "Content-Type": "application/json",
        }

    async def list_endpoints(self, company_id: str) -> list[WebhookEndpoint]:
        """Lista destinos com filtro explícito de empresa."""
        rows = await self._request(
            "GET",
            "/rest/v1/outgoing_webhook_endpoints"
            f"?company_id=eq.{quote(company_id, safe='')}"
            "&select=*&order=created_at.desc",
        )
        return [_endpoint_from_row(row) for row in rows]

    async def get_endpoint(self, company_id: str, endpoint_id: str) -> WebhookEndpoint | None:
        """Busca destino por empresa e id."""
        rows = await self._request(
            "GET",
            "/rest/v1/outgoing_webhook_endpoints"
            f"?company_id=eq.{quote(company_id, safe='')}"
            f"&id=eq.{quote(endpoint_id, safe='')}&select=*&limit=1",
        )
        return _endpoint_from_row(rows[0]) if rows else None

    async def create_endpoint(
        self,
        company_id: str,
        payload: WebhookEndpointCreate,
        *,
        encrypted_secret: str,
    ) -> WebhookEndpoint:
        """Cria destino no tenant autenticado."""
        rows = await self._request(
            "POST",
            "/rest/v1/outgoing_webhook_endpoints",
            json={
                "company_id": company_id,
                "name": payload.name,
                "url": payload.url,
                "subscribed_events": sorted(event.value for event in payload.subscribed_events),
                "encrypted_secret": encrypted_secret,
                "secret_version": 1,
                "is_active": True,
            },
            headers={"Prefer": "return=representation"},
        )
        return _endpoint_from_row(rows[0])

    async def update_endpoint(
        self,
        company_id: str,
        endpoint_id: str,
        *,
        name: str | None = None,
        url: str | None = None,
        subscribed_events: frozenset[DomainEventType] | None = None,
        is_active: bool | None = None,
    ) -> WebhookEndpoint | None:
        """Atualiza destino filtrando empresa e id."""
        body: dict[str, Any] = {"updated_at": datetime.now(timezone.utc).isoformat()}
        if name is not None:
            body["name"] = name
        if url is not None:
            body["url"] = url
        if subscribed_events is not None:
            body["subscribed_events"] = sorted(event.value for event in subscribed_events)
        if is_active is not None:
            body["is_active"] = is_active
        rows = await self._request(
            "PATCH",
            "/rest/v1/outgoing_webhook_endpoints"
            f"?company_id=eq.{quote(company_id, safe='')}"
            f"&id=eq.{quote(endpoint_id, safe='')}",
            json=body,
            headers={"Prefer": "return=representation"},
        )
        return _endpoint_from_row(rows[0]) if rows else None

    async def append_event(self, event: DomainEvent) -> bool:
        """Insere outbox e entregas na mesma transação RPC."""
        inserted = await self._request(
            "POST",
            "/rest/v1/rpc/enqueue_domain_event",
            json={
                "target_company_id": event.company_id,
                "target_event_id": event.id,
                "target_event_type": event.event_type.value,
                "target_subject_user_id": event.subject_user_id,
                "target_request_id": event.request_id,
                "target_payload": event.payload,
                "target_occurred_at": event.occurred_at.isoformat(),
            },
        )
        return bool(inserted)

    async def get_event(self, company_id: str, event_id: str) -> DomainEvent | None:
        """Busca evento por empresa e id."""
        rows = await self._request(
            "GET",
            "/rest/v1/domain_event_outbox"
            f"?company_id=eq.{quote(company_id, safe='')}"
            f"&id=eq.{quote(event_id, safe='')}&select=*&limit=1",
        )
        return _event_from_row(rows[0]) if rows else None

    async def list_subscribed_endpoints(
        self,
        company_id: str,
        event_type: DomainEventType,
    ) -> list[WebhookEndpoint]:
        """Filtra destinos ativos do tenant inscritos no evento."""
        # PostgREST usa literal de array Postgres (`{a,b}`), não JSON (`["a"]`).
        # O formato JSON gerava 22P02 e derrubava o webhook Cakto com 500.
        array_literal = quote("{" + event_type.value + "}", safe="")
        rows = await self._request(
            "GET",
            "/rest/v1/outgoing_webhook_endpoints"
            f"?company_id=eq.{quote(company_id, safe='')}"
            f"&is_active=eq.true&subscribed_events=cs.{array_literal}"
            "&select=*",
        )
        return [_endpoint_from_row(row) for row in rows]

    async def save_delivery(self, delivery: WebhookDelivery) -> WebhookDelivery:
        """Faz upsert do estado sanitizado da entrega."""
        rows = await self._request(
            "POST",
            "/rest/v1/webhook_deliveries?on_conflict=company_id,id",
            json=_delivery_row(delivery),
            headers={"Prefer": "resolution=merge-duplicates,return=representation"},
        )
        return _delivery_from_row(rows[0])

    async def list_deliveries(
        self,
        company_id: str,
        *,
        endpoint_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[WebhookDelivery]:
        """Lista histórico paginado do tenant."""
        path = (
            "/rest/v1/webhook_deliveries"
            f"?company_id=eq.{quote(company_id, safe='')}"
            f"&select=*&order=created_at.desc&limit={limit}&offset={offset}"
        )
        if endpoint_id:
            path += f"&endpoint_id=eq.{quote(endpoint_id, safe='')}"
        return [_delivery_from_row(row) for row in await self._request("GET", path)]

    async def get_delivery(self, company_id: str, delivery_id: str) -> WebhookDelivery | None:
        """Busca entrega com filtro explícito de empresa."""
        rows = await self._request(
            "GET",
            "/rest/v1/webhook_deliveries"
            f"?company_id=eq.{quote(company_id, safe='')}"
            f"&id=eq.{quote(delivery_id, safe='')}&select=*&limit=1",
        )
        return _delivery_from_row(rows[0]) if rows else None

    async def get_delivery_for_event(
        self,
        company_id: str,
        endpoint_id: str,
        event_id: str,
    ) -> WebhookDelivery | None:
        """Busca tentativa por empresa, destino e evento."""
        rows = await self._request(
            "GET",
            "/rest/v1/webhook_deliveries"
            f"?company_id=eq.{quote(company_id, safe='')}"
            f"&endpoint_id=eq.{quote(endpoint_id, safe='')}"
            f"&event_id=eq.{quote(event_id, safe='')}&select=*&limit=1",
        )
        return _delivery_from_row(rows[0]) if rows else None

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """Executa I/O PostgREST assíncrono sem registrar credenciais."""
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as client:
            response = await client.request(
                method,
                f"{self.supabase_url}{path}",
                headers={**self.headers, **(headers or {})},
                json=json,
            )
        response.raise_for_status()
        return response.json() if response.content else []


def _parse_datetime(value: Any) -> datetime:
    """Normaliza timestamp ISO para UTC."""
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _endpoint_from_row(row: dict[str, Any]) -> WebhookEndpoint:
    """Converte linha PostgREST em destino."""
    return WebhookEndpoint(
        id=str(row["id"]),
        company_id=str(row["company_id"]),
        name=str(row["name"]),
        url=str(row["url"]),
        subscribed_events=frozenset(
            DomainEventType(value) for value in row.get("subscribed_events") or []
        ),
        encrypted_secret=str(row["encrypted_secret"]),
        secret_version=int(row.get("secret_version") or 1),
        is_active=bool(row.get("is_active", True)),
        created_at=_parse_datetime(row["created_at"]),
        updated_at=_parse_datetime(row["updated_at"]),
    )


def _event_from_row(row: dict[str, Any]) -> DomainEvent:
    """Converte linha PostgREST em evento."""
    return DomainEvent(
        id=str(row["id"]),
        company_id=str(row["company_id"]),
        event_type=DomainEventType(str(row["event_type"])),
        subject_user_id=str(row["subject_user_id"]) if row.get("subject_user_id") else None,
        request_id=str(row["request_id"]),
        payload=dict(row.get("payload") or {}),
        occurred_at=_parse_datetime(row["occurred_at"]),
        created_at=_parse_datetime(row["created_at"]),
        processed_at=_parse_datetime(row["processed_at"]) if row.get("processed_at") else None,
    )


def _delivery_row(delivery: WebhookDelivery) -> dict[str, Any]:
    """Serializa entrega sem corpo, segredo ou PII."""
    return {
        "id": delivery.id,
        "company_id": delivery.company_id,
        "endpoint_id": delivery.endpoint_id,
        "event_id": delivery.event_id,
        "request_id": delivery.request_id,
        "status": delivery.status.value,
        "attempt_count": delivery.attempt_count,
        "response_status": delivery.response_status,
        "latency_ms": delivery.latency_ms,
        "next_attempt_at": (
            delivery.next_attempt_at.isoformat() if delivery.next_attempt_at else None
        ),
        "last_error_code": delivery.last_error_code,
        "created_at": delivery.created_at.isoformat(),
        "updated_at": delivery.updated_at.isoformat(),
    }


def _delivery_from_row(row: dict[str, Any]) -> WebhookDelivery:
    """Converte linha PostgREST em entrega."""
    return WebhookDelivery(
        id=str(row["id"]),
        company_id=str(row["company_id"]),
        endpoint_id=str(row["endpoint_id"]),
        event_id=str(row["event_id"]),
        request_id=str(row["request_id"]),
        status=DeliveryStatus(str(row["status"])),
        attempt_count=int(row.get("attempt_count") or 0),
        response_status=int(row["response_status"]) if row.get("response_status") else None,
        latency_ms=int(row["latency_ms"]) if row.get("latency_ms") is not None else None,
        next_attempt_at=(
            _parse_datetime(row["next_attempt_at"]) if row.get("next_attempt_at") else None
        ),
        last_error_code=str(row["last_error_code"]) if row.get("last_error_code") else None,
        created_at=_parse_datetime(row["created_at"]),
        updated_at=_parse_datetime(row["updated_at"]),
    )
