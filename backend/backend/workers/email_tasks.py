"""Worker Celery para envio de emails nativos."""

from __future__ import annotations

import asyncio
import os

from celery import Celery

from backend.email_models import EmailDeliveryStatus
from backend.email_repository import SupabaseEmailRepository
from backend.email_service import EmailConfig, EmailService
from backend.env_prefix import env_prefixed


def _environment_value(name: str, default: str = "") -> str:
    """Obtém configuração segregada pelo ambiente atual (PROD_/DEV_/STAGING_)."""
    return env_prefixed(name, default)


celery_app = Celery(
    "elcapo-emails",
    broker=_environment_value("REDIS_URL", "redis://redis:6379/0"),
    backend=_environment_value("REDIS_URL", "redis://redis:6379/0"),
)

# Reutiliza o app do worker de webhooks quando importado no mesmo processo.
try:
    from backend.workers.webhook_tasks import celery_app as shared_celery_app

    celery_app = shared_celery_app
except Exception:
    celery_app.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        task_acks_late=True,
        worker_prefetch_multiplier=1,
    )


def _service() -> EmailService:
    """Monta dependências server-only do processo worker."""
    supabase_url = os.getenv("SUPABASE_URL", "").strip()
    service_role_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not supabase_url or not service_role_key:
        raise RuntimeError("Supabase não configurado para o worker de email")
    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:5173").strip()
    return EmailService(
        SupabaseEmailRepository(supabase_url, service_role_key),
        EmailConfig.from_environment(frontend_url),
    )


@celery_app.task(
    bind=True,
    name="emails.deliver",
    max_retries=6,
    autoretry_for=(),
)
def deliver_email(
    self,
    company_id: str,
    delivery_id: str,
    recipient_email: str,
    subject: str,
    html_body: str,
) -> dict[str, str | int | None]:
    """
    Envia um email sem I/O bloqueante no FastAPI.

    Args:
        company_id: Tenant resolvido no servidor.
        delivery_id: Entrega persistida no tenant.
        recipient_email: Destinatário (somente na fila, não no banco em texto).
        subject: Assunto já renderizado.
        html_body: HTML já renderizado.

    Returns:
        Estado mínimo sem PII.
    """
    service = _service()
    delivery = asyncio.run(service.repository.get_delivery(company_id, delivery_id))
    if delivery is None:
        return {"delivery_id": delivery_id, "status": "missing"}
    updated = asyncio.run(
        service.deliver_now(
            delivery,
            recipient_email=recipient_email,
            html_body=html_body,
            subject=subject,
        )
    )
    if updated.status == EmailDeliveryStatus.RETRYING and updated.next_attempt_at is not None:
        countdown = max(
            1,
            int((updated.next_attempt_at - updated.updated_at).total_seconds()),
        )
        raise self.retry(countdown=countdown)
    return {
        "delivery_id": updated.id,
        "status": updated.status.value,
        "attempt_count": updated.attempt_count,
        "latency_ms": updated.latency_ms,
    }
