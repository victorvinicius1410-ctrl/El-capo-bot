"""Contratos REST finos da central de Webhooks e API."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from backend.account_link_service import SupabaseAccountLinkService
from backend.admin_models import AdminActor, AdminPermission
from backend.webhook_models import DomainEventType, WebhookEndpointCreate
from backend.webhook_service import (
    WebhookAuthorizationError,
    WebhookError,
    WebhookNotFoundError,
    WebhookService,
)


logger = logging.getLogger("backend-webhook-router")


class EndpointCreatePayload(BaseModel):
    """Contrato de criação de um destino."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    url: str = Field(min_length=1, max_length=2048)
    subscribed_events: set[DomainEventType] = Field(min_length=1)

    def to_domain(self) -> WebhookEndpointCreate:
        """Converte o contrato HTTP no modelo de domínio."""
        return WebhookEndpointCreate(
            name=self.name,
            url=self.url,
            subscribed_events=frozenset(self.subscribed_events),
        )


class EndpointUpdatePayload(BaseModel):
    """Contrato parcial de atualização."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    url: str | None = Field(default=None, min_length=1, max_length=2048)
    subscribed_events: set[DomainEventType] | None = None
    is_active: bool | None = None


class TestDeliveryPayload(BaseModel):
    """Evento escolhido para uma entrega de teste."""

    model_config = ConfigDict(extra="forbid")

    event_type: DomainEventType = DomainEventType.PURCHASE_COMPLETED


class PasswordRecoveryPayload(BaseModel):
    """Solicitação pública com resposta anti-enumeração."""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(
        min_length=3,
        max_length=254,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    )


def create_webhook_router(
    service: WebhookService,
    require_admin_user: Callable[..., Any],
    account_links: SupabaseAccountLinkService | None = None,
) -> APIRouter:
    """
    Cria rotas administrativas com dependência de autenticação.

    Args:
        service: Regras do domínio de webhooks.
        require_admin_user: Identidade e RBAC validados pelo gateway.

    Returns:
        Router pronto para inclusão na aplicação.
    """
    router = APIRouter()

    @router.post("/auth/password-recovery", status_code=202)
    async def request_password_recovery(
        payload: PasswordRecoveryPayload,
        request: Request,
    ) -> Response:
        """Gera link de uso único sem confirmar se a conta existe."""
        request_id = _request_id(request)
        if account_links is None:
            logger.warning(
                "password_recovery.links_disabled request_id=%s",
                request_id,
            )
        else:
            link = await account_links.create_recovery_link(payload.email.strip().casefold())
            if link is None:
                # A resposta é 202 de qualquer jeito (anti-enumeração); sem este
                # log, e-mail inexistente e falha de integração ficam iguais.
                logger.info(
                    "password_recovery.link_unavailable request_id=%s",
                    request_id,
                )
            else:
                event = await service.enqueue_event(
                    company_id=link.company_id,
                    event_type=DomainEventType.PASSWORD_RECOVERY_REQUESTED,
                    subject_user_id=link.user_id,
                    request_id=request_id,
                    customer={
                        "id": link.user_id,
                        "email": payload.email.strip().casefold(),
                    },
                    plan=None,
                    data={
                        "recovery_url": link.action_link,
                        "expires_in_seconds": 3600,
                    },
                )
                try:
                    await service.queue_event_deliveries(event)
                except WebhookError:
                    logger.error(
                        "password_recovery.queue.failed request_id=%s event_id=%s",
                        request_id,
                        event.id,
                        exc_info=True,
                    )
        response = JSONResponse(
            status_code=202,
            content={
                "ok": True,
                "data": {
                    "message": (
                        "Se a conta existir, as instruções de recuperação serão enviadas."
                    )
                },
            },
        )
        response.headers["X-Request-ID"] = request_id
        return response

    @router.get("/admin/webhooks/catalog")
    async def webhook_catalog(
        request: Request,
        auth: dict[str, str] = Depends(require_admin_user),
    ) -> Response:
        """Documenta eventos e exemplos sem dados reais."""
        try:
            actor = _actor(auth)
            await service.list_endpoints(actor)
        except WebhookError as exc:
            return _error_response(exc, request)
        return JSONResponse(
            {
                "ok": True,
                "data": [
                    {
                        "event_type": item.event_type.value,
                        "description": item.description,
                        "example": _catalog_example(item.event_type, item.example_data),
                    }
                    for item in service.catalog()
                ],
            }
        )

    @router.get("/admin/webhooks")
    async def list_webhooks(
        request: Request,
        auth: dict[str, str] = Depends(require_admin_user),
    ) -> Response:
        """Lista destinos do tenant sem segredos."""
        try:
            endpoints = await service.list_endpoints(_actor(auth))
        except WebhookError as exc:
            return _error_response(exc, request)
        return JSONResponse({"ok": True, "data": [_endpoint_view(item) for item in endpoints]})

    @router.post("/admin/webhooks", status_code=201)
    async def create_webhook(
        payload: EndpointCreatePayload,
        request: Request,
        auth: dict[str, str] = Depends(require_admin_user),
    ) -> Response:
        """Cria destino e revela o segredo somente nesta resposta."""
        try:
            result = await service.create_endpoint(_actor(auth), payload.to_domain())
        except WebhookError as exc:
            return _error_response(exc, request)
        return JSONResponse(
            status_code=201,
            content={
                "ok": True,
                "data": {
                    **_endpoint_view(result.endpoint),
                    "signing_secret": result.signing_secret,
                },
            },
        )

    @router.patch("/admin/webhooks/{endpoint_id}")
    async def update_webhook(
        endpoint_id: str,
        payload: EndpointUpdatePayload,
        request: Request,
        auth: dict[str, str] = Depends(require_admin_user),
    ) -> Response:
        """Atualiza destino dentro do tenant autenticado."""
        values = payload.model_dump(exclude_unset=True)
        if "subscribed_events" in values and values["subscribed_events"] is not None:
            values["subscribed_events"] = frozenset(values["subscribed_events"])
        try:
            endpoint = await service.update_endpoint(_actor(auth), endpoint_id, **values)
        except WebhookError as exc:
            return _error_response(exc, request)
        return JSONResponse({"ok": True, "data": _endpoint_view(endpoint)})

    @router.delete("/admin/webhooks/{endpoint_id}", status_code=204)
    async def delete_webhook(
        endpoint_id: str,
        request: Request,
        auth: dict[str, str] = Depends(require_admin_user),
    ) -> Response:
        """Desativa destino preservando histórico."""
        try:
            await service.deactivate_endpoint(_actor(auth), endpoint_id)
        except WebhookError as exc:
            return _error_response(exc, request)
        return Response(status_code=204)

    @router.get("/admin/webhook-deliveries")
    async def list_webhook_deliveries(
        request: Request,
        endpoint_id: str | None = None,
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        auth: dict[str, str] = Depends(require_admin_user),
    ) -> Response:
        """Lista tentativas sanitizadas e paginadas."""
        try:
            deliveries = await service.list_deliveries(
                _actor(auth),
                endpoint_id=endpoint_id,
                limit=limit,
                offset=offset,
            )
        except WebhookError as exc:
            return _error_response(exc, request)
        return JSONResponse(
            {
                "ok": True,
                "data": {
                    "items": [_delivery_view(item) for item in deliveries],
                    "limit": limit,
                    "offset": offset,
                },
            }
        )

    @router.post("/admin/webhook-deliveries/{delivery_id}/replay")
    async def replay_webhook_delivery(
        delivery_id: str,
        request: Request,
        auth: dict[str, str] = Depends(require_admin_user),
    ) -> Response:
        """Reenvia uma entrega autorizada do mesmo tenant."""
        try:
            delivery = await service.replay_delivery(_actor(auth), delivery_id)
        except WebhookError as exc:
            return _error_response(exc, request)
        return JSONResponse({"ok": True, "data": _delivery_view(delivery)})

    return router


def _actor(auth: dict[str, str]) -> AdminActor:
    """Converte identidade segura em ator de domínio."""
    known = {permission.value for permission in AdminPermission}
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
            if auth.get("manageable_role_ids") == "*"
            else frozenset(
                value
                for value in auth.get("manageable_role_ids", "").split(",")
                if value
            )
        ),
    )


def _endpoint_view(endpoint: Any) -> dict[str, Any]:
    """Serializa destino sem segredo criptografado."""
    return {
        "id": endpoint.id,
        "name": endpoint.name,
        "url": endpoint.url,
        "subscribed_events": sorted(event.value for event in endpoint.subscribed_events),
        "is_active": endpoint.is_active,
        "created_at": endpoint.created_at.isoformat(),
        "updated_at": endpoint.updated_at.isoformat(),
    }


def _delivery_view(delivery: Any) -> dict[str, Any]:
    """Serializa auditoria sem corpo nem PII."""
    return {
        "id": delivery.id,
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


def _catalog_example(event_type: DomainEventType, data: dict[str, Any]) -> dict[str, Any]:
    """Monta exemplo completo e explicitamente fictício."""
    return {
        "event_id": "evt_01JEXAMPLE",
        "event_type": event_type.value,
        "api_version": "2026-07-18",
        "occurred_at": "2026-07-18T12:00:00+00:00",
        "request_id": "req_example",
        "customer": {
            "id": "user_example",
            "name": "Cliente Exemplo",
            "email": "cliente@example.com",
            "phone": "+5511999999999",
        },
        "plan": {"id": "plan_example", "name": "Mensal"},
        "data": data,
    }


def _request_id(request: Request) -> str:
    """Obtém correlation ID do middleware."""
    return str(
        getattr(request.state, "request_id", None)
        or request.headers.get("x-request-id")
        or "request-unavailable"
    )


def _error_response(error: Exception, request: Request) -> JSONResponse:
    """Converte erro técnico em envelope seguro e rastreável."""
    if isinstance(error, WebhookAuthorizationError):
        status_code = 403
    elif isinstance(error, WebhookNotFoundError):
        status_code = 404
    else:
        status_code = 422
    code = str(error) or error.__class__.__name__
    request_id = _request_id(request)
    logger.warning(
        "webhook.request.failed request_id=%s path=%s error=%s",
        request_id,
        request.url.path,
        code,
        exc_info=True,
    )
    response = JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": "Não foi possível processar a solicitação de webhook.",
                "request_id": request_id,
            }
        },
    )
    response.headers["X-Request-ID"] = request_id
    return response
