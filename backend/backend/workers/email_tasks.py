"""Worker Celery para envio de emails nativos."""

from __future__ import annotations

import asyncio
import logging
import os

from backend.email_models import EmailDeliveryStatus
from backend.email_repository import SupabaseEmailRepository
from backend.email_service import EmailConfig, EmailService

logger = logging.getLogger(__name__)


def _service() -> EmailService:
    """
    Monta dependências server-only do processo worker.

    Returns:
        EmailService com repositório Supabase e config do ambiente.

    Raises:
        RuntimeError: Se SUPABASE_URL ou SUPABASE_SERVICE_ROLE_KEY ausentes.
    """
    supabase_url = os.getenv("SUPABASE_URL", "").strip()
    service_role_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not supabase_url or not service_role_key:
        raise RuntimeError("Supabase não configurado para o worker de email")
    frontend_url = os.getenv("FRONTEND_URL", "").strip()
    if not frontend_url:
        # Sem isso os CTAs dos e-mails do worker apontam para localhost.
        frontend_url = "http://localhost:5173"
        logger.warning("worker.frontend_url_default url=%s", frontend_url)
    return EmailService(
        SupabaseEmailRepository(supabase_url, service_role_key),
        EmailConfig.from_environment(frontend_url),
    )


# Sempre reutiliza o app do webhook-worker (mesma fila Redis / mesmo processo).
from backend.workers.webhook_tasks import celery_app  # noqa: E402


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
