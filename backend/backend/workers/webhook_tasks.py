"""Worker Celery standalone para entregas externas."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from typing import TYPE_CHECKING
from urllib.parse import quote

from celery import Celery
import httpx

from backend.services.encryption_service import EncryptionService
from backend.webhook_models import DeliveryStatus, DomainEventType
from backend.webhook_repository import SupabaseWebhookRepository
from backend.webhook_service import WebhookService

if TYPE_CHECKING:  # pragma: no cover - evita o ciclo email_tasks <-> webhook_tasks
    from backend.email_service import EmailService


from backend.env_prefix import env_prefixed


logger = logging.getLogger(__name__)


def _environment_value(name: str, default: str = "") -> str:
    """Obtém configuração segregada pelo ambiente atual (PROD_/DEV_/STAGING_)."""
    return env_prefixed(name, default)


celery_app = Celery(
    "elcapo-webhooks",
    broker=_environment_value("REDIS_URL", "redis://redis:6379/0"),
    backend=_environment_value("REDIS_URL", "redis://redis:6379/0"),
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    # Garante emails.deliver no mesmo processo do webhook-worker.
    # Sem isso a entrega fica forever em status=pending (fila sem consumidor).
    imports=("backend.workers.email_tasks",),
)


@lru_cache(maxsize=1)
def _emails() -> "EmailService | None":
    """
    EmailService do processo worker, ou ``None`` quando e-mail está desligado.

    Sem isto o ``WebhookService`` do worker nasce sem e-mail e tasks como
    ``trials.end`` rodam mudas. O import é tardio porque ``email_tasks`` importa
    ``celery_app`` deste módulo — no topo, o ciclo pega o módulo pela metade.
    O cache evita que cada task recrie o repositório e repita o probe de
    ``_prefer_storage``.

    Returns:
        ``EmailService`` pronto, ou ``None`` se desligado/incompleto.
    """
    from backend.workers.email_tasks import _service as _email_service

    try:
        service = _email_service()
    except (RuntimeError, ValueError):
        # Config de e-mail incompleta não pode derrubar webhooks.deliver nem trials.end.
        logger.warning("worker.emails_unavailable", exc_info=True)
        return None
    if not service.config.enabled:
        return None
    return service


def _service() -> WebhookService:
    """Monta dependências server-only do processo worker."""
    supabase_url = os.getenv("SUPABASE_URL", "").strip()
    service_role_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not supabase_url or not service_role_key:
        raise RuntimeError("Supabase não configurado para o worker")
    encryption_key = _environment_value("ENCRYPTION_KEY")
    return WebhookService(
        SupabaseWebhookRepository(supabase_url, service_role_key),
        EncryptionService(encryption_key),
        emails=_emails(),
    )


@celery_app.task(
    bind=True,
    name="webhooks.deliver",
    max_retries=6,
    autoretry_for=(),
)
def deliver_webhook(
    self,
    company_id: str,
    endpoint_id: str,
    event_id: str,
) -> dict[str, str | int | None]:
    """
    Entrega um evento sem executar I/O bloqueante no FastAPI.

    Args:
        company_id: Tenant resolvido no servidor.
        endpoint_id: Destino pertencente ao tenant.
        event_id: Evento da outbox no mesmo tenant.

    Returns:
        Estado mínimo e sem PII da entrega.

    Raises:
        Retry: Quando a política persistida indicar nova tentativa.
    """
    delivery = asyncio.run(
        _service().deliver_event(
            endpoint_id,
            event_id,
            company_id=company_id,
        )
    )
    if delivery.status == DeliveryStatus.RETRYING and delivery.next_attempt_at is not None:
        countdown = max(
            1,
            int(
                (
                    delivery.next_attempt_at
                    - delivery.updated_at
                ).total_seconds()
            ),
        )
        raise self.retry(countdown=countdown)
    return {
        "delivery_id": delivery.id,
        "status": delivery.status.value,
        "attempt_count": delivery.attempt_count,
        "response_status": delivery.response_status,
    }


async def _expire_trial(
    company_id: str,
    user_id: str,
    request_id: str,
) -> dict[str, str | bool]:
    """Expira um trial usando filtros explícitos de empresa e usuário."""
    supabase_url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    service_role_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not supabase_url or not service_role_key:
        raise RuntimeError("Supabase não configurado para expiração de trial")
    headers = {
        "apikey": service_role_key,
        "Authorization": f"Bearer {service_role_key}",
        "Content-Type": "application/json",
    }
    path = (
        "/rest/v1/user_access_profiles"
        f"?company_id=eq.{quote(company_id, safe='')}"
        f"&user_id=eq.{quote(user_id, safe='')}"
        "&account_type=eq.trial&deleted_at=is.null"
        "&select=user_id,company_id,name,email,phone,plan_id,plan_name,expires_at,grant_access"
        "&limit=1"
    )
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
        response = await client.get(f"{supabase_url}{path}", headers=headers)
        response.raise_for_status()
        rows = response.json()
        if not rows:
            return {"expired": False, "reason": "trial_not_found"}
        profile = rows[0]
        expires_at = datetime.fromisoformat(
            str(profile["expires_at"]).replace("Z", "+00:00")
        )
        now = datetime.now(timezone.utc)
        if expires_at > now:
            return {"expired": False, "reason": "trial_not_due"}
        updated = await client.patch(
            f"{supabase_url}{path.split('&select=', 1)[0]}",
            headers={**headers, "Prefer": "return=representation"},
            json={
                "grant_access": False,
                "plan_status": "expired",
                "updated_at": now.isoformat(),
            },
        )
        updated.raise_for_status()

    service = _service()
    event = await service.enqueue_event(
        company_id=company_id,
        event_type=DomainEventType.TRIAL_ENDED,
        subject_user_id=user_id,
        request_id=request_id,
        customer={
            "id": user_id,
            "name": profile.get("name"),
            "email": profile.get("email"),
            "phone": profile.get("phone"),
        },
        plan=(
            {"id": profile.get("plan_id"), "name": profile.get("plan_name")}
            if profile.get("plan_id") or profile.get("plan_name")
            else None
        ),
        data={"ended_at": now.isoformat()},
        event_id=str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"trial-ended:{company_id}:{user_id}:{expires_at.isoformat()}",
            )
        ),
        occurred_at=now,
    )
    await service.queue_event_deliveries(event)
    return {"expired": True, "event_id": event.id}


@celery_app.task(name="trials.end")
def end_trial(company_id: str, user_id: str, request_id: str) -> dict[str, str | bool]:
    """
    Expira um trial no horário agendado sem bloquear o gateway.

    Args:
        company_id: Tenant confiável persistido no cadastro.
        user_id: Usuário do mesmo tenant.
        request_id: Correlação do início do trial.

    Returns:
        Resultado mínimo do encerramento.
    """
    return asyncio.run(_expire_trial(company_id, user_id, request_id))


# Import tardio: registra `emails.deliver` no mesmo Celery app do webhook-worker.
# Sem este import, o worker só conhece webhooks.deliver/trials.end e os e-mails
# ficam eternamente em status=pending (nunca enviados de verdade).
from backend.workers import email_tasks as _email_tasks  # noqa: E402,F401