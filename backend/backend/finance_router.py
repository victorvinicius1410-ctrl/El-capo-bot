"""Rotas finas do catálogo, ledger e integração Cakto."""

from __future__ import annotations

import logging
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from backend.admin_models import AdminActor, AdminPermission
from backend.cakto_service import (
    CaktoAuthenticationError,
    CaktoConfigurationError,
    CaktoService,
)
from backend.finance_models import BillingPlanCreate, BillingPlanUpdate
from backend.finance_service import (
    FinanceAuthorizationError,
    FinanceError,
    FinanceNotFoundError,
    FinanceService,
    FinanceValidationError,
)


logger = logging.getLogger("backend-finance-router")


class PlanCreatePayload(BaseModel):
    """Contrato administrativo de criação de plano."""

    model_config = ConfigDict(extra="forbid")

    slug: str
    name: str
    description: str = ""
    price: Decimal = Field(ge=0)
    currency: str = "BRL"
    billing_interval_months: int = Field(ge=1)
    features: list[str] = Field(default_factory=list)
    is_featured: bool = False
    is_active: bool = False
    display_order: int = 0
    cakto_product_id: str | None = None
    cakto_offer_id: str | None = None
    checkout_url: str | None = None

    def to_domain(self) -> BillingPlanCreate:
        """Converte o contrato HTTP no modelo de domínio."""
        return BillingPlanCreate(
            **{
                **self.model_dump(),
                "features": tuple(self.features),
            }
        )


class PlanUpdatePayload(BaseModel):
    """Contrato parcial de atualização de plano."""

    model_config = ConfigDict(extra="forbid")

    slug: str | None = None
    name: str | None = None
    description: str | None = None
    price: Decimal | None = Field(default=None, ge=0)
    currency: str | None = None
    billing_interval_months: int | None = Field(default=None, ge=1)
    features: list[str] | None = None
    is_featured: bool | None = None
    is_active: bool | None = None
    display_order: int | None = None
    cakto_product_id: str | None = None
    cakto_offer_id: str | None = None
    checkout_url: str | None = None

    def to_domain(self) -> BillingPlanUpdate:
        """Converte somente campos fornecidos no modelo parcial."""
        values = self.model_dump(exclude_unset=True)
        if "features" in values and values["features"] is not None:
            values["features"] = tuple(values["features"])
        return BillingPlanUpdate(**values)


def create_finance_router(
    service: FinanceService,
    cakto: CaktoService,
    require_authenticated_user: Callable[..., Any],
    require_admin_user: Callable[..., Any],
    *,
    public_webhook_url: str,
) -> APIRouter:
    """
    Cria contratos financeiros com dependências da aplicação.

    Args:
        service: Regras do domínio financeiro.
        cakto: Validador e cliente HTTP da Cakto.
        require_authenticated_user: Identidade de cliente validada.
        require_admin_user: Identidade administrativa e RBAC validados.
        public_webhook_url: URL pública exibida sem segredo.

    Returns:
        Router pronto para inclusão no FastAPI.
    """
    router = APIRouter()

    @router.get("/billing/plans")
    async def list_billing_plans(
        auth: dict[str, str] = Depends(require_authenticated_user),
    ) -> dict[str, Any]:
        """Lista catálogo ativo do tenant autenticado."""
        plans = await service.list_client_plans(auth["company_id"])
        return {"ok": True, "data": [_plan_view(plan) for plan in plans]}

    @router.get("/billing/history")
    async def billing_history(
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        auth: dict[str, str] = Depends(require_authenticated_user),
    ) -> dict[str, Any]:
        """Lista histórico paginado do próprio cliente."""
        events = await service.get_history(
            auth["company_id"],
            auth["user_id"],
            limit=limit,
            offset=offset,
        )
        return {
            "ok": True,
            "data": {
                "items": [_event_view(event) for event in events],
                "limit": limit,
                "offset": offset,
            },
        }

    @router.get("/billing/checkout/{plan_id}")
    async def billing_checkout(
        plan_id: str,
        request: Request,
        auth: dict[str, str] = Depends(require_authenticated_user),
    ) -> Response:
        """Retorna somente a URL segura do checkout do mesmo tenant."""
        try:
            checkout_url = await service.get_checkout_url(auth["company_id"], plan_id)
        except FinanceError as exc:
            return _error_response(exc, request)
        return JSONResponse({"ok": True, "data": {"checkout_url": checkout_url}})

    @router.get("/admin/finance/plans")
    async def list_admin_finance_plans(
        request: Request,
        auth: dict[str, str] = Depends(require_admin_user),
    ) -> Response:
        """Lista planos ativos e inativos para administração."""
        try:
            plans = await service.list_admin_plans(_actor(auth))
        except FinanceError as exc:
            return _error_response(exc, request)
        return JSONResponse({"ok": True, "data": [_plan_view(plan) for plan in plans]})

    @router.post("/admin/finance/plans", status_code=201)
    async def create_admin_finance_plan(
        payload: PlanCreatePayload,
        request: Request,
        auth: dict[str, str] = Depends(require_admin_user),
    ) -> Response:
        """Cria plano com tenant e RBAC derivados da sessão."""
        try:
            plan = await service.create_plan(
                _actor(auth),
                payload.to_domain(),
                cakto,
            )
        except (FinanceError, CaktoConfigurationError) as exc:
            return _error_response(exc, request)
        except httpx.HTTPError:
            return _error_response(
                FinanceValidationError("CAKTO_API_UNAVAILABLE"),
                request,
                status_code=502,
            )
        except ValueError as exc:
            return _error_response(FinanceValidationError(str(exc)), request)
        return JSONResponse(
            status_code=201,
            content={"ok": True, "data": _plan_view(plan)},
        )

    @router.patch("/admin/finance/plans/{plan_id}")
    async def update_admin_finance_plan(
        plan_id: str,
        payload: PlanUpdatePayload,
        request: Request,
        auth: dict[str, str] = Depends(require_admin_user),
    ) -> Response:
        """Atualiza plano do tenant autenticado."""
        try:
            plan = await service.update_plan(_actor(auth), plan_id, payload.to_domain())
        except FinanceError as exc:
            return _error_response(exc, request)
        return JSONResponse({"ok": True, "data": _plan_view(plan)})

    @router.delete("/admin/finance/plans/{plan_id}", status_code=204)
    async def delete_admin_finance_plan(
        plan_id: str,
        request: Request,
        auth: dict[str, str] = Depends(require_admin_user),
    ) -> Response:
        """Desativa plano sem remover o histórico."""
        try:
            await service.deactivate_plan(_actor(auth), plan_id)
        except FinanceError as exc:
            return _error_response(exc, request)
        return Response(status_code=204)

    @router.get("/admin/finance/metrics")
    async def finance_metrics(
        request: Request,
        days: int = Query(default=30, ge=1, le=3660),
        auth: dict[str, str] = Depends(require_admin_user),
    ) -> Response:
        """Retorna indicadores financeiros BRL por empresa e período."""
        try:
            metrics = await service.get_metrics(_actor(auth), days=days)
        except FinanceError as exc:
            return _error_response(exc, request)
        return JSONResponse({"ok": True, "data": metrics})

    @router.get("/admin/finance/settings")
    async def finance_settings(
        request: Request,
        auth: dict[str, str] = Depends(require_admin_user),
    ) -> Response:
        """Retorna somente flags e timestamps não secretos."""
        try:
            settings = await service.get_settings(
                _actor(auth),
                cakto,
                webhook_url=public_webhook_url,
            )
        except FinanceError as exc:
            return _error_response(exc, request)
        return JSONResponse({"ok": True, "data": settings})

    @router.post("/admin/finance/reconcile")
    async def reconcile_finance(
        request: Request,
        auth: dict[str, str] = Depends(require_admin_user),
    ) -> Response:
        """Reconcilia uma página da API Cakto sem bloquear o event loop."""
        try:
            result = await service.reconcile(_actor(auth), cakto, page_size=50)
        except (FinanceError, CaktoConfigurationError) as exc:
            return _error_response(exc, request)
        except httpx.HTTPError:
            return _error_response(
                FinanceValidationError("CAKTO_API_UNAVAILABLE"),
                request,
                status_code=502,
            )
        return JSONResponse({"ok": True, "data": result})

    @router.post("/webhooks/cakto")
    async def cakto_webhook(payload: dict[str, Any], request: Request) -> Response:
        """
        Recebe webhook público e valida o secret enviado no próprio body.

        A Cakto não documenta assinatura criptográfica do conteúdo; por isso a
        integração usa o secret do payload com ``hmac.compare_digest``.
        """
        request_id = _request_id(request)
        try:
            cakto.validate_webhook_payload(payload)
            result = await service.process_cakto_event(
                payload,
                request_id=request_id,
            )
        except CaktoAuthenticationError as exc:
            return _error_response(exc, request, status_code=401)
        except CaktoConfigurationError as exc:
            return _error_response(exc, request, status_code=503)
        except FinanceError as exc:
            return _error_response(exc, request)
        response = JSONResponse(
            status_code=202,
            content={
                "ok": True,
                "data": {
                    "accepted": True,
                    "processed": result.processed,
                    "duplicate": result.duplicate,
                },
            },
        )
        response.headers["X-Request-ID"] = request_id
        return response

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


def _plan_view(plan: Any) -> dict[str, Any]:
    """Serializa plano sem informação sensível."""
    return {
        "id": plan.id,
        "company_id": plan.company_id,
        "slug": plan.slug,
        "name": plan.name,
        "description": plan.description,
        "price": float(plan.price),
        "currency": plan.currency,
        "billing_interval_months": plan.billing_interval_months,
        "features": list(plan.features),
        "is_featured": plan.is_featured,
        "is_active": plan.is_active,
        "display_order": plan.display_order,
        "cakto_product_id": plan.cakto_product_id,
        "cakto_offer_id": plan.cakto_offer_id,
        "checkout_url": plan.checkout_url,
    }


def _event_view(event: Any) -> dict[str, Any]:
    """Serializa evento do próprio cliente sem metadata interna."""
    return {
        "id": event.id,
        "event": event.event_name,
        "status": event.status.value,
        "amount": float(event.amount),
        "currency": event.currency,
        "occurred_at": event.occurred_at.isoformat(),
    }


def _request_id(request: Request) -> str:
    """Obtém correlation ID criado pelo middleware ou recebido no header."""
    return str(
        getattr(request.state, "request_id", None)
        or request.headers.get("x-request-id")
        or "request-unavailable"
    )


def _error_response(
    error: Exception,
    request: Request,
    *,
    status_code: int | None = None,
) -> JSONResponse:
    """Converte erros seguros no envelope REST com request_id."""
    resolved_status = status_code
    if resolved_status is None:
        if isinstance(error, FinanceAuthorizationError):
            resolved_status = 403
        elif isinstance(error, FinanceNotFoundError):
            resolved_status = 404
        else:
            resolved_status = 422
    code = str(error) or error.__class__.__name__
    request_id = _request_id(request)
    logger.warning(
        "finance.request.failed request_id=%s path=%s error=%s",
        request_id,
        request.url.path,
        code,
        exc_info=True,
    )
    response = JSONResponse(
        status_code=resolved_status,
        content={
            "error": {
                "code": code,
                "message": "Não foi possível processar a solicitação financeira.",
                "request_id": request_id,
            }
        },
    )
    response.headers["X-Request-ID"] = request_id
    return response
