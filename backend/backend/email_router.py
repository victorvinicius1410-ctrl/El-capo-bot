"""Contratos REST finos da central de Emails."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from backend.admin_models import AdminActor, AdminPermission
from backend.email_models import EmailTemplateUpdate
from backend.email_service import (
    EmailAuthorizationError,
    EmailError,
    EmailNotFoundError,
    EmailService,
    EmailValidationError,
)
from backend.webhook_models import DomainEventType


logger = logging.getLogger("backend-email-router")


class TemplateUpdatePayload(BaseModel):
    """Contrato de atualização de layout."""

    model_config = ConfigDict(extra="forbid")

    subject: str = Field(min_length=1, max_length=200)
    html_body: str = Field(min_length=1, max_length=200_000)
    is_enabled: bool = False

    def to_domain(self) -> EmailTemplateUpdate:
        """Converte para o modelo de domínio."""
        return EmailTemplateUpdate(
            subject=self.subject,
            html_body=self.html_body,
            is_enabled=self.is_enabled,
        )


class PreviewPayload(BaseModel):
    """Preview opcional com HTML ainda não salvo."""

    model_config = ConfigDict(extra="forbid")

    subject: str | None = Field(default=None, max_length=200)
    html_body: str | None = Field(default=None, max_length=200_000)


def create_email_router(
    service: EmailService,
    require_admin_user: Callable[..., Any],
) -> APIRouter:
    """
    Monta rotas administrativas de email.

    Args:
        service: Domínio de email.
        require_admin_user: Identidade e RBAC validados pelo gateway.

    Returns:
        Router FastAPI.
    """
    router = APIRouter()

    @router.get("/admin/emails/settings")
    async def get_settings(
        request: Request,
        auth: dict[str, str] = Depends(require_admin_user),
    ) -> Response:
        """Flags do provedor sem segredos."""
        try:
            _ = _actor(auth)
            settings = service.settings()
        except EmailError as exc:
            return _error_response(exc, request)
        return JSONResponse(
            {
                "ok": True,
                "data": {
                    "enabled": settings.enabled,
                    "from_configured": settings.from_configured,
                    "provider": settings.provider,
                    "smtp_configured": settings.smtp_configured,
                    "available_variables": list(settings.available_variables),
                    "variable_descriptions": settings.variable_descriptions or {},
                    "events": list(settings.events),
                },
            }
        )

    @router.get("/admin/emails/templates")
    async def list_templates(
        request: Request,
        auth: dict[str, str] = Depends(require_admin_user),
    ) -> Response:
        """Lista templates do tenant."""
        try:
            items = await service.list_templates(_actor(auth))
        except EmailError as exc:
            return _error_response(exc, request)
        return JSONResponse(
            {"ok": True, "data": [_template_view(item) for item in items]}
        )

    @router.get("/admin/emails/templates/{event_type}")
    async def get_template(
        event_type: DomainEventType,
        request: Request,
        auth: dict[str, str] = Depends(require_admin_user),
    ) -> Response:
        """Detalhe de um template."""
        try:
            template = await service.get_template(_actor(auth), event_type)
        except EmailError as exc:
            return _error_response(exc, request)
        return JSONResponse({"ok": True, "data": _template_view(template)})

    @router.put("/admin/emails/templates/{event_type}")
    @router.patch("/admin/emails/templates/{event_type}")
    async def update_template(
        event_type: DomainEventType,
        payload: TemplateUpdatePayload,
        request: Request,
        auth: dict[str, str] = Depends(require_admin_user),
    ) -> Response:
        """Atualiza layout HTML (PUT legado; PATCH preferido)."""
        try:
            template = await service.update_template(
                _actor(auth),
                event_type,
                payload.to_domain(),
            )
        except EmailError as exc:
            return _error_response(exc, request)
        return JSONResponse({"ok": True, "data": _template_view(template)})

    @router.post("/admin/emails/templates/{event_type}/preview")
    async def preview_template(
        event_type: DomainEventType,
        payload: PreviewPayload,
        request: Request,
        auth: dict[str, str] = Depends(require_admin_user),
    ) -> Response:
        """Renderiza preview sem enviar."""
        try:
            rendered = await service.preview(
                _actor(auth),
                event_type,
                subject=payload.subject,
                html_body=payload.html_body,
            )
        except EmailError as exc:
            return _error_response(exc, request)
        return JSONResponse(
            {
                "ok": True,
                "data": {
                    "subject": rendered.subject,
                    "html_body": rendered.html_body,
                },
            }
        )

    @router.post("/admin/emails/templates/{event_type}/test", status_code=202)
    async def send_test_email(
        event_type: DomainEventType,
        request: Request,
        auth: dict[str, str] = Depends(require_admin_user),
    ) -> Response:
        """Envia teste ao email da sessão autenticada (nunca do body)."""
        recipient = (auth.get("email") or "").strip().lower()
        if not recipient:
            return _error_response(EmailValidationError("RECIPIENT_INVALID"), request)
        try:
            delivery = await service.send_test(_actor(auth), event_type, recipient)
        except EmailError as exc:
            return _error_response(exc, request)
        response = JSONResponse(
            status_code=202,
            content={"ok": True, "data": _delivery_view(delivery)},
        )
        response.headers["X-Request-ID"] = _request_id(request)
        return response

    @router.get("/admin/emails/deliveries")
    async def list_deliveries(
        request: Request,
        auth: dict[str, str] = Depends(require_admin_user),
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> Response:
        """Histórico sanitizado de entregas."""
        try:
            items = await service.list_deliveries(
                _actor(auth),
                limit=limit,
                offset=offset,
            )
        except EmailError as exc:
            return _error_response(exc, request)
        return JSONResponse(
            {"ok": True, "data": [_delivery_view(item) for item in items]}
        )

    return router


def _actor(auth: dict[str, str]) -> AdminActor:
    """Converte identidade segura em ator de domínio."""
    known = {permission.value for permission in AdminPermission}
    if auth.get("is_admin") == "true":
        permissions = frozenset(AdminPermission)
    else:
        permissions = frozenset(
            AdminPermission(value)
            for value in auth.get("permissions", "").split(",")
            if value in known
        )
    return AdminActor(
        user_id=auth["user_id"],
        company_id=auth["company_id"],
        permissions=permissions,
        manageable_role_ids=(
            None
            if auth.get("is_admin") == "true" or auth.get("manageable_role_ids") == "*"
            else frozenset(
                value
                for value in auth.get("manageable_role_ids", "").split(",")
                if value
            )
        ),
    )


def _template_view(template: Any) -> dict[str, Any]:
    """Serializa template para a UI."""
    return {
        "id": template.id,
        "event_type": template.event_type.value,
        "subject": template.subject,
        "html_body": template.html_body,
        "is_enabled": template.is_enabled,
        "created_at": template.created_at.isoformat(),
        "updated_at": template.updated_at.isoformat(),
    }


def _delivery_view(delivery: Any) -> dict[str, Any]:
    """Serializa entrega sem PII do destinatário."""
    return {
        "id": delivery.id,
        "event_id": delivery.event_id,
        "event_type": delivery.event_type.value,
        "subject": delivery.subject,
        "status": delivery.status.value,
        "attempt_count": delivery.attempt_count,
        "latency_ms": delivery.latency_ms,
        "last_error_code": delivery.last_error_code,
        "request_id": delivery.request_id,
        "created_at": delivery.created_at.isoformat(),
        "updated_at": delivery.updated_at.isoformat(),
    }


def _request_id(request: Request) -> str:
    """Correlation id da requisição."""
    return request.headers.get("x-request-id") or getattr(
        request.state,
        "request_id",
        "unknown",
    )


def _error_response(exc: EmailError, request: Request) -> JSONResponse:
    """Mapeia erros de domínio para o envelope padrão."""
    request_id = _request_id(request)
    if isinstance(exc, EmailAuthorizationError):
        status, code, message = 403, "FORBIDDEN", "Sem permissão"
    elif isinstance(exc, EmailNotFoundError):
        status, code, message = 404, "NOT_FOUND", "Template não encontrado"
    elif isinstance(exc, EmailValidationError):
        status, code, message = 400, "VALIDATION_ERROR", str(exc)
    else:
        logger.error("email.router_error request_id=%s", request_id, exc_info=True)
        status, code, message = 500, "EMAIL_ERROR", "Erro interno"
    response = JSONResponse(
        status_code=status,
        content={
            "error": {
                "code": code,
                "message": message,
                "request_id": request_id,
            }
        },
    )
    response.headers["X-Request-ID"] = request_id
    return response
