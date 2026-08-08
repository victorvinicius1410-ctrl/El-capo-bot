"""Persistência assíncrona e multi-tenant de emails nativos."""

from __future__ import annotations

import json
import logging
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx

from backend.email_models import EmailDelivery, EmailDeliveryStatus, EmailTemplate
from backend.webhook_models import DomainEventType

logger = logging.getLogger("backend-email-repository")

# Bucket privado usado quando as tabelas SQL ainda não existem no Supabase.
EMAIL_STORAGE_BUCKET = "email-templates"


class EmailRepository(ABC):
    """Contrato de persistência do domínio."""

    @abstractmethod
    async def list_templates(self, company_id: str) -> list[EmailTemplate]:
        """Lista templates de uma empresa."""

    @abstractmethod
    async def get_template(
        self,
        company_id: str,
        event_type: DomainEventType,
    ) -> EmailTemplate | None:
        """Busca template pela chave composta."""

    @abstractmethod
    async def upsert_template(self, template: EmailTemplate) -> EmailTemplate:
        """Cria ou atualiza template no tenant."""

    @abstractmethod
    async def save_delivery(self, delivery: EmailDelivery) -> EmailDelivery:
        """Insere ou atualiza entrega sanitizada."""

    @abstractmethod
    async def get_delivery(
        self,
        company_id: str,
        delivery_id: str,
    ) -> EmailDelivery | None:
        """Busca entrega por empresa e id."""

    @abstractmethod
    async def get_delivery_by_event(
        self,
        company_id: str,
        event_id: str,
        event_type: DomainEventType,
    ) -> EmailDelivery | None:
        """Busca entrega idempotente do evento."""

    @abstractmethod
    async def list_deliveries(
        self,
        company_id: str,
        *,
        limit: int,
        offset: int,
    ) -> list[EmailDelivery]:
        """Lista entregas recentes do tenant."""


class InMemoryEmailRepository(EmailRepository):
    """Repositório em memória para testes."""

    def __init__(self) -> None:
        self.templates: dict[tuple[str, str], EmailTemplate] = {}
        self.deliveries: dict[tuple[str, str], EmailDelivery] = {}

    async def list_templates(self, company_id: str) -> list[EmailTemplate]:
        """Lista templates filtrados por empresa."""
        return sorted(
            [item for key, item in self.templates.items() if key[0] == company_id],
            key=lambda item: item.event_type.value,
        )

    async def get_template(
        self,
        company_id: str,
        event_type: DomainEventType,
    ) -> EmailTemplate | None:
        """Busca template no tenant."""
        return self.templates.get((company_id, event_type.value))

    async def upsert_template(self, template: EmailTemplate) -> EmailTemplate:
        """Persiste template no mapa em memória."""
        self.templates[(template.company_id, template.event_type.value)] = template
        return template

    async def save_delivery(self, delivery: EmailDelivery) -> EmailDelivery:
        """Persiste entrega idempotente por evento."""
        if delivery.event_id:
            for key, existing in list(self.deliveries.items()):
                if (
                    existing.company_id == delivery.company_id
                    and existing.event_id == delivery.event_id
                    and existing.event_type == delivery.event_type
                ):
                    self.deliveries.pop(key, None)
        self.deliveries[(delivery.company_id, delivery.id)] = delivery
        return delivery

    async def get_delivery(
        self,
        company_id: str,
        delivery_id: str,
    ) -> EmailDelivery | None:
        """Busca entrega por id."""
        return self.deliveries.get((company_id, delivery_id))

    async def get_delivery_by_event(
        self,
        company_id: str,
        event_id: str,
        event_type: DomainEventType,
    ) -> EmailDelivery | None:
        """Busca entrega pelo evento."""
        for delivery in self.deliveries.values():
            if (
                delivery.company_id == company_id
                and delivery.event_id == event_id
                and delivery.event_type == event_type
            ):
                return delivery
        return None

    async def list_deliveries(
        self,
        company_id: str,
        *,
        limit: int,
        offset: int,
    ) -> list[EmailDelivery]:
        """Lista entregas paginadas do tenant."""
        items = sorted(
            [item for item in self.deliveries.values() if item.company_id == company_id],
            key=lambda item: item.created_at,
            reverse=True,
        )
        return items[offset : offset + limit]


class SupabaseEmailRepository(EmailRepository):
    """
    Persistência no Supabase com filtro explícito de empresa.

    Preferência: tabelas ``email_templates`` / ``email_deliveries``.
    Se elas ainda não existirem (PGRST205), usa o bucket privado
    ``email-templates`` no Storage até a migration SQL ser aplicada.
    """

    def __init__(self, supabase_url: str, service_role_key: str) -> None:
        """
        Inicializa o cliente server-only.

        Args:
            supabase_url: URL do projeto Supabase.
            service_role_key: Chave de serviço (nunca no frontend).
        """
        self.base_url = supabase_url.rstrip("/")
        self.headers = {
            "apikey": service_role_key,
            "Authorization": f"Bearer {service_role_key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }
        self._use_storage: bool | None = None

    async def list_templates(self, company_id: str) -> list[EmailTemplate]:
        """Lista templates do tenant."""
        if await self._prefer_storage():
            items: list[EmailTemplate] = []
            for event_type in DomainEventType:
                template = await self._storage_get_template(company_id, event_type)
                if template is not None:
                    items.append(template)
            return sorted(items, key=lambda item: item.event_type.value)
        rows = await self._get(
            "email_templates",
            {
                "company_id": f"eq.{company_id}",
                "order": "event_type.asc",
            },
        )
        return [self._template_from_row(row) for row in rows]

    async def get_template(
        self,
        company_id: str,
        event_type: DomainEventType,
    ) -> EmailTemplate | None:
        """Busca um template no tenant."""
        if await self._prefer_storage():
            return await self._storage_get_template(company_id, event_type)
        rows = await self._get(
            "email_templates",
            {
                "company_id": f"eq.{company_id}",
                "event_type": f"eq.{event_type.value}",
                "limit": "1",
            },
        )
        if not rows:
            return None
        return self._template_from_row(rows[0])

    async def upsert_template(self, template: EmailTemplate) -> EmailTemplate:
        """Upsert por company_id + event_type."""
        if await self._prefer_storage():
            await self._storage_save_template(template)
            return template
        payload = {
            "id": template.id,
            "company_id": template.company_id,
            "event_type": template.event_type.value,
            "subject": template.subject,
            "html_body": template.html_body,
            "is_enabled": template.is_enabled,
            "updated_at": template.updated_at.isoformat(),
            "created_at": template.created_at.isoformat(),
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.base_url}/rest/v1/email_templates",
                headers={
                    **self.headers,
                    "Prefer": "resolution=merge-duplicates,return=representation",
                },
                params={"on_conflict": "company_id,event_type"},
                json=payload,
            )
            response.raise_for_status()
            rows = response.json()
        return self._template_from_row(rows[0])

    async def save_delivery(self, delivery: EmailDelivery) -> EmailDelivery:
        """Upsert de entrega sanitizada."""
        if await self._prefer_storage():
            rows = await self._storage_load_deliveries(delivery.company_id)
            payload = self._delivery_to_row(delivery)
            replaced = False
            for index, existing in enumerate(rows):
                same_id = str(existing.get("id")) == delivery.id
                same_event = (
                    delivery.event_id
                    and existing.get("event_id") == delivery.event_id
                    and existing.get("event_type") == delivery.event_type.value
                )
                if same_id or same_event:
                    rows[index] = payload
                    replaced = True
                    break
            if not replaced:
                rows.append(payload)
            await self._storage_save_deliveries(delivery.company_id, rows)
            return delivery
        payload = self._delivery_to_row(delivery)
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.base_url}/rest/v1/email_deliveries",
                headers={
                    **self.headers,
                    "Prefer": "resolution=merge-duplicates,return=representation",
                },
                params={"on_conflict": "company_id,id"},
                json=payload,
            )
            response.raise_for_status()
            rows = response.json()
        return self._delivery_from_row(rows[0])

    async def get_delivery(
        self,
        company_id: str,
        delivery_id: str,
    ) -> EmailDelivery | None:
        """Busca entrega por id."""
        if await self._prefer_storage():
            for row in await self._storage_load_deliveries(company_id):
                if str(row.get("id")) == delivery_id:
                    return self._delivery_from_row(row)
            return None
        rows = await self._get(
            "email_deliveries",
            {
                "company_id": f"eq.{company_id}",
                "id": f"eq.{delivery_id}",
                "limit": "1",
            },
        )
        if not rows:
            return None
        return self._delivery_from_row(rows[0])

    async def get_delivery_by_event(
        self,
        company_id: str,
        event_id: str,
        event_type: DomainEventType,
    ) -> EmailDelivery | None:
        """Busca entrega idempotente."""
        if await self._prefer_storage():
            for row in await self._storage_load_deliveries(company_id):
                if (
                    row.get("event_id") == event_id
                    and row.get("event_type") == event_type.value
                ):
                    return self._delivery_from_row(row)
            return None
        rows = await self._get(
            "email_deliveries",
            {
                "company_id": f"eq.{company_id}",
                "event_id": f"eq.{event_id}",
                "event_type": f"eq.{event_type.value}",
                "limit": "1",
            },
        )
        if not rows:
            return None
        return self._delivery_from_row(rows[0])

    async def list_deliveries(
        self,
        company_id: str,
        *,
        limit: int,
        offset: int,
    ) -> list[EmailDelivery]:
        """Lista entregas recentes."""
        if await self._prefer_storage():
            items = [
                self._delivery_from_row(row)
                for row in await self._storage_load_deliveries(company_id)
            ]
            items.sort(key=lambda item: item.created_at, reverse=True)
            return items[offset : offset + limit]
        rows = await self._get(
            "email_deliveries",
            {
                "company_id": f"eq.{company_id}",
                "order": "created_at.desc",
                "limit": str(limit),
                "offset": str(offset),
            },
        )
        return [self._delivery_from_row(row) for row in rows]

    async def _prefer_storage(self) -> bool:
        """
        Detecta se as tabelas SQL existem; cacheia o resultado no processo.

        Returns:
            True quando deve usar Storage como fallback.
        """
        if self._use_storage is not None:
            return self._use_storage
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{self.base_url}/rest/v1/email_templates",
                headers=self.headers,
                params={"select": "id", "limit": "1"},
            )
        if response.status_code == 404 and (
            "PGRST205" in response.text or "email_templates" in response.text
        ):
            logger.warning(
                "email.templates_table_missing fallback=storage "
                "migration=migration_native_emails.sql"
            )
            self._use_storage = True
            await self._ensure_bucket()
            return True
        response.raise_for_status()
        self._use_storage = False
        return False

    async def _ensure_bucket(self) -> None:
        """Garante o bucket privado de fallback."""
        async with httpx.AsyncClient(timeout=15.0) as client:
            listed = await client.get(
                f"{self.base_url}/storage/v1/bucket",
                headers=self.headers,
            )
            listed.raise_for_status()
            buckets = listed.json() if isinstance(listed.json(), list) else []
            names = {str(item.get("name") or item.get("id")) for item in buckets}
            if EMAIL_STORAGE_BUCKET in names:
                return
            created = await client.post(
                f"{self.base_url}/storage/v1/bucket",
                headers=self.headers,
                json={
                    "id": EMAIL_STORAGE_BUCKET,
                    "name": EMAIL_STORAGE_BUCKET,
                    "public": False,
                    "file_size_limit": 2_097_152,
                    "allowed_mime_types": ["application/json", "text/plain"],
                },
            )
            if created.status_code not in (200, 201):
                # Bucket pode já existir em corrida; 409/400 com nome duplicado é ok.
                if created.status_code not in (400, 409):
                    created.raise_for_status()

    @staticmethod
    def _template_prefix(company_id: str, event_type: DomainEventType) -> str:
        """Prefixo versionado dos templates no Storage."""
        # event_type usa ponto (trial.started); no path vira hífen.
        safe_event = event_type.value.replace(".", "__")
        return f"{company_id}/templates/{safe_event}/"

    @staticmethod
    def _deliveries_prefix(company_id: str) -> str:
        """Prefixo versionado das entregas no Storage."""
        return f"{company_id}/deliveries/"

    async def _storage_get_template(
        self,
        company_id: str,
        event_type: DomainEventType,
    ) -> EmailTemplate | None:
        """Lê a revisão mais recente do template no Storage."""
        latest = await self._storage_latest_object(
            self._template_prefix(company_id, event_type)
        )
        if latest is None:
            # Compatibilidade com o caminho fixo usado no primeiro fallback.
            legacy = await self._storage_get_json(
                f"{company_id}/templates/{event_type.value}.json"
            )
            if legacy is None:
                return None
            return self._template_from_row(legacy)
        row = await self._storage_get_json(latest)
        if row is None:
            return None
        return self._template_from_row(row)

    async def _storage_save_template(self, template: EmailTemplate) -> None:
        """Persiste uma nova revisão do template (caminho único)."""
        revision = f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}_{uuid.uuid4().hex[:8]}.json"
        path = f"{self._template_prefix(template.company_id, template.event_type)}{revision}"
        await self._storage_put_json(
            path,
            {
                "id": template.id,
                "company_id": template.company_id,
                "event_type": template.event_type.value,
                "subject": template.subject,
                "html_body": template.html_body,
                "is_enabled": template.is_enabled,
                "updated_at": template.updated_at.isoformat(),
                "created_at": template.created_at.isoformat(),
            },
        )
        await self._storage_prune_prefix(
            self._template_prefix(template.company_id, template.event_type),
            keep=3,
        )

    async def _storage_load_deliveries(self, company_id: str) -> list[dict[str, Any]]:
        """Carrega o índice de entregas do tenant (revisão mais recente)."""
        latest = await self._storage_latest_object(self._deliveries_prefix(company_id))
        if latest is None:
            legacy = await self._storage_get_json(f"{company_id}/deliveries.json")
            if isinstance(legacy, list):
                return [item for item in legacy if isinstance(item, dict)]
            return []
        data = await self._storage_get_json(latest)
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        return []

    async def _storage_save_deliveries(
        self,
        company_id: str,
        rows: list[dict[str, Any]],
    ) -> None:
        """Persiste uma nova revisão do índice de entregas."""
        revision = f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}_{uuid.uuid4().hex[:8]}.json"
        path = f"{self._deliveries_prefix(company_id)}{revision}"
        await self._storage_put_json(path, rows)
        await self._storage_prune_prefix(self._deliveries_prefix(company_id), keep=3)

    async def _storage_latest_object(self, prefix: str) -> str | None:
        """
        Retorna o caminho completo do objeto mais recente sob um prefixo.

        Usa a API de listagem (não CDN) para evitar GET stale na mesma chave.
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.base_url}/storage/v1/object/list/{EMAIL_STORAGE_BUCKET}",
                headers=self.headers,
                json={
                    "prefix": prefix,
                    "limit": 100,
                    "offset": 0,
                    "sortBy": {"column": "name", "order": "desc"},
                },
            )
            response.raise_for_status()
            items = response.json()
        if not isinstance(items, list) or not items:
            return None
        # Nomes versionados começam por timestamp; ordem lexicográfica desc = mais novo.
        names = sorted(
            (
                str(item.get("name"))
                for item in items
                if isinstance(item, dict) and item.get("name")
            ),
            reverse=True,
        )
        if not names:
            return None
        return f"{prefix}{names[0]}"

    async def _storage_prune_prefix(self, prefix: str, *, keep: int) -> None:
        """Mantém só as N revisões mais recentes sob o prefixo."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.base_url}/storage/v1/object/list/{EMAIL_STORAGE_BUCKET}",
                headers=self.headers,
                json={
                    "prefix": prefix,
                    "limit": 100,
                    "offset": 0,
                    "sortBy": {"column": "name", "order": "desc"},
                },
            )
            if response.status_code >= 400:
                return
            items = response.json()
        if not isinstance(items, list):
            return
        names = sorted(
            (
                str(item.get("name"))
                for item in items
                if isinstance(item, dict) and item.get("name")
            ),
            reverse=True,
        )
        stale = names[keep:]
        if not stale:
            return
        async with httpx.AsyncClient(timeout=30.0) as client:
            for name in stale:
                await client.delete(
                    f"{self.base_url}/storage/v1/object/{EMAIL_STORAGE_BUCKET}/{prefix}{name}",
                    headers=self.headers,
                )

    async def _storage_get_json(self, object_path: str) -> Any | None:
        """GET de objeto JSON no Storage (None se inexistente)."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self.base_url}/storage/v1/object/{EMAIL_STORAGE_BUCKET}/{object_path}",
                headers=self.headers,
            )
        if response.status_code == 404:
            return None
        if response.status_code == 400:
            # Supabase Storage costuma devolver 400 + JSON statusCode 404
            # quando a chave não existe.
            try:
                payload = response.json()
            except Exception:
                payload = {}
            status = str(payload.get("statusCode") or "")
            error = str(payload.get("error") or "").lower()
            code = str(payload.get("code") or "")
            if status == "404" or error == "not_found" or code == "NoSuchKey":
                return None
        response.raise_for_status()
        return response.json()

    async def _storage_put_json(self, object_path: str, payload: Any) -> None:
        """Grava um objeto JSON novo (caminho deve ser único)."""
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.base_url}/storage/v1/object/{EMAIL_STORAGE_BUCKET}/{object_path}",
                headers={
                    **self.headers,
                    "Content-Type": "application/json",
                    "x-upsert": "true",
                },
                content=body,
            )
            response.raise_for_status()

    async def _get(self, table: str, params: dict[str, str]) -> list[dict[str, Any]]:
        """GET REST genérico."""
        query = "&".join(f"{key}={quote(value, safe='.,=*')}" for key, value in params.items())
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self.base_url}/rest/v1/{table}?{query}",
                headers=self.headers,
            )
            response.raise_for_status()
            data = response.json()
        return data if isinstance(data, list) else []

    @staticmethod
    def _parse_dt(value: str | None) -> datetime:
        """Converte timestamp ISO em datetime UTC."""
        if not value:
            return datetime.now(timezone.utc)
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    @staticmethod
    def _delivery_to_row(delivery: EmailDelivery) -> dict[str, Any]:
        """Serializa entrega para linha SQL/Storage."""
        return {
            "id": delivery.id,
            "company_id": delivery.company_id,
            "event_id": delivery.event_id,
            "event_type": delivery.event_type.value,
            "recipient_email_hash": delivery.recipient_email_hash,
            "subject": delivery.subject,
            "status": delivery.status.value,
            "attempt_count": delivery.attempt_count,
            "provider_message_id": delivery.provider_message_id,
            "latency_ms": delivery.latency_ms,
            "next_attempt_at": (
                delivery.next_attempt_at.isoformat() if delivery.next_attempt_at else None
            ),
            "last_error_code": delivery.last_error_code,
            "request_id": delivery.request_id,
            "created_at": delivery.created_at.isoformat(),
            "updated_at": delivery.updated_at.isoformat(),
        }

    def _template_from_row(self, row: dict[str, Any]) -> EmailTemplate:
        """Mapeia linha SQL para modelo."""
        return EmailTemplate(
            id=str(row["id"]),
            company_id=str(row["company_id"]),
            event_type=DomainEventType(str(row["event_type"])),
            subject=str(row["subject"]),
            html_body=str(row["html_body"]),
            is_enabled=bool(row["is_enabled"]),
            created_at=self._parse_dt(row.get("created_at")),
            updated_at=self._parse_dt(row.get("updated_at")),
        )

    def _delivery_from_row(self, row: dict[str, Any]) -> EmailDelivery:
        """Mapeia linha SQL para entrega."""
        return EmailDelivery(
            id=str(row["id"]),
            company_id=str(row["company_id"]),
            event_id=str(row["event_id"]) if row.get("event_id") else None,
            event_type=DomainEventType(str(row["event_type"])),
            recipient_email_hash=str(row["recipient_email_hash"]),
            subject=str(row["subject"]),
            status=EmailDeliveryStatus(str(row["status"])),
            attempt_count=int(row.get("attempt_count") or 0),
            provider_message_id=(
                str(row["provider_message_id"]) if row.get("provider_message_id") else None
            ),
            latency_ms=int(row["latency_ms"]) if row.get("latency_ms") is not None else None,
            next_attempt_at=(
                self._parse_dt(row["next_attempt_at"]) if row.get("next_attempt_at") else None
            ),
            last_error_code=str(row["last_error_code"]) if row.get("last_error_code") else None,
            request_id=str(row["request_id"]),
            created_at=self._parse_dt(row.get("created_at")),
            updated_at=self._parse_dt(row.get("updated_at")),
        )


def new_template_id() -> str:
    """Gera identificador de template."""
    return str(uuid.uuid4())
