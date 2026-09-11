"""Rotas finas para gestão administrativa e acesso do usuário."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.admin_models import (
    AccountType,
    AdminActor,
    AdminCreate,
    AdminPermission,
    AdminUpdate,
    ApprovalStatus,
    ClientCreate,
    ClientRecord,
    ClientUpdate,
    MarketingMode,
    PaymentStatus,
)
from backend.admin_clients_cache import (
    clear_admin_clients_cache_for_company,
    read_admin_clients_cache,
    write_admin_clients_cache,
)
from backend.admin_dashboard_cache import (
    clear_admin_dashboard_cache_for_company,
    read_admin_dashboard_cache,
    write_admin_dashboard_cache,
)
from backend.admin_dashboard_service import calculate_admin_dashboard
from backend.admin_dashboard_warm import AdminDashboardWarmer
from backend.brasilia_time import history_cutoff
from backend.admin_service import (
    AdminManagementService,
    AuthorizationError,
    NotFoundError,
    ValidationError,
)
from backend.marketing_simulation_service import MarketingSimulationService

AssetPayoutResolver = Callable[[str, str], Awaitable[int | None]]
RobotHistoryDeleter = Callable[..., Any]
HistoryLoader = Callable[[str, int], list[dict[str, Any]]]
HistoryBatchLoader = Callable[[list[str], int], dict[str, list[dict[str, Any]]]]
logger = logging.getLogger("backend-admin-router")


def _invalidate_admin_list_caches(company_id: str) -> None:
    """Limpa caches de listagem/dashboard após mutação de clientes no tenant."""
    clear_admin_clients_cache_for_company(company_id)
    clear_admin_dashboard_cache_for_company(company_id)


async def _load_all_company_clients(
    service: AdminManagementService,
    actor: AdminActor,
) -> list[ClientRecord]:
    """
    Carrega todos os clientes do tenant com páginas paralelas após a 1ª.

    A primeira página define se há mais dados; as seguintes vão em lotes de 3
    para reduzir a latência serial do dashboard sob muitos leads.
    """
    first = await service.list_clients(
        actor,
        include_deleted=True,
        limit=100,
        offset=0,
    )
    clients = list(first)
    if len(first) < 100:
        return clients

    offset = 100
    while True:
        pages = await asyncio.gather(
            service.list_clients(
                actor, include_deleted=True, limit=100, offset=offset
            ),
            service.list_clients(
                actor, include_deleted=True, limit=100, offset=offset + 100
            ),
            service.list_clients(
                actor, include_deleted=True, limit=100, offset=offset + 200
            ),
        )
        for page in pages:
            clients.extend(page)
        if any(len(page) < 100 for page in pages):
            break
        offset += 300
    return clients


async def compute_admin_dashboard_payload(
    *,
    service: AdminManagementService,
    company_id: str,
    days: int,
    history_loader: HistoryLoader,
    history_batch_loader: HistoryBatchLoader | None = None,
) -> dict[str, Any]:
    """
    Computa o payload do dashboard admin para um tenant (sem auth HTTP).

    Usado pelo endpoint ``GET /admin/dashboard`` e pelo
    ``AdminDashboardWarmer`` em background.

    Args:
        service: Serviço administrativo do tenant.
        company_id: Empresa autenticada (nunca do body do frontend).
        days: Período do dashboard (1–365).
        history_loader: Leitor de histórico por usuário.
        history_batch_loader: Opcional — batch de histórico (preferido).

    Returns:
        Dict com KPIs do dashboard (mesmo formato de ``calculate_admin_dashboard``).
    """
    actor = AdminActor(
        user_id="__dashboard_warmer__",
        company_id=company_id,
        permissions=frozenset(),
        manageable_role_ids=None,
    )
    clients = await _load_all_company_clients(service, actor)

    user_ids = [client.user_id for client in clients]
    if history_batch_loader is not None:
        histories_by_user = await asyncio.to_thread(
            history_batch_loader,
            user_ids,
            days,
        )
    else:
        history_pages = await asyncio.gather(
            *(
                asyncio.to_thread(history_loader, client.user_id, days)
                for client in clients
            )
        )
        histories_by_user = {
            client.user_id: history
            for client, history in zip(clients, history_pages, strict=True)
        }
    for client in clients:
        histories_by_user.setdefault(client.user_id, [])

    period_end = datetime.now(timezone.utc)
    period_start = history_cutoff(days, period_end)
    revenue_events, lifecycle_events = await asyncio.gather(
        service.repository.list_revenue_events(
            actor.company_id,
            period_start,
            period_end,
        ),
        service.repository.list_lifecycle_events(
            actor.company_id,
            period_start,
            period_end,
        ),
    )
    return calculate_admin_dashboard(
        clients,
        histories_by_user,
        days=days,
        revenue_events=revenue_events,
        lifecycle_events=lifecycle_events,
        now=period_end,
    )


class ClientCreatePayload(BaseModel):
    """Payload validado para criação de cliente."""

    model_config = ConfigDict(extra="forbid")

    name: str
    email: str
    phone: str | None = None
    trader_id: str
    password: str
    account_type: AccountType
    trial_days: int | None = Field(default=None, ge=1, le=365)
    payment_status: PaymentStatus
    plan_id: str | None = None
    marketing_mode: MarketingMode | None = None
    marketing_win_rate: int | None = Field(default=None, ge=0, le=100)


class ClientUpdatePayload(BaseModel):
    """Payload parcial para edição de cliente."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    email: str | None = None
    phone: str | None = None
    trader_id: str | None = None
    password: str | None = None
    account_type: AccountType | None = None
    trial_days: int | None = Field(default=None, ge=1, le=365)
    payment_status: PaymentStatus | None = None
    plan_id: str | None = None
    marketing_mode: MarketingMode | None = None
    marketing_win_rate: int | None = Field(default=None, ge=0, le=100)


class AdminCreatePayload(BaseModel):
    """Payload de criação de administrador."""

    model_config = ConfigDict(extra="forbid")

    name: str
    email: str
    job_title: str
    password: str
    permissions: list[AdminPermission]
    manageable_role_ids: list[str] | None = None
    manageable_roles: list[str] | Literal["all"] | None = None


class AdminUpdatePayload(BaseModel):
    """Payload parcial de administrador."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    email: str | None = None
    job_title: str | None = None
    password: str | None = None
    permissions: list[AdminPermission] | None = None
    manageable_role_ids: list[str] | None = None
    manageable_roles: list[str] | Literal["all"] | None = None


class ImpersonationCreatePayload(BaseModel):
    """Solicitação auditável para acessar uma conta em modo de suporte."""

    model_config = ConfigDict(extra="forbid")

    target_user_id: str
    reason: str
    duration_minutes: int = Field(default=15, ge=1, le=15)


class ApproveLeadPayload(BaseModel):
    """Payload para aprovar lead liberando N dias de acesso."""

    model_config = ConfigDict(extra="forbid")

    access_days: int = Field(ge=1, le=365)


class AdminUserCreatePayload(BaseModel):
    """Contrato novo para criação segura de cliente administrativo."""

    model_config = ConfigDict(extra="forbid")

    name: str
    email: str
    phone: str | None = None
    trader_id: str
    password: str
    customer_type: AccountType
    trial_days: int | None = Field(default=None, ge=1, le=365)
    plan_id: str | None = None
    payment_status: PaymentStatus
    marketing_target_win_rate: int | None = Field(default=None, ge=0, le=100)


class AdminUserUpdatePayload(BaseModel):
    """Contrato parcial novo para edição de cliente."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    email: str | None = None
    phone: str | None = None
    trader_id: str | None = None
    password: str | None = None
    customer_type: AccountType | None = None
    trial_days: int | None = Field(default=None, ge=1, le=365)
    plan_id: str | None = None
    payment_status: PaymentStatus | None = None
    marketing_target_win_rate: int | None = Field(default=None, ge=0, le=100)


class SupportSessionCreatePayload(BaseModel):
    """Contrato de emissão de sessão temporária de suporte."""

    model_config = ConfigDict(extra="forbid")

    subject_user_id: str
    reason: str = Field(min_length=10)


class SimulatedTradeCreatePayload(BaseModel):
    """Valores opcionais definidos antes de exibir a operação no overlay."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    amount: float | None = Field(default=None, gt=0)
    payout: int | None = Field(default=None, ge=0, le=100)
    asset: str | None = Field(default=None, min_length=1, max_length=50)
    direction: Literal["CALL", "PUT"] | None = None
    result: Literal["WIN", "LOSS"] | None = None
    created_at: str | None = Field(default=None, min_length=10, max_length=64)

    @field_validator("asset")
    @classmethod
    def validate_create_asset(cls, value: str | None) -> str | None:
        """Normaliza ativo opcional e rejeita string só com espaços."""
        if value is None:
            return None
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("Ativo não pode ser vazio")
        return normalized

    @field_validator("created_at")
    @classmethod
    def validate_create_created_at(cls, value: str | None) -> str | None:
        """Normaliza data/hora ISO8601 para UTC; None = usar agora no serviço."""
        if value is None:
            return None
        try:
            return MarketingSimulationService.normalize_created_at(value)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc


class GenerateScoreHistoryPayload(BaseModel):
    """Contrato para gerar o histórico automaticamente a partir do placar."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    wins: int = Field(ge=0, le=100)
    losses: int = Field(ge=0, le=100)
    amount: float = Field(gt=0)
    payout: int | None = Field(default=None, ge=0, le=100)
    asset: str | None = Field(default=None, min_length=1, max_length=50)
    period: Literal["M1", "M5", "M15"] = "M5"

    @field_validator("asset")
    @classmethod
    def validate_score_asset(cls, value: str | None) -> str | None:
        """Normaliza ativo opcional do gerador de placar."""
        if value is None:
            return None
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("Ativo não pode ser vazio")
        return normalized

    @model_validator(mode="after")
    def validate_total_trades(self) -> GenerateScoreHistoryPayload:
        """Garante ao menos uma operação e respeita o teto do serviço."""
        total = self.wins + self.losses
        if total <= 0:
            raise ValueError("Informe ao menos uma operação no placar")
        if total > MarketingSimulationService.MAX_GENERATED_TRADES:
            raise ValueError(
                f"Placar máximo de {MarketingSimulationService.MAX_GENERATED_TRADES} operações"
            )
        return self


class SimulatedTradeUpdatePayload(BaseModel):
    """Campos editáveis de uma operação sintética existente."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    result: Literal["WIN", "LOSS"] | None = None
    profit: float | None = None
    amount: float | None = Field(default=None, gt=0)
    asset: str | None = Field(default=None, min_length=1, max_length=50)
    direction: Literal["CALL", "PUT"] | None = None
    payout: int | None = Field(default=None, ge=0, le=100)

    @field_validator("asset")
    @classmethod
    def validate_asset(cls, value: str | None) -> str | None:
        """
        Remove espaços e rejeita nome de ativo vazio.

        Args:
            value: Nome opcional recebido no PATCH.

        Returns:
            Nome normalizado ou None.

        Raises:
            ValueError: Quando o nome contém somente espaços.
        """
        if value is None:
            return None
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("Ativo não pode ser vazio")
        return normalized

    @model_validator(mode="after")
    def require_changes(self) -> SimulatedTradeUpdatePayload:
        """
        Exige ao menos um campo editável no PATCH.

        Returns:
            O próprio payload validado.

        Raises:
            ValueError: Quando nenhum campo foi enviado ou um campo é nulo.
        """
        if not self.model_fields_set:
            raise ValueError("Informe ao menos um campo para atualizar")
        if any(getattr(self, field_name) is None for field_name in self.model_fields_set):
            raise ValueError("Campos informados não podem ser nulos")
        return self


def create_admin_router(
    service: AdminManagementService,
    require_authenticated_user: Callable[..., Any],
    require_admin_user: Callable[..., Any],
    require_secure_authenticated_user: Callable[..., Any],
    require_secure_admin_user: Callable[..., Any],
    history_loader: Callable[[str, int], list[dict[str, Any]]],
    *,
    app_env: str,
    marketing_display_sync: Callable[[str, list[dict[str, Any]], dict[str, Any]], None] | None = None,
    asset_payout_resolver: AssetPayoutResolver | None = None,
    robot_history_deleter: RobotHistoryDeleter | None = None,
    marketing_score_remover: Callable[[str, dict[str, Any]], None] | None = None,
    history_batch_loader: Callable[[list[str], int], dict[str, list[dict[str, Any]]]] | None = None,
    access_profile_invalidator: Callable[[str], None] | None = None,
    dashboard_warmer: AdminDashboardWarmer | None = None,
) -> APIRouter:
    """
    Cria router administrativo com dependências fornecidas pela aplicação.

    Args:
        service: Serviço de regras administrativas.
        require_authenticated_user: Dependência de sessão.
        require_admin_user: Dependência administrativa.
        history_loader: Leitor de histórico real já existente.
        app_env: Ambiente usado para hardening do cookie.
        marketing_display_sync: Opcional — alinha placar/histórico do robô ao
            histórico editável do Shift+O (conta marketing).
        asset_payout_resolver: Opcional — consulta o payout real do ativo
            (user_id, symbol) → percentual 0-100.
        robot_history_deleter: Opcional — remove operação do histórico `/robot`
            por order_id (ex.: ordens ao vivo da Bullex na conta marketing).
        marketing_score_remover: Opcional — subtrai WIN/LOSS/lucro do placar
            após exclusão (uma vez, mesmo limpando UUID + broker_order_id).
        history_batch_loader: Opcional — carrega histórico de N usuários em
            lote (evita N+1 no ``GET /admin/dashboard``).
        access_profile_invalidator: Opcional — limpa cache de auth do gateway
            após approve/update/delete (evita grant_access stale no TTL).
        dashboard_warmer: Opcional — registra o tenant para warm periódico
            do cache do dashboard (7d/30d).

    Returns:
        Router pronto para inclusão no FastAPI.
    """
    router = APIRouter()
    simulations: dict[tuple[str, str], MarketingSimulationService] = {}

    def _invalidate_access_profile(user_id: str) -> None:
        """Propaga mudança de perfil para o cache de ``authenticate``."""
        if access_profile_invalidator is None:
            return
        access_profile_invalidator(user_id)

    async def get_simulator(auth: dict[str, str]) -> MarketingSimulationService:
        """Obtém o simulador cacheado e isolado pela empresa e usuário."""
        key = (auth["company_id"], auth["user_id"])
        simulator = simulations.get(key)
        if simulator is None:
            persisted = await service.repository.list_simulated_trades(
                auth["company_id"],
                auth["user_id"],
                limit=10_000,
            )
            simulator = MarketingSimulationService(
                seed=auth["user_id"],
                target_win_rate=int(auth.get("marketing_win_rate") or "0"),
                history=persisted,
                repository=service.repository,
                company_id=auth["company_id"],
                user_id=auth["user_id"],
            )
            simulations[key] = simulator
        return simulator

    async def sync_marketing_display(
        auth: dict[str, str],
        history: list[dict[str, Any]] | None = None,
        stats: dict[str, Any] | None = None,
    ) -> None:
        """Propaga o lote informado para o overlay, sem apagar o histórico antigo."""
        if marketing_display_sync is None:
            return
        if history is None:
            history = await service.repository.list_simulated_trades(
                auth["company_id"],
                auth["user_id"],
                limit=10_000,
            )
            from backend.marketing_simulation_service import MarketingSimulationService

            history = [
                MarketingSimulationService.enrich_trade_with_strategy(dict(item))
                for item in history
            ]
            simulator = await get_simulator(auth)
            simulator.replace_history(history)
            stats = simulator.build_stats()
        marketing_display_sync(auth["user_id"], history, stats or {})

    @router.get("/me/access")
    async def get_my_access(
        auth: dict[str, str] = Depends(require_authenticated_user),
    ) -> dict[str, Any]:
        """Retorna acesso efetivo calculado no servidor."""
        grant_access = auth.get("grant_access") == "true"
        approval_status = (auth.get("approval_status") or "approved").strip().lower()
        if grant_access:
            access_status = "active"
        elif approval_status == ApprovalStatus.PENDING.value and not grant_access:
            access_status = "pending_approval"
        else:
            access_status = "inactive"
        return {
            "ok": True,
            "data": {
                "user_id": auth["user_id"],
                "company_id": auth["company_id"],
                "is_admin": auth.get("is_admin") == "true",
                "permissions": _permission_values(auth),
                "grant_access": grant_access,
                "approval_status": approval_status,
                "access_status": access_status,
                "account_type": auth.get("account_type", "client"),
                "payment_status": auth.get("payment_status", "pending"),
                "expires_at": auth.get("expires_at") or None,
                "marketing_mode": auth.get("marketing_mode") or None,
                "marketing_win_rate": (
                    int(auth["marketing_win_rate"]) if auth.get("marketing_win_rate") else None
                ),
                "allowed_routes": (
                    ["*"] if grant_access or auth.get("is_admin") == "true" else ["/feedbacks", "/payments"]
                ),
                "impersonating": auth.get("impersonating") == "true",
                "impersonation_session_id": auth.get("impersonation_session_id") or None,
                "impersonation_expires_at": auth.get("impersonation_expires_at") or None,
                "actor_user_id": auth.get("actor_user_id") or None,
            },
        }

    @router.get("/admin/overview")
    async def admin_overview(
        auth: dict[str, str] = Depends(require_secure_admin_user),
    ) -> dict[str, Any]:
        """Retorna contadores administrativos do tenant autenticado."""
        actor = _actor_from_auth(auth)
        rows = await service.list_clients(
            actor,
            include_deleted=True,
            limit=100,
            offset=0,
        )
        return {
            "ok": True,
            "data": {
                "active": sum(
                    item.account_type == AccountType.CLIENT
                    and item.grant_access
                    and item.deleted_at is None
                    for item in rows
                ),
                "trial": sum(
                    item.account_type == AccountType.TRIAL and item.deleted_at is None
                    for item in rows
                ),
                "marketing": sum(
                    item.account_type == AccountType.MARKETING and item.deleted_at is None
                    for item in rows
                ),
                "inactive": sum(
                    item.deleted_at is not None or not item.grant_access for item in rows
                ),
            },
        }

    @router.get("/admin/users")
    async def list_admin_users(
        stage: str | None = Query(default=None, pattern="^(active|trial|marketing|inactive)$"),
        search: str | None = Query(default=None, max_length=100),
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        order_by: str = Query(
            default="created_at",
            pattern="^(created_at|updated_at|name|email)$",
        ),
        order_direction: str = Query(default="desc", pattern="^(asc|desc)$"),
        auth: dict[str, str] = Depends(require_secure_admin_user),
    ) -> dict[str, Any]:
        """Lista clientes com filtros, busca, ordenação e paginação."""
        actor = _actor_from_auth(auth)
        account_type = {
            "active": AccountType.CLIENT,
            "trial": AccountType.TRIAL,
            "marketing": AccountType.MARKETING,
        }.get(stage or "")
        rows = await service.list_clients(
            actor,
            account_type=account_type,
            include_deleted=stage == "inactive",
            limit=min(100, limit + 1),
            offset=offset,
            search=search,
            order_by=order_by,
            order_direction=order_direction,
        )
        if stage == "active":
            rows = [row for row in rows if row.grant_access and row.deleted_at is None]
        elif stage == "inactive":
            rows = [row for row in rows if row.deleted_at is not None or not row.grant_access]
        visible = rows[:limit]
        return {
            "ok": True,
            "data": {
                "items": [_client_view(item) for item in visible],
                "limit": limit,
                "offset": offset,
                "has_more": len(rows) > limit,
            },
        }

    @router.get("/admin/users/{user_id}")
    async def get_admin_user(
        user_id: str,
        auth: dict[str, str] = Depends(require_secure_admin_user),
    ) -> dict[str, Any]:
        """Obtém um cliente somente dentro da empresa autenticada."""
        actor = _actor_from_auth(auth)
        record = await service.repository.get_client(actor.company_id, user_id)
        if record is None:
            raise HTTPException(status_code=404, detail="CLIENT_NOT_FOUND")
        return {"ok": True, "data": _client_view(record)}

    @router.post("/admin/users", status_code=201)
    async def create_admin_user(
        payload: AdminUserCreatePayload,
        request: Request,
        auth: dict[str, str] = Depends(require_secure_admin_user),
    ) -> dict[str, Any]:
        """Cria cliente via Supabase Auth Admin no backend."""
        marketing_mode = (
            MarketingMode.SIMULATION
            if payload.customer_type == AccountType.MARKETING
            else None
        )
        try:
            created = await service.create_client(
                _actor_from_auth(auth),
                ClientCreate(
                    name=payload.name,
                    email=payload.email,
                    phone=payload.phone,
                    trader_id=payload.trader_id,
                    password=payload.password,
                    account_type=payload.customer_type,
                    trial_days=payload.trial_days,
                    payment_status=payload.payment_status,
                    plan_id=payload.plan_id,
                    marketing_mode=marketing_mode,
                    marketing_win_rate=payload.marketing_target_win_rate,
                ),
                request_id=request.state.request_id,
            )
        except (AuthorizationError, ValidationError) as exc:
            raise _domain_http_error(exc) from exc
        return {"ok": True, "data": _client_view(created)}

    @router.patch("/admin/users/{user_id}")
    async def update_admin_user(
        user_id: str,
        payload: AdminUserUpdatePayload,
        request: Request,
        auth: dict[str, str] = Depends(require_secure_admin_user),
    ) -> dict[str, Any]:
        """Edita cliente respeitando transições de trial, pagamento e marketing."""
        values = payload.model_dump(exclude_unset=True)
        if "customer_type" in values:
            values["account_type"] = values.pop("customer_type")
        if "marketing_target_win_rate" in values:
            values["marketing_win_rate"] = values.pop("marketing_target_win_rate")
        if values.get("account_type") == AccountType.MARKETING:
            values["marketing_mode"] = MarketingMode.SIMULATION
        try:
            updated = await service.update_client(
                _actor_from_auth(auth),
                user_id,
                ClientUpdate(**values),
                request_id=request.state.request_id,
            )
        except (AuthorizationError, ValidationError, NotFoundError) as exc:
            raise _domain_http_error(exc) from exc
        _invalidate_access_profile(user_id)
        return {"ok": True, "data": _client_view(updated)}

    @router.delete("/admin/users/{user_id}", status_code=204)
    async def delete_admin_user(
        user_id: str,
        request: Request,
        auth: dict[str, str] = Depends(require_secure_admin_user),
    ) -> Response:
        """Desativa cliente e preserva seu histórico."""
        try:
            await service.delete_client(
                _actor_from_auth(auth),
                user_id,
                request_id=request.state.request_id,
            )
        except (AuthorizationError, NotFoundError) as exc:
            raise _domain_http_error(exc) from exc
        _invalidate_access_profile(user_id)
        return Response(status_code=204)

    @router.get("/admin/users/{user_id}/history")
    async def admin_user_history(
        user_id: str,
        limit: int = Query(default=100, ge=1, le=200),
        auth: dict[str, str] = Depends(require_secure_admin_user),
    ) -> dict[str, Any]:
        """Lista eventos administrativos append-only do cliente."""
        actor = _actor_from_auth(auth)
        if not {
            AdminPermission.CLIENTS_VIEW_HISTORY,
            AdminPermission.CLIENTS_HISTORY_READ,
        }.intersection(actor.permissions):
            raise HTTPException(status_code=403, detail="FORBIDDEN")
        if await service.repository.get_client(actor.company_id, user_id) is None:
            raise HTTPException(status_code=404, detail="CLIENT_NOT_FOUND")
        events = await service.repository.list_audit_events(
            actor.company_id,
            user_id,
            limit=limit,
        )
        return {"ok": True, "data": events}

    @router.get("/admin/access-users")
    async def list_access_users(
        auth: dict[str, str] = Depends(require_secure_admin_user),
    ) -> dict[str, Any]:
        """Lista administradores e seus escopos explícitos."""
        rows = await service.list_admins(_actor_from_auth(auth))
        return {"ok": True, "data": [_admin_view(row) for row in rows]}

    @router.post("/admin/access-users", status_code=201)
    async def create_access_user(
        payload: AdminCreatePayload,
        request: Request,
        auth: dict[str, str] = Depends(require_secure_admin_user),
    ) -> dict[str, Any]:
        """Cria acesso administrativo sem permitir escalada de privilégio."""
        if not {"manageable_role_ids", "manageable_roles"}.intersection(
            payload.model_fields_set
        ):
            raise HTTPException(status_code=422, detail="MANAGEABLE_ROLES_REQUIRED")
        return await create_admin(payload, request, auth)

    @router.patch("/admin/access-users/{user_id}")
    async def update_access_user(
        user_id: str,
        payload: AdminUpdatePayload,
        request: Request,
        auth: dict[str, str] = Depends(require_secure_admin_user),
    ) -> dict[str, Any]:
        """Edita acesso administrativo dentro do escopo gerenciável."""
        return await update_admin(user_id, payload, request, auth)

    @router.delete("/admin/access-users/{user_id}", status_code=204)
    async def delete_access_user(
        user_id: str,
        request: Request,
        auth: dict[str, str] = Depends(require_secure_admin_user),
    ) -> Response:
        """Desativa um acesso administrativo."""
        return await delete_admin(user_id, request, auth)

    @router.post("/admin/support-sessions", status_code=201)
    async def create_support_session(
        payload: SupportSessionCreatePayload,
        request: Request,
        response: Response,
        auth: dict[str, str] = Depends(require_secure_admin_user),
    ) -> dict[str, Any]:
        """Emite sessão de suporte com duração fixa e escopo mínimo."""
        try:
            record, token = await service.start_support_session(
                _actor_from_auth(auth),
                payload.subject_user_id,
                payload.reason,
                request_id=request.state.request_id,
            )
        except (AuthorizationError, ValidationError) as exc:
            raise _domain_http_error(exc) from exc
        secure = app_env == "production"
        response.set_cookie(
            "__Host-elcapo-support" if secure else "elcapo-support",
            token,
            max_age=15 * 60,
            httponly=True,
            secure=secure,
            samesite="lax",
            path="/",
        )
        return {"ok": True, "data": _support_view(record)}

    @router.get("/admin/support-sessions/current")
    async def get_support_session(
        auth: dict[str, str] = Depends(require_secure_authenticated_user),
    ) -> dict[str, Any]:
        """Consulta o contexto de suporte resolvido pelo servidor."""
        if auth.get("support_session_id") is None:
            raise HTTPException(status_code=404, detail="SUPPORT_SESSION_NOT_FOUND")
        return {
            "ok": True,
            "data": {
                "session_id": auth["support_session_id"],
                "actor_user_id": auth["actor_user_id"],
                "subject_user_id": auth["user_id"],
                "expires_at": auth["support_expires_at"],
                "scopes": ["account.view", "broker_account.edit"],
            },
        }

    @router.delete("/admin/support-sessions/{session_id}", status_code=204)
    async def revoke_support_session(
        session_id: str,
        request: Request,
        auth: dict[str, str] = Depends(require_secure_admin_user),
    ) -> Response:
        """Revoga uma sessão pertencente ao ator autenticado."""
        await service.end_support_session(
            _actor_from_auth(auth),
            session_id,
            request_id=request.state.request_id,
        )
        response = Response(status_code=204)
        response.delete_cookie("elcapo-support", path="/")
        response.delete_cookie("__Host-elcapo-support", path="/", secure=True)
        return response

    @router.get("/admin/clients")
    async def list_clients(
        segment: str = Query(default="active"),
        limit: int = Query(default=10, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        search: str | None = Query(default=None, max_length=120),
        auth: dict[str, str] = Depends(require_admin_user),
    ) -> dict[str, Any]:
        """Lista clientes paginados por segmento, com busca opcional por nome/email/ID."""
        actor = _actor_from_auth(auth)
        normalized_search = search.strip() if search and search.strip() else None
        cached = read_admin_clients_cache(
            actor.company_id,
            segment,
            offset,
            limit,
            search=normalized_search,
        )
        if cached is not None:
            return {"ok": True, "data": cached}

        fetch_limit = limit + 1

        if segment == "pending":
            rows = await service.list_clients(
                actor,
                include_deleted=False,
                limit=fetch_limit,
                offset=offset,
                search=normalized_search,
                approval_status="pending",
                grant_access=False,
            )
        elif segment == "active":
            rows = await service.list_clients(
                actor,
                account_type=AccountType.CLIENT,
                include_deleted=False,
                limit=fetch_limit,
                offset=offset,
                search=normalized_search,
                grant_access=True,
            )
        elif segment == "inactive":
            rows = await service.list_clients(
                actor,
                include_deleted=True,
                limit=fetch_limit,
                offset=offset,
                search=normalized_search,
                grant_access=False,
                exclude_approval_status="pending",
            )
        elif segment == "trial":
            rows = await service.list_clients(
                actor,
                account_type=AccountType.TRIAL,
                include_deleted=False,
                limit=fetch_limit,
                offset=offset,
                search=normalized_search,
            )
        elif segment == "marketing":
            rows = await service.list_clients(
                actor,
                account_type=AccountType.MARKETING,
                include_deleted=False,
                limit=fetch_limit,
                offset=offset,
                search=normalized_search,
            )
        else:
            rows = await service.list_clients(
                actor,
                include_deleted=False,
                limit=fetch_limit,
                offset=offset,
                search=normalized_search,
            )

        visible = rows[:limit]
        payload = {
            "items": [_client_view(item) for item in visible],
            "has_more": len(rows) > limit,
            "next_offset": offset + len(visible),
        }
        write_admin_clients_cache(
            actor.company_id,
            segment,
            offset,
            limit,
            payload,
            search=normalized_search,
        )
        return {"ok": True, "data": payload}

    @router.get("/admin/dashboard")
    async def admin_dashboard(
        days: int = Query(default=30, ge=1, le=365),
        auth: dict[str, str] = Depends(require_admin_user),
    ) -> dict[str, Any]:
        """Consolida clientes e resultados reais do tenant no período."""
        actor = _actor_from_auth(auth)
        if dashboard_warmer is not None:
            dashboard_warmer.register_company(actor.company_id)

        cached = read_admin_dashboard_cache(actor.company_id, days)
        if cached is not None:
            return {"ok": True, "data": cached}

        logger.info(
            "[ADMIN_DASHBOARD_CACHE_MISS] company_id=%s days=%s",
            actor.company_id,
            days,
        )
        dashboard = await compute_admin_dashboard_payload(
            service=service,
            company_id=actor.company_id,
            days=days,
            history_loader=history_loader,
            history_batch_loader=history_batch_loader,
        )
        write_admin_dashboard_cache(actor.company_id, days, dashboard)
        if dashboard_warmer is not None:
            dashboard_warmer.register_company(actor.company_id)
        return {"ok": True, "data": dashboard}

    @router.post("/admin/clients", status_code=201)
    async def create_client(
        payload: ClientCreatePayload,
        auth: dict[str, str] = Depends(require_admin_user),
    ) -> dict[str, Any]:
        """Cria cliente usando Auth Admin API apenas pelo backend."""
        try:
            created = await service.create_client(
                _actor_from_auth(auth),
                ClientCreate(**payload.model_dump()),
            )
        except (AuthorizationError, ValidationError) as exc:
            raise _domain_http_error(exc) from exc
        _invalidate_admin_list_caches(created.company_id)
        _invalidate_access_profile(created.user_id)
        return {"ok": True, "data": _client_view(created)}

    @router.post("/admin/clients/{user_id}/approve")
    async def approve_client(
        user_id: str,
        payload: ApproveLeadPayload,
        request: Request,
        background_tasks: BackgroundTasks,
        auth: dict[str, str] = Depends(require_admin_user),
    ) -> dict[str, Any]:
        """Aprova lead pendente liberando acesso por N dias."""
        request_id = getattr(request.state, "request_id", None)
        actor = _actor_from_auth(auth)
        try:
            approved = await service.approve_lead(
                actor,
                user_id,
                payload.access_days,
                request_id=request_id,
                emit_webhooks=False,
                defer_side_effects=True,
            )
        except (AuthorizationError, ValidationError, NotFoundError) as exc:
            raise _domain_http_error(exc) from exc

        _invalidate_admin_list_caches(actor.company_id)
        _invalidate_access_profile(user_id)

        async def _finalize_approval() -> None:
            try:
                await service.complete_lead_approval_side_effects(
                    actor,
                    approved,
                    request_id=request_id,
                )
            except Exception:
                logger.exception(
                    "admin.approve.side_effects_failed user_id=%s request_id=%s",
                    user_id,
                    request_id,
                )

        background_tasks.add_task(_finalize_approval)
        return {"ok": True, "data": _client_view(approved)}

    @router.patch("/admin/clients/{user_id}")
    async def update_client(
        user_id: str,
        payload: ClientUpdatePayload,
        auth: dict[str, str] = Depends(require_admin_user),
    ) -> dict[str, Any]:
        """Edita cliente com validação de pagamento, trial e marketing."""
        try:
            updated = await service.update_client(
                _actor_from_auth(auth),
                user_id,
                ClientUpdate(**payload.model_dump(exclude_unset=True)),
            )
        except (AuthorizationError, ValidationError, NotFoundError) as exc:
            raise _domain_http_error(exc) from exc
        _invalidate_admin_list_caches(updated.company_id)
        _invalidate_access_profile(user_id)
        return {"ok": True, "data": _client_view(updated)}

    @router.delete("/admin/clients/{user_id}", status_code=204)
    async def delete_client(
        user_id: str,
        auth: dict[str, str] = Depends(require_admin_user),
    ) -> Response:
        """Desativa cliente sem apagar histórico."""
        actor = _actor_from_auth(auth)
        try:
            await service.delete_client(actor, user_id)
        except (AuthorizationError, NotFoundError) as exc:
            raise _domain_http_error(exc) from exc
        _invalidate_admin_list_caches(actor.company_id)
        _invalidate_access_profile(user_id)
        return Response(status_code=204)

    @router.get("/admin/clients/{user_id}/history")
    async def client_history(
        user_id: str,
        days: int = Query(default=30, ge=1, le=90),
        auth: dict[str, str] = Depends(require_admin_user),
    ) -> dict[str, Any]:
        """Retorna histórico real somente com a permissão correspondente."""
        actor = _actor_from_auth(auth)
        if AdminPermission.CLIENTS_HISTORY_READ not in actor.permissions:
            raise HTTPException(status_code=403, detail="FORBIDDEN")
        client = await service.repository.get_client(actor.company_id, user_id)
        if client is None:
            raise HTTPException(status_code=404, detail="CLIENT_NOT_FOUND")
        history = await asyncio.to_thread(history_loader, user_id, days)
        return {"ok": True, "data": history}

    @router.get("/admin/admins")
    async def list_admins(
        auth: dict[str, str] = Depends(require_admin_user),
    ) -> dict[str, Any]:
        """Lista acessos administrativos da empresa."""
        rows = await service.list_admins(_actor_from_auth(auth))
        return {"ok": True, "data": [_admin_view(row) for row in rows]}

    @router.post("/admin/admins", status_code=201)
    async def create_admin(
        payload: AdminCreatePayload,
        request: Request,
        auth: dict[str, str] = Depends(require_admin_user),
    ) -> dict[str, Any]:
        """Cria administrador com permissões explícitas."""
        if (
            "manageable_role_ids" in payload.model_fields_set
            and "manageable_roles" in payload.model_fields_set
        ):
            raise HTTPException(status_code=422, detail="MANAGEABLE_ROLES_CONFLICT")
        manageable_roles = (
            payload.manageable_roles
            if "manageable_roles" in payload.model_fields_set
            else payload.manageable_role_ids
        )
        try:
            created = await service.create_admin(
                _actor_from_auth(auth),
                AdminCreate(
                    name=payload.name,
                    email=payload.email,
                    job_title=payload.job_title,
                    password=payload.password,
                    permissions=frozenset(payload.permissions),
                    manageable_role_ids=(
                        None
                        if manageable_roles == "all"
                        else frozenset(manageable_roles)
                        if manageable_roles is not None
                        else None
                    ),
                ),
                request_id=request.state.request_id,
            )
        except (AuthorizationError, ValidationError) as exc:
            raise _domain_http_error(exc) from exc
        _invalidate_access_profile(created.user_id)
        return {"ok": True, "data": _admin_view(created)}

    @router.patch("/admin/admins/{user_id}")
    async def update_admin(
        user_id: str,
        payload: AdminUpdatePayload,
        request: Request,
        auth: dict[str, str] = Depends(require_admin_user),
    ) -> dict[str, Any]:
        """Edita cargo e permissões sem permitir escalada."""
        if (
            "manageable_role_ids" in payload.model_fields_set
            and "manageable_roles" in payload.model_fields_set
        ):
            raise HTTPException(status_code=422, detail="MANAGEABLE_ROLES_CONFLICT")
        values = payload.model_dump(exclude_unset=True)
        if "manageable_roles" in values:
            manageable_roles = values.pop("manageable_roles")
            values["manageable_role_ids"] = (
                None if manageable_roles == "all" else frozenset(manageable_roles or [])
            )
        if "permissions" in values:
            values["permissions"] = frozenset(values["permissions"])
        if "manageable_role_ids" in values and values["manageable_role_ids"] is not None:
            values["manageable_role_ids"] = frozenset(values["manageable_role_ids"])
        values["manageable_roles_supplied"] = bool(
            {"manageable_role_ids", "manageable_roles"}.intersection(payload.model_fields_set)
        )
        try:
            updated = await service.update_admin(
                _actor_from_auth(auth),
                user_id,
                AdminUpdate(**values),
                request_id=request.state.request_id,
            )
        except (AuthorizationError, ValidationError, NotFoundError) as exc:
            raise _domain_http_error(exc) from exc
        _invalidate_access_profile(user_id)
        return {"ok": True, "data": _admin_view(updated)}

    @router.delete("/admin/admins/{user_id}", status_code=204)
    async def delete_admin(
        user_id: str,
        request: Request,
        auth: dict[str, str] = Depends(require_admin_user),
    ) -> Response:
        """Desativa administrador preservando auditoria."""
        try:
            await service.delete_admin(
                _actor_from_auth(auth),
                user_id,
                request_id=request.state.request_id,
            )
        except (AuthorizationError, NotFoundError) as exc:
            raise _domain_http_error(exc) from exc
        _invalidate_access_profile(user_id)
        return Response(status_code=204)

    @router.post("/admin/impersonations", status_code=201)
    async def start_impersonation(
        payload: ImpersonationCreatePayload,
        response: Response,
        auth: dict[str, str] = Depends(require_admin_user),
    ) -> dict[str, Any]:
        """Inicia visualização temporária e somente leitura como cliente."""
        try:
            record, token = await service.start_impersonation(
                _actor_from_auth(auth),
                payload.target_user_id,
                payload.reason,
                duration_minutes=payload.duration_minutes,
            )
        except (AuthorizationError, ValidationError) as exc:
            raise _domain_http_error(exc) from exc
        secure = app_env == "production"
        cookie_name = "__Host-elcapo-impersonation" if secure else "elcapo-impersonation"
        response.set_cookie(
            cookie_name,
            token,
            max_age=payload.duration_minutes * 60,
            httponly=True,
            secure=secure,
            samesite="lax",
            path="/",
        )
        return {
            "ok": True,
            "data": {
                "session_id": record.session_id,
                "target_user_id": record.target_user_id,
                "expires_at": record.expires_at.isoformat(),
                "read_only": True,
            },
        }

    @router.delete("/admin/impersonations/current", status_code=204)
    async def end_impersonation(
        auth: dict[str, str] = Depends(require_authenticated_user),
    ) -> Response:
        """Revoga a sessão atual e remove os cookies de suporte."""
        if auth.get("impersonating") != "true":
            raise HTTPException(status_code=404, detail="IMPERSONATION_NOT_FOUND")
        actor_permissions = frozenset(
            AdminPermission(value)
            for value in auth.get("actor_permissions", "").split(",")
            if value in {permission.value for permission in AdminPermission}
        )
        actor = AdminActor(
            user_id=auth["actor_user_id"],
            company_id=auth["actor_company_id"],
            permissions=actor_permissions,
            manageable_role_ids=None,
        )
        await service.end_impersonation(actor, auth["impersonation_session_id"])
        response = Response(status_code=204)
        response.delete_cookie(
            "elcapo-impersonation",
            path="/",
            httponly=True,
            secure=False,
            samesite="lax",
        )
        response.delete_cookie(
            "__Host-elcapo-impersonation",
            path="/",
            httponly=True,
            secure=True,
            samesite="lax",
        )
        return response

    @router.post("/marketing-simulation/trades", status_code=201)
    async def create_simulated_trade(
        payload: SimulatedTradeCreatePayload | None = Body(default=None),
        auth: dict[str, str] = Depends(require_authenticated_user),
    ) -> dict[str, Any]:
        """
        Gera operação sintética apenas para conta marketing em simulação.

        O body é opcional: amount/payout/asset/direction/result/created_at
        definidos no Shift+O são aplicados antes da geração. Sem ``result``,
        o WIN/LOSS segue a taxa de acertividade. Sem ``created_at``, usa o
        instante atual.
        """
        _require_marketing_simulation(auth)
        simulator = await get_simulator(auth)
        overrides = payload.model_dump(exclude_unset=True) if payload is not None else {}
        try:
            generated = simulator.next_trade(**overrides)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        stored = await service.repository.save_simulated_trade(
            auth["company_id"],
            auth["user_id"],
            generated,
        )
        from backend.marketing_simulation_service import MarketingSimulationService

        stored = MarketingSimulationService.preserve_simulated_metadata(generated, stored)
        simulator.history[-1] = dict(stored)
        await sync_marketing_display(
            auth,
            history=[dict(stored)],
            stats=_score_stats_from_trades([stored], accumulate=True),
        )
        return {"ok": True, "data": _simulated_trade_view(stored)}

    @router.post("/marketing-simulation/generate-history", status_code=201)
    async def generate_score_history(
        payload: GenerateScoreHistoryPayload,
        auth: dict[str, str] = Depends(require_authenticated_user),
    ) -> dict[str, Any]:
        """
        Substitui o placar do overlay pelo lote informado no Shift+O.

        Body: wins, losses, amount (valor de entrada), period e asset opcional.
        O payout é consultado automaticamente na corretora a partir do ativo
        (pode ser enviado manualmente só para compatibilidade/testes).
        O histórico antigo permanece; só o placar visual do El Capo é
        reescrito para o lote gerado.
        """
        _require_marketing_simulation(auth)
        simulator = await get_simulator(auth)
        body = payload.model_dump(exclude_unset=True)
        period = str(body.get("period") or "M5")
        fixed_asset = body.get("asset")
        fixed_payout = body.get("payout")

        async def resolve_payout(symbol: str) -> int | None:
            if asset_payout_resolver is None:
                return None
            return await asset_payout_resolver(auth["user_id"], symbol)

        if fixed_payout is None and asset_payout_resolver is None:
            raise HTTPException(
                status_code=503,
                detail="Consulta de payout indisponível",
            )

        try:
            generated = await simulator.generate_score_history(
                wins=int(body["wins"]),
                losses=int(body["losses"]),
                amount=float(body["amount"]),
                payout=int(fixed_payout) if fixed_payout is not None else None,
                asset=str(fixed_asset) if fixed_asset else None,
                period=period,
                payout_resolver=None if fixed_payout is not None else resolve_payout,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        await sync_marketing_display(
            auth,
            history=generated,
            stats=_score_stats_from_trades(generated),
        )
        return {
            "ok": True,
            "data": [_simulated_trade_view(item) for item in generated],
        }

    @router.get("/marketing-simulation/history")
    async def simulated_history(
        auth: dict[str, str] = Depends(require_authenticated_user),
    ) -> dict[str, Any]:
        """Lista exclusivamente o histórico sintético da sessão atual."""
        _require_marketing_simulation(auth)
        # `limit=10_000` como em todos os outros chamadores. Sem ele valia o
        # default 100 do repositório e, como a ordem é `synthetic_sequence.asc`,
        # o corte comia justamente as operações NOVAS: em 01/09 a conta
        # `81c49f33` tinha 525 sintéticas e o painel só recebia as 100 mais
        # antigas (26/07 a 30/07), sumindo com 425. Pior, o `replace_history`
        # logo abaixo recalculava o placar em cima dessa fatia truncada, então
        # abrir a aba Histórico corrompia o placar exibido.
        history = await service.repository.list_simulated_trades(
            auth["company_id"],
            auth["user_id"],
            limit=10_000,
        )
        key = (auth["company_id"], auth["user_id"])
        if key in simulations:
            simulations[key].replace_history(history)
        return {"ok": True, "data": [_simulated_trade_view(item) for item in history]}

    @router.get("/marketing-simulation/stats")
    async def simulated_stats(
        auth: dict[str, str] = Depends(require_authenticated_user),
    ) -> dict[str, Any]:
        """Retorna o placar sintético usado pelo robô e pelo painel."""
        _require_marketing_simulation(auth)
        simulator = await get_simulator(auth)
        return {"ok": True, "data": simulator.build_stats()}

    @router.patch("/marketing-simulation/trades/{trade_id}")
    async def update_simulated_trade(
        trade_id: str,
        payload: SimulatedTradeUpdatePayload,
        auth: dict[str, str] = Depends(require_authenticated_user),
    ) -> dict[str, Any]:
        """Edita um trade sintético pertencente à sessão autenticada."""
        _require_marketing_simulation(auth)
        simulator = await get_simulator(auth)
        updated = await simulator.update_trade(
            trade_id,
            payload.model_dump(exclude_unset=True),
        )
        if updated is None:
            raise HTTPException(status_code=404, detail="SIMULATED_TRADE_NOT_FOUND")
        await sync_marketing_display(
            auth,
            history=[dict(updated)],
            stats={"skip_score": True},
        )
        return {"ok": True, "data": _simulated_trade_view(updated)}

    @router.delete("/marketing-simulation/trades/{trade_id}", status_code=204)
    async def delete_simulated_trade(
        trade_id: str,
        auth: dict[str, str] = Depends(require_authenticated_user),
    ) -> Response:
        """
        Exclui uma operação do histórico editável da conta marketing.

        Aceita o UUID de ``marketing_simulated_trades`` (Shift+O) **ou** o
        ``order_id`` exibido em ``/robot/history`` (operações ao vivo Bullex).
        IDs não-UUID não consultam a coluna UUID do Supabase (evita 400/500).

        Limpa UUID e ``broker_order_id`` nas fontes do robô e ajusta o placar
        **uma vez** (antes o placar só caía se a linha existisse em
        ``robot_trade_history`` com o mesmo id — exclusão pelo UUID do
        espelho deixava WIN/LOSS no overlay).
        """
        _require_marketing_simulation(auth)
        simulator = await get_simulator(auth)
        deleted_marketing = await simulator.delete_trade(trade_id)
        order_ids: set[str] = {str(trade_id or "").strip()}
        trade_meta: dict[str, Any] | None = (
            dict(deleted_marketing) if isinstance(deleted_marketing, dict) else None
        )
        if trade_meta:
            for key in ("id", "broker_order_id", "order_id"):
                value = str(trade_meta.get(key) or "").strip()
                if value:
                    order_ids.add(value)

        # O deleter real aceita `adjust_score`; mocks antigos de teste só
        # aceitam (user_id, order_id). Decidir pela assinatura em vez de
        # capturar TypeError: um TypeError vindo de DENTRO do deleter caía no
        # fallback com `adjust_score=True` e o placar era decrementado duas
        # vezes — aqui e no `marketing_score_remover` logo abaixo.
        takes_adjust_score = True
        if robot_history_deleter is not None:
            try:
                inspect.signature(robot_history_deleter).bind(
                    "user",
                    "order",
                    adjust_score=False,
                )
            except (TypeError, ValueError):
                takes_adjust_score = False

        removed_robot = False
        if robot_history_deleter is not None:
            for order_id in sorted(order_ids):
                if not order_id:
                    continue
                if takes_adjust_score:
                    removed = robot_history_deleter(
                        auth["user_id"],
                        order_id,
                        adjust_score=False,
                    )
                else:
                    removed = robot_history_deleter(auth["user_id"], order_id)
                if removed:
                    removed_robot = True
                    if trade_meta is None and isinstance(removed, dict):
                        trade_meta = removed

        if trade_meta is None and not deleted_marketing and not removed_robot:
            # Idempotente: duplo clique / cache stale após exclusão bem-sucedida.
            return Response(status_code=204)

        if trade_meta is not None and marketing_score_remover is not None:
            marketing_score_remover(auth["user_id"], trade_meta)
        return Response(status_code=204)

    return router


def _actor_from_auth(auth: dict[str, str]) -> AdminActor:
    """Converte a sessão validada em ator de domínio."""
    permissions = frozenset(
        AdminPermission(value)
        for value in _permission_values(auth)
        if value in {permission.value for permission in AdminPermission}
    )
    return AdminActor(
        user_id=auth["user_id"],
        company_id=auth["company_id"],
        permissions=permissions,
        manageable_role_ids=(
            None
            if auth.get("manageable_role_ids") == "*"
            else frozenset(
                value for value in auth.get("manageable_role_ids", "").split(",") if value
            )
        ),
    )


def _permission_values(auth: dict[str, str]) -> list[str]:
    """Extrai permissões serializadas da identidade."""
    return [value for value in auth.get("permissions", "").split(",") if value]


def _require_marketing_simulation(auth: dict[str, str]) -> None:
    """Restringe o contrato a contas marketing em modo de simulação."""
    if auth.get("account_type") != "marketing" or auth.get("marketing_mode") != "simulation":
        raise HTTPException(status_code=403, detail="MARKETING_SIMULATION_ONLY")


def _simulated_trade_view(item: dict[str, Any]) -> dict[str, Any]:
    """Remove metadados internos do sequenciamento antes da resposta."""
    return {key: value for key, value in item.items() if not key.startswith("_")}


def _score_stats_from_trades(
    trades: list[dict[str, Any]],
    *,
    accumulate: bool = False,
) -> dict[str, Any]:
    """
    Calcula o placar de um lote de operações sintéticas.

    Args:
        trades: Operações recém-criadas ou editadas.
        accumulate: Quando True, o overlay soma este lote ao placar atual
            em vez de substituí-lo.

    Returns:
        Contadores no formato esperado por ``sync_marketing_display_to_robot``.
    """
    wins = sum(1 for item in trades if str(item.get("result") or "").upper() == "WIN")
    losses = sum(1 for item in trades if str(item.get("result") or "").upper() == "LOSS")
    profit = round(sum(float(item.get("profit") or 0) for item in trades), 2)
    total = wins + losses
    return {
        "wins": wins,
        "losses": losses,
        "total_trades": total,
        "win_rate": round((wins / total) * 100, 2) if total else 0.0,
        "profit": profit,
        "accumulate": accumulate,
    }


def _is_pending_lead(item: ClientRecord) -> bool:
    """
    Identifica lead aguardando aprovação (coluna ou fallback legado).

    Args:
        item: Cliente persistido.

    Returns:
        True quando está pendente, sem acesso e não excluído.
    """
    if item.deleted_at is not None or item.grant_access:
        return False
    if item.approval_status == ApprovalStatus.PENDING:
        return True
    plan_name = str(item.plan_name or "").strip().casefold()
    return plan_name.startswith("aguardando")


def _client_view(item: ClientRecord) -> dict[str, Any]:
    """Serializa cliente sem credenciais."""
    return {
        "id": item.user_id,
        "name": item.name,
        "email": item.email,
        "phone": item.phone,
        "trader_id": item.trader_id,
        "account_type": item.account_type.value,
        "customer_type": item.account_type.value,
        "payment_status": item.payment_status.value,
        "plan_name": item.plan_name,
        "plan_id": item.plan_id,
        "grant_access": item.grant_access,
        "approval_status": item.approval_status.value,
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
        "expires_at": item.expires_at.isoformat() if item.expires_at else None,
        "deleted_at": item.deleted_at.isoformat() if item.deleted_at else None,
        "marketing_mode": item.marketing_mode.value if item.marketing_mode else None,
        "marketing_win_rate": item.marketing_win_rate,
        "marketing_target_win_rate": item.marketing_win_rate,
    }


def _admin_view(item: Any) -> dict[str, Any]:
    """Serializa administrador sem senha ou token."""
    return {
        "id": item.user_id,
        "name": item.name,
        "email": item.email,
        "job_title": item.job_title,
        "role_id": item.role_id,
        "permissions": sorted(permission.value for permission in item.permissions),
        "manageable_role_ids": (
            sorted(item.manageable_role_ids) if item.manageable_role_ids is not None else None
        ),
    }


def _support_view(item: Any) -> dict[str, Any]:
    """Serializa sessão sem expor token, hash ou motivo sensível."""
    return {
        "session_id": item.session_id,
        "actor_user_id": item.actor_user_id,
        "subject_user_id": item.target_user_id,
        "expires_at": item.expires_at.isoformat(),
        "scopes": sorted(item.scopes),
    }


def _domain_http_error(error: Exception) -> HTTPException:
    """Mapeia erro de domínio para resposta HTTP consistente."""
    if isinstance(error, AuthorizationError):
        return HTTPException(status_code=403, detail=str(error))
    if isinstance(error, NotFoundError):
        return HTTPException(status_code=404, detail=str(error))
    return HTTPException(status_code=422, detail=str(error))
