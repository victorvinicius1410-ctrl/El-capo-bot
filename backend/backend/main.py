import logging
import math
import os
import asyncio
import base64
import json
import re
import secrets
import threading
from uuid import uuid4
from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from time import monotonic
from typing import Any, Callable

import httpx
from fastapi import APIRouter, Body, Cookie, Depends, FastAPI, Header, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from backend.auto_trader import (
    AutoTrader,
    RobotConfigUpdate,
    normalize_stop_mode,
    parse_datetime,
    resolve_robot_stop_reason,
    strip_ai_fields,
    utc_now,
)
from backend.status import (
    STATUS_ACCOUNT_DISCONNECTED,
    STATUS_ACTIVE_COOLDOWN,
    STATUS_ANALYSIS_ERROR,
    STATUS_ANALYSIS_TIMEOUT,
    STATUS_ANALYZING,
    STATUS_BULLEX_ACTIVE_MODE_NOT_REAL,
    STATUS_BUYING,
    STATUS_CONNECTION_BACKOFF,
    STATUS_ERROR,
    STATUS_INSUFFICIENT_BALANCE,
    STATUS_OPERATION_OPEN,
    STATUS_ORDER_REJECTED,
    STATUS_PAYOUT_COOLDOWN,
    STATUS_SYNCING,
    STATUS_PENDING_GALE_RESULT,
    STATUS_PENDING_RESULT,
    STATUS_SENDING_GALE_ORDER,
    STATUS_SENDING_ORDER,
    STATUS_SIGNAL_FOUND,
    STATUS_SIGNAL_EXPIRED,
    STATUS_STOP_LOSS_HIT,
    STATUS_STOP_WIN_HIT,
    STATUS_STOPPED,
    STATUS_WAITING_ANALYSIS_WINDOW,
    STATUS_WAITING_ENTRY,
    STATUS_WAITING_ENTRY_WINDOW,
    STATUS_WAITING_GALE_ENTRY,
    STATUS_WAITING_RESULT,
    STATUS_WAITING_NEXT_CYCLE,
    STATUS_WAITING_RECOVERY,
    TEMPORARY_WAIT_STATUSES,
    normalize_robot_status,
)
from backend.robot_persistence import (
    RobotPersistence,
    create_robot_persistence,
    extract_robot_settings,
)
from backend.pattern_memory import (
    PATTERN_MEMORY_BLOCK,
    create_pattern_memory_service,
)
from backend.signal_engine import (
    ANALYSIS_TIMEFRAMES,
    CONTINUATION_DEAD_RSI_HARD_BLOCK,
    CONTINUATION_DEAD_RSI_MAX,
    CONTINUATION_DEAD_RSI_MIN,
    FREQUENCY_RECOVERY_AFTER_CYCLES,
    FREQUENCY_RECOVERY_MIN_SCORE,
    FREQUENCY_RECOVERY_SOFT_BLOCKS,
    LAST_3_ALIGNMENT_HARD_BLOCK,
    MTF_VOTE_CONFIDENCE_MIN,
    STRATEGY_PROFILES,
    TREND_CLEAR_HARD_BLOCK,
    WEAK_CONTINUATION_PUT_HARD_BLOCK,
    analyze_signal,
    cycle_minutes_for_timeframe,
    is_weak_continuation_put_asset,
    merge_multi_timeframe_signals,
    minimum_operations_per_hour,
)
from backend.trade_result_monitor import TradeResultMonitor, normalize_trade_result
from backend.user_store import InMemoryUserStore, UserStore, create_user_store
from backend.admin_models import AdminActor, AdminPermission
from backend.admin_repository import InMemoryAdminRepository
from backend.admin_dashboard_warm import AdminDashboardWarmer
from backend.admin_router import (
    compute_admin_dashboard_payload,
    create_admin_router,
)
from backend.admin_service import AdminManagementService
from backend.auth_service import AuthenticationError, SupabaseAuthService
from backend.auth_session_service import AuthSessionService, extract_access_token_from_request
from backend.auth_router import create_auth_router
from backend.robot_state_ws import (
    RobotStateWsHub,
    close_robot_websocket,
    robot_ws_receive_loop,
)
from backend.robot_bus import RobotBus, robot_runtime_mode
from backend.registration_service import RegistrationService
from backend.account_link_service import SupabaseAccountLinkService
from backend.cakto_service import CaktoConfig, CaktoService
from backend.finance_repository import (
    InMemoryFinanceRepository,
    SupabaseFinanceRepository,
)
from backend.finance_router import create_finance_router
from backend.finance_service import FinanceService
from backend.services.encryption_service import EncryptionService
from backend.services.bullex_credentials_service import BullexCredentialsService
from backend.env_prefix import environment_var_prefix
from backend.supabase_admin_repository import SupabaseAdminRepository
from backend.marketing_simulation_service import MarketingSimulationService
from backend.webhook_repository import (
    InMemoryWebhookRepository,
    SupabaseWebhookRepository,
)
from backend.webhook_router import create_webhook_router
from backend.webhook_service import WebhookService
from backend.email_repository import InMemoryEmailRepository, SupabaseEmailRepository
from backend.email_router import create_email_router
from backend.email_service import EmailConfig, EmailService
from backend.feedback_store import (
    FeedbackStore,
    FeedbackValidationError,
    admin_feedback_view,
    create_feedback_store,
    owner_feedback_view,
    public_feedback_view,
)


logger = logging.getLogger("backend-gateway")

CORS_ALLOWED_ORIGINS_DEFAULT = (
    "https://elcapobot.online,"
    "https://www.elcapobot.online,"
    "http://localhost:5173,"
    "http://localhost:3000"
)
CORS_ALLOWED_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
CORS_ALLOWED_HEADERS = [
    "x-api-key",
    "x-user-id",
    "x-user-email",
    "x-request-id",
    "content-type",
    "authorization",
]
ROBOT_CONFIG_DEFAULTS = {
    "account_mode": "REAL",
    "timeframe": "M1",
    "market_mode": "OTC",
    "strategy_mode": "conservative",
    "entry_value": 5.0,
    "cycle_minutes": 1,
    "min_confidence": 80,
    "min_payout": 80.0,
    "stop_win": 50.0,
    "stop_loss": 30.0,
    "stop_win_mode": "money",
    "stop_loss_mode": "money",
    "stop_win_operations": 5,
    "stop_loss_operations": 3,
    "max_entries_per_cycle": 1,
    "allow_real": True,
    "confirm_real": True,
    "martingale_enabled": False,
    "martingale_steps": 1,
    "martingale_multiplier": 2.0,
}
ROBOT_CONFIG_ALLOWED_FIELDS = set(ROBOT_CONFIG_DEFAULTS)
# Qualquer valor estritamente positivo é válido; só 0/negativo são bloqueados.
MIN_REAL_ENTRY = 5.0
MIN_STOP_MONEY = 5.0
MIN_STOP_OPERATIONS = 1
MAX_REAL_ENTRY = 1000.0

ASSET_NOT_ALLOWED = "ASSET_NOT_ALLOWED"
SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
SESSION_DISCONNECTED = "SESSION_DISCONNECTED"
LOW_QUALITY_SIGNAL = "Sinal bloqueado por baixa qualidade"
MAX_ORDER_ATTEMPTS_PER_CYCLE = 3
MAX_MTF_CANDIDATES_PER_CYCLE = 3
MAX_RECOVERY_MTF_CANDIDATES_PER_CYCLE = 10
ROBOT_WORKER_STALE_SECONDS = 120
ROBOT_CYCLE_TIMEOUT_SECONDS = 110.0
RECOVERY_MIN_CONFIDENCE = 70
NO_AVAILABLE_ASSET_ERROR = "Nenhum ativo disponível no momento da compra."
CRITICAL_TRADE_BLOCKS = {
    STATUS_ACCOUNT_DISCONNECTED,
    "STOP_WIN_HIT",
    "STOP_LOSS_HIT",
    "ACTIVE_CLOSED",
    "ACTIVE_COOLDOWN",
    "ASSET_COOLDOWN",
    "GLOBAL_LOSS_COOLDOWN",
    "OPERATION_IN_PROGRESS",
    "CANDLES_UNAVAILABLE",
    # CANDLE_STRENGTH / DOJI / SIDEWAYS / WICK / DEAD_RSI: soft.
    # Em frequency_recovery, TREND_CLEAR / PRICE_ACTION_SETUP / LEVEL_REJECTION
    # também saem do hard (ver signal_engine.FREQUENCY_RECOVERY_SOFT_BLOCKS).
    "PRICE_ACTION_SETUP",
    "REVERSAL_AGAINST",
    "LEVEL_CONFLICT",
    "LEVEL_REJECTION",
    "SR_ZONE",
    "TREND_CLEAR",
    "LAST_3_ALIGNMENT",
    "WEAK_CONTINUATION_PUT",
    PATTERN_MEMORY_BLOCK,
}
RECOVERY_NON_RELAXABLE_TRADE_BLOCKS = {
    STATUS_ACCOUNT_DISCONNECTED,
    "STOP_WIN_HIT",
    "STOP_LOSS_HIT",
    "ACTIVE_CLOSED",
    "OPERATION_IN_PROGRESS",
    "CANDLES_UNAVAILABLE",
    "PAYOUT_UNAVAILABLE",
    "MIN_CONFIDENCE",
    "MIN_PAYOUT",
    "MTF_CONFLUENCE",
    "MTF_HIGHER_TF_CONFLICT",
    "MTF_DATA_UNAVAILABLE",
    "ASSET_COOLDOWN",
    "GLOBAL_LOSS_COOLDOWN",
    "SR_ZONE",
    "TREND_CLEAR",
    "LAST_3_ALIGNMENT",
    "WEAK_CONTINUATION_PUT",
    PATTERN_MEMORY_BLOCK,
}
ANALYSIS_DETAIL_FIELDS = (
    "ema9",
    "ema21",
    "rsi",
    "rsi14",
    "atr",
    "atr_pct",
    "body_ratio",
    "candle_body",
    "upper_wick",
    "lower_wick",
    "upper_wick_ratio",
    "lower_wick_ratio",
    "directional_candles_5",
    "pullback_confirmed",
    "alternating_last_3",
    "current_candle_direction",
    "price_action_setup",
    "near_support_resistance",
    "near_support",
    "near_resistance",
    "support_level",
    "resistance_level",
    "zigzag_reversal",
    "reversal_against",
    "sideways",
    "volatility",
    "extreme_candle",
    "direction_score_edge",
    "raw_direction_score",
    "confidence_model_version",
    "strategy_setup",
    "strategy_setups",
    "setup_types",
    "candle_reading",
    "entry_reason",
    "block_reasons",
    "metrics",
    "matched_strategies",
    "used_strategies",
    "mtf_ready",
    "mtf_votes",
    "mtf_qualified_votes",
    "mtf_confluence",
    "mtf_analysis",
    "confidence_relaxed_after_skip",
    "pattern_relaxed_after_skip",
    "recovery_relaxed_filters",
    "normal_min_confidence",
    "effective_min_confidence",
)
ORDER_AVAILABILITY_ERROR_TERMS = (
    "asset is not available",
    "active suspended",
    "cannot purchase",
    "active not found",
)
BINARY_ALLOWED_ASSETS = [
    "EURUSD",
    "EURUSD-OTC",
    "EURGBP",
    "EURGBP-OTC",
    "USDCHF",
    "USDCHF-OTC",
    "EURJPY",
    "EURJPY-OTC",
    "NZDUSD-OTC",
    "GBPUSD",
    "GBPUSD-OTC",
    "GBPJPY",
    "GBPJPY-OTC",
    "USDJPY",
    "USDJPY-OTC",
    "AUDCAD-OTC",
    "AUDUSD",
    "AUDUSD-OTC",
    "USDCAD",
    "USDCAD-OTC",
    "AUDJPY",
    "AUDJPY-OTC",
    "GBPCAD-OTC",
    "GBPCHF-OTC",
    "GBPAUD-OTC",
    "EURCAD-OTC",
    "CHFJPY-OTC",
    "CADCHF-OTC",
    "EURAUD-OTC",
    "EURNZD-OTC",
    "AUDCHF-OTC",
]
BINARY_ALLOWED_ASSET_SET = set(BINARY_ALLOWED_ASSETS)
ANALYSIS_BASE_ASSETS = [
    "EURUSD",
    "GBPUSD",
    "USDJPY",
    "EURJPY",
    "AUDUSD",
    "EURGBP",
    "USDCHF",
    "USDCAD",
    "GBPJPY",
    "AUDJPY",
]
ANALYSIS_ASSETS_OTC = [f"{symbol}-OTC" for symbol in ANALYSIS_BASE_ASSETS]
ANALYSIS_ASSETS_OPEN = list(ANALYSIS_BASE_ASSETS)
# Compat: lista histórica = OTC (10 ativos).
ANALYSIS_ASSETS = list(ANALYSIS_ASSETS_OTC)
ROBOT_MAX_ASSETS_PER_CYCLE = len(ANALYSIS_BASE_ASSETS) * 2
CHART_ALLOWED_ASSET_SET = set(ANALYSIS_ASSETS_OTC) | set(ANALYSIS_ASSETS_OPEN)
SESSION_CACHE_TTL_SECONDS = 20
SESSION_STATUS_THROTTLE_SECONDS = 20
# HTTP /robot/state é fallback (WS é o canal primário). Poll lento = 30s.
ROBOT_STATE_MIN_POLL_SECONDS = 30
ACCOUNT_MIN_POLL_SECONDS = 25
SESSION_STATUS_MIN_POLL_SECONDS = 25
ROBOT_SESSION_REFRESH_SECONDS = 25
SESSION_OFFLINE_TTL_SECONDS = 60
OFFLINE_CONFIRMATION_FAILURES = 3
SESSION_FAILURE_BACKOFF_SECONDS = (10, 30, 60, 300)
SESSION_CACHEABLE_PATHS = {"/sessions/status", "/account"}
ACTIVE_USER_TTL_SECONDS = 300
ACCOUNT_CACHE_TTL_SECONDS = 25
ORDER_RESULT_CACHE_TTL_SECONDS = 1
BULLEX_UPSTREAM_TIMEOUT_SECONDS = 5.0
# Candles/payouts: precisa cobrir fila do lock por usuário no bullex-service.
# Com 2s, scan sequencial de 10–20 ativos estourava e OTC caía em CANDLES_TIMEOUT.
BULLEX_MARKET_DATA_TIMEOUT_SECONDS = 8.0
BULLEX_CONNECT_TIMEOUT_SECONDS = 60.0
# Fila de aquisição de conexão no pool do httpx. Sem teto explícito, uma
# requisição fica presa esperando slot livre e só morre no wait_for externo —
# sem NUNCA chegar no bullex-service (queda de 08/08: 0 req chegando enquanto
# o robot-runtime acumulava 201 timeouts em 10min).
BULLEX_POOL_TIMEOUT_SECONDS = 3.0
# O httpx precisa vencer a corrida contra o wait_for externo. Com os dois no
# MESMO valor eles empatavam; quando o wait_for vencia, cancelava a request no
# meio e a conexão vazava do pool (ver [BULLEX_POOL_STATS]). A folga é tirada do
# timeout do httpx — o wait_for continua no valor original, preservando o
# contrato de erro das rotas (ex.: connect -> 504/LOGIN_TIMEOUT).
BULLEX_CLIENT_TIMEOUT_MARGIN_SECONDS = 0.5
BULLEX_TEMPORARY_UNAVAILABLE = "BULLEX_TEMPORARY_UNAVAILABLE"
BULLEX_REQUESTS_LIMIT_EXCEEDED = "BULLEX_REQUESTS_LIMIT_EXCEEDED"
# Quando a corretora bloqueia login por IP, o auto-reconnect NÃO deve tentar
# a cada 60s (piora o TTL). Usa o retry_after do upstream (default 1h).
BULLEX_LOGIN_RATE_LIMIT_DEFAULT_SECONDS = 3600
PASSTHROUGH_CONNECT_ERRORS = {
    BULLEX_REQUESTS_LIMIT_EXCEEDED,
    "LOGIN_TIMEOUT",
    "invalid_credentials",
    "BULLEX_ACCOUNT_STILL_PRACTICE",
    "BULLEX_ACTIVE_MODE_NOT_REAL",
    "REAL_BALANCE_NOT_DETECTED",
    "REAL_MODE_NOT_CONFIRMED",
}
BAD_GATEWAY_PROTECTED_PATHS = {
    "/bullex/account",
    "/bullex/status",
    "/bullex/connect",
    "/robot/state",
    "/robot/start",
    "/robot/stop",
}
ASSETS_CACHE_TTL_SECONDS = 300
ASSETS_RETRY_BACKOFF_SECONDS = (10, 30, 60)
PAYOUT_CACHE_TTL_SECONDS = 60
CANDLES_CACHE_TTL_SECONDS = 60
CANDLES_REQUEST_TIMEOUT_SECONDS = 5.0
ACTIVE_COOLDOWN_SECONDS = 15
# Só o símbolo rejeitado pela corretora ("asset is not available") fica de fora
# por 1 vela M1. A varredura dos OUTROS ativos continua a cada ciclo — o
# robô não "para 5 minutos". O importante é o skip duro (sem furar com cache)
# + marcar o canal fechado; cooldown longo só atrasava oportunidades boas.
UNAVAILABLE_ASSET_COOLDOWN_SECONDS = 60
PAYOUT_COOLDOWN_SECONDS = 15
# Revalidação do canal turbo/binary na hora da compra. O sinal fica travado a
# vela inteira antes de entrar (M1: ~40-55s), então o cache de payout já pode
# não refletir o canal — e a corretora rejeita com "asset is not available".
# Os limites abaixo existem para caber na janela de compra (0-5s da vela):
# acima de CHANNEL_CACHE_MAX_AGE_SECONDS busca dado fresco, gastando no máximo
# CHANNEL_REVALIDATION_TIMEOUT_SECONDS por ativo e CHANNEL_REVALIDATION_BUDGET_SECONDS
# somando todos os candidatos. Estourou o orçamento → segue com o cache.
CHANNEL_CACHE_MAX_AGE_SECONDS = 10.0
CHANNEL_REVALIDATION_TIMEOUT_SECONDS = 1.2
CHANNEL_REVALIDATION_BUDGET_SECONDS = 2.0
STALE_MARKET_DATA_SECONDS = 120
ROBOT_ASSET_QUEUE_SLEEP_SECONDS = 0.0
ROBOT_CANDLE_COUNT = 100
# Após 1 LOSS: 1 vela M1 (60s). 3 min matava a frequência (centenas de
# [GLOBAL_LOSS_COOLDOWN] numa manhã) sem ganho claro vs. só pular a próxima.
GLOBAL_LOSS_COOLDOWN_AFTER_ONE_SECONDS = 60
# Após 2 LOSSes consecutivos: 10 min (ASSET_COOLDOWN já cobre o mesmo ativo 30 min).
GLOBAL_LOSS_COOLDOWN_AFTER_TWO_MINUTES = 10
ACTIVE_DATA_TIMEOUT_SECONDS = 8.0
# Por ativo no scan do robô (candles+payout). Sequencial: Bullex só aceita 1 call/user.
ROBOT_ANALYSIS_ASSET_TIMEOUT_SECONDS = 18.0


def build_success(data: Any) -> dict[str, Any]:
    return {"ok": True, "data": data, "error": None}


def build_error(message: str) -> dict[str, Any]:
    return {"ok": False, "data": None, "error": message}


INSUFFICIENT_BALANCE_START_MESSAGE = "Você está sem saldo para iniciar. Faça um depósito na BullEx."
ENTRY_VALUE_EXCEEDS_BALANCE_MESSAGE = "Seu saldo é menor que o valor da entrada."


def normalize_service_payload(
    payload: Any,
    *,
    error: str = BULLEX_TEMPORARY_UNAVAILABLE,
) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    logger.warning(
        "[INVALID_UPSTREAM_PAYLOAD_HANDLED] payload_type=%s",
        type(payload).__name__,
    )
    return build_error(error)


def build_controlled_upstream_error(
    detail: Any,
    *,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normaliza falhas do bullex-service para o painel.

    Rate limit e timeouts conhecidos passam o código real; o resto vira
    ``BULLEX_TEMPORARY_UNAVAILABLE`` para não vazar detalhes internos.
    """
    code, retry_after, detail_text = classify_bullex_connect_error(detail, data)
    payload_data: dict[str, Any] = dict(data) if isinstance(data, dict) else {}
    if retry_after is not None:
        payload_data.setdefault("retry_after_seconds", retry_after)
    return {
        "ok": False,
        "data": payload_data or None,
        "error": code,
        "detail": detail_text[:240] if detail_text else code,
    }


def classify_bullex_connect_error(
    detail: Any,
    data: dict[str, Any] | None = None,
) -> tuple[str, int | None, str]:
    """
    Classifica erro de connect/reconnect da Bullex.

    Returns:
        Tupla ``(codigo, retry_after_seconds|None, detalhe)``.
    """
    text = str(detail or "").strip()
    upper = text.upper()
    retry_after: int | None = None
    if isinstance(data, dict):
        raw_retry = data.get("retry_after_seconds") or data.get("ttl")
        try:
            if raw_retry is not None:
                retry_after = max(60, int(raw_retry))
        except (TypeError, ValueError):
            retry_after = None

    if upper == BULLEX_REQUESTS_LIMIT_EXCEEDED or "REQUESTS_LIMIT_EXCEEDED" in upper:
        if retry_after is None:
            retry_after = _extract_ttl_from_text(text) or BULLEX_LOGIN_RATE_LIMIT_DEFAULT_SECONDS
        return BULLEX_REQUESTS_LIMIT_EXCEEDED, retry_after, text or BULLEX_REQUESTS_LIMIT_EXCEEDED

    if "requests_limit_exceeded" in text.lower():
        if retry_after is None:
            retry_after = _extract_ttl_from_text(text) or BULLEX_LOGIN_RATE_LIMIT_DEFAULT_SECONDS
        return BULLEX_REQUESTS_LIMIT_EXCEEDED, retry_after, text

    # Corretora devolve JSON embutido: falha ao conectar: {"code":"invalid_credentials",...}
    if "invalid_credentials" in text.lower():
        return "invalid_credentials", retry_after, text

    broker_code = _extract_embedded_broker_code(text)
    if broker_code == "invalid_credentials":
        return "invalid_credentials", retry_after, text

    for known in PASSTHROUGH_CONNECT_ERRORS:
        if upper == known.upper() or text == known:
            return known, retry_after, text or known
        if known.lower() in text.lower():
            return known, retry_after, text or known

    if upper == "LOGIN_TIMEOUT" or "LOGIN_TIMEOUT" in upper:
        return "LOGIN_TIMEOUT", retry_after, text or "LOGIN_TIMEOUT"

    return BULLEX_TEMPORARY_UNAVAILABLE, retry_after, text or BULLEX_TEMPORARY_UNAVAILABLE


def _extract_embedded_broker_code(text: str) -> str | None:
    """Lê ``code`` de um JSON embutido na mensagem de falha do connect."""
    start = (text or "").find("{")
    end = (text or "").rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(text[start : end + 1])
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    code = str(payload.get("code") or "").strip().lower()
    return code or None


def _extract_ttl_from_text(text: str) -> int | None:
    match = re.search(r'"ttl"\s*:\s*(\d+)', text or "", flags=re.IGNORECASE)
    if not match:
        return None
    try:
        return max(60, int(match.group(1)))
    except (TypeError, ValueError):
        return None


def note_bullex_login_rate_limit(user_id: str | None, retry_after: int | None) -> None:
    """Ativa cooldown global + por usuário após rate limit de login."""
    seconds = max(60, int(retry_after or BULLEX_LOGIN_RATE_LIMIT_DEFAULT_SECONDS))
    until = utc_now() + timedelta(seconds=seconds)
    global bullex_login_rate_limited_until
    if bullex_login_rate_limited_until is None or until > bullex_login_rate_limited_until:
        bullex_login_rate_limited_until = until
    if user_id:
        bullex_auto_reconnect_at[user_id] = until
    logger.warning(
        "[BULLEX_LOGIN_RATE_LIMIT] user_id=%s retry_after=%s until=%s",
        user_id or "global",
        seconds,
        until.isoformat(),
    )


def bullex_login_rate_limit_remaining() -> int:
    """Segundos restantes do bloqueio global de login no gateway."""
    if bullex_login_rate_limited_until is None:
        return 0
    remaining = (bullex_login_rate_limited_until - utc_now()).total_seconds()
    return max(0, int(remaining))


CONFIG_LOCK_ERROR = {
    "ok": False,
    "error": "ROBOT_RUNNING_CONFIG_LOCKED",
    "message": "Pare o robô antes de alterar configurações.",
}


ROBOT_BASIC_CONFIG_FIELDS = {
    "stop_win",
    "stop_loss",
    "stop_win_mode",
    "stop_loss_mode",
    "stop_win_operations",
    "stop_loss_operations",
    "entry_value",
    "account_mode",
    "allow_real",
    "confirm_real",
    "timeframe",
    "market_mode",
    "min_confidence",
    "min_payout",
    "martingale_enabled",
    "martingale_steps",
    "martingale_multiplier",
}
ROBOT_BASIC_CONFIG_ALIASES = {
    "stopWin",
    "stopLoss",
    "stopWinMode",
    "stopLossMode",
    "stopWinOperations",
    "stopLossOperations",
    "entryValue",
    "accountMode",
    "allowReal",
    "confirmReal",
    "timeframe",
    "marketMode",
    "market_mode",
    "minConfidence",
    "minPayout",
    "martingaleEnabled",
    "martingaleSteps",
    "martingaleMultiplier",
}


def ignored_ai_config_fields(payload: dict[str, Any]) -> list[str]:
    ignored: list[str] = []
    for key in payload:
        normalized = str(key or "").strip().lower()
        if normalized.startswith(("ai", "use_ai", "openai", "gemini")):
            ignored.append(str(key))
    return ignored


def strip_ai_config_fields(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in payload.items()
        if not str(key or "").strip().lower().startswith(("ai", "use_ai", "openai", "gemini"))
    }


def filter_robot_config_payload(payload: dict[str, Any]) -> dict[str, Any]:
    filtered = strip_ai_config_fields(payload)
    return {
        key: value
        for key, value in filtered.items()
        if key in ROBOT_BASIC_CONFIG_FIELDS or key in ROBOT_BASIC_CONFIG_ALIASES
    }


def current_robot_config_payload(state: Any) -> dict[str, Any]:
    return {
        "account_mode": str(getattr(state, "account_mode", ROBOT_CONFIG_DEFAULTS["account_mode"]) or ROBOT_CONFIG_DEFAULTS["account_mode"]),
        "timeframe": str(getattr(state, "timeframe", ROBOT_CONFIG_DEFAULTS["timeframe"]) or ROBOT_CONFIG_DEFAULTS["timeframe"]),
        "market_mode": coerce_selectable_market_mode(
            getattr(state, "market_mode", ROBOT_CONFIG_DEFAULTS["market_mode"])
        ),
        "strategy_mode": str(getattr(state, "strategy_mode", ROBOT_CONFIG_DEFAULTS["strategy_mode"]) or ROBOT_CONFIG_DEFAULTS["strategy_mode"]),
        "entry_value": float(getattr(state, "entry_value", ROBOT_CONFIG_DEFAULTS["entry_value"]) or ROBOT_CONFIG_DEFAULTS["entry_value"]),
        "cycle_minutes": int(getattr(state, "cycle_minutes", ROBOT_CONFIG_DEFAULTS["cycle_minutes"]) or ROBOT_CONFIG_DEFAULTS["cycle_minutes"]),
        "min_confidence": int(getattr(state, "min_confidence", ROBOT_CONFIG_DEFAULTS["min_confidence"]) or ROBOT_CONFIG_DEFAULTS["min_confidence"]),
        "min_payout": float(getattr(state, "min_payout", ROBOT_CONFIG_DEFAULTS["min_payout"]) or ROBOT_CONFIG_DEFAULTS["min_payout"]),
        "stop_win": float(getattr(state, "stop_win", ROBOT_CONFIG_DEFAULTS["stop_win"]) or ROBOT_CONFIG_DEFAULTS["stop_win"]),
        "stop_loss": float(getattr(state, "stop_loss", ROBOT_CONFIG_DEFAULTS["stop_loss"]) or ROBOT_CONFIG_DEFAULTS["stop_loss"]),
        "stop_win_mode": str(
            getattr(state, "stop_win_mode", ROBOT_CONFIG_DEFAULTS["stop_win_mode"])
            or ROBOT_CONFIG_DEFAULTS["stop_win_mode"]
        ),
        "stop_loss_mode": str(
            getattr(state, "stop_loss_mode", ROBOT_CONFIG_DEFAULTS["stop_loss_mode"])
            or ROBOT_CONFIG_DEFAULTS["stop_loss_mode"]
        ),
        "stop_win_operations": int(
            getattr(state, "stop_win_operations", ROBOT_CONFIG_DEFAULTS["stop_win_operations"])
            or ROBOT_CONFIG_DEFAULTS["stop_win_operations"]
        ),
        "stop_loss_operations": int(
            getattr(state, "stop_loss_operations", ROBOT_CONFIG_DEFAULTS["stop_loss_operations"])
            or ROBOT_CONFIG_DEFAULTS["stop_loss_operations"]
        ),
        "max_entries_per_cycle": int(getattr(state, "max_entries_per_cycle", ROBOT_CONFIG_DEFAULTS["max_entries_per_cycle"]) or ROBOT_CONFIG_DEFAULTS["max_entries_per_cycle"]),
        "allow_real": bool(getattr(state, "allow_real", ROBOT_CONFIG_DEFAULTS["allow_real"])),
        "confirm_real": bool(getattr(state, "confirm_real", ROBOT_CONFIG_DEFAULTS["confirm_real"])),
        "martingale_enabled": bool(getattr(state, "martingale_enabled", ROBOT_CONFIG_DEFAULTS["martingale_enabled"])),
        "martingale_steps": int(getattr(state, "martingale_steps", ROBOT_CONFIG_DEFAULTS["martingale_steps"]) or ROBOT_CONFIG_DEFAULTS["martingale_steps"]),
        "martingale_multiplier": float(getattr(state, "martingale_multiplier", ROBOT_CONFIG_DEFAULTS["martingale_multiplier"]) or ROBOT_CONFIG_DEFAULTS["martingale_multiplier"]),
    }


def build_local_signal_review(signal: dict[str, Any]) -> dict[str, Any]:
    blocked_filters = [str(item) for item in (signal.get("blocked_filters") or [])]
    confidence = int(signal.get("confidence") or 0)
    payout = float(signal.get("payout") or 0)
    approved = bool(signal.get("trade_allowed")) and str(signal.get("signal") or "").upper() in {"CALL", "PUT"}
    if not approved:
        risk = "HIGH"
    elif confidence >= 85 and payout >= 85:
        risk = "LOW"
    elif confidence >= 78 and payout >= 80:
        risk = "MEDIUM"
    else:
        risk = "HIGH"
    recommendation = "VALID_SIGNAL" if approved else "REJECT_SIGNAL"
    summary = str(signal.get("reason") or signal.get("entry_reason") or "Revisao local concluida.").strip()
    return {
        "approved": approved,
        "risk": risk,
        "quality": max(0, min(100, confidence)),
        "summary": summary,
        "warnings": blocked_filters,
        "recommendation": recommendation,
        "source": "local",
    }


def normalize_binary_active(active: str) -> str:
    return (active or "").strip().upper()


def normalize_market_mode(value: Any) -> str:
    normalized = str(value or "").strip().upper()
    if normalized in {"OPEN", "ABERTO", "MARKET", "MERCADO", "MERCADO_ABERTO"}:
        return "OPEN"
    if normalized in {"BOTH", "AMBOS", "ALL", "OTC_AND_OPEN", "OPEN_AND_OTC"}:
        return "BOTH"
    return "OTC"


def is_forex_open_market_open(now: datetime | None = None) -> bool:
    """
    Indica se o mercado forex (não-OTC) está na sessão regular.

    Janela: domingo 22:00 UTC → sexta 22:00 UTC. Fora disso o modo OPEN
    fica bloqueado no El Capo (cadeado + contagem na UI).
    """
    current = now or utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    else:
        current = current.astimezone(timezone.utc)
    weekday = current.weekday()  # Mon=0 … Sun=6
    minute_of_day = current.hour * 60 + current.minute
    friday_close = 22 * 60
    sunday_open = 22 * 60
    if weekday == 5:  # sábado
        return False
    if weekday == 6:  # domingo
        return minute_of_day >= sunday_open
    if weekday == 4:  # sexta
        return minute_of_day < friday_close
    return True


def next_forex_open_market_at(now: datetime | None = None) -> datetime | None:
    """
    Próxima abertura do mercado forex (domingo 22:00 UTC).

    Returns:
        Datetime timezone-aware da próxima abertura, ou None se já aberto.
    """
    current = now or utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    else:
        current = current.astimezone(timezone.utc)
    if is_forex_open_market_open(current):
        return None
    # weekday: Mon=0 … Sun=6 → dias até domingo
    days_until_sunday = 0 if current.weekday() == 6 else (6 - current.weekday())
    next_open = current.replace(hour=22, minute=0, second=0, microsecond=0) + timedelta(
        days=days_until_sunday
    )
    if next_open <= current:
        next_open = next_open + timedelta(days=7)
    return next_open


def hours_until_forex_open_market(now: datetime | None = None) -> int | None:
    """
    Horas restantes (ceil) até a reabertura do mercado aberto.

    Returns:
        Inteiro de horas, ou None se a sessão forex já estiver aberta.
    """
    next_open = next_forex_open_market_at(now)
    if next_open is None:
        return None
    current = now or utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    else:
        current = current.astimezone(timezone.utc)
    seconds = max(0.0, (next_open - current).total_seconds())
    return int(math.ceil(seconds / 3600.0))


def effective_market_mode(
    value: Any,
    *,
    now: datetime | None = None,
) -> str:
    """
    Resolve o mercado efetivo da varredura/ordens.

    - BOTH → sempre OTC (operação só em OTC).
    - OPEN com sessão forex fechada → OTC (fallback seguro).
    - OPEN com sessão aberta → OPEN.
    - OTC → OTC.
    """
    mode = normalize_market_mode(value)
    if mode == "BOTH":
        return "OTC"
    if mode == "OPEN" and not is_forex_open_market_open(now):
        return "OTC"
    return mode


def coerce_selectable_market_mode(
    value: Any,
    *,
    now: datetime | None = None,
) -> str:
    """
    Normaliza a escolha do usuário para um modo ainda selecionável na UI.

    Com o mercado aberto fechado, OPEN deixa de existir e cai para OTC.
    BOTH permanece selecionável (mas opera só OTC via ``effective_market_mode``).
    """
    mode = normalize_market_mode(value)
    if mode == "OPEN" and not is_forex_open_market_open(now):
        return "OTC"
    return mode


def resolve_analysis_assets(
    market_mode: str | None = None,
    *,
    now: datetime | None = None,
) -> list[str]:
    mode = effective_market_mode(market_mode, now=now)
    if mode == "OPEN":
        return list(ANALYSIS_ASSETS_OPEN)
    # OTC e BOTH (efetivo): só pares *-OTC. BOTH nunca varre aberto —
    # evita payout null, timeouts em cadeia e perda da janela 0–5s.
    return list(ANALYSIS_ASSETS)


def is_binary_asset_allowed(active: str) -> bool:
    return normalize_binary_active(active) in BINARY_ALLOWED_ASSET_SET


def select_analysis_assets_for_cycle(
    user_id: str,
    *,
    max_assets: int | None,
    market_mode: str | None = None,
) -> list[str]:
    """Seleciona ativos a partir do primeiro item ainda não concluído.

    Args:
        user_id: Identificador autenticado do dono do ciclo.
        max_assets: Quantidade máxima de ativos que o ciclo pode analisar.
        market_mode: Mercado OTC, aberto ou ambos.

    Returns:
        Lista circular de ativos, iniciada no cursor persistido do usuário.
    """
    assets = resolve_analysis_assets(market_mode)
    if not assets:
        return []
    state = auto_trader.get(user_id)
    persisted_offset = max(0, int(getattr(state, "analysis_asset_cursor", 0) or 0))
    offset = analysis_asset_queue_offsets.get(user_id, persisted_offset) % len(assets)
    limit = len(assets) if max_assets is None or max_assets <= 0 else min(max_assets, len(assets))
    selected = [assets[(offset + index) % len(assets)] for index in range(limit)]
    logger.info(
        "[ANALYSIS_ASSET_QUEUE] user_id=%s market_mode=%s offset=%s limit=%s assets=%s",
        user_id,
        normalize_market_mode(market_mode),
        offset,
        limit,
        ",".join(selected),
    )
    return selected


def advance_analysis_asset_cursor(
    user_id: str,
    *,
    market_mode: str | None = None,
) -> None:
    """Avança e persiste o cursor após concluir a análise de um ativo.

    Args:
        user_id: Identificador autenticado do dono do ciclo.
        market_mode: Mercado usado para determinar o tamanho da fila.

    Returns:
        None.
    """
    assets = resolve_analysis_assets(market_mode)
    if not assets:
        return
    state = auto_trader.get(user_id)
    current = analysis_asset_queue_offsets.get(
        user_id,
        max(0, int(getattr(state, "analysis_asset_cursor", 0) or 0)),
    )
    next_offset = (current + 1) % len(assets)
    analysis_asset_queue_offsets[user_id] = next_offset
    state.analysis_asset_cursor = next_offset


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[str, tuple[str, WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, user_id: str, active: str, websocket: WebSocket) -> None:
        await websocket.accept()
        previous: WebSocket | None = None
        async with self._lock:
            existing = self._connections.get(user_id)
            if existing is not None and existing[1] is not websocket:
                previous = existing[1]
            self._connections[user_id] = (active, websocket)
        if previous is not None:
            with suppress(Exception):
                await previous.close(code=1000)

    async def disconnect(self, user_id: str, active: str, websocket: WebSocket) -> None:
        async with self._lock:
            existing = self._connections.get(user_id)
            if existing is not None and existing == (active, websocket):
                self._connections.pop(user_id, None)

    async def disconnect_user(self, user_id: str) -> None:
        async with self._lock:
            existing = self._connections.pop(user_id, None)
        if existing is not None:
            with suppress(Exception):
                await existing[1].close(code=1000)

    async def broadcast_to_user_active(self, user_id: str, active: str, payload: dict[str, Any]) -> None:
        async with self._lock:
            existing = self._connections.get(user_id)
            targets = [existing[1]] if existing is not None and existing[0] == active else []
        for websocket in targets:
            try:
                await websocket.send_json(payload)
            except Exception:
                logger.exception("falha ao enviar payload WS para %s %s", user_id, active)
                await self.disconnect(user_id, active, websocket)


manager = ConnectionManager()
robot_state_ws_hub = RobotStateWsHub()
robot_bus = RobotBus()
admin_dashboard_warmer: AdminDashboardWarmer | None = None


@dataclass
class BullexResponseCacheEntry:
    status_code: int
    payload: dict[str, Any]
    expires_at: datetime


@dataclass
class BullexUserSessionCache:
    responses: dict[str, BullexResponseCacheEntry] = field(default_factory=dict)
    last_successful_responses: dict[str, BullexResponseCacheEntry] = field(default_factory=dict)
    failure_count: int = 0
    next_retry_at: datetime | None = None
    offline_until: datetime | None = None
    last_request_at: dict[str, datetime] = field(default_factory=dict)
    assets_failure_count: int = 0
    assets_next_retry_at: datetime | None = None


session_response_cache: dict[str, BullexUserSessionCache] = {}
active_cooldowns: dict[str, dict[str, datetime]] = {}
payout_cooldowns: dict[str, dict[str, datetime]] = {}
active_users: dict[str, datetime] = {}
analysis_asset_queue_offsets: dict[str, int] = {}
background_refresh_tasks: set[tuple[str, str, str]] = set()


def get_session_cache(user_id: str) -> BullexUserSessionCache:
    return session_response_cache.setdefault(user_id, BullexUserSessionCache())


def schedule_background_refresh(
    user_id: str,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
) -> None:
    if method != "GET" or path not in {"/candles", "/payouts"}:
        return
    cache_key = build_cache_key(path, params)
    task_key = (user_id, path, cache_key)
    if task_key in background_refresh_tasks:
        return
    background_refresh_tasks.add(task_key)

    async def refresh() -> None:
        try:
            await call_bullex_service(
                method,
                path,
                user_id,
                params=params,
                allow_failure_backoff=False,
                force_refresh=True,
            )
        except Exception as exc:
            logger.info(
                "[BACKGROUND_REFRESH_SKIPPED] user_id=%s path=%s reason=%s",
                user_id,
                path,
                exc.__class__.__name__,
            )
        finally:
            background_refresh_tasks.discard(task_key)

    try:
        asyncio.create_task(refresh())
    except RuntimeError:
        background_refresh_tasks.discard(task_key)


def session_backoff_seconds(failure_count: int) -> int:
    if failure_count <= 0:
        return 0
    return SESSION_OFFLINE_TTL_SECONDS


def payload_connected_state(payload: dict[str, Any]) -> bool | None:
    payload = normalize_service_payload(payload)
    data = payload.get("data")
    if not isinstance(data, dict) or "connected" not in data:
        return None
    return bool(data.get("connected"))


def disconnected_cache_payload(*, source: str) -> dict[str, Any]:
    return build_success({"connected": False, "connection_status_source": source})


def cache_bullex_response(user_id: str, path: str, status_code: int, payload: dict[str, Any]) -> None:
    cache = get_session_cache(user_id)
    ttl_seconds = request_cache_ttl_seconds(path) or SESSION_CACHE_TTL_SECONDS
    entry = BullexResponseCacheEntry(
        status_code=status_code,
        payload=deepcopy(payload),
        expires_at=utc_now() + timedelta(seconds=ttl_seconds),
    )
    cache.responses[path] = entry
    if payload.get("ok") and payload_connected_state(payload) is not False:
        cache.last_successful_responses[path] = deepcopy(entry)


def cached_successful_response(
    user_id: str,
    cache_key: str,
) -> BullexResponseCacheEntry | None:
    cache = get_session_cache(user_id)
    cached = cache.last_successful_responses.get(cache_key)
    if cached is not None:
        return cached
    current = cache.responses.get(cache_key)
    if (
        current is not None
        and current.payload.get("ok")
        and (
            cache_key not in SESSION_CACHEABLE_PATHS
            or payload_connected_state(current.payload) is not False
        )
    ):
        return current
    return None


def add_stale_warning(payload: dict[str, Any]) -> dict[str, Any]:
    fallback = deepcopy(payload)
    fallback["warning"] = BULLEX_TEMPORARY_UNAVAILABLE
    data = fallback.get("data")
    if isinstance(data, dict):
        data["from_cache"] = True
    meta = fallback.get("meta")
    fallback["meta"] = {
        **(meta if isinstance(meta, dict) else {}),
        "source": "cache",
        "stale": True,
    }
    return fallback


def temporary_upstream_response(
    user_id: str,
    path: str,
    cache_key: str,
    *,
    reason: str,
    allow_failure_backoff: bool = True,
) -> tuple[int, dict[str, Any]]:
    # Grace também em /account: evita painel com connected:false enquanto a
    # Bullex ainda responde REAL no cache fresco.
    recent_account_payload = (
        recent_real_account_connection_payload(user_id)
        if path in {"/sessions/status", "/account"}
        else None
    )
    if recent_account_payload is not None:
        if path == "/sessions/status":
            logger.warning(
                "[SESSION_STATUS_GRACE_FROM_ACCOUNT] user_id=%s reason=%s",
                user_id,
                reason,
            )
            return 200, recent_account_payload
        logger.warning(
            "[ACCOUNT_GRACE_FROM_CACHE] user_id=%s reason=%s",
            user_id,
            reason,
        )
        cached_account = get_session_cache(user_id).responses.get("/account")
        if cached_account is not None and payload_connected_state(cached_account.payload) is True:
            return 200, add_stale_warning(deepcopy(cached_account.payload))
        memory = memory_account_fallback(user_id)
        if memory is not None:
            return 200, memory
        return 200, recent_account_payload
    if not allow_failure_backoff:
        logger.info(
            "[BACKOFF_SKIPPED_RESTORE] user_id=%s path=%s",
            user_id,
            path,
        )
        cached = cached_successful_response(user_id, cache_key)
        if cached is not None:
            return 200, add_stale_warning(cached.payload)
        return 503, build_error(BULLEX_TEMPORARY_UNAVAILABLE)
    if not is_user_active(user_id):
        logger.info(
            "[BACKOFF_SKIPPED_OFFLINE_USER] user_id=%s path=%s reason=%s",
            user_id,
            path,
            reason,
        )
        cached = cached_successful_response(user_id, cache_key)
        if cached is not None:
            return 200, add_stale_warning(cached.payload)
        return 200, disconnected_cache_payload(source="offline_user")
    cache = get_session_cache(user_id)
    cache.failure_count += 1
    cache.next_retry_at = utc_now() + timedelta(seconds=SESSION_STATUS_THROTTLE_SECONDS)
    logger.warning(
        "[UPSTREAM_ERROR_HANDLED] user_id=%s path=%s reason=%s",
        user_id,
        path,
        reason,
    )
    cached = cached_successful_response(user_id, cache_key)
    if cached is not None:
        if path == "/account":
            logger.warning(
                "[ACCOUNT_FETCH_FALLBACK] user_id=%s source=last_valid_cache reason=%s",
                user_id,
                reason,
            )
            logger.warning(
                "[ACCOUNT_CACHE_RETURNED] user_id=%s source=last_valid_cache",
                user_id,
            )
        return 200, add_stale_warning(cached.payload)
    return 503, build_error(BULLEX_TEMPORARY_UNAVAILABLE)


def reset_session_failure_counters(user_id: str) -> None:
    """Zera contadores de falha/backoff sem descartar o cache de resposta.

    Usado quando uma checagem OK acabou de gravar seu próprio cache
    (``call_bullex_service``): limpar o cache de resposta aqui apagaria a
    entrada recém-escrita e devolveria o throttle de 10s (`/sessions/status`,
    `/account`) inútil — toda checagem "conectado" voltaria a bater no
    upstream.
    """
    cache = get_session_cache(user_id)
    cache.failure_count = 0
    cache.next_retry_at = None
    cache.offline_until = None


def reset_session_connection_cache(user_id: str) -> None:
    """Apaga cache de sessão/conta desconectado para não mascarar um login novo.

    Sem isso, um POST /bullex/connect bem-sucedido era seguido de GET /bullex/status
    que ainda devolvia o `connected: false` antigo em cache — e o painel voltava
    para a tela de login da corretora.
    """
    reset_session_failure_counters(user_id)
    cache = get_session_cache(user_id)
    for path in SESSION_CACHEABLE_PATHS:
        cache.responses.pop(path, None)
        cache.last_successful_responses.pop(path, None)
        cache.last_request_at.pop(path, None)


def clear_session_recovery_status(user_id: str) -> None:
    state = auto_trader.get(user_id)
    if state.status in {STATUS_CONNECTION_BACKOFF, STATUS_WAITING_RECOVERY} and state.enabled:
        logger.info("[RECOVERY_SUCCESS] user_id=%s", user_id)
        auto_trader.defer_cycle(user_id, STATUS_WAITING_NEXT_CYCLE, wait_seconds=1, rejection_reason=None)


def clear_session_backoff(user_id: str) -> None:
    reset_session_connection_cache(user_id)
    clear_session_recovery_status(user_id)


def apply_manual_disconnect_session_state(user_id: str) -> None:
    """Limpa grace/backoff após ``POST /bullex/disconnect`` explícito.

    ``mark_session_failure(force_offline=True)`` preservava o último
    ``/account`` REAL em ``last_successful_responses``. O poll seguinte
    (``resolve_backoff_panel_payload`` / grace) devolvia ``connected:true``
    de novo — o botão Desconectar parecia não fazer nada.
    """
    reset_session_connection_cache(user_id)
    cache = get_session_cache(user_id)
    cache.failure_count = 0
    cache.offline_until = None
    cache.next_retry_at = None
    for path in SESSION_CACHEABLE_PATHS:
        cache_bullex_response(
            user_id,
            path,
            200 if path == "/account" else 404,
            disconnected_cache_payload(source="manual_disconnect"),
        )
    logger.info("[BULLEX_MANUAL_DISCONNECT_CACHE_CLEARED] user_id=%s", user_id)


def seed_connected_session_cache(
    user_id: str,
    *,
    active_mode: str | None,
    email: str | None = None,
    balance: float | None = None,
    currency: str | None = None,
) -> None:
    """Grava status/conta conectados no cache logo após login bem-sucedido."""
    status_payload = build_success(
        {
            "user_id": user_id,
            "connected": True,
            "requires_2fa": False,
            "email": email,
            "active_mode": active_mode,
        }
    )
    cache_bullex_response(user_id, "/sessions/status", 200, status_payload)
    account_data: dict[str, Any] = {
        "connected": True,
        "mode": active_mode,
        "active_mode": active_mode,
        "active_mode_from_bullex": active_mode,
        "user_id": user_id,
        "email": email,
        "requires_2fa": False,
    }
    if balance is not None:
        account_data["balance"] = balance
        if active_mode == "REAL":
            account_data["balance_real"] = balance
            account_data["active_mode_real_detected"] = True
    if currency:
        account_data["currency"] = currency
    cache_bullex_response(user_id, "/account", 200, build_success(account_data))


def mark_session_failure(user_id: str, *, offline: bool = False, force_offline: bool = False) -> None:
    if not force_offline and not is_user_active(user_id):
        logger.info(
            "[BACKOFF_SKIPPED_OFFLINE_USER] user_id=%s offline=%s",
            user_id,
            offline,
        )
        return
    cache = get_session_cache(user_id)
    cache.failure_count += 1
    now = utc_now()
    # Um "connected: false" isolado pode ser flap transitório do BullEx
    # (SESSION-CHECK/reconexão). Só derruba a sessão em cache (e o painel)
    # após falhas consecutivas confirmadas — exceto em desconexão explícita.
    offline_confirmed = force_offline or (
        offline and cache.failure_count >= OFFLINE_CONFIRMATION_FAILURES
    )
    auto_trader.defer_cycle(
        user_id,
        STATUS_WAITING_RECOVERY if offline_confirmed else STATUS_CONNECTION_BACKOFF,
        wait_seconds=SESSION_OFFLINE_TTL_SECONDS,
        rejection_reason="WAITING_RECOVERY" if offline_confirmed else "CONNECTION_BACKOFF",
        last_rejection_reason="WAITING_RECOVERY" if offline_confirmed else "CONNECTION_BACKOFF",
    )
    logger.warning(
        "[%s] user_id=%s retry_at=%s failures=%s",
        "WAITING_RECOVERY" if offline_confirmed else "USER_BACKOFF_ACTIVE",
        user_id,
        (now + timedelta(seconds=SESSION_OFFLINE_TTL_SECONDS)).isoformat(),
        cache.failure_count,
    )
    if offline_confirmed:
        cache.offline_until = now + timedelta(seconds=SESSION_OFFLINE_TTL_SECONDS)
        cache.next_retry_at = cache.offline_until
        # Preserva last_successful /account REAL para o worker retomar análise
        # sob carga (fila no bullex não é queda real da corretora).
        preserved_account = cache.last_successful_responses.get("/account")
        for path in SESSION_CACHEABLE_PATHS:
            cache.responses.pop(path, None)
            if path == "/account" and preserved_account is not None:
                data = (
                    preserved_account.payload.get("data")
                    if isinstance(preserved_account.payload, dict)
                    else None
                )
                mode = None
                if isinstance(data, dict):
                    mode = str(
                        data.get("active_mode")
                        or data.get("mode")
                        or ""
                    ).strip().upper() or None
                if mode == "REAL" and data.get("connected") is True:
                    continue
            cache_bullex_response(
                user_id,
                path,
                200 if path == "/account" else 404,
                disconnected_cache_payload(source="offline_cache"),
            )
        return

    cache.next_retry_at = now + timedelta(seconds=session_backoff_seconds(cache.failure_count))


def connection_guard_reason(user_id: str) -> tuple[str, float] | None:
    cache = get_session_cache(user_id)
    now = utc_now()
    if cache.offline_until is not None and now < cache.offline_until:
        return "offline", (cache.offline_until - now).total_seconds()
    if cache.next_retry_at is not None and now < cache.next_retry_at:
        return "backoff", (cache.next_retry_at - now).total_seconds()
    return None


def request_cache_ttl_seconds(path: str, params: dict[str, Any] | None = None) -> int | None:
    if is_order_result_path(path):
        return ORDER_RESULT_CACHE_TTL_SECONDS
    if path == "/sessions/status":
        return SESSION_CACHE_TTL_SECONDS
    if path == "/account":
        return ACCOUNT_CACHE_TTL_SECONDS
    if path == "/assets":
        return ASSETS_CACHE_TTL_SECONDS
    if path == "/payouts":
        return PAYOUT_CACHE_TTL_SECONDS
    if path == "/candles":
        return CANDLES_CACHE_TTL_SECONDS
    return None


def metric_label_for_path(path: str) -> str | None:
    if path == "/candles":
        return "CANDLES_FETCH_MS"
    if path == "/payouts":
        return "PAYOUT_FETCH_MS"
    if path == "/account":
        return "ACCOUNT_FETCH_MS"
    return None


def log_fetch_metric(path: str, started_at: float, *, user_id: str, source: str, status_code: int | None = None) -> None:
    label = metric_label_for_path(path)
    if label is None:
        return
    elapsed_ms = int((monotonic() - started_at) * 1000)
    logger.info(
        "[%s] user_id=%s path=%s source=%s status_code=%s ms=%s",
        label,
        user_id,
        path,
        source,
        status_code,
        elapsed_ms,
    )


def stale_successful_response(
    user_id: str,
    cache_key: str,
    *,
    max_age_seconds: int = STALE_MARKET_DATA_SECONDS,
) -> BullexResponseCacheEntry | None:
    cached = cached_successful_response(user_id, cache_key)
    if cached is None:
        return None
    stale_until = cached.expires_at + timedelta(seconds=max_age_seconds)
    return cached if utc_now() <= stale_until else None


def cached_market_response(
    user_id: str,
    path: str,
    params: dict[str, Any],
    *,
    max_age_seconds: int = STALE_MARKET_DATA_SECONDS,
) -> BullexResponseCacheEntry | None:
    exact = stale_successful_response(
        user_id,
        build_cache_key(path, params),
        max_age_seconds=max_age_seconds,
    )
    if exact is not None:
        return exact

    symbol = normalize_binary_active(str(params.get("active") or ""))
    if not symbol:
        return None
    cache = get_session_cache(user_id)
    candidates: list[BullexResponseCacheEntry] = []
    for cache_key, entry in cache.last_successful_responses.items():
        if not cache_key.startswith(f"{path}?") or utc_now() > entry.expires_at + timedelta(seconds=max_age_seconds):
            continue
        if f"active={symbol}" not in cache_key:
            continue
        if path == "/candles":
            interval = str(params.get("interval") or "")
            count = str(params.get("count") or "")
            if interval and f"interval={interval}" not in cache_key:
                continue
            if count and f"count={count}" not in cache_key:
                continue
        candidates.append(entry)
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.expires_at)


def cached_candles_for_active(
    user_id: str,
    symbol: str,
    timeframe: str,
    *,
    endtime: int | None = None,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {
        "active": normalize_binary_active(symbol),
        "interval": TIMEFRAME_SECONDS[timeframe],
        "count": ROBOT_CANDLE_COUNT,
    }
    if endtime is not None:
        params["endtime"] = endtime
    cached = cached_market_response(user_id, "/candles", params)
    return extract_candles(cached.payload) if cached is not None else []


def cached_payout_for_active(user_id: str, symbol: str) -> float | None:
    normalized_symbol = normalize_binary_active(symbol)
    cached = cached_market_response(
        user_id,
        "/payouts",
        {"active": normalized_symbol},
    )
    return extract_payout(cached.payload, normalized_symbol) if cached is not None else None


def cached_payout_payload_for_active(user_id: str, symbol: str) -> dict[str, Any] | None:
    """Retorna o payload completo de ``/payouts`` em cache (inclui open_turbo/binary).

    Args:
        user_id: Identificador autenticado do usuário.
        symbol: Ativo normalizado ou bruto.

    Returns:
        Payload cacheado ou ``None`` quando ausente/expirado.
    """
    normalized_symbol = normalize_binary_active(symbol)
    cached = cached_market_response(
        user_id,
        "/payouts",
        {"active": normalized_symbol},
    )
    return cached.payload if cached is not None else None


def cached_asset_open_for_active(
    user_id: str,
    symbol: str,
    timeframe: str,
) -> bool | None:
    """Lê do cache se o canal turbo/binary do timeframe está aberto.

    Args:
        user_id: Identificador autenticado do usuário.
        symbol: Ativo a consultar.
        timeframe: Timeframe operacional (define turbo vs binary).

    Returns:
        ``True``/``False`` quando o cache tem a flag; ``None`` se desconhecido.
    """
    payload = cached_payout_payload_for_active(user_id, symbol)
    if payload is None:
        return None
    return extract_asset_open(payload, symbol, timeframe)


def execution_channel_flag_for_timeframe(timeframe: str) -> str:
    """Nome da flag de canal de execução para o timeframe operacional.

    Args:
        timeframe: Timeframe do robô (ex.: ``M1`` → turbo, ``M15`` → binary).

    Returns:
        ``open_turbo`` ou ``open_binary``.
    """
    minutes = TIMEFRAME_SECONDS.get(str(timeframe or "").strip().upper(), 60) // 60
    return "open_turbo" if 0 < minutes <= 5 else "open_binary"


def mark_execution_channel_unavailable(
    user_id: str,
    symbol: str,
    timeframe: str,
    *,
    seconds: int = UNAVAILABLE_ASSET_COOLDOWN_SECONDS,
) -> None:
    """Após rejeição da corretora, força cooldown e canal fechado no cache.

    O ``enabled``/``is_suspended`` do payout às vezes diz aberto e o ``buy``
    ainda devolve "asset is not available". Sem marcar o cache como fechado, a
    revalidação pré-ordem e o scan com candles em cache relançam o mesmo ativo.

    Args:
        user_id: Identificador autenticado.
        symbol: Ativo rejeitado na compra.
        timeframe: Timeframe operacional (define turbo vs binary).
        seconds: Duração do cooldown duro.

    Returns:
        None.
    """
    normalized = normalize_binary_active(symbol)
    if not normalized:
        return
    set_named_cooldown(
        active_cooldowns,
        user_id,
        normalized,
        seconds=seconds,
        log_label="ACTIVE_COOLDOWN",
        status=STATUS_ACTIVE_COOLDOWN,
        reason="ACTIVE_COOLDOWN",
    )
    channel_key = execution_channel_flag_for_timeframe(timeframe)
    cache = get_session_cache(user_id)
    patched = 0
    for store in (cache.responses, cache.last_successful_responses):
        for cache_key, entry in list(store.items()):
            if not cache_key.startswith("/payouts?"):
                continue
            if f"active={normalized}" not in cache_key:
                continue
            payload = deepcopy(entry.payload)
            data = payload.get("data")
            items = data if isinstance(data, list) else [data] if isinstance(data, dict) else []
            changed = False
            for item in items:
                if not isinstance(item, dict):
                    continue
                if normalize_binary_active(str(item.get("symbol") or "")) != normalized:
                    continue
                item[channel_key] = False
                item["is_open"] = False
                changed = True
            if not changed:
                continue
            store[cache_key] = BullexResponseCacheEntry(
                status_code=entry.status_code,
                payload=payload,
                expires_at=entry.expires_at,
            )
            patched += 1
    logger.warning(
        "[EXECUTION_CHANNEL_MARKED_UNAVAILABLE] user_id=%s symbol=%s channel=%s "
        "cooldown=%ss cache_entries=%s",
        user_id,
        normalized,
        channel_key,
        seconds,
        patched,
    )


def apply_execution_channel_open(
    signal: dict[str, Any],
    *,
    channel_open: bool | None,
    timeframe: str,
) -> dict[str, Any]:
    """Propaga o status do canal de execução e bloqueia se estiver fechado.

    O payout digital pode existir com o canal turbo/binary fechado — nesses
    casos a corretora rejeita a compra com "asset is not available".

    Args:
        signal: Sinal/candidato a anotar.
        channel_open: Status conhecido do canal (ou ``None``).
        timeframe: Timeframe operacional (só para log).

    Returns:
        O mesmo ``signal`` mutado, para encadeamento.
    """
    if channel_open is False:
        signal["is_open"] = False
        blocked = list(signal.get("blocked_filters") or [])
        if "ACTIVE_CLOSED" not in blocked:
            blocked.append("ACTIVE_CLOSED")
        signal["blocked_filters"] = blocked
        signal["trade_allowed"] = False
        signal["quality_reason"] = "ACTIVE_CLOSED"
        logger.warning(
            "[BINARY_CHANNEL_CLOSED] symbol=%s timeframe=%s source=signal_annotate",
            signal.get("symbol"),
            timeframe,
        )
    elif channel_open is True:
        signal["is_open"] = True
    return signal


def invalidate_account_cache(user_id: str) -> None:
    cache = get_session_cache(user_id)
    cache.responses.pop("/account", None)
    cache.last_successful_responses.pop("/account", None)
    logger.info("[ACCOUNT_CACHE_INVALIDATED] user_id=%s reason=ORDER_SENT", user_id)


def seconds_until_next_candle_after_trade(state: Any) -> float | None:
    last_trade = getattr(state, "last_trade", None)
    if not isinstance(last_trade, dict):
        return None
    if str(last_trade.get("result") or "").strip().upper() not in {"WIN", "LOSS", "TIMEOUT"}:
        return None
    finished_at = parse_datetime(last_trade.get("finished_at"))
    if finished_at is None:
        return None
    timeframe = str(getattr(state, "timeframe", "M1") or "M1").strip().upper()
    interval = TIMEFRAME_SECONDS.get(timeframe, 60)
    now = utc_now()
    seconds_since_finish = (now - finished_at).total_seconds()
    if seconds_since_finish < 0:
        return 1.0
    seconds_into_candle = now.timestamp() % interval
    remaining = interval - seconds_into_candle
    if remaining <= 0:
        remaining = interval
    if seconds_since_finish >= remaining + 0.5:
        return None
    return max(0.5, remaining)


def normalize_allowed_assets_list(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        return []
    return [
        asset
        for asset in payload
        if isinstance(asset, dict) and is_binary_asset_allowed(str(asset.get("symbol") or ""))
    ]


def build_assets_payload(
    assets: list[dict[str, Any]],
    *,
    source: str,
    stale: bool,
) -> dict[str, Any]:
    return {
        "ok": True,
        "data": assets,
        "error": None,
        "meta": {
            "source": source,
            "syncing": stale,
            "stale": stale,
        },
    }


def get_cached_response_entry(user_id: str, cache_key: str) -> BullexResponseCacheEntry | None:
    return get_session_cache(user_id).responses.get(cache_key)


def get_assets_cache_entry(user_id: str) -> BullexResponseCacheEntry | None:
    return get_cached_response_entry(user_id, "/assets")


def get_cached_assets_payload(user_id: str) -> dict[str, Any] | None:
    cached = get_assets_cache_entry(user_id)
    if cached is None:
        return None
    assets = normalize_allowed_assets_list(cached.payload.get("data"))
    if not assets:
        return None
    return build_assets_payload(assets, source="cache", stale=True)


def get_snapshot_assets_payload(user_id: str) -> dict[str, Any] | None:
    try:
        assets = normalize_allowed_assets_list(user_store.get_market_assets_snapshot(user_id))
    except Exception:
        logger.exception("falha ao carregar snapshot de market_assets para %s", user_id)
        return None
    if not assets:
        return None
    return build_assets_payload(assets, source="snapshot", stale=True)


def clear_assets_backoff(user_id: str) -> None:
    cache = get_session_cache(user_id)
    cache.assets_failure_count = 0
    cache.assets_next_retry_at = None


def schedule_assets_retry(user_id: str) -> int:
    cache = get_session_cache(user_id)
    cache.assets_failure_count += 1
    index = min(cache.assets_failure_count - 1, len(ASSETS_RETRY_BACKOFF_SECONDS) - 1)
    retry_seconds = ASSETS_RETRY_BACKOFF_SECONDS[index]
    cache.assets_next_retry_at = utc_now() + timedelta(seconds=retry_seconds)
    return retry_seconds


def assets_retry_remaining(user_id: str) -> float | None:
    retry_at = get_session_cache(user_id).assets_next_retry_at
    if retry_at is None:
        return None
    remaining = (retry_at - utc_now()).total_seconds()
    return remaining if remaining > 0 else None


def account_still_connected(user_id: str) -> bool:
    if bool(getattr(auto_trader.get(user_id), "connected", False)):
        return True
    return get_user_account_snapshot(user_id).get("connected") is True


def log_ignored_disconnect(user_id: str, path: str, payload: dict[str, Any]) -> None:
    payload = normalize_service_payload(payload)
    if not is_session_disconnected(payload):
        return
    logger.warning(
        "[DISCONNECT_IGNORED_NON_SESSION_ERROR] user_id=%s path=%s error=%s",
        user_id,
        path,
        payload.get("error"),
    )


def build_cache_key(path: str, params: dict[str, Any] | None = None) -> str:
    if not params:
        return path
    normalized_params = dict(params)
    if path == "/candles":
        try:
            interval = int(normalized_params.get("interval") or TIMEFRAME_SECONDS["M1"])
        except (TypeError, ValueError):
            interval = TIMEFRAME_SECONDS["M1"]
        try:
            endtime = int(normalized_params.get("endtime") or utc_now().timestamp())
        except (TypeError, ValueError):
            endtime = int(utc_now().timestamp())
        normalized_params["endtime"] = int(endtime // interval) * interval
    parts = [f"{key}={normalized_params[key]}" for key in sorted(normalized_params)]
    return f"{path}?{'&'.join(parts)}"


def should_throttle_session_status(user_id: str, cache_key: str) -> bool:
    cache = get_session_cache(user_id)
    now = utc_now()
    last_request_at = cache.last_request_at.get(cache_key)
    cache.last_request_at[cache_key] = now
    if last_request_at is None:
        return False
    return 0 <= (now - last_request_at).total_seconds() < SESSION_STATUS_THROTTLE_SECONDS


def cached_session_status_response(user_id: str, cache_key: str) -> tuple[int, dict[str, Any]] | None:
    cached = get_session_cache(user_id).responses.get(cache_key)
    if cached is None:
        return None
    logger.info("[SESSION_STATUS_THROTTLED] user_id=%s path=/sessions/status", user_id)
    return cached.status_code, cached.payload


def get_named_cooldown(
    store: dict[str, dict[str, datetime]],
    user_id: str,
    symbol: str,
) -> float | None:
    user_cooldowns = store.get(user_id, {})
    expires_at = user_cooldowns.get(symbol)
    if expires_at is None:
        return None
    remaining = (expires_at - utc_now()).total_seconds()
    if remaining <= 0:
        user_cooldowns.pop(symbol, None)
        if not user_cooldowns:
            store.pop(user_id, None)
        return None
    return remaining


def set_named_cooldown(
    store: dict[str, dict[str, datetime]],
    user_id: str,
    symbol: str,
    *,
    seconds: int,
    log_label: str,
    status: str,
    reason: str,
) -> None:
    normalized = normalize_binary_active(symbol)
    if not normalized:
        return
    expires_at = utc_now() + timedelta(seconds=seconds)
    store.setdefault(user_id, {})[normalized] = expires_at
    logger.warning("[%s] user_id=%s symbol=%s until=%s", log_label, user_id, normalized, expires_at.isoformat())


def active_cooldown_remaining(user_id: str, symbol: str) -> float | None:
    return get_named_cooldown(active_cooldowns, user_id, normalize_binary_active(symbol))


def payout_cooldown_remaining(user_id: str, symbol: str) -> float | None:
    return get_named_cooldown(payout_cooldowns, user_id, normalize_binary_active(symbol))


class GatewayConfig:
    def __init__(self) -> None:
        self.bullex_service_url = os.getenv("BULLEX_SERVICE_URL", "http://bullex-service:8000").rstrip("/")
        self.panel_api_key = os.getenv("PANEL_API_KEY", "")
        self.supabase_url = os.getenv("SUPABASE_URL", "").strip()
        self.supabase_service_role_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        self.app_env = os.getenv("APP_ENV", "development").strip().lower()
        # LEI 13: production → PROD_ (não PRODUCTION_)
        environment_prefix = environment_var_prefix(self.app_env)
        self.webhooks_enabled = (
            os.getenv(
                f"{environment_prefix}_WEBHOOKS_ENABLED",
                os.getenv("WEBHOOKS_ENABLED", "false"),
            ).strip().lower()
            == "true"
        )
        self.encryption_key = os.getenv(
            f"{environment_prefix}_ENCRYPTION_KEY",
            os.getenv("ENCRYPTION_KEY", ""),
        ).strip()
        self.frontend_url = os.getenv(
            "FRONTEND_URL",
            "http://localhost:5173",
        ).strip().rstrip("/")
        self.public_api_url = os.getenv(
            "PUBLIC_API_URL",
            "http://127.0.0.1:8080",
        ).strip().rstrip("/")
        self.allow_legacy_auth = (
            os.getenv("AUTH_ALLOW_LEGACY_HEADERS", "").strip().lower() == "true"
            or (self.app_env != "production" and not self.supabase_url)
        )
        self.robot_real_max_entry = float(os.getenv("ROBOT_REAL_MAX_ENTRY", str(MAX_REAL_ENTRY)))
        self.admin_emails = {
            email.strip().lower()
            for email in os.getenv("ADMIN_EMAILS", "").split(",")
            if email.strip()
        }
        self.cors_origins = [
            origin.strip()
            for origin in os.getenv(
                "CORS_ORIGINS",
                CORS_ALLOWED_ORIGINS_DEFAULT,
            ).split(",")
            if origin.strip()
        ]
        if self.webhooks_enabled:
            if not self.supabase_url or not self.supabase_service_role_key:
                raise ValueError("Webhooks exigem Supabase server-side configurado")
            EncryptionService(self.encryption_key)
            if self.app_env == "production" and not self.frontend_url.startswith("https://"):
                raise ValueError("FRONTEND_URL deve usar HTTPS em produção")
        self.emails_enabled = (
            os.getenv(
                f"{environment_prefix}_EMAILS_ENABLED",
                os.getenv("EMAILS_ENABLED", "false"),
            )
            .strip()
            .lower()
            == "true"
        )
        self.email_config = EmailConfig.from_environment(self.frontend_url)
        if self.emails_enabled and not self.email_config.enabled:
            # from_environment já valida chave/from; sincroniza flag local.
            self.emails_enabled = self.email_config.enabled
        if self.email_config.enabled and (
            not self.supabase_url or not self.supabase_service_role_key
        ):
            raise ValueError("Emails nativos exigem Supabase server-side configurado")


config = GatewayConfig()
cakto_config = CaktoConfig.from_environment()
cakto_service = CaktoService(cakto_config)
supabase_auth_service = (
    SupabaseAuthService(config.supabase_url, config.supabase_service_role_key)
    if config.supabase_url and config.supabase_service_role_key
    else None
)
auth_session_service = (
    AuthSessionService(config.supabase_url, config.supabase_service_role_key)
    if config.supabase_url and config.supabase_service_role_key
    else None
)
app = FastAPI(title="backend-gateway", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.cors_origins,
    allow_credentials=True,
    allow_methods=CORS_ALLOWED_METHODS,
    allow_headers=CORS_ALLOWED_HEADERS,
)

@app.middleware("http")
async def log_cors_allowed_origin(request: Request, call_next):
    request_id = (request.headers.get("x-request-id") or str(uuid4())).strip()
    request.state.request_id = request_id
    # Safety-net: se o on_event("startup") não ligar o hub (uvicorn/log),
    # o primeiro HTTP sobe WS push + warmer + relay Redis.
    if not getattr(app.state, "gateway_bg_booted", False):
        try:
            _boot_gateway_background_services()
            app.state.gateway_bg_booted = True
        except Exception:
            logger.exception("[GATEWAY_BG_BOOT_FAILED]")
    origin = str(request.headers.get("origin") or "").strip()
    if origin and origin in config.cors_origins:
        logger.info(
            "[CORS_ALLOWED_ORIGIN] origin=%s method=%s path=%s",
            origin,
            request.method,
            request.url.path,
        )
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        if request.url.path.startswith(("/admin", "/marketing-simulation")):
            response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["X-Content-Type-Options"] = "nosniff"
        return response
    except Exception as exc:
        if request.url.path not in BAD_GATEWAY_PROTECTED_PATHS:
            raise
        logger.warning(
            "[UPSTREAM_ERROR_HANDLED] path=%s reason=%s",
            request.url.path,
            exc.__class__.__name__,
            exc_info=True,
        )
        response = JSONResponse(
            status_code=200,
            content=build_controlled_upstream_error(exc),
        )
        if origin in config.cors_origins:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Vary"] = "Origin"
        response.headers["X-Request-ID"] = request_id
        if request.url.path.startswith(("/admin", "/marketing-simulation")):
            response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["X-Content-Type-Options"] = "nosniff"
        return response


user_store: UserStore = create_user_store()
# Cofre de credenciais Bullex: usa a mesma ENCRYPTION_KEY do ambiente (PROD_/DEV_).
# Sem chave válida, o login continua funcionando — só não persiste senha para auto-reconnect.
try:
    bullex_credentials_service: BullexCredentialsService | None = (
        BullexCredentialsService(user_store, EncryptionService(config.encryption_key))
        if config.encryption_key
        else None
    )
except ValueError:
    logger.warning("[BULLEX_CREDENTIALS_ENCRYPTION_DISABLED] ENCRYPTION_KEY inválida ou ausente")
    bullex_credentials_service = None
auto_trader = AutoTrader()
robot_persistence: RobotPersistence = create_robot_persistence()
pattern_memory = create_pattern_memory_service()
feedback_store: FeedbackStore = create_feedback_store()
# Rate-limit de auto-reconexão por usuário (evita martelar login Bullex).
bullex_auto_reconnect_at: dict[str, datetime] = {}
# Soft reconnect via SSID (não conta como login HTTP; cooldown mais curto).
bullex_ssid_reconnect_at: dict[str, datetime] = {}
# Quem clicou "Desconectar Bullex" no painel fica aqui até conectar de novo por
# vontade própria. Sem isso o botão não funcionava para quem tinha senha salva:
# a desconexão acontecia de verdade e o auto-reconnect logava de volta em
# segundos. Em 08/08 foram 356 cliques de 77 clientes — 76 deles clicando
# repetido, um chegou a 25 vezes, achando que o botão estava quebrado.
bullex_manual_disconnect: set[str] = set()
# Gate global: após requests_limit_exceeded, ninguém tenta login até o TTL.
bullex_login_rate_limited_until: datetime | None = None
BULLEX_AUTO_RECONNECT_COOLDOWN_SECONDS = 60
BULLEX_SSID_RECONNECT_COOLDOWN_SECONDS = 15
admin_repository = (
    SupabaseAdminRepository(config.supabase_url, config.supabase_service_role_key)
    if config.supabase_url and config.supabase_service_role_key
    else InMemoryAdminRepository()
)
registration_service = RegistrationService(admin_repository)

# Auth de painel: depende do repositório admin para POST /auth/register.
if auth_session_service is not None:
    app.include_router(
        create_auth_router(
            auth_session_service,
            secure_cookies=config.app_env == "production",
            registration_service=registration_service,
            on_access_token_invalidated=(
                (lambda token: supabase_auth_service.invalidate_token(token))
                if supabase_auth_service is not None
                else None
            ),
        )
    )
else:
    _auth_stub = APIRouter(tags=["auth"])

    @_auth_stub.api_route("/auth/{full_path:path}", methods=["GET", "POST", "PATCH", "DELETE"])
    async def auth_not_configured() -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={
                "ok": False,
                "error": {
                    "code": "AUTH_NOT_CONFIGURED",
                    "message": "Configure SUPABASE_URL e SUPABASE_SERVICE_ROLE_KEY no backend.",
                },
            },
        )

    app.include_router(_auth_stub)

# Conta marketing: operação real no placar; Shift+O edita métricas manualmente.
_marketing_override_by_user: dict[str, dict[str, Any]] = {}


def register_marketing_override_context(
    user_id: str,
    *,
    company_id: str,
    win_rate: int,
) -> None:
    """
    Registra contexto da conta marketing para espelhar operações no Shift+O.

    ``win_rate`` permanece disponível para geração AUTO no painel; operações
    ao vivo usam o resultado real da corretora no placar.
    """
    if not user_id or not company_id:
        return
    _marketing_override_by_user[user_id] = {
        "company_id": company_id,
        "win_rate": max(0, min(100, int(win_rate))),
    }


async def apply_marketing_result_override(
    user_id: str,
    result: str,
    profit: float,
) -> tuple[str, float]:
    """
    Se a sessão for marketing, espelha a operação real no histórico Shift+O.

    O placar usa o WIN/LOSS e o lucro reais da corretora. Alterações manuais
    de métricas continuam exclusivas do painel Shift+O.
    """
    context = _marketing_override_by_user.get(user_id)
    if not context:
        return result, profit
    normalized = str(result or "").strip().upper()
    if normalized not in {"WIN", "LOSS"}:
        return result, profit
    state = auto_trader.get(user_id)
    trade = dict(state.last_trade or {})
    amount = float(trade.get("amount") or state.entry_value or 0)
    payout = trade.get("payout")
    if payout is None:
        payout = 85
    real_profit = float(profit)
    logger.info(
        "[MARKETING_RESULT_MIRROR] user_id=%s broker_result=%s amount=%s "
        "payout=%s profit=%s",
        user_id,
        normalized,
        amount,
        payout,
        real_profit,
    )
    try:
        await admin_repository.save_simulated_trade(
            str(context["company_id"]),
            user_id,
            {
                # UUID do banco é gerado pelo Postgres; id local só alimenta
                # logs. Não usar order_id Bullex como synthetic_sequence.
                "id": f"synthetic-live-{uuid4().hex[:8]}",
                "result": normalized,
                "asset": str(trade.get("active") or trade.get("asset") or "EURUSD-OTC"),
                "direction": str(trade.get("direction") or "CALL").upper(),
                "amount": amount,
                "payout": int(round(float(payout))),
                "profit": real_profit,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "is_simulated": True,
                "source": "marketing_demo",
                "account_mode": "REAL",
                "disclaimer": "Resultado real da corretora espelhado no painel marketing",
                "broker_order_id": str(trade.get("order_id") or ""),
            },
        )
    except Exception:
        logger.exception(
            "[MARKETING_HISTORY_SYNC_FAILED] user_id=%s order_id=%s",
            user_id,
            trade.get("order_id"),
        )
    return normalized, real_profit


def sync_marketing_display_to_robot(
    user_id: str,
    history: list[dict[str, Any]],
    stats: dict[str, Any],
) -> None:
    """
    Alinha placar e histórico do robô ao histórico editável do Shift+O.

    Mantém o visual da conta marketing idêntico ao do cliente: dashboard,
    histórico e overlay leem os mesmos endpoints `/robot/*`.
    """
    if not user_id:
        return
    try:
        state = auto_trader.get(user_id)
        state.wins = max(0, int(stats.get("wins") or 0))
        state.losses = max(0, int(stats.get("losses") or 0))
        state.profit = float(stats.get("profit") or 0)
        persist_robot(user_id)
    except Exception:
        logger.exception("[MARKETING_SCORE_SYNC_FAILED] user_id=%s", user_id)
        return

    try:
        # As três fontes de `/robot/history` (banco, espelho de restauração e
        # memória) precisam sair alinhadas, senão a exclusão volta no F5.
        robot_persistence.clear_trade_history(user_id)
        robot_persistence.clear_finished_trades(user_id)
        synced_trades: list[dict[str, Any]] = []
        for item in history:
            # Operação ao vivo espelhada no Shift+O mantém o order_id da
            # Bullex para não duplicar com a linha já registrada pelo robô.
            trade_id = str(item.get("broker_order_id") or item.get("id") or "").strip()
            result = str(item.get("result") or "").strip().upper()
            if not trade_id or result not in {"WIN", "LOSS"}:
                continue
            created_at = item.get("created_at") or datetime.now(timezone.utc).isoformat()
            trade = {
                "order_id": trade_id,
                "active": str(item.get("asset") or "EURUSD-OTC"),
                "direction": str(item.get("direction") or "CALL").upper(),
                "amount": float(item.get("amount") or 0),
                "payout": item.get("payout"),
                "result": result,
                "profit": float(item.get("profit") or 0),
                "sent_at": created_at,
                "finished_at": created_at,
                "account_mode": "REAL",
                "is_gale": False,
                "gale_step": None,
            }
            robot_persistence.save_trade_history(user_id, trade)
            invalidate_daily_history_cache(user_id)
            synced_trades.append(trade)
        auto_trader.replace_history(user_id, synced_trades)
    except Exception:
        logger.exception("[MARKETING_HISTORY_SYNC_FAILED] user_id=%s", user_id)


def delete_marketing_robot_history_item(user_id: str, order_id: str) -> bool:
    """
    Remove uma operação do histórico `/robot` e ajusta o placar da sessão.

    Usado quando a exclusão na conta marketing aponta para um ``order_id`` da
    Bullex (não-UUID), que não existe em ``marketing_simulated_trades``.
    Remove a linha das três fontes lidas pelo histórico — ``robot_trade_history``,
    o espelho de restauração ``robot_trades`` e a memória do ``auto_trader`` —
    para ela não voltar no próximo ``GET /robot/history`` nem após um restart.

    Args:
        user_id: Usuário autenticado da sessão marketing.
        order_id: Identificador da operação no histórico do robô.

    Returns:
        True quando a linha foi removida de ao menos uma das fontes.
    """
    normalized_user = str(user_id or "").strip()
    normalized_order = str(order_id or "").strip()
    if not normalized_user or not normalized_order:
        return False
    try:
        deleted = robot_persistence.delete_trade_history_item(
            normalized_user,
            normalized_order,
        )
    except Exception:
        logger.exception(
            "[MARKETING_ROBOT_HISTORY_DELETE_FAILED] user_id=%s order_id=%s",
            normalized_user,
            normalized_order,
        )
        return False

    removed_memory = False
    try:
        removed_memory = bool(
            auto_trader.remove_history_trade(normalized_user, normalized_order)
        )
    except Exception:
        logger.exception(
            "[MARKETING_MEMORY_HISTORY_DELETE_FAILED] user_id=%s order_id=%s",
            normalized_user,
            normalized_order,
        )

    removed_mirror = False
    try:
        removed_mirror = bool(
            robot_persistence.delete_trade(normalized_user, normalized_order)
        )
    except Exception:
        logger.exception(
            "[MARKETING_ROBOT_TRADE_DELETE_FAILED] user_id=%s order_id=%s",
            normalized_user,
            normalized_order,
        )

    if not deleted and not removed_memory and not removed_mirror:
        return False

    if deleted:
        result = str(deleted.get("result") or "").strip().upper()
        profit = float(deleted.get("profit") or 0)
        try:
            state = auto_trader.get(normalized_user)
            if result == "WIN":
                state.wins = max(0, int(state.wins) - 1)
            elif result == "LOSS":
                state.losses = max(0, int(state.losses) - 1)
            state.profit = round(float(state.profit) - profit, 2)
            persist_robot(normalized_user)
        except Exception:
            logger.exception(
                "[MARKETING_SCORE_ADJUST_FAILED] user_id=%s order_id=%s",
                normalized_user,
                normalized_order,
            )
    return True


async def resolve_marketing_asset_payout(user_id: str, symbol: str) -> int | None:
    """
    Consulta o payout digital real do ativo na Bullex para o Shift+O.

    Args:
        user_id: Usuário autenticado da sessão marketing.
        symbol: Ativo normalizado (ex.: EURUSD-OTC).

    Returns:
        Percentual inteiro 0-100, ou None quando a corretora não responde
        ou o ativo não é permitido no robô binário.
    """
    active = normalize_binary_active(symbol)
    if not active or not is_binary_asset_allowed(active):
        logger.info(
            "[MARKETING_PAYOUT_ASSET_SKIPPED] user_id=%s symbol=%s reason=NOT_ALLOWED",
            user_id,
            symbol,
        )
        return None

    try:
        snapshot = user_store.get_market_assets_snapshot(user_id)
        for item in snapshot:
            if not isinstance(item, dict):
                continue
            if normalize_binary_active(str(item.get("symbol") or "")) != active:
                continue
            cached = item.get("payout")
            if cached is not None:
                value = int(round(float(cached)))
                if 0 <= value <= 100:
                    return value
    except Exception:
        logger.exception(
            "[MARKETING_PAYOUT_CACHE_READ_FAILED] user_id=%s symbol=%s",
            user_id,
            active,
        )

    try:
        status_code, payload = await call_bullex_service(
            "GET",
            "/payouts",
            user_id,
            params={"active": active},
        )
    except Exception:
        logger.exception(
            "[MARKETING_PAYOUT_QUERY_FAILED] user_id=%s symbol=%s",
            user_id,
            active,
        )
        return None

    payload = normalize_service_payload(
        payload,
        error="PAYOUTS_TEMPORARY_UNAVAILABLE",
    )
    if status_code >= 400 or not payload.get("ok"):
        logger.info(
            "[MARKETING_PAYOUT_UNAVAILABLE] user_id=%s symbol=%s status=%s ok=%s",
            user_id,
            active,
            status_code,
            payload.get("ok"),
        )
        return None

    extracted = extract_payout(payload, active)
    if extracted is None:
        logger.info(
            "[MARKETING_PAYOUT_MISSING] user_id=%s symbol=%s",
            user_id,
            active,
        )
        return None
    value = int(round(float(extracted)))
    if value < 0 or value > 100:
        return None
    try:
        user_store.save_market_asset_payout(user_id, active, value)
    except Exception:
        logger.exception(
            "[MARKETING_PAYOUT_CACHE_WRITE_FAILED] user_id=%s symbol=%s",
            user_id,
            active,
        )
    return value


webhook_repository = (
    SupabaseWebhookRepository(config.supabase_url, config.supabase_service_role_key)
    if (config.webhooks_enabled or config.email_config.enabled)
    and config.supabase_url
    and config.supabase_service_role_key
    else InMemoryWebhookRepository()
)
webhook_encryption_key = config.encryption_key or base64.urlsafe_b64encode(
    secrets.token_bytes(32)
).decode()
email_repository = (
    SupabaseEmailRepository(config.supabase_url, config.supabase_service_role_key)
    if config.supabase_url and config.supabase_service_role_key
    else InMemoryEmailRepository()
)
email_service = EmailService(email_repository, config.email_config)
webhook_service = WebhookService(
    webhook_repository,
    EncryptionService(webhook_encryption_key),
    emails=email_service if config.email_config.enabled else None,
)


def apply_local_admin_permissions(
    auth: dict[str, str],
    x_api_key: str | None,
) -> dict[str, str]:
    """
    Atualiza permissões do administrador allowlisted somente no localhost.

    Args:
        auth: Identidade previamente validada pelo Supabase Auth.
        x_api_key: Chave local enviada pelo frontend.

    Returns:
        Identidade original ou cópia com o catálogo administrativo atual.
    """
    local_admin = (
        config.app_env != "production"
        and config.allow_legacy_auth
        and auth.get("email", "").lower() in config.admin_emails
        and bool(config.panel_api_key)
        and bool(x_api_key)
        and secrets.compare_digest(x_api_key or "", config.panel_api_key)
    )
    if not local_admin:
        return auth
    return {
        **auth,
        "is_admin": "true",
        "permissions": ",".join(permission.value for permission in AdminPermission),
        "manageable_role_ids": "*",
    }
account_link_service = (
    SupabaseAccountLinkService(
        config.supabase_url,
        config.supabase_service_role_key,
        redirect_url=f"{config.frontend_url}/reset-password",
    )
    if (config.webhooks_enabled or config.email_config.enabled)
    and config.supabase_url
    and config.supabase_service_role_key
    else None
)
admin_management_service = AdminManagementService(
    admin_repository,
    webhooks=webhook_service
    if (config.webhooks_enabled or config.email_config.enabled)
    else None,
)
finance_repository = (
    SupabaseFinanceRepository(config.supabase_url, config.supabase_service_role_key)
    if config.supabase_url and config.supabase_service_role_key
    else InMemoryFinanceRepository()
)
finance_service = FinanceService(
    finance_repository,
    webhooks=webhook_service
    if (config.webhooks_enabled or config.email_config.enabled)
    else None,
    account_links=account_link_service,
)
robot_tasks: dict[str, asyncio.Task[None]] = {}
robot_worker_last_tick_at: dict[str, datetime] = {}
robot_worker_restart_attempted: set[str] = set()
restorable_robot_states: dict[str, dict[str, Any]] = {}
robot_state_hydrated_users: set[str] = set()
chart_candles_cache: dict[tuple[str, str, str], dict[str, Any]] = {}
# Heartbeat só do painel (poll /robot/state) — distinto do mark_user_active do worker.
panel_heartbeat_at: dict[str, datetime] = {}
PANEL_ONLINE_TTL_SECONDS = 45
CONNECTION_GRACE_SECONDS = 30
ROBOT_VALID_CACHE_SECONDS = 300


def mark_user_active(user_id: str) -> None:
    active_users[user_id] = utc_now()


def mark_panel_heartbeat(user_id: str) -> None:
    """Registra que o frontend (aba aberta) consultou o estado do robô."""
    panel_heartbeat_at[user_id] = utc_now()


def is_panel_online(user_id: str) -> bool:
    last_seen = panel_heartbeat_at.get(user_id)
    if last_seen is None:
        return False
    return (utc_now() - last_seen).total_seconds() < PANEL_ONLINE_TTL_SECONDS


def is_user_active(user_id: str) -> bool:
    last_seen = active_users.get(user_id)
    if last_seen is not None and (utc_now() - last_seen).total_seconds() < ACTIVE_USER_TTL_SECONDS:
        return True
    # Robô habilitado com worker vivo conta como atividade: o usuário pode
    # fechar a aba e o El Capo continua operando em segundo plano.
    state = auto_trader.get(user_id)
    if state.enabled and user_id in robot_tasks:
        return True
    if last_seen is not None:
        active_users.pop(user_id, None)
    return False


def inactive_user_payload(user_id: str, path: str) -> dict[str, Any]:
    cached = cached_successful_response(user_id, path)
    if cached is not None:
        return add_stale_warning(cached.payload)
    if path == "/account":
        memory = memory_account_fallback(user_id)
        if memory is not None:
            return memory
    if path in {"/account", "/sessions/status"}:
        grace = recent_real_account_connection_payload(user_id)
        if grace is not None:
            return grace
    return disconnected_cache_payload(source="offline_user")


def backoff_payload(user_id: str, remaining: float) -> dict[str, Any]:
    return build_success(
        {
            "connected": False,
            "status": "backoff",
            "retry_in": max(0, int(math.ceil(remaining))),
        }
    )


def resolve_backoff_panel_payload(user_id: str, *, path: str) -> dict[str, Any] | None:
    """Evita devolver backoff ``connected:false`` vazio ao painel.

    O early-return de ``status=backoff`` em ``/bullex/account`` e
    ``/bullex/status`` fazia o frontend trocar o snapshot bom (email/saldo)
    por um desconectado sem métricas — exatamente o flicker ao entrar/sair
    de Configurações, enquanto o robô seguia operando.

    Args:
        user_id: Usuário autenticado.
        path: ``/account`` ou ``/sessions/status``.

    Returns:
        Payload ``ok`` com a última conta REAL conhecida, ou ``None``.
    """
    if path == "/sessions/status":
        status_fallback = memory_status_fallback(user_id)
        if status_fallback is not None:
            return status_fallback
        grace = recent_real_account_connection_payload(user_id)
        if grace is not None:
            return grace
        return None

    account_fallback = memory_account_fallback(user_id)
    if account_fallback is not None:
        return account_fallback
    grace = recent_real_account_connection_payload(user_id)
    if grace is not None:
        return grace
    return None


def normalize_ws_value(value: Any) -> str:
    return str(value or "").strip()


def build_market_ws_payload(user_id: str, active: str, candle: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "candle",
        "user_id": user_id,
        "active": active,
        "time": candle.get("from") or candle.get("time"),
        "open": candle.get("open"),
        "high": candle.get("max") if "max" in candle else candle.get("high"),
        "low": candle.get("min") if "min" in candle else candle.get("low"),
        "close": candle.get("close"),
        "volume": candle.get("volume", 0),
    }


def extract_latest_candle(payload: dict[str, Any]) -> dict[str, Any] | None:
    payload = normalize_service_payload(payload)
    data = payload.get("data")
    candles: list[dict[str, Any]] = []
    if isinstance(data, list):
        candles = [item for item in data if isinstance(item, dict)]
    elif isinstance(data, dict) and isinstance(data.get("candles"), list):
        candles = [item for item in data["candles"] if isinstance(item, dict)]

    if not candles:
        return None
    return candles[-1]


def extract_candles(payload: dict[str, Any]) -> list[dict[str, Any]]:
    payload = normalize_service_payload(payload)
    data = payload.get("data")
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict) and isinstance(data.get("candles"), list):
        return [item for item in data["candles"] if isinstance(item, dict)]
    return []


def normalize_timeframe_seconds(timeframe: str | None, interval: int | None = None) -> tuple[str, int]:
    if timeframe is not None:
        normalized = str(timeframe).strip().upper()
        if normalized not in TIMEFRAME_SECONDS:
            raise HTTPException(status_code=422, detail="INVALID_TIMEFRAME")
        return normalized, TIMEFRAME_SECONDS[normalized]
    if interval is None:
        return "M1", TIMEFRAME_SECONDS["M1"]
    for label, seconds in TIMEFRAME_SECONDS.items():
        if int(interval) == seconds:
            return label, seconds
    raise HTTPException(status_code=422, detail="INVALID_TIMEFRAME")


def numeric_candle_time(candle: dict[str, Any]) -> float | None:
    value = candle.get("time")
    if value is None:
        for key in ("from", "at", "id"):
            candidate = candle.get(key)
            if candidate is not None:
                value = candidate
                break
    parsed = parse_datetime(value)
    if parsed is not None:
        return parsed.timestamp()
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def number_or_none(value: Any) -> float | int | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return int(parsed) if parsed.is_integer() else parsed


def normalize_live_candle(candle: dict[str, Any], *, interval: int, server_time: float) -> dict[str, Any] | None:
    candle_time = numeric_candle_time(candle)
    if candle_time is None:
        return None
    high = candle.get("high") if "high" in candle else candle.get("max")
    low = candle.get("low") if "low" in candle else candle.get("min")
    normalized = {
        "time": int(candle_time),
        "open": number_or_none(candle.get("open")),
        "high": number_or_none(high),
        "low": number_or_none(low),
        "close": number_or_none(candle.get("close")),
        "volume": number_or_none(candle.get("volume")) or 0,
        "is_closed": server_time >= candle_time + interval,
    }
    if normalized["high"] is None:
        normalized["high"] = normalized["close"] if normalized["close"] is not None else normalized["open"]
    if normalized["low"] is None:
        normalized["low"] = normalized["close"] if normalized["close"] is not None else normalized["open"]
    return normalized


def build_live_candles_payload(
    symbol: str,
    timeframe: str,
    interval: int,
    limit: int,
    server_time: float,
    payload: dict[str, Any],
) -> dict[str, Any]:
    current_candle_time = int(server_time // interval) * interval
    candles = [
        normalized
        for candle in extract_candles(payload)
        if (normalized := normalize_live_candle(candle, interval=interval, server_time=server_time)) is not None
    ]
    candles.sort(key=lambda item: int(item["time"]))

    latest_close = next(
        (candle.get("close") for candle in reversed(candles) if candle.get("close") is not None),
        None,
    )
    if candles and int(candles[-1]["time"]) < current_candle_time and latest_close is not None:
        candles.append(
            {
                "time": current_candle_time,
                "open": latest_close,
                "high": latest_close,
                "low": latest_close,
                "close": latest_close,
                "volume": 0,
                "is_closed": False,
            }
        )
    if candles:
        candles[-1]["is_closed"] = False
    return {
        "symbol": symbol,
        "active": symbol,
        "timeframe": timeframe,
        "interval": interval,
        "count": min(limit, len(candles)),
        "limit": limit,
        "server_time": server_time,
        "candles": candles[-limit:],
    }


def build_chart_candles_success(
    data: dict[str, Any],
    *,
    from_cache: bool,
    limit: int,
) -> dict[str, Any]:
    normalized = deepcopy(data)
    candles = normalized.get("candles")
    if isinstance(candles, list):
        normalized["candles"] = candles[-limit:]
        normalized["count"] = len(normalized["candles"])
    normalized["limit"] = limit
    normalized["from_cache"] = from_cache
    normalized["updating"] = from_cache
    payload = build_success(normalized)
    if from_cache:
        payload["warning"] = "Atualizando candles..."
    return payload


def build_chart_candles_unavailable() -> dict[str, Any]:
    return {
        "ok": False,
        "data": {
            "candles": [],
            "from_cache": False,
        },
        "error": "CANDLES_TEMPORARY_UNAVAILABLE",
        "warning": "Atualizando candles...",
    }


def is_session_disconnected(payload: dict[str, Any]) -> bool:
    payload = normalize_service_payload(payload)
    error = str(payload.get("error") or "").strip().upper()
    return error in {"SESSION_NOT_FOUND", "SESSION_DISCONNECTED"}


def payload_indicates_offline(status_code: int, payload: dict[str, Any]) -> bool:
    connected = payload_connected_state(payload)
    return status_code == 404 or is_session_disconnected(payload) or connected is False


async def close_market_websocket(websocket: WebSocket, payload: dict[str, Any]) -> None:
    try:
        await websocket.send_json(payload)
    except Exception:
        logger.exception("falha ao enviar mensagem final do websocket de mercado")
    try:
        await websocket.close(code=1008)
    except Exception:
        logger.exception("falha ao fechar websocket de mercado")


async def stream_market_updates(websocket: WebSocket, user_id: str, active: str) -> None:
    previous_signature: tuple[Any, Any] | None = None
    while True:
        try:
            status_code, payload = await call_bullex_service(
                "GET",
                "/candles",
                user_id,
                params={"active": active, "interval": 60, "count": 2},
            )
            if not payload.get("ok"):
                if is_session_disconnected(payload):
                    mark_disconnected_from_payload(user_id, payload)
                    await close_market_websocket(
                        websocket,
                        {
                            "type": "error",
                            "error": "SESSION_DISCONNECTED",
                        },
                    )
                    return
                logger.warning("[MARKET WS ERROR] user_id=%s active=%s status=%s error=%s", user_id, active, status_code, payload.get("error"))
                await websocket.send_json({"type": "warning", "error": "MARKET_STREAM_TEMPORARY_ERROR"})
                await asyncio.sleep(1)
                continue

            latest_candle = extract_latest_candle(payload)
            if latest_candle is None:
                logger.warning("[MARKET WS ERROR] user_id=%s active=%s error=UNEXPECTED_CANDLES_PAYLOAD", user_id, active)
                await websocket.send_json({"type": "warning", "error": "MARKET_STREAM_TEMPORARY_ERROR"})
                await asyncio.sleep(1)
                continue

            current_signature = (
                latest_candle.get("from") or latest_candle.get("time"),
                latest_candle.get("close"),
            )
            if current_signature != previous_signature:
                message = build_market_ws_payload(user_id, active, latest_candle)
                logger.info("[MARKET WS MESSAGE] user_id=%s active=%s payload=%s", user_id, active, message)
                await manager.broadcast_to_user_active(user_id, active, message)
                previous_signature = current_signature
        except WebSocketDisconnect:
            raise
        except Exception:
            logger.exception("[MARKET WS ERROR] user_id=%s active=%s error=UNHANDLED_STREAM_EXCEPTION", user_id, active)
            try:
                await websocket.send_json({"type": "warning", "error": "MARKET_STREAM_TEMPORARY_ERROR"})
            except Exception:
                raise
        await asyncio.sleep(1)


async def require_headers(
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
    x_user_email: str | None = Header(default=None),
    local_impersonation_cookie: str | None = Cookie(
        default=None,
        alias="elcapo-impersonation",
    ),
    secure_impersonation_cookie: str | None = Cookie(
        default=None,
        alias="__Host-elcapo-impersonation",
    ),
) -> dict[str, str]:
    impersonation_token = secure_impersonation_cookie or local_impersonation_cookie
    if supabase_auth_service is not None:
        token = extract_access_token_from_request(
            authorization,
            {
                "elcapo-access": request.cookies.get("elcapo-access") or "",
                "__Host-elcapo-access": request.cookies.get("__Host-elcapo-access") or "",
            },
        )
        if not token:
            raise HTTPException(status_code=401, detail="MISSING_BEARER_TOKEN")
        try:
            user = await supabase_auth_service.authenticate(token)
        except AuthenticationError as exc:
            raise HTTPException(status_code=401, detail="INVALID_SESSION") from exc
        if x_user_id and x_user_id.strip() != user.user_id:
            raise HTTPException(status_code=403, detail="IDENTITY_HEADER_MISMATCH")
        if x_user_email and x_user_email.strip().lower() != user.email:
            raise HTTPException(status_code=403, detail="IDENTITY_HEADER_MISMATCH")
        allowed_when_inactive = (
            request.url.path.startswith("/feedbacks")
            or request.url.path.startswith("/admin")
            or request.url.path.startswith("/billing")
            or request.url.path == "/me/access"
        )
        if not user.grant_access and not user.is_admin and not allowed_when_inactive:
            raise HTTPException(status_code=403, detail="ACCESS_INACTIVE")
        # Conta marketing opera como cliente (BullEx + robô + ciclos 1m/5m/15m).
        # Operações ao vivo usam WIN/LOSS real; Shift+O edita métricas manualmente.
        if user.account_type == "marketing" and user.marketing_mode == "simulation":
            register_marketing_override_context(
                user.user_id,
                company_id=user.company_id,
                win_rate=int(user.marketing_win_rate or 0),
            )
        authenticated = {
            "user_id": user.user_id,
            "email": user.email,
            "company_id": user.company_id,
            "is_admin": str(user.is_admin).lower(),
            "permissions": ",".join(sorted(permission.value for permission in user.permissions)),
            "manageable_role_ids": (
                "*" if user.manageable_role_ids is None else ",".join(sorted(user.manageable_role_ids))
            ),
            "grant_access": str(user.grant_access).lower(),
            "approval_status": getattr(user, "approval_status", None) or "approved",
            "account_type": user.account_type,
            "payment_status": user.payment_status,
            "expires_at": user.expires_at or "",
            "marketing_mode": user.marketing_mode or "",
            "marketing_win_rate": (
                str(user.marketing_win_rate) if user.marketing_win_rate is not None else ""
            ),
        }
        authenticated = apply_local_admin_permissions(authenticated, x_api_key)
        return await apply_impersonation(request, authenticated, impersonation_token)

    if config.app_env == "production" or not config.allow_legacy_auth:
        raise HTTPException(status_code=503, detail="SERVER_AUTH_NOT_CONFIGURED")

    require_api_key_value(x_api_key)

    user_id = (x_user_id or "").strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="MISSING_USER_ID")

    legacy_email = (x_user_email or "").strip().lower()
    legacy_is_admin = legacy_email in config.admin_emails
    authenticated = {
        "user_id": user_id,
        "email": legacy_email,
        "company_id": "00000000-0000-0000-0000-000000000001",
        "is_admin": str(legacy_is_admin).lower(),
        "permissions": (
            ",".join(permission.value for permission in AdminPermission)
            if legacy_is_admin
            else ""
        ),
        "manageable_role_ids": "*" if legacy_is_admin else "",
        "grant_access": "true",
        "approval_status": "approved",
        "account_type": "client",
        "payment_status": "paid",
        "expires_at": "",
        "marketing_mode": "",
        "marketing_win_rate": "",
    }
    return await apply_impersonation(request, authenticated, impersonation_token)


async def require_secure_headers(
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
    x_user_email: str | None = Header(default=None),
    local_support_cookie: str | None = Cookie(default=None, alias="elcapo-support"),
    secure_support_cookie: str | None = Cookie(default=None, alias="__Host-elcapo-support"),
) -> dict[str, str]:
    """
    Valida Bearer JWT Supabase ou o fallback explicitamente local.

    Em produção, headers legados e a chave do painel são sempre ignorados.
    """
    if supabase_auth_service is None:
        if config.app_env == "production" or not config.allow_legacy_auth:
            raise HTTPException(status_code=503, detail="SERVER_AUTH_NOT_CONFIGURED")
        require_api_key_value(x_api_key)
        user_id = (x_user_id or "").strip()
        email = (x_user_email or "").strip().lower()
        if not user_id:
            raise HTTPException(status_code=400, detail="MISSING_USER_ID")
        is_admin = email in config.admin_emails
        authenticated = {
            "user_id": user_id,
            "email": email,
            "company_id": "00000000-0000-0000-0000-000000000001",
            "is_admin": str(is_admin).lower(),
            "permissions": (
                ",".join(sorted(permission.value for permission in AdminPermission))
                if is_admin
                else ""
            ),
            "manageable_role_ids": "*" if is_admin else "",
            "grant_access": "true",
            "approval_status": "approved",
            "account_type": "client",
            "payment_status": "not_required" if is_admin else "paid",
            "expires_at": "",
            "marketing_mode": "",
            "marketing_win_rate": "",
        }
        return await apply_impersonation(
            request,
            authenticated,
            secure_support_cookie or local_support_cookie,
        )

    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        token = extract_access_token_from_request(
            authorization,
            {
                "elcapo-access": request.cookies.get("elcapo-access") or "",
                "__Host-elcapo-access": request.cookies.get("__Host-elcapo-access") or "",
            },
        ) or ""
    else:
        token = token.strip()
    if not token:
        raise HTTPException(status_code=401, detail="MISSING_BEARER_TOKEN")
    try:
        user = await supabase_auth_service.authenticate(token)
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail="INVALID_SESSION") from exc
    authenticated = {
        "user_id": user.user_id,
        "email": user.email,
        "company_id": user.company_id,
        "is_admin": str(user.is_admin).lower(),
        "permissions": ",".join(sorted(permission.value for permission in user.permissions)),
        "manageable_role_ids": (
            "*" if user.manageable_role_ids is None else ",".join(sorted(user.manageable_role_ids))
        ),
        "grant_access": str(user.grant_access).lower(),
        "approval_status": getattr(user, "approval_status", None) or "approved",
        "account_type": user.account_type,
        "payment_status": user.payment_status,
        "expires_at": user.expires_at or "",
        "marketing_mode": user.marketing_mode or "",
        "marketing_win_rate": (
            str(user.marketing_win_rate) if user.marketing_win_rate is not None else ""
        ),
    }
    authenticated = apply_local_admin_permissions(authenticated, x_api_key)
    support_token = secure_support_cookie or local_support_cookie
    if request.method == "DELETE" and request.url.path.startswith("/admin/support-sessions/"):
        return authenticated
    return await apply_impersonation(request, authenticated, support_token)


async def require_secure_admin(
    auth: dict[str, str] = Depends(require_secure_headers),
) -> dict[str, str]:
    """Exige perfil administrativo derivado do JWT e RBAC persistido."""
    if auth.get("is_admin") != "true":
        raise HTTPException(status_code=403, detail="FORBIDDEN")
    return auth


async def apply_impersonation(
    request: Request,
    auth: dict[str, str],
    token: str | None,
) -> dict[str, str]:
    """Aplica identidade efetiva somente leitura quando o cookie é válido."""
    if not token or auth.get("is_admin") != "true":
        return auth
    permissions = frozenset(
        AdminPermission(value)
        for value in auth.get("permissions", "").split(",")
        if value in {permission.value for permission in AdminPermission}
    )
    resolved = await admin_management_service.resolve_impersonation(
        AdminActor(
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
        ),
        token,
    )
    if resolved is None:
        return auth
    record, target = resolved
    ending_session = (
        request.method == "DELETE"
        and request.url.path in {
            "/admin/impersonations/current",
        }
    )
    support_safe_paths = {
        ("GET", "/me/access"),
        ("GET", "/bullex/account"),
        ("GET", "/bullex/status"),
        ("GET", "/bullex/balance"),
        # Leituras do robô são permitidas para o painel de suporte renderizar;
        # qualquer escrita (start/stop/config/ordens) continua bloqueada.
        ("GET", "/robot"),
        ("GET", "/robot/state"),
        ("GET", "/robot/history"),
        ("GET", "/robot/stats"),
        ("GET", "/admin/support-sessions/current"),
        ("GET", "/admin/impersonations/current"),
        ("POST", "/bullex/connect"),
        ("POST", "/bullex/disconnect"),
        ("POST", "/bullex/reconnect"),
        ("DELETE", "/bullex/credentials"),
        ("GET", "/bullex/credentials"),
    }
    if not ending_session and (request.method, request.url.path) not in support_safe_paths:
        raise HTTPException(status_code=403, detail="IMPERSONATION_READ_ONLY")
    return {
        "user_id": target.user_id,
        "email": target.email,
        "company_id": target.company_id,
        "is_admin": "false",
        "permissions": "",
        "grant_access": str(target.grant_access).lower(),
        "approval_status": target.approval_status.value,
        "account_type": target.account_type.value,
        "payment_status": target.payment_status.value,
        "expires_at": target.expires_at.isoformat() if target.expires_at else "",
        "marketing_mode": target.marketing_mode.value if target.marketing_mode else "",
        "marketing_win_rate": (
            str(target.marketing_win_rate) if target.marketing_win_rate is not None else ""
        ),
        "impersonating": "true",
        "impersonation_session_id": record.session_id,
        "impersonation_expires_at": record.expires_at.isoformat(),
        "support_session_id": record.session_id,
        "support_expires_at": record.expires_at.isoformat(),
        "actor_user_id": auth["user_id"],
        "actor_company_id": auth["company_id"],
        "actor_permissions": auth.get("permissions", ""),
    }


async def require_admin(
    auth: dict[str, str] = Depends(require_headers),
) -> dict[str, str]:
    """Exige identidade administrativa validada no servidor."""
    email = auth.get("email", "")
    legacy_admin = bool(config.allow_legacy_auth and email in config.admin_emails)
    if auth.get("is_admin") != "true" and not legacy_admin:
        raise HTTPException(status_code=403, detail="FORBIDDEN")
    if legacy_admin:
        return {
            **auth,
            "is_admin": "true",
            "permissions": ",".join(permission.value for permission in AdminPermission),
        }
    return auth


class FeedbackCreatePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    author_name: str
    content: str | None = None
    description: str | None = None
    result: str | None = None
    video_url: str | None = None
    rating: int | None = None

    def resolved_content(self) -> str:
        """Aceita `content` ou `description` (mesmo campo)."""
        value = (self.content or self.description or "").strip()
        if not value:
            raise FeedbackValidationError("Descrição é obrigatória")
        return value


class FeedbackAdminUpdatePayload(BaseModel):
    """Alteração administrativa permitida para um feedback."""

    model_config = ConfigDict(extra="forbid")

    status: str


def build_feedback_page(
    items: list[dict[str, Any]],
    limit: int,
    offset: int,
    item_view: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    """
    Monta a resposta paginada sem expor o item extra de detecção.

    Args:
        items: Registros consultados com um item extra.
        limit: Tamanho solicitado para a página.
        offset: Posição inicial da página.
        item_view: Função que remove campos não permitidos da resposta.

    Returns:
        Página com itens, indicador de continuação e próximo offset.
    """
    visible_items = items[:limit]
    return {
        "items": [item_view(item) for item in visible_items],
        "has_more": len(items) > limit,
        "next_offset": offset + len(visible_items),
    }


def require_api_key_value(x_api_key: str | None) -> None:
    if not config.panel_api_key:
        raise HTTPException(status_code=500, detail="PANEL_API_KEY_NOT_CONFIGURED")
    if x_api_key != config.panel_api_key:
        raise HTTPException(status_code=401, detail="INVALID_API_KEY")


async def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    require_api_key_value(x_api_key)


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=build_error(str(exc.detail)))


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    message = "; ".join(error["msg"] for error in exc.errors())
    return JSONResponse(status_code=422, content=build_error(message))


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    if request.url.path in BAD_GATEWAY_PROTECTED_PATHS:
        logger.warning(
            "[UPSTREAM_ERROR_HANDLED] path=%s reason=%s",
            request.url.path,
            exc.__class__.__name__,
            exc_info=True,
        )
        return JSONResponse(
            status_code=200,
            content=build_controlled_upstream_error(exc),
        )
    return JSONResponse(status_code=500, content=build_error("INTERNAL_ERROR"))


_bullex_http_client: httpx.AsyncClient | None = None


def get_bullex_http_client() -> httpx.AsyncClient:
    """
    Retorna o client HTTP keep-alive para o bullex-service.

    Returns:
        ``httpx.AsyncClient`` compartilhado do processo (timeout por request).
    """
    global _bullex_http_client
    closed = bool(getattr(_bullex_http_client, "is_closed", False))
    if _bullex_http_client is None or closed:
        _bullex_http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                BULLEX_UPSTREAM_TIMEOUT_SECONDS,
                pool=BULLEX_POOL_TIMEOUT_SECONDS,
            ),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=40),
        )
    return _bullex_http_client


def bullex_pool_stats() -> str:
    """
    Fotografia do pool de conexões do client (diagnóstico de saturação).

    Returns:
        ``"total=N ativas=N ociosas=N"`` ou ``"indisponivel"`` se o httpx mudar
        a estrutura interna (API privada, best-effort).
    """
    try:
        connections = _bullex_http_client._transport._pool.connections
        total = len(connections)
        idle = sum(1 for connection in connections if connection.is_idle())
        return f"total={total} ativas={total - idle} ociosas={idle}"
    except Exception:
        return "indisponivel"


async def aclose_bullex_http_client() -> None:
    """Fecha o client HTTP do bullex-service no shutdown."""
    global _bullex_http_client
    client = _bullex_http_client
    _bullex_http_client = None
    if client is not None and hasattr(client, "aclose") and not getattr(client, "is_closed", False):
        await client.aclose()


async def call_bullex_service(
    method: str,
    path: str,
    user_id: str,
    json_body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    *,
    allow_session_restore: bool = False,
    allow_failure_backoff: bool = True,
    force_refresh: bool = False,
) -> tuple[int, dict[str, Any]]:
    cache_key = build_cache_key(path, params)
    ttl_seconds = request_cache_ttl_seconds(path, params)
    if method == "GET" and ttl_seconds is not None and not allow_session_restore and not force_refresh:
        cache = get_session_cache(user_id)
        cached = cache.responses.get(cache_key)
        now = utc_now()
        if cached is not None and now < cached.expires_at:
            log_fetch_metric(path, monotonic(), user_id=user_id, source="fresh_cache", status_code=cached.status_code)
            if path in {"/candles", "/payouts"}:
                symbol = normalize_binary_active(str((params or {}).get("active") or ""))
                logger.info("[ACTIVE_CACHE] user_id=%s symbol=%s path=%s", user_id, symbol, path)
                schedule_background_refresh(user_id, method, path, params=params)
            elif path == "/account":
                schedule_background_refresh(user_id, method, path, params=params)
            if path == "/account":
                logger.info(
                    "[ACCOUNT_CACHE_HIT] user_id=%s ttl_remaining=%.2f",
                    user_id,
                    (cached.expires_at - now).total_seconds(),
                )
                logger.info("[ACCOUNT_CACHE_RETURNED] user_id=%s source=fresh_cache", user_id)
            elif path == "/sessions/status":
                logger.info(
                    "[SESSION_STATUS_CACHE_HIT] user_id=%s path=%s ttl_remaining=%.2f",
                    user_id,
                    path,
                    (cached.expires_at - now).total_seconds(),
                )
            elif is_order_result_path(path):
                logger.info(
                    "[ORDER_RESULT_POLL_THROTTLED] user_id=%s path=%s ttl_remaining=%.2f",
                    user_id,
                    path,
                    (cached.expires_at - now).total_seconds(),
                )
            else:
                logger.info(
                    "[CACHE_HIT] user_id=%s path=%s ttl_remaining=%.2f",
                    user_id,
                    path,
                    (cached.expires_at - now).total_seconds(),
                )
            return cached.status_code, deepcopy(cached.payload)
        if path in SESSION_CACHEABLE_PATHS and not is_user_active(user_id):
            logger.info("[OFFLINE_USER_SKIPPED] user_id=%s path=%s", user_id, path)
            logger.info("[BACKOFF_SKIPPED_OFFLINE_USER] user_id=%s path=%s", user_id, path)
            return 200, inactive_user_payload(user_id, cache_key)
        if path == "/sessions/status" and should_throttle_session_status(user_id, cache_key):
            throttled = cached_session_status_response(user_id, cache_key)
            if throttled is not None:
                return throttled
        if path == "/sessions/status":
            logger.info("[SESSION_STATUS_CACHE_MISS] user_id=%s path=%s", user_id, path)
        else:
            logger.info("[CACHE_MISS] user_id=%s path=%s", user_id, path)
        if path in SESSION_CACHEABLE_PATHS:
            guard = connection_guard_reason(user_id)
        else:
            guard = None
        if guard is not None:
            reason, remaining = guard
            logger.warning("[SESSION_CHECK_SKIPPED] user_id=%s path=%s reason=%s retry_in=%.2f", user_id, path, reason, remaining)
            if reason == "offline":
                logger.warning("[USER_OFFLINE_SKIPPED] user_id=%s path=%s retry_in=%.2f", user_id, path, remaining)
            else:
                logger.warning("[BACKOFF_ACTIVE] user_id=%s path=%s retry_in=%.2f", user_id, path, remaining)
            logger.warning("[CPU_LOOP_PROTECTION] user_id=%s path=%s reason=%s", user_id, path, reason)
            successful = cached_successful_response(user_id, cache_key)
            if successful is not None:
                if path == "/account":
                    logger.warning(
                        "[ACCOUNT_FETCH_FALLBACK] user_id=%s source=last_valid_cache reason=%s",
                        user_id,
                        reason,
                    )
                    logger.warning(
                        "[ACCOUNT_CACHE_RETURNED] user_id=%s source=last_valid_cache",
                        user_id,
                    )
                return 200, add_stale_warning(successful.payload)
            # Preferir sessão REAL conhecida a inventar connected:false (backoff
            # sem last_successful). Isso impedia o front de abrir /robot/start.
            if path in {"/account", "/sessions/status"}:
                grace = recent_real_account_connection_payload(user_id)
                if grace is not None and path == "/sessions/status":
                    logger.warning(
                        "[BACKOFF_GRACE_FROM_ACCOUNT] user_id=%s path=%s reason=%s",
                        user_id,
                        path,
                        reason,
                    )
                    return 200, grace
                memory = memory_account_fallback(user_id)
                if memory is not None and path == "/account":
                    logger.warning(
                        "[ACCOUNT_FETCH_FALLBACK] user_id=%s source=memory reason=%s",
                        user_id,
                        reason,
                    )
                    return 200, memory
                if cached is not None and payload_connected_state(cached.payload) is True:
                    return cached.status_code, deepcopy(cached.payload)
                # Sem cache útil: não devolver connected:false — tenta upstream.
                logger.warning(
                    "[BACKOFF_BYPASS_NO_CACHE] user_id=%s path=%s reason=%s",
                    user_id,
                    path,
                    reason,
                )
            elif cached is not None:
                return cached.status_code, deepcopy(cached.payload)
            else:
                return 200, backoff_payload(user_id, remaining)
        # Market data has per-active isolation; session backoff must not stop the robot cycle.

    headers = {"x-user-id": user_id}
    if allow_session_restore:
        headers["x-allow-session-restore"] = "true"
    url = f"{config.bullex_service_url}{path}"
    timeout_seconds = (
        BULLEX_CONNECT_TIMEOUT_SECONDS
        if method == "POST" and path == "/sessions/connect"
        else BULLEX_MARKET_DATA_TIMEOUT_SECONDS
        if method == "GET" and path in {"/candles", "/payouts"}
        else BULLEX_UPSTREAM_TIMEOUT_SECONDS
    )

    request_started_at = monotonic()
    try:
        # Client HTTP reutilizado (keep-alive) — abrir AsyncClient por chamada
        # saturava sockets sob polling de dezenas de usuários.
        client = get_bullex_http_client()
        response = await asyncio.wait_for(
            client.request(
                method=method,
                url=url,
                headers=headers,
                json=json_body,
                params=params,
                # Folga proposital: o httpx expira ANTES do wait_for e encerra a
                # request por conta própria, devolvendo a conexão ao pool. O
                # wait_for permanece no valor original, só como rede de
                # segurança contra travamento real.
                timeout=max(0.05, timeout_seconds - BULLEX_CLIENT_TIMEOUT_MARGIN_SECONDS),
            ),
            timeout=timeout_seconds,
        )
    except (asyncio.TimeoutError, httpx.TimeoutException) as timeout_exc:
        log_fetch_metric(path, request_started_at, user_id=user_id, source="timeout")
        # Diagnóstico puro: NÃO altera o fluxo de erro já existente abaixo.
        # `PoolTimeout` = a request morreu na fila do pool, sem sequer sair para
        # o bullex-service — assinatura da queda de 08/08.
        logger.warning(
            "[BULLEX_POOL_STATS] user_id=%s path=%s kind=%s %s",
            user_id,
            path,
            type(timeout_exc).__name__,
            bullex_pool_stats(),
        )
        if path == "/account":
            logger.warning(
                "[ACCOUNT_FETCH_TIMEOUT] user_id=%s timeout_seconds=%s",
                user_id,
                timeout_seconds,
            )
            logger.warning(
                "[ACCOUNT_TIMEOUT_HANDLED] user_id=%s timeout_seconds=%s",
                user_id,
                timeout_seconds,
            )
        if method == "POST" and path == "/sessions/connect":
            logger.warning(
                "[CONNECT_TIMEOUT_HANDLED] user_id=%s timeout_seconds=%s",
                user_id,
                timeout_seconds,
            )
            return 504, build_error("LOGIN_TIMEOUT")
        if method == "GET" and path in SESSION_CACHEABLE_PATHS:
            return temporary_upstream_response(
                user_id,
                path,
                cache_key,
                reason="timeout",
                allow_failure_backoff=allow_failure_backoff,
            )
        logger.warning(
            "[UPSTREAM_ERROR_HANDLED] user_id=%s path=%s reason=timeout",
            user_id,
            path,
        )
        if method == "GET" and path in {"/candles", "/payouts"}:
            stale = stale_successful_response(user_id, cache_key)
            if stale is not None:
                logger.warning(
                    "[MARKET_DATA_STALE_FALLBACK] user_id=%s path=%s cache_key=%s reason=timeout",
                    user_id,
                    path,
                    cache_key,
                )
                logger.info(
                    "[ACTIVE_CACHE] user_id=%s symbol=%s path=%s stale=true",
                    user_id,
                    normalize_binary_active(str((params or {}).get("active") or "")),
                    path,
                )
                return 200, add_stale_warning(stale.payload)
            symbol = normalize_binary_active(str((params or {}).get("active") or ""))
            if symbol:
                set_named_cooldown(
                    active_cooldowns if path == "/candles" else payout_cooldowns,
                    user_id,
                    symbol,
                    seconds=ACTIVE_COOLDOWN_SECONDS if path == "/candles" else PAYOUT_COOLDOWN_SECONDS,
                    log_label="ACTIVE_TIMEOUT" if path == "/candles" else "PAYOUT_TIMEOUT",
                    status=STATUS_ACTIVE_COOLDOWN if path == "/candles" else STATUS_PAYOUT_COOLDOWN,
                    reason="ACTIVE_TIMEOUT" if path == "/candles" else "PAYOUT_TIMEOUT",
                )
        return 503, build_error(BULLEX_TEMPORARY_UNAVAILABLE)
    except httpx.HTTPError as exc:
        log_fetch_metric(path, request_started_at, user_id=user_id, source=exc.__class__.__name__)
        if method == "GET" and path == "/payouts":
            symbol = normalize_binary_active(str((params or {}).get("active") or ""))
            if symbol:
                set_named_cooldown(
                    payout_cooldowns,
                    user_id,
                    symbol,
                    seconds=PAYOUT_COOLDOWN_SECONDS,
                    log_label="PAYOUT_COOLDOWN",
                    status=STATUS_PAYOUT_COOLDOWN,
                    reason="PAYOUT_COOLDOWN",
                )
        if method == "GET" and path in SESSION_CACHEABLE_PATHS:
            return temporary_upstream_response(
                user_id,
                path,
                cache_key,
                reason=exc.__class__.__name__,
                allow_failure_backoff=allow_failure_backoff,
            )
        logger.warning(
            "[UPSTREAM_ERROR_HANDLED] user_id=%s path=%s reason=%s",
            user_id,
            path,
            exc.__class__.__name__,
        )
        if method == "GET" and path in {"/candles", "/payouts"}:
            stale = stale_successful_response(user_id, cache_key)
            if stale is not None:
                logger.warning(
                    "[MARKET_DATA_STALE_FALLBACK] user_id=%s path=%s cache_key=%s reason=%s",
                    user_id,
                    path,
                    cache_key,
                    exc.__class__.__name__,
                )
                logger.info(
                    "[ACTIVE_CACHE] user_id=%s symbol=%s path=%s stale=true",
                    user_id,
                    normalize_binary_active(str((params or {}).get("active") or "")),
                    path,
                )
                return 200, add_stale_warning(stale.payload)
            symbol = normalize_binary_active(str((params or {}).get("active") or ""))
            if symbol:
                set_named_cooldown(
                    active_cooldowns if path == "/candles" else payout_cooldowns,
                    user_id,
                    symbol,
                    seconds=ACTIVE_COOLDOWN_SECONDS if path == "/candles" else PAYOUT_COOLDOWN_SECONDS,
                    log_label="ACTIVE_SKIPPED",
                    status=STATUS_ACTIVE_COOLDOWN if path == "/candles" else STATUS_PAYOUT_COOLDOWN,
                    reason=exc.__class__.__name__,
                )
        return 503, build_error(BULLEX_TEMPORARY_UNAVAILABLE)
    except Exception as exc:
        log_fetch_metric(path, request_started_at, user_id=user_id, source=exc.__class__.__name__)
        logger.warning(
            "[UPSTREAM_ERROR_HANDLED] user_id=%s path=%s reason=%s",
            user_id,
            path,
            exc.__class__.__name__,
            exc_info=True,
        )
        if method == "GET" and path in SESSION_CACHEABLE_PATHS:
            return temporary_upstream_response(
                user_id,
                path,
                cache_key,
                reason=exc.__class__.__name__,
                allow_failure_backoff=allow_failure_backoff,
            )
        if method == "GET" and path in {"/candles", "/payouts"}:
            stale = stale_successful_response(user_id, cache_key)
            if stale is not None:
                logger.warning(
                    "[MARKET_DATA_STALE_FALLBACK] user_id=%s path=%s cache_key=%s reason=%s",
                    user_id,
                    path,
                    cache_key,
                    exc.__class__.__name__,
                )
                logger.info(
                    "[ACTIVE_CACHE] user_id=%s symbol=%s path=%s stale=true",
                    user_id,
                    normalize_binary_active(str((params or {}).get("active") or "")),
                    path,
                )
                return 200, add_stale_warning(stale.payload)
        return 503, build_error(BULLEX_TEMPORARY_UNAVAILABLE)

    log_fetch_metric(path, request_started_at, user_id=user_id, source="upstream", status_code=response.status_code)

    try:
        payload = response.json()
    except ValueError:
        payload = build_error("INVALID_BULLEX_RESPONSE")

    if response.status_code == 422 and path == "/orders/buy-real":
        logger.error(
            "[BUY_REAL_UPSTREAM_VALIDATION_ERROR] user_id=%s payload=%s detail=%s",
            user_id,
            strip_ai_fields(json_body or {}),
            payload,
        )
        return response.status_code, build_error("BUY_REAL_PAYLOAD_REQUIRED_FIELDS")

    response_contract_valid = (
        isinstance(payload, dict)
        and "ok" in payload
        and "data" in payload
        and "error" in payload
    )
    if not response_contract_valid:
        payload = build_success(payload) if response.is_success else build_error("INVALID_BULLEX_RESPONSE")

    if (
        method == "GET"
        and path in SESSION_CACHEABLE_PATHS
        and (response.status_code >= 500 or not response_contract_valid)
    ):
        return temporary_upstream_response(
            user_id,
            path,
            cache_key,
            reason=f"status_{response.status_code}",
            allow_failure_backoff=allow_failure_backoff,
        )

    if response.status_code >= 500:
        error = str(payload.get("error") or "").strip().upper()
        logger.warning(
            "[UPSTREAM_ERROR_HANDLED] user_id=%s path=%s reason=status_%s",
            user_id,
            path,
            response.status_code,
        )
        if method == "POST" and path == "/sessions/connect" and error == "LOGIN_TIMEOUT":
            logger.warning("[CONNECT_TIMEOUT_HANDLED] user_id=%s source=upstream", user_id)
            return 504, build_error("LOGIN_TIMEOUT")
        if method == "GET" and path in {"/candles", "/payouts"}:
            stale = stale_successful_response(user_id, cache_key)
            if stale is not None:
                logger.warning(
                    "[MARKET_DATA_STALE_FALLBACK] user_id=%s path=%s cache_key=%s reason=status_%s",
                    user_id,
                    path,
                    cache_key,
                    response.status_code,
                )
                logger.info(
                    "[ACTIVE_CACHE] user_id=%s symbol=%s path=%s stale=true",
                    user_id,
                    normalize_binary_active(str((params or {}).get("active") or "")),
                    path,
                )
                return 200, add_stale_warning(stale.payload)
        return 503, build_error(BULLEX_TEMPORARY_UNAVAILABLE)

    cacheable_success = (
        method == "GET"
        and ttl_seconds is not None
        and (
            (
                response.is_success
                and payload.get("ok")
                and (
                    path not in SESSION_CACHEABLE_PATHS
                    or payload_connected_state(payload) is not False
                )
            )
            or is_order_result_path(path)
        )
    )
    if cacheable_success:
        entry = BullexResponseCacheEntry(
            status_code=response.status_code,
            payload=deepcopy(payload),
            expires_at=utc_now() + timedelta(seconds=ttl_seconds),
        )
        cache = get_session_cache(user_id)
        cache.responses[cache_key] = entry
        cache.last_successful_responses[cache_key] = deepcopy(entry)

    if method == "GET" and path in SESSION_CACHEABLE_PATHS:
        if payload.get("ok") and payload_connected_state(payload) is True:
            # Só reseta contadores de falha/backoff — reset_session_connection_cache
            # apagaria a própria entrada de cache escrita acima (cacheable_success),
            # inutilizando o throttle de 10s para usuários conectados.
            reset_session_failure_counters(user_id)
            clear_session_recovery_status(user_id)
        elif payload_indicates_offline(response.status_code, payload):
            if allow_failure_backoff:
                mark_session_failure(user_id, offline=True)
            else:
                logger.info(
                    "[BACKOFF_SKIPPED_RESTORE] user_id=%s path=%s",
                    user_id,
                    path,
                )
        else:
            if allow_failure_backoff:
                mark_session_failure(user_id)
            else:
                logger.info(
                    "[BACKOFF_SKIPPED_RESTORE] user_id=%s path=%s",
                    user_id,
                    path,
                )

    if method == "GET" and path in {"/candles", "/payouts"}:
        symbol = normalize_binary_active(str((params or {}).get("active") or ""))
        error_text = str(payload.get("error") or "").strip().lower()
        if symbol and (
            response.status_code == 404
            or "asset unavailable" in error_text
            or "active suspended" in error_text
            or "active not found" in error_text
        ):
            set_named_cooldown(
                active_cooldowns,
                user_id,
                symbol,
                seconds=ACTIVE_COOLDOWN_SECONDS,
                log_label="ACTIVE_COOLDOWN",
                status=STATUS_ACTIVE_COOLDOWN,
                reason="ACTIVE_COOLDOWN",
            )
        elif path == "/payouts" and symbol and (response.status_code >= 500 or not payload.get("ok")):
            set_named_cooldown(
                payout_cooldowns,
                user_id,
                symbol,
                seconds=PAYOUT_COOLDOWN_SECONDS,
                log_label="PAYOUT_COOLDOWN",
                status=STATUS_PAYOUT_COOLDOWN,
                reason="PAYOUT_COOLDOWN",
            )

    return response.status_code, payload


def polling_headers(seconds: int) -> dict[str, str]:
    safe_seconds = max(1, int(seconds))
    return {
        "Cache-Control": f"private, max-age={safe_seconds}",
        "Retry-After": str(safe_seconds),
    }


def json_response(
    status_code: int,
    payload: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=normalize_service_payload(payload),
        headers=headers,
    )


def build_connection_payload(data: dict[str, Any], fallback_email: str | None = None) -> dict[str, Any]:
    """Monta patch de ``bullex_connections`` a partir do payload da corretora.

    Campos ``bullex_email``, ``last_balance`` e ``currency`` **nunca** são
    gravados como ``None``/string vazia — o poll de ``/account`` sob carga
    omitia esses valores e o upsert apagava o email salvo, quebrando
    auto-reconexão (``load()`` exige email + senha).
    """
    updates: dict[str, Any] = {}
    clean_fallback = str(fallback_email or "").strip()
    if clean_fallback:
        updates["bullex_email"] = clean_fallback

    field_map = {
        "email": "bullex_email",
        "connected": "connected",
        "balance": "last_balance",
        "currency": "currency",
        "mode": "account_mode",
        "active_mode": "account_mode",
        "active_mode_from_bullex": "account_mode",
        "requires_2fa": "requires_2fa",
    }
    # Nunca sobrescrever com vazio: merge-duplicates do Supabase apagaria o valor.
    preserve_if_empty = {"bullex_email", "last_balance", "currency"}
    for source_field, target_field in field_map.items():
        if source_field not in data:
            continue
        value = data[source_field]
        if target_field in preserve_if_empty:
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
        updates[target_field] = value
    if updates.get("connected") is True:
        updates["last_connected_at"] = datetime.now(timezone.utc).isoformat()
    return updates


def build_real_account_contract(payload: dict[str, Any]) -> dict[str, Any]:
    payload = normalize_service_payload(
        payload,
        error="REAL_BALANCE_NOT_DETECTED",
    )
    raw_data = payload.get("data")
    data = deepcopy(raw_data) if isinstance(raw_data, dict) else {}
    connected = bool(data.get("connected"))
    active_mode = str(
        data.get("active_mode_from_bullex")
        or data.get("active_mode")
        or data.get("mode")
        or ""
    ).strip().upper() or None
    balance_real = number_or_none(data.get("balance_real"))
    balance_practice = number_or_none(data.get("balance_practice"))
    current_balance = number_or_none(data.get("balance"))
    if active_mode == "REAL" and balance_real is None:
        balance_real = current_balance
    elif active_mode == "PRACTICE" and balance_practice is None:
        balance_practice = current_balance

    data.update(
        {
            "connected": connected,
            "active_mode_real_detected": active_mode == "REAL",
            "active_mode": active_mode,
            "active_mode_from_bullex": active_mode,
            "balance_real": balance_real,
            "balance_practice": balance_practice,
            "balance": balance_real if active_mode == "REAL" else None,
            "mode": active_mode,
        }
    )
    # Modo REAL explícito confirma a conta mesmo se o saldo vier omitido
    # temporariamente (comum no status/account). Sem isso, o poll de
    # /bullex/account tratava REAL_BALANCE_NOT_DETECTED e desligava o robô.
    if active_mode == "REAL":
        if balance_real is not None:
            logger.info("[REAL_BALANCE_DETECTED] balance=%s", balance_real)
        else:
            logger.warning(
                "[REAL_BALANCE_OMITTED] active_mode=REAL connected=%s",
                connected,
            )
        return build_success(data)
    if balance_practice is not None:
        logger.warning("[PRACTICE_BALANCE_IGNORED] balance=%s", balance_practice)
    logger.warning("[REAL_MODE_NOT_CONFIRMED] active_mode=%s", active_mode)
    return {
        "ok": False,
        "data": data,
        "error": (
            "BULLEX_ACTIVE_MODE_NOT_REAL"
            if active_mode is not None and active_mode != "REAL"
            else "REAL_BALANCE_NOT_DETECTED"
        ),
    }


def recover_real_account_contract_for_start(
    user_id: str,
    failed_contract: dict[str, Any],
) -> dict[str, Any] | None:
    """Recupera contrato REAL no start quando o /account omitiu o modo sob carga.

    Args:
        user_id: Usuário autenticado.
        failed_contract: Contrato já marcado com ``ok=False``.

    Returns:
        Novo contrato ``ok=True`` com saldo REAL do cache/snapshot, ou ``None``.
    """
    error = str(failed_contract.get("error") or "").strip().upper()
    if error != "REAL_BALANCE_NOT_DETECTED":
        return None

    cached = get_cached_account_snapshot(user_id)
    cached_balance = number_or_none(cached.get("balance"))
    if cached.get("mode") == "REAL" and cached_balance is not None and float(cached_balance) > 0:
        logger.warning(
            "[REAL_BALANCE_START_RECOVERED] user_id=%s source=account_cache balance=%s",
            user_id,
            cached_balance,
        )
        return build_real_account_contract(
            build_success(
                {
                    "connected": True,
                    "active_mode": "REAL",
                    "active_mode_from_bullex": "REAL",
                    "mode": "REAL",
                    "balance": cached_balance,
                    "balance_real": cached_balance,
                    "currency": cached.get("currency") or "BRL",
                    "email": cached.get("email"),
                }
            )
        )

    fallback = memory_account_fallback(user_id)
    if fallback is not None:
        recovered = build_real_account_contract(fallback)
        recovered_data = recovered.get("data") if isinstance(recovered.get("data"), dict) else {}
        recovered_balance = number_or_none(recovered_data.get("balance_real") or recovered_data.get("balance"))
        # Sem saldo positivo o fallback só mascara SESSION_NOT_FOUND e o start
        # caía em INSUFFICIENT_BALANCE falso (conta “conectada” sem saldo).
        if (
            recovered.get("ok")
            and recovered_balance is not None
            and float(recovered_balance) > 0
        ):
            logger.warning(
                "[REAL_BALANCE_START_RECOVERED] user_id=%s source=memory_fallback balance=%s",
                user_id,
                recovered_balance,
            )
            return recovered
        logger.warning(
            "[REAL_BALANCE_START_MEMORY_SKIPPED] user_id=%s reason=no_positive_balance balance=%s",
            user_id,
            recovered_balance,
        )

    snapshot = get_user_account_snapshot(user_id)
    snapshot_balance = number_or_none(snapshot.get("balance"))
    if (
        snapshot.get("mode") == "REAL"
        and snapshot_balance is not None
        and float(snapshot_balance) > 0
    ):
        logger.warning(
            "[REAL_BALANCE_START_RECOVERED] user_id=%s source=user_snapshot balance=%s",
            user_id,
            snapshot_balance,
        )
        return build_real_account_contract(
            build_success(
                {
                    "connected": True,
                    "active_mode": "REAL",
                    "active_mode_from_bullex": "REAL",
                    "mode": "REAL",
                    "balance": snapshot_balance,
                    "balance_real": snapshot_balance,
                    "currency": snapshot.get("currency") or "BRL",
                    "email": snapshot.get("email"),
                }
            )
        )
    return None


def recover_real_account_contract_for_poll(
    user_id: str,
    failed_contract: dict[str, Any],
) -> dict[str, Any] | None:
    """Recupera contrato REAL no poll de ``/bullex/account`` sob carga.

    O start já usava cache; o poll ainda devolvia ``ok=False`` /
    ``REAL_BALANCE_NOT_DETECTED`` ao frontend — o painel mostrava o banner
    vermelho e saldo ``-`` mesmo com o robô operando em REAL.

    Args:
        user_id: Usuário autenticado.
        failed_contract: Contrato já marcado com ``ok=False``.

    Returns:
        Contrato ``ok=True`` com modo REAL (saldo do cache/snapshot se houver),
        ou ``None`` se não houver evidência REAL.
    """
    error = str(failed_contract.get("error") or "").strip().upper()
    if error != "REAL_BALANCE_NOT_DETECTED":
        return None

    recovered = recover_real_account_contract_for_start(user_id, failed_contract)
    if recovered is not None and recovered.get("ok"):
        logger.warning(
            "[REAL_BALANCE_POLL_RECOVERED] user_id=%s source=start_cache",
            user_id,
        )
        return recovered

    fallback = memory_account_fallback(user_id)
    if fallback is not None:
        recovered = build_real_account_contract(fallback)
        if recovered.get("ok"):
            logger.warning(
                "[REAL_BALANCE_POLL_RECOVERED] user_id=%s source=memory_fallback",
                user_id,
            )
            return recovered

    state = auto_trader.get(user_id)
    active_mode = str(getattr(state, "active_mode", "") or "").strip().upper()
    robot_confirms_real = active_mode == "REAL" and (
        bool(getattr(state, "enabled", False)) or bool(getattr(state, "connected", False))
    )
    if not robot_confirms_real:
        return None

    cached = get_cached_account_snapshot(user_id)
    snapshot = get_user_account_snapshot(user_id)
    balance = number_or_none(cached.get("balance"))
    if balance is None:
        balance = number_or_none(snapshot.get("balance"))
    recovered = build_real_account_contract(
        build_success(
            {
                "connected": True,
                "active_mode": "REAL",
                "active_mode_from_bullex": "REAL",
                "mode": "REAL",
                "balance": balance,
                "balance_real": balance,
                "currency": cached.get("currency") or snapshot.get("currency") or "BRL",
                "email": cached.get("email") or snapshot.get("email"),
            }
        )
    )
    if recovered.get("ok"):
        logger.warning(
            "[REAL_BALANCE_POLL_RECOVERED] user_id=%s source=robot_state balance=%s",
            user_id,
            balance,
        )
        return recovered
    return None


def finalize_account_contract_with_poll_recovery(
    user_id: str,
    contract: dict[str, Any],
    *,
    upstream_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Aplica recuperação REAL no poll e sincroniza estado quando o contrato ok.

    Args:
        user_id: Usuário autenticado.
        contract: Contrato atual (pode estar ``ok=False``).
        upstream_payload: Payload bruto do bullex-service (para source de conexão).

    Returns:
        Contrato final (recuperado ou original).
    """
    if not contract.get("ok"):
        recovered = recover_real_account_contract_for_poll(user_id, contract)
        if recovered is not None:
            contract = recovered
    if contract.get("ok"):
        sync_user_store_from_payload(user_id, contract)
        source = "poll_recovered"
        if upstream_payload is not None:
            source = connection_source_from_payload(upstream_payload) or source
        auto_trader.sync_connection(
            user_id,
            connected=True,
            active_mode="REAL",
            source=source,
            align_status=True,
        )
    return contract


def should_stop_robot_for_account_contract(user_id: str, contract: dict[str, Any]) -> bool:
    """Decide se falha de /account deve desligar o robô.

    Só PRACTICE/DEMO explícito derruba. Saldo omitido ou contrato incompleto
    com robô já em REAL **não** chama ``require_real_mode`` (evita stop
    segundos após o start no poll do painel).
    """
    if contract.get("ok"):
        return False
    error = str(contract.get("error") or "").strip().upper()
    if error in {"BULLEX_ACTIVE_MODE_NOT_REAL", "BULLEX_ACCOUNT_STILL_PRACTICE"}:
        return True
    data = contract.get("data") if isinstance(contract.get("data"), dict) else {}
    reported_mode = str(
        data.get("active_mode") or data.get("mode") or ""
    ).strip().upper() or None
    if reported_mode in {"PRACTICE", "DEMO"}:
        return True
    if reported_mode == "REAL":
        return False
    state = auto_trader.get(user_id)
    if state.enabled and str(state.active_mode or "").strip().upper() == "REAL":
        logger.warning(
            "[ACCOUNT_CONTRACT_KEEP_ROBOT] user_id=%s error=%s kept_active_mode=REAL enabled=True",
            user_id,
            error or "unknown",
        )
        return False
    return True


def build_insufficient_balance_start_response(state: Any, *, message: str) -> dict[str, Any]:
    data = build_robot_payload(state)["data"]
    data.update(
        {
            "enabled": False,
            "worker_running": False,
            "operation_in_progress": False,
            "status": STATUS_INSUFFICIENT_BALANCE,
            "operation_message": message,
            "status_message": message,
        }
    )
    return {
        "ok": False,
        "error": STATUS_INSUFFICIENT_BALANCE,
        "message": message,
        "data": data,
    }


def credentials_saved_flag(user_id: str) -> bool:
    """Indica se o usuário tem senha Bullex criptografada salva (sem expor segredo)."""
    if bullex_credentials_service is None:
        return False
    try:
        return bullex_credentials_service.has_saved(user_id)
    except Exception:
        logger.warning("[BULLEX_CREDENTIALS_FLAG_FAILED] user_id=%s", user_id, exc_info=True)
        return False


def persist_bullex_credentials(user_id: str, email: str | None, password: str | None) -> None:
    """Criptografa e grava email/senha após connect bem-sucedido."""
    if bullex_credentials_service is None:
        return
    clean_email = str(email or "").strip()
    clean_password = str(password or "")
    if not clean_email or not clean_password:
        return
    try:
        bullex_credentials_service.save(user_id, clean_email, clean_password)
    except Exception:
        logger.warning("[BULLEX_CREDENTIALS_SAVE_FAILED] user_id=%s", user_id, exc_info=True)


def attach_credentials_meta(payload: dict[str, Any], user_id: str) -> dict[str, Any]:
    """Anexa flags públicas de credenciais salvas à resposta (nunca a senha)."""
    payload = normalize_service_payload(payload)
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    data = dict(data)
    data["credentials_saved"] = credentials_saved_flag(user_id)
    if not data.get("email"):
        try:
            record = user_store.get_user(user_id)
            if record and record.bullex_email:
                data["email"] = record.bullex_email
        except Exception:
            logger.warning("[BULLEX_CREDENTIALS_EMAIL_LOOKUP_FAILED] user_id=%s", user_id, exc_info=True)
    payload["data"] = data
    return payload


def _seed_robot_connection_after_reconnect(
    user_id: str,
    payload: dict[str, Any],
    *,
    email: str | None = None,
    is_new_connection: bool = False,
) -> bool:
    """
    Sincroniza estado do robô/cache após reconnect bem-sucedido.

    Args:
        user_id: Usuário autenticado.
        payload: Resposta normalizada do bullex-service.
        email: Email Bullex conhecido (opcional).
        is_new_connection: Se True, trata como login novo no user_store.

    Returns:
        True se ``connected=true`` e modo REAL (ou modo omitido com sessão viva).
    """
    sync_user_store_from_payload(user_id, payload, email, is_new_connection=is_new_connection)
    connected, active_mode = extract_account_status(payload)
    if not connected:
        return False
    # Status/reconnect às vezes omitem active_mode com a sessão viva.
    if active_mode is None:
        state = auto_trader.get(user_id)
        active_mode = state.active_mode if state.active_mode == "REAL" else "REAL"
    if active_mode != "REAL":
        return False
    state = auto_trader.sync_connection(
        user_id,
        connected=True,
        active_mode=active_mode,
        source="bullex_service",
        align_status=True,
    )
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    seed_connected_session_cache(
        user_id,
        active_mode=active_mode,
        email=str(data.get("email") or email or "") or None,
        balance=(float(data["balance"]) if data.get("balance") is not None else None),
        currency=str(data.get("currency") or "") or None,
    )
    if user_id in robot_state_hydrated_users or state.enabled:
        persist_robot(user_id)
    return True


async def try_ssid_session_reconnect(user_id: str) -> bool:
    """
    Restaura a sessão Bullex via SSID (sem novo login email/senha).

    Evita fechar o websocket e invalidar o login do usuário no site/app
    da corretora. Usado antes do fallback com senha salva.

    Args:
        user_id: Identificador autenticado do usuário.

    Returns:
        True se a sessão voltou ``connected`` em modo REAL.
    """
    last_at = bullex_ssid_reconnect_at.get(user_id)
    if last_at is not None:
        elapsed = (utc_now() - last_at).total_seconds()
        if elapsed < BULLEX_SSID_RECONNECT_COOLDOWN_SECONDS:
            logger.info(
                "[BULLEX_SSID_RECONNECT_COOLDOWN] user_id=%s retry_in=%.1f",
                user_id,
                BULLEX_SSID_RECONNECT_COOLDOWN_SECONDS - elapsed,
            )
            return False
    bullex_ssid_reconnect_at[user_id] = utc_now()
    mark_user_active(user_id)
    logger.info("[BULLEX_SSID_RECONNECT_START] user_id=%s", user_id)
    try:
        _status_code, payload = await call_bullex_service(
            "POST",
            "/sessions/reconnect",
            user_id,
        )
    except Exception:
        logger.warning(
            "[BULLEX_SSID_RECONNECT_FAILED] user_id=%s reason=upstream_exception",
            user_id,
            exc_info=True,
        )
        return False
    payload = normalize_service_payload(payload)
    connected, active_mode = extract_account_status(payload)
    if not connected:
        logger.info(
            "[BULLEX_SSID_RECONNECT_FAILED] user_id=%s detail=%s",
            user_id,
            payload.get("error"),
        )
        return False
    if not _seed_robot_connection_after_reconnect(user_id, payload, is_new_connection=False):
        logger.warning(
            "[BULLEX_SSID_RECONNECT_FAILED] user_id=%s connected=%s mode=%s",
            user_id,
            connected,
            active_mode,
        )
        return False
    logger.info(
        "[BULLEX_AUTO_RECONNECT_OK] user_id=%s active_mode=%s via=ssid",
        user_id,
        active_mode or "REAL",
    )
    return True


async def try_auto_reconnect_with_saved_credentials(user_id: str) -> bool:
    """
    Reconecta a Bullex preferindo SSID; senha salva só como último recurso.

    Ordem:
    1. ``POST /sessions/reconnect`` (SSID / restore) — não cria login novo.
    2. Login com email/senha criptografados (pode derrubar sessão paralela
       do usuário na corretora; usar só se o SSID falhar).

    Usado pelo robot_worker, start e painel. Rate-limited no passo de senha.

    Returns:
        True se a reconexão restabeleceu ``connected=true``.
    """
    if user_id in bullex_manual_disconnect:
        # Desconexão manual vence a reconexão automática — é decisão explícita
        # do cliente. Só `POST /bullex/connect` (ou /bullex/reconnect) libera.
        logger.info("[BULLEX_AUTO_RECONNECT_SKIPPED] user_id=%s reason=manual_disconnect", user_id)
        return False

    # Soft path primeiro: não respeita o gate de login HTTP (SSID não autentica).
    if await try_ssid_session_reconnect(user_id):
        return True

    if bullex_credentials_service is None:
        return False
    global_remaining = bullex_login_rate_limit_remaining()
    if global_remaining > 0:
        logger.info(
            "[BULLEX_AUTO_RECONNECT_RATE_LIMIT] user_id=%s retry_in=%s",
            user_id,
            global_remaining,
        )
        return False
    last_at = bullex_auto_reconnect_at.get(user_id)
    if last_at is not None:
        elapsed = (utc_now() - last_at).total_seconds()
        # last_at pode ser um "until" futuro após rate limit.
        if last_at > utc_now():
            logger.info(
                "[BULLEX_AUTO_RECONNECT_COOLDOWN] user_id=%s retry_in=%.1f",
                user_id,
                (last_at - utc_now()).total_seconds(),
            )
            return False
        if elapsed < BULLEX_AUTO_RECONNECT_COOLDOWN_SECONDS:
            logger.info(
                "[BULLEX_AUTO_RECONNECT_COOLDOWN] user_id=%s retry_in=%.1f",
                user_id,
                BULLEX_AUTO_RECONNECT_COOLDOWN_SECONDS - elapsed,
            )
            return False
    credentials = bullex_credentials_service.load(user_id)
    if credentials is None:
        logger.info("[BULLEX_AUTO_RECONNECT_SKIPPED] user_id=%s reason=no_saved_credentials", user_id)
        return False
    bullex_auto_reconnect_at[user_id] = utc_now()
    logger.info("[BULLEX_AUTO_RECONNECT_START] user_id=%s via=password", user_id)
    mark_user_active(user_id)
    reset_session_connection_cache(user_id)
    connect_body = {
        "email": credentials.email,
        "password": credentials.password,
        "account_mode": "REAL",
        "mode": "REAL",
    }
    try:
        status_code, payload = await call_bullex_service(
            "POST",
            "/sessions/connect",
            user_id,
            json_body=connect_body,
        )
    except Exception:
        logger.warning("[BULLEX_AUTO_RECONNECT_FAILED] user_id=%s reason=upstream_exception", user_id, exc_info=True)
        return False
    payload = normalize_service_payload(payload)
    if not payload.get("ok"):
        error_code, retry_after, _detail = classify_bullex_connect_error(
            payload.get("error"),
            payload.get("data") if isinstance(payload.get("data"), dict) else None,
        )
        if error_code == BULLEX_REQUESTS_LIMIT_EXCEEDED:
            note_bullex_login_rate_limit(user_id, retry_after)
        logger.warning(
            "[BULLEX_AUTO_RECONNECT_FAILED] user_id=%s detail=%s status=%s",
            user_id,
            payload.get("error"),
            status_code,
        )
        return False
    if not _seed_robot_connection_after_reconnect(
        user_id,
        payload,
        email=credentials.email,
        is_new_connection=True,
    ):
        connected, active_mode = extract_account_status(payload)
        logger.warning(
            "[BULLEX_AUTO_RECONNECT_FAILED] user_id=%s connected=%s mode=%s",
            user_id,
            connected,
            active_mode,
        )
        return False
    connected, active_mode = extract_account_status(payload)
    logger.info(
        "[BULLEX_AUTO_RECONNECT_OK] user_id=%s active_mode=%s via=password",
        user_id,
        active_mode,
    )
    return True


def mark_robot_start_blocked_without_disconnect(user_id: str, *, reason: str) -> Any:
    """
    Bloqueia o start sem marcar a conta Bullex como desconectada.

    ``disconnect_account`` força ``ACCOUNT_DISCONNECTED`` + ``enabled=False`` e
    faz o painel parecer que a corretora "saiu". Em falhas transitórias de
    /account (fila, timeout, saldo omitido) a sessão WS costuma continuar viva.

    Args:
        user_id: Usuário autenticado.
        reason: Código de log (ex.: ``ACCOUNT_CONTRACT``, ``BALANCE_UNKNOWN``).

    Returns:
        Estado atual do robô (sem wipe de conexão).
    """
    state = auto_trader.get(user_id)
    logger.warning(
        "[ROBOT_START_BLOCKED_KEEP_SESSION] user_id=%s reason=%s connected=%s active_mode=%s",
        user_id,
        reason,
        state.connected,
        state.active_mode,
    )
    return state


def finalize_connected_status_payload(
    user_id: str,
    payload: dict[str, Any],
    active_mode: str | None,
) -> dict[str, Any]:
    """Anexa robot/state ao payload de status quando a sessão Bullex está viva.

    Importante: ``active_mode=None`` (status omite o modo) **não** desliga o
    robô. Só PRACTICE/DEMO explícitos chamam ``require_real_mode``.
    """
    if active_mode == "REAL":
        state = auto_trader.sync_connection(
            user_id,
            connected=True,
            active_mode=active_mode,
            source=connection_source_from_payload(payload),
            align_status=True,
        )
    elif active_mode is not None and active_mode != "REAL":
        logger.warning(
            "[REAL_MODE_NOT_CONFIRMED] user_id=%s active_mode=%s",
            user_id,
            active_mode,
        )
        state = auto_trader.require_real_mode(user_id)
        state.connected = True
        state.active_mode = active_mode
        persist_robot(user_id)
    else:
        # Sessão viva sem modo no status — preserva REAL conhecido e o worker.
        current = auto_trader.get(user_id)
        logger.warning(
            "[ACTIVE_MODE_OMITTED_STATUS] user_id=%s kept_active_mode=%s enabled=%s",
            user_id,
            current.active_mode,
            current.enabled,
        )
        state = auto_trader.sync_connection(
            user_id,
            connected=True,
            active_mode=None,
            source=connection_source_from_payload(payload),
            align_status=False,
        )
    resolved_mode = state.active_mode or active_mode
    data = payload.get("data")
    if isinstance(data, dict):
        data["robot"] = build_robot_payload(
            state,
            connected=True,
            active_mode=resolved_mode,
            connection_checked_at=state.connection_checked_at.isoformat()
            if state.connection_checked_at is not None
            else None,
            connection_status_source=state.connection_status_source,
        )["data"]
        if resolved_mode and not data.get("active_mode"):
            data["active_mode"] = resolved_mode
            data["mode"] = resolved_mode
    return attach_credentials_meta(payload, user_id)


async def refresh_connected_status_after_auto_reconnect(user_id: str) -> dict[str, Any] | None:
    """
    Após login salvo bem-sucedido, reconsulta /sessions/status.

    Returns:
        Payload normalizado conectado, ou ``None`` se a sessão ainda estiver morta.
    """
    try:
        _, payload = await call_bullex_service("GET", "/sessions/status", user_id)
        payload = normalize_service_payload(payload)
    except Exception:
        logger.warning(
            "[BULLEX_AUTO_RECONNECT_STATUS_REFRESH_FAILED] user_id=%s",
            user_id,
            exc_info=True,
        )
        return None
    connected, active_mode = extract_account_status(payload)
    if not (payload.get("ok") and connected):
        return None
    return finalize_connected_status_payload(user_id, payload, active_mode)


async def refresh_connected_account_after_auto_reconnect(user_id: str) -> dict[str, Any] | None:
    """
    Após login salvo bem-sucedido, reconsulta /account em modo REAL.

    Returns:
        Contrato de conta conectada, ou ``None`` se ainda não houver saldo REAL.
    """
    try:
        _, payload = await call_bullex_service("GET", "/account", user_id)
        payload = normalize_service_payload(payload, error="ACCOUNT_TEMPORARY_UNAVAILABLE")
    except Exception:
        logger.warning(
            "[BULLEX_AUTO_RECONNECT_ACCOUNT_REFRESH_FAILED] user_id=%s",
            user_id,
            exc_info=True,
        )
        return None
    if not isinstance(payload.get("data"), dict):
        return None
    contract = build_real_account_contract(payload)
    if not contract.get("ok"):
        return None
    sync_user_store_from_payload(user_id, contract)
    auto_trader.sync_connection(
        user_id,
        connected=True,
        active_mode="REAL",
        source=connection_source_from_payload(payload),
        align_status=True,
    )
    return attach_credentials_meta(contract, user_id)


def sync_user_store_from_payload(
    user_id: str,
    payload: dict[str, Any],
    fallback_email: str | None = None,
    *,
    is_new_connection: bool = False,
) -> None:
    payload = normalize_service_payload(payload)
    try:
        if not payload.get("ok"):
            if payload.get("error") in {SESSION_DISCONNECTED, SESSION_NOT_FOUND}:
                user_store.disconnect(user_id)
            return

        data = payload.get("data")
        if not isinstance(data, dict):
            return

        updates = build_connection_payload(data, fallback_email)
        if updates:
            if is_new_connection:
                user_store.save_connection(user_id, updates)
            else:
                user_store.update_connection(user_id, updates)
    except Exception as exc:
        logger.warning(
            "[SUPABASE PERSISTENCE WARNING] user_id=%s operation=bullex_connection error=%s",
            user_id,
            exc,
        )


def mark_disconnected_from_payload(user_id: str, payload: dict[str, Any]) -> None:
    payload = normalize_service_payload(payload)
    if payload.get("error") not in {SESSION_DISCONNECTED, SESSION_NOT_FOUND}:
        return
    try:
        user_store.disconnect(user_id)
    except Exception:
        logger.exception("falha ao marcar sessao desconectada para %s", user_id)


def extract_account_status(payload: dict[str, Any]) -> tuple[bool, str | None]:
    payload = normalize_service_payload(payload)
    data = payload.get("data")
    if not payload.get("ok") or not isinstance(data, dict):
        return False, None
    connected = bool(data.get("connected"))
    mode = data.get("active_mode") or data.get("mode")
    return connected, str(mode).strip().upper() if mode else None


def connection_source_from_payload(payload: dict[str, Any], *, default: str = "bullex_service") -> str:
    payload = normalize_service_payload(payload)
    data = payload.get("data")
    if payload.get("ok") and isinstance(data, dict):
        raw_source = str(data.get("connection_status_source") or data.get("source") or "").strip()
        return raw_source if raw_source in {"memory", "bullex_service", "cached", "cached_grace"} else default
    return "disconnected" if is_session_disconnected(payload) else "cached"


def connection_grace_until(state: Any) -> datetime | None:
    grace_until = parse_datetime(getattr(state, "connection_grace_until", None))
    if grace_until is not None:
        return grace_until
    last_connected_at = parse_datetime(getattr(state, "last_connected_at", None))
    if last_connected_at is None:
        return None
    return last_connected_at + timedelta(seconds=CONNECTION_GRACE_SECONDS)


def connection_grace_active(state: Any) -> bool:
    grace_until = connection_grace_until(state)
    return grace_until is not None and utc_now() <= grace_until


def keep_connection_in_grace(user_id: str, state: Any, active_mode: str | None, checked_at: datetime) -> Any:
    state = auto_trader.sync_connection(
        user_id,
        connected=False,
        active_mode=state.active_mode or active_mode,
        source="cached_grace",
        checked_at=checked_at,
    )
    state.connected = True
    state.connection_grace_until = connection_grace_until(state)
    logger.warning(
        "[CONNECTION_GRACE_ACTIVE] user_id=%s failures=%s grace_until=%s",
        user_id,
        state.connection_failure_count,
        state.connection_grace_until,
    )
    return state


def sync_robot_connection_from_payload(
    user_id: str,
    payload: dict[str, Any],
    *,
    source: str | None = None,
) -> tuple[Any, bool, str | None, str]:
    state = auto_trader.get(user_id)
    connected, active_mode = extract_account_status(payload)
    checked_at = utc_now()
    resolved_source = source or connection_source_from_payload(payload)
    if connected:
        state = auto_trader.sync_connection(
            user_id,
            connected=True,
            active_mode=active_mode,
            source=resolved_source,
            checked_at=checked_at,
        )
        logger.info(
            "[ROBOT_CONNECTION_SYNCED] user_id=%s connected=true active_mode=%s source=%s",
            user_id,
            state.active_mode,
            resolved_source,
        )
        return state, True, state.active_mode or active_mode, resolved_source

    # Anti-flap: 1–2 falhas isoladas NÃO derrubam a sessão — mesmo se a janela
    # de grace de 30s já expirou (ex.: entre trades M1/M5). Sem isso o ciclo
    # chamava disconnect_account e desligava o robô (enabled=False) sem stop win.
    if state.connected and state.connection_failure_count < OFFLINE_CONFIRMATION_FAILURES:
        state = keep_connection_in_grace(user_id, state, active_mode, checked_at)
        return state, True, state.active_mode, "cached_grace"

    state = auto_trader.sync_connection(
        user_id,
        connected=False,
        active_mode=active_mode,
        source="disconnected",
        checked_at=checked_at,
    )
    logger.warning(
        "[ROBOT_CONNECTION_CHECK_FAILED] user_id=%s failures=%s source=disconnected",
        user_id,
        state.connection_failure_count,
    )
    return state, False, active_mode, "disconnected"


async def resolve_active_mode_from_account(
    user_id: str,
    *,
    fallback_mode: str | None = None,
) -> tuple[bool, str | None, dict[str, Any] | None]:
    """
    Busca /account para preencher active_mode quando /sessions/status omite o modo.

    Returns:
        Tupla (connected, active_mode, account_payload). ``account_payload`` é
        ``None`` quando a chamada falha.
    """
    try:
        _, account_payload = await call_bullex_service("GET", "/account", user_id)
    except Exception:
        logger.warning(
            "[ACTIVE_MODE_ACCOUNT_LOOKUP_FAILED] user_id=%s",
            user_id,
            exc_info=True,
        )
        return False, fallback_mode, None
    account_payload = normalize_service_payload(account_payload)
    account_connected, account_active_mode = extract_account_status(account_payload)
    return account_connected, account_active_mode or fallback_mode, account_payload


async def reconcile_robot_connection_from_payload(
    user_id: str,
    payload: dict[str, Any],
    *,
    source: str | None = None,
) -> tuple[Any, bool, str | None, str]:
    state, connected, active_mode, resolved_source = sync_robot_connection_from_payload(
        user_id,
        payload,
        source=source,
    )
    payload_connected, _ = extract_account_status(payload)
    # Status às vezes vem connected=true com active_mode=null (get_balance_mode
    # falha). Sem o modo, robot_connection_unavailable bloqueia o start mesmo
    # com /account em REAL — resolve o modo antes de retornar.
    if payload_connected and active_mode is None:
        if state.active_mode:
            logger.warning(
                "[ACTIVE_MODE_KEPT_FROM_STATE] user_id=%s active_mode=%s reason=status_omitted_mode",
                user_id,
                state.active_mode,
            )
            sync_user_store_from_payload(user_id, payload)
            return state, True, state.active_mode, resolved_source or "bullex_service"
        account_connected, account_active_mode, account_payload = await resolve_active_mode_from_account(
            user_id,
            fallback_mode=None,
        )
        if account_connected and account_active_mode and account_payload is not None:
            sync_user_store_from_payload(user_id, account_payload)
            state = auto_trader.sync_connection(
                user_id,
                connected=True,
                active_mode=account_active_mode,
                source="bullex_service",
                align_status=True,
            )
            logger.info(
                "[ACTIVE_MODE_RESOLVED_FROM_ACCOUNT] user_id=%s active_mode=%s",
                user_id,
                account_active_mode,
            )
            return state, True, account_active_mode, "bullex_service"
    if payload_connected:
        sync_user_store_from_payload(user_id, payload)
        return state, connected, active_mode or state.active_mode, resolved_source

    account_status, account_payload = await call_bullex_service("GET", "/account", user_id)
    account_connected, account_active_mode = extract_account_status(account_payload)
    if account_connected:
        sync_user_store_from_payload(user_id, account_payload)
        state = auto_trader.sync_connection(
            user_id,
            connected=True,
            active_mode=account_active_mode or active_mode,
            source="bullex_service",
            align_status=True,
        )
        logger.warning(
            "[CONNECTION_FALSE_NEGATIVE_IGNORED] user_id=%s failures=%s session_status=%s account_status=%s",
            user_id,
            state.connection_failure_count,
            payload.get("error") or payload.get("status"),
            account_status,
        )
        logger.info(
            "[ROBOT_CONNECTION_SYNCED] user_id=%s connected=true active_mode=%s source=bullex_service",
            user_id,
            state.active_mode,
        )
        return state, True, state.active_mode, "bullex_service"

    if state.connection_failure_count >= OFFLINE_CONFIRMATION_FAILURES and not connection_grace_active(state):
        state = auto_trader.defer_cycle(
            user_id,
            STATUS_WAITING_RECOVERY,
            wait_seconds=SESSION_OFFLINE_TTL_SECONDS,
            rejection_reason="WAITING_RECOVERY",
            last_rejection_reason="WAITING_RECOVERY",
            last_order_error="WAITING_RECOVERY",
        )
        state.connected = False
        state.active_mode = account_active_mode or active_mode
        logger.warning(
            "[WAITING_RECOVERY] user_id=%s failures=%s account_status=%s",
            user_id,
            state.connection_failure_count,
            account_status,
        )
        return state, False, state.active_mode, "backoff_active"

    logger.warning(
        "[CONNECTION_GRACE_ACTIVE] user_id=%s failures=%s account_connected=false grace_until=%s",
        user_id,
        state.connection_failure_count,
        state.connection_grace_until,
    )
    if not connected and connection_grace_active(state):
        state.connected = True
        state.connection_status_source = "cached_grace"
        return state, True, state.active_mode or active_mode, "cached_grace"
    return state, connected, active_mode, resolved_source


async def fetch_and_sync_robot_connection(
    user_id: str,
    *,
    allow_session_restore: bool = False,
) -> tuple[int, dict[str, Any], Any, bool, str | None, str]:
    status_code, payload = await call_bullex_service(
        "GET",
        "/sessions/status",
        user_id,
        allow_session_restore=allow_session_restore,
    )
    state, connected, active_mode, source = await reconcile_robot_connection_from_payload(user_id, payload)
    return status_code, payload, state, connected, active_mode, source


async def refresh_account_snapshot_if_needed(
    user_id: str,
    *,
    connected: bool,
    active_mode: str | None,
) -> dict[str, Any]:
    snapshot = get_user_account_snapshot(user_id)
    needs_refresh = (
        connected
        and (
            snapshot.get("connected") is not True
            or snapshot.get("balance") is None
            or snapshot.get("currency") is None
            or (active_mode is not None and snapshot.get("mode") != active_mode)
        )
    )
    if not needs_refresh:
        return snapshot
    _, payload = await call_bullex_service("GET", "/account", user_id)
    payload = normalize_service_payload(payload)
    if payload.get("ok"):
        sync_user_store_from_payload(user_id, payload)
        data = payload.get("data")
        if isinstance(data, dict):
            snapshot = get_user_account_snapshot(user_id)
            if active_mode == "REAL" and snapshot.get("balance") == 0:
                logger.info("[ACCOUNT_REAL_CONNECTED] user_id=%s balance=0 mode=REAL", user_id)
            return snapshot
    return snapshot


def fresh_robot_connection(state: Any, *, max_age_seconds: int = ROBOT_SESSION_REFRESH_SECONDS) -> bool:
    checked_at = getattr(state, "connection_checked_at", None)
    if checked_at is None:
        return False
    age = (utc_now() - checked_at).total_seconds()
    return 0 <= age <= max_age_seconds


def cached_robot_connection_payload(state: Any) -> dict[str, Any]:
    return build_success(
        {
            "connected": bool(getattr(state, "connected", False)),
            "active_mode": getattr(state, "active_mode", None),
            "server_time": None,
            "connection_status_source": getattr(state, "connection_status_source", "cached"),
        }
    )


def estimate_state_server_timestamp(state: Any) -> float | None:
    checked_at = getattr(state, "connection_checked_at", None)
    server_time = getattr(state, "server_time", None)
    if checked_at is None or not server_time:
        return None
    parsed_server_time = parse_datetime(server_time)
    if parsed_server_time is None:
        return None
    elapsed = (utc_now() - checked_at).total_seconds()
    if elapsed < 0:
        return None
    return parsed_server_time.timestamp() + elapsed


def build_guarded_connection_payload(reason: str) -> dict[str, Any]:
    return disconnected_cache_payload(source="offline_cache" if reason == "offline" else "backoff_active")


TIMEFRAME_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800}
# Janela de compra no início da vela. 0–8s: tolerância para latência de
# refresh/conta/revalidação de canal (em prod o worker perdia em ~6,3s com
# [ENTRY_WINDOW_MISSED] e descartava setups bons).
ENTRY_WINDOWS = {
    "M1": (0, 8),
    "M5": (0, 8),
    "M15": (0, 8),
    "M30": (0, 8),
}
ANALYSIS_WINDOWS = {
    "M1": (5, 20),
    "M5": (5, 20),
    "M15": (5, 20),
    "M30": (5, 20),
}
EXPIRATION_SAFETY_SECONDS = 1
ORDER_EXPIRATION_FIELDS = (
    "expected_expire_at",
    "expires_at",
    "expire_at",
    "close_time",
    "closed_at",
    "expiration_time",
    "expiration_at",
    "expiration_timestamp",
    "expire_timestamp",
    "close_timestamp",
)


def closed_candle_endtime(timestamp: int | float, timeframe: str) -> int:
    """
    Alinha a consulta ao fechamento da última vela completa do timeframe.

    Args:
        timestamp: Horário de referência em Unix timestamp.
        timeframe: Timeframe operacional suportado.

    Returns:
        Limite temporal no início da vela atual, que não entra na análise.

    Raises:
        ValueError: Se o timeframe não for suportado.
    """
    normalized_timeframe = str(timeframe or "").strip().upper()
    if normalized_timeframe not in TIMEFRAME_SECONDS:
        raise ValueError(f"Timeframe não suportado: {timeframe}")
    interval = TIMEFRAME_SECONDS[normalized_timeframe]
    return int(float(timestamp) // interval) * interval


def extract_server_timestamp(payload: dict[str, Any]) -> float | None:
    payload = normalize_service_payload(payload)
    data = payload.get("data")
    if not payload.get("ok") or not isinstance(data, dict):
        return None
    try:
        timestamp = float(data["server_time"])
    except (KeyError, TypeError, ValueError):
        return None
    return timestamp if timestamp > 0 else None


def robot_worker_entry_wait_seconds(seconds_until_entry: float | int | None) -> float:
    """
    Sleep do worker enquanto aguarda a janela de compra (início da vela).

    Não dorme a espera inteira de uma vez: acorda cedo e faz poll fino perto
    da abertura, para não perder a janela curta (0–8s).

    Args:
        seconds_until_entry: Segundos restantes até a janela abrir (0 se já aberta).

    Returns:
        Delay em segundos para o próximo tick do worker.
    """
    until = float(seconds_until_entry or 0)
    if until <= 0:
        return 0.15
    if until <= 3:
        return 0.15
    if until <= 10:
        return 0.35
    # Acorda ~0.5s antes da abertura; no máximo 4s entre heartbeats.
    return max(0.35, min(until - 0.5, 4.0))


def get_entry_window(
    timeframe: str,
    server_timestamp: float | None = None,
    *,
    server_time_source: str = "bullex",
) -> dict[str, Any]:
    normalized = str(timeframe).strip().upper()
    if normalized not in TIMEFRAME_SECONDS:
        raise ValueError("INVALID_TIMEFRAME")
    if server_timestamp is None:
        server_timestamp = utc_now().timestamp()
        server_time_source = "vps_fallback"

    expiration_seconds = TIMEFRAME_SECONDS[normalized]
    window_start, window_end = ENTRY_WINDOWS[normalized]
    analysis_window_start, analysis_window_end = ANALYSIS_WINDOWS[normalized]
    seconds_in_candle = float(server_timestamp) % expiration_seconds
    seconds_until_close = expiration_seconds - seconds_in_candle
    analysis_window_open = analysis_window_start <= seconds_in_candle <= analysis_window_end
    entry_window_open = window_start <= seconds_in_candle <= window_end
    if analysis_window_open:
        seconds_until_analysis_window = 0
    elif seconds_in_candle < analysis_window_start:
        seconds_until_analysis_window = math.ceil(analysis_window_start - seconds_in_candle)
    else:
        seconds_until_analysis_window = math.ceil(
            expiration_seconds - seconds_in_candle + analysis_window_start
        )
    missed_entry_window = seconds_in_candle > window_end
    if entry_window_open:
        seconds_until_entry_window = 0
    else:
        seconds_until_entry_window = math.ceil(expiration_seconds - seconds_in_candle + window_start)

    return {
        "server_timestamp": float(server_timestamp),
        "_captured_monotonic": monotonic(),
        "server_time": datetime.fromtimestamp(server_timestamp, timezone.utc).isoformat(),
        "server_time_source": server_time_source,
        "timeframe": normalized,
        "analysis_window_open": analysis_window_open,
        "seconds_until_analysis_window": seconds_until_analysis_window,
        "analysis_window_start_second": analysis_window_start,
        "analysis_window_end_second": analysis_window_end,
        "entry_window_open": entry_window_open,
        "missed_entry_window": missed_entry_window,
        "seconds_until_entry_window": seconds_until_entry_window,
        "current_candle_seconds": round(seconds_in_candle, 3),
        "entry_window_start_second": window_start,
        "entry_window_end_second": window_end,
        "buy_target_second": window_start,
        "seconds_until_close": round(seconds_until_close, 3),
        "expiration_seconds": expiration_seconds,
        "expiration": normalized,
        "expiration_minutes": expiration_seconds // 60,
    }


def refresh_entry_window_after_analysis(window: dict[str, Any]) -> dict[str, Any]:
    """Atualiza o relógio de entrada pelo tempo gasto durante o scan.

    Args:
        window: Contrato de janela capturado antes da análise dos ativos.

    Returns:
        Nova janela no mesmo timeframe, avançada pelo tempo monotônico decorrido.

    Raises:
        ValueError: Se o timeframe original não for suportado.
    """
    captured_at = float(window.get("_captured_monotonic") or monotonic())
    elapsed_seconds = max(0.0, monotonic() - captured_at)
    refreshed_timestamp = float(window["server_timestamp"]) + elapsed_seconds
    return get_entry_window(
        str(window["timeframe"]),
        refreshed_timestamp,
        server_time_source=str(window.get("server_time_source") or "bullex"),
    )


def refresh_cycle_entry_window(
    user_id: str,
    state: Any,
    window: dict[str, Any],
) -> dict[str, Any]:
    """Recalcula e aplica a janela usando o tempo real consumido pelo scan.

    Args:
        user_id: Identificador autenticado do dono do ciclo.
        state: Estado atual do robô.
        window: Janela capturada antes da análise.

    Returns:
        Janela atualizada e aplicada ao estado informado.
    """
    refreshed_window = refresh_entry_window_after_analysis(window)
    refreshed_state = auto_trader.update_entry_window(user_id, refreshed_window)
    if refreshed_state is not state:
        logger.debug(
            "[ENTRY_WINDOW_STATE_REFRESHED] user_id=%s previous_state_replaced=true",
            user_id,
        )
    return refreshed_window


def parse_order_expiration_value(value: Any) -> datetime | None:
    parsed = parse_datetime(value)
    if parsed is not None:
        return parsed

    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return None
    if timestamp <= 0:
        return None
    if timestamp > 1_000_000_000_000:
        timestamp = timestamp / 1000
    return datetime.fromtimestamp(timestamp, timezone.utc)


def extract_order_expiration(order_data: dict[str, Any]) -> tuple[datetime | None, str | None]:
    for field in ORDER_EXPIRATION_FIELDS:
        if field not in order_data:
            continue
        parsed = parse_order_expiration_value(order_data.get(field))
        if parsed is not None:
            return parsed, field
    return None, None


def calculate_expected_expire_at(
    timeframe: str,
    order_data: dict[str, Any],
    entry_window: dict[str, Any],
    sent_at: datetime,
) -> tuple[datetime, str]:
    """
    Calcula a expiração da ordem para o monitor de resultado.

    Nunca retorna data no passado em relação a ``sent_at`` — expiração velha
    fazia o monitor esperar só ~15s, marcar TIMEOUT (sem placar) enquanto a
    Bullex ainda fechava WIN/LOSS.
    """
    normalized_tf = str(timeframe or "M1").strip().upper()
    if normalized_tf not in TIMEFRAME_SECONDS:
        normalized_tf = "M1"
    interval = TIMEFRAME_SECONDS[normalized_tf]
    sent_at_utc = sent_at if sent_at.tzinfo is not None else sent_at.replace(tzinfo=timezone.utc)

    returned_expiration, source = extract_order_expiration(order_data)
    if returned_expiration is not None and source is not None and returned_expiration > sent_at_utc:
        return returned_expiration, source

    try:
        server_timestamp = float(entry_window["server_timestamp"])
    except (KeyError, TypeError, ValueError):
        server_timestamp = sent_at_utc.timestamp()
    # Usa o maior entre clock Bullex e sent_at real (evita server_time stale).
    reference_timestamp = max(server_timestamp, sent_at_utc.timestamp())
    aligned_timestamp = math.ceil(reference_timestamp / interval) * interval
    if aligned_timestamp <= reference_timestamp:
        aligned_timestamp += interval
    aligned_timestamp += EXPIRATION_SAFETY_SECONDS
    expected = datetime.fromtimestamp(aligned_timestamp, timezone.utc)
    minimum = sent_at_utc + timedelta(seconds=interval)
    if expected < minimum:
        return minimum, "sent_at_minimum"
    if server_timestamp + 1.0 < sent_at_utc.timestamp():
        return expected, "sent_at_realigned"
    return expected, "server_time_aligned"


async def refresh_entry_window(user_id: str, state: Any) -> tuple[int, dict[str, Any], dict[str, Any] | None]:
    if fresh_robot_connection(state) or robot_has_recent_real_cache(user_id, state):
        estimated_timestamp = estimate_state_server_timestamp(state)
        window = get_entry_window(
            state.timeframe,
            estimated_timestamp,
            server_time_source="bullex" if estimated_timestamp is not None else "vps_fallback",
        )
        auto_trader.update_entry_window(user_id, window)
        logger.info(
            "[ENTRY_WINDOW_CALCULATED] user_id=%s timeframe=%s current_candle_seconds=%s "
            "server_time_source=%s analysis_window_start=%s analysis_window_end=%s analysis_open=%s "
            "window_start=%s window_end=%s buy_target_second=%s open=%s",
            user_id,
            state.timeframe,
            window["current_candle_seconds"],
            window["server_time_source"],
            window["analysis_window_start_second"],
            window["analysis_window_end_second"],
            window["analysis_window_open"],
            window["entry_window_start_second"],
            window["entry_window_end_second"],
            window["buy_target_second"],
            window["entry_window_open"],
        )
        return 200, cached_robot_connection_payload(state), window

    status_code, payload = await call_bullex_service("GET", "/sessions/status", user_id)
    timestamp = extract_server_timestamp(payload)
    if timestamp is None:
        timestamp = utc_now().timestamp()
        window = get_entry_window(
            state.timeframe,
            timestamp,
            server_time_source="vps_fallback",
        )
        logger.warning(
            "[SERVER_TIME_FALLBACK] user_id=%s status_code=%s current_candle_seconds=%s",
            user_id,
            status_code,
            window["current_candle_seconds"],
        )
    else:
        previous_source = getattr(state, "server_time_source", None)
        window = get_entry_window(
            state.timeframe,
            timestamp,
            server_time_source="bullex",
        )
        if previous_source == "vps_fallback" and getattr(state, "server_time", None):
            logger.info("[SERVER_TIME_BULLEX_RESTORED] user_id=%s", user_id)
    auto_trader.update_entry_window(user_id, window)
    logger.info(
        "[ENTRY_WINDOW_CALCULATED] user_id=%s timeframe=%s current_candle_seconds=%s "
        "server_time_source=%s analysis_window_start=%s analysis_window_end=%s analysis_open=%s "
        "window_start=%s window_end=%s buy_target_second=%s open=%s",
        user_id,
        state.timeframe,
        window["current_candle_seconds"],
        window["server_time_source"],
        window["analysis_window_start_second"],
        window["analysis_window_end_second"],
        window["analysis_window_open"],
        window["entry_window_start_second"],
        window["entry_window_end_second"],
        window["buy_target_second"],
        window["entry_window_open"],
    )
    return status_code, payload, window


def real_block_reason(
    state: Any,
    *,
    connected: bool,
    active_mode: str | None,
    user_id: str | None = None,
) -> str | None:
    reason: str | None = None
    if state.account_mode != "REAL":
        reason = "ACCOUNT_MODE_NOT_REAL"
    elif state.operation_in_progress:
        reason = "OPERATION_IN_PROGRESS"
    elif robot_connection_unavailable(connected, active_mode):
        reason = "BULLEX_NOT_CONNECTED"
    elif active_mode != "REAL":
        reason = "BULLEX_ACTIVE_MODE_NOT_REAL"
    elif state.entry_value <= 0:
        reason = "AMOUNT_MUST_BE_POSITIVE"
    else:
        stop_reason = (daily_stop_reason(user_id, state) if user_id is not None else None) or robot_stop_reason(state)
        if stop_reason is not None:
            reason = stop_reason
        elif state.entry_value > MAX_REAL_ENTRY:
            reason = "REAL_ENTRY_VALUE_EXCEEDS_MAX"
    logger.info(
        "[REAL_READY_CHECK] user_id=%s account_mode=%s active_mode=%s connected=%s allow_real=%s confirm_real=%s reason=%s",
        user_id,
        getattr(state, "account_mode", None),
        active_mode,
        connected,
        getattr(state, "allow_real", None),
        getattr(state, "confirm_real", None),
        reason,
    )
    return reason


def validate_real_buy_gateway_payload(body: dict[str, Any]) -> str | None:
    if body.get("confirm_real") is not True:
        return "CONFIRM_REAL_REQUIRED"
    try:
        amount = float(body.get("amount"))
    except (TypeError, ValueError):
        return "AMOUNT_MUST_BE_POSITIVE"
    if amount <= 0:
        return "AMOUNT_MUST_BE_POSITIVE"
    return None


def real_buy_gateway_block_reason(user_id: str, state: Any, body: dict[str, Any]) -> str | None:
    if state.account_mode != "REAL":
        return "ACCOUNT_MODE_NOT_REAL"
    payload_reason = validate_real_buy_gateway_payload(body)
    if payload_reason is not None:
        return payload_reason
    if state.operation_in_progress:
        return "OPERATION_IN_PROGRESS"
    return daily_stop_reason(user_id, state) or robot_stop_reason(state)


def extract_payout(payload: dict[str, Any], symbol: str) -> float | None:
    payload = normalize_service_payload(payload)
    data = payload.get("data")
    if not isinstance(data, list):
        return None
    for item in data:
        if not isinstance(item, dict) or normalize_binary_active(str(item.get("symbol") or "")) != symbol:
            continue
        try:
            return float(item["payout"])
        except (KeyError, TypeError, ValueError):
            return None
    return None


def extract_asset_open(payload: dict[str, Any], symbol: str, timeframe: str) -> bool | None:
    """Lê o status de abertura do canal turbo/binary no payload de /payouts.

    O ativo é considerado "aberto" pelo payout digital, mas a ordem é enviada
    pelo canal turbo/binary. Aqui priorizamos o canal do timeframe operacional
    (turbo ≤ 5min, binary acima) para não escolher um ativo cujo canal de
    execução esteja fechado/suspenso.

    Args:
        payload: Resposta de ``/payouts`` do bullex-service.
        symbol: Ativo a consultar.
        timeframe: Timeframe operacional (ex.: ``M1``), define turbo vs binary.

    Returns:
        ``True``/``False`` quando conhecido; ``None`` quando ausente.
    """
    payload = normalize_service_payload(payload)
    data = payload.get("data")
    if not isinstance(data, list):
        return None
    normalized = normalize_binary_active(symbol)
    minutes = TIMEFRAME_SECONDS.get(str(timeframe or "").strip().upper(), 60) // 60
    channel = "open_turbo" if 0 < minutes <= 5 else "open_binary"
    for item in data:
        if not isinstance(item, dict) or normalize_binary_active(str(item.get("symbol") or "")) != normalized:
            continue
        specific = item.get(channel)
        if isinstance(specific, bool):
            return specific
        is_open = item.get("is_open")
        return is_open if isinstance(is_open, bool) else None
    return None


# `build_management_summary` roda em TODA serialização do estado (cada poll do
# painel, cada tick do ciclo, cada push do WS) e lia o histórico do dia no
# Supabase com cliente SÍNCRONO — ~1,5 leitura/segundo POR usuário, cada uma
# congelando o event loop do robot-runtime (queda de 08/08). O TTL é curto e a
# gravação de qualquer operação invalida na hora, então stop win/loss continuam
# enxergando o resultado assim que ele é registrado.
DAILY_HISTORY_CACHE_TTL_SECONDS = 5.0
_daily_history_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def invalidate_daily_history_cache(user_id: str) -> None:
    """Descarta o histórico do dia em cache (chamar ao gravar operação)."""
    _daily_history_cache.pop(str(user_id), None)


def load_daily_history_cached(user_id: str) -> list[dict[str, Any]]:
    """
    Histórico do dia com TTL curto, para não bloquear o event loop a cada poll.

    Args:
        user_id: Cliente dono do histórico.

    Returns:
        Mesma lista que ``load_robot_history_items(user_id, 1)`` retornaria.
    """
    now = monotonic()
    cached = _daily_history_cache.get(user_id)
    if cached is not None and now - cached[0] < DAILY_HISTORY_CACHE_TTL_SECONDS:
        return cached[1]
    items = load_robot_history_items(user_id, 1)
    _daily_history_cache[user_id] = (now, items)
    return items


def build_management_summary(user_id: str, state: Any) -> dict[str, Any]:
    today = datetime.now(timezone.utc).date()
    reset_at = parse_datetime(getattr(state, "stop_reset_at", None))
    gross_profit = 0.0
    gross_loss = 0.0
    net_profit = 0.0
    trades_count = 0
    try:
        trades = load_daily_history_cached(user_id)
    except Exception:
        logger.warning("[MANAGEMENT_HISTORY_FALLBACK] user_id=%s", user_id, exc_info=True)
        trades = auto_trader.history(user_id).get("trades", [])

    for trade in trades:
        result = str(trade.get("result") or trade.get("final_result") or "").strip().upper()
        if result not in {"WIN", "LOSS"}:
            continue
        finished_at = parse_datetime(trade.get("finished_at"))
        if finished_at is None or finished_at.date() != today:
            continue
        if reset_at is not None and finished_at < reset_at:
            continue
        trade_profit = float(trade.get("profit") or 0)
        trades_count += 1
        net_profit += trade_profit
        if trade_profit > 0:
            gross_profit += trade_profit
        elif trade_profit < 0:
            gross_loss += abs(trade_profit)

    stop_win = float(getattr(state, "stop_win", 0) or 0)
    stop_loss = float(getattr(state, "stop_loss", 0) or 0)
    stop_reason = resolve_robot_stop_reason(
        state,
        wins=int(getattr(state, "wins", 0) or 0),
        losses=int(getattr(state, "losses", 0) or 0),
        gross_profit=gross_profit,
        gross_loss=gross_loss,
    )

    return {
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "net_profit": round(net_profit, 2),
        "trades_count": trades_count,
        "stop_win": stop_win,
        "stop_loss": stop_loss,
        "stop_win_mode": normalize_stop_mode(getattr(state, "stop_win_mode", "money")),
        "stop_loss_mode": normalize_stop_mode(getattr(state, "stop_loss_mode", "money")),
        "stop_win_operations": int(getattr(state, "stop_win_operations", 0) or 0),
        "stop_loss_operations": int(getattr(state, "stop_loss_operations", 0) or 0),
        "stop_reason": stop_reason,
        "reset_at": reset_at.isoformat() if reset_at is not None else None,
    }


def daily_stop_reason(user_id: str, state: Any) -> str | None:
    summary = build_management_summary(user_id, state)
    if summary["stop_reason"] == STATUS_STOP_LOSS_HIT:
        return "STOP_LOSS_HIT"
    if summary["stop_reason"] == STATUS_STOP_WIN_HIT:
        return "STOP_WIN_HIT"
    return None


def asset_cooldown_reason(user_id: str, symbol: str) -> str | None:
    remaining = active_cooldown_remaining(user_id, symbol)
    if remaining is not None:
        return "ACTIVE_COOLDOWN"
    losses = []
    for trade in auto_trader.history(user_id).get("trades", []):
        if normalize_binary_active(str(trade.get("active") or "")) != symbol:
            continue
        if trade.get("result") != "LOSS":
            break
        finished_at = parse_datetime(trade.get("finished_at"))
        if finished_at is None:
            break
        losses.append(finished_at)
        if len(losses) == 2:
            break
    if len(losses) < 2:
        return None
    if datetime.now(timezone.utc) < losses[0] + timedelta(minutes=30):
        return "ASSET_COOLDOWN"
    return None


def frequency_recovery_active(user_id: str | None) -> bool:
    """True quando a conta está em seca longa sem entrada aprovada."""
    if not user_id:
        return False
    try:
        state = auto_trader.get(user_id)
    except Exception:
        return False
    return int(getattr(state, "consecutive_no_opportunity_cycles", 0) or 0) >= FREQUENCY_RECOVERY_AFTER_CYCLES


def effective_critical_trade_blocks(*, frequency_recovery: bool = False) -> set[str]:
    """Bloqueios críticos efetivos (com ou sem recovery de frequência)."""
    blocks = set(CRITICAL_TRADE_BLOCKS)
    if frequency_recovery:
        blocks -= set(FREQUENCY_RECOVERY_SOFT_BLOCKS)
    return blocks


def classify_no_opportunity_reason(blocked: set[str] | list[str] | None) -> str:
    """Classifica por que o ciclo fechou sem entrada.

    Rejeição de qualidade/estratégia NÃO pode virar ``ACTIVE_CLOSED`` só porque
    o melhor candidato também tem ``PAYOUT_UNAVAILABLE`` — isso forçava backoff
    operacional de 30s e o robô parecia parado.

    Args:
        blocked: Filtros bloqueados do melhor candidato (ou vazio).

    Returns:
        Código de rejeição para ``schedule_next_analysis_session``.
    """
    blocked_set = {str(item) for item in (blocked or [])}
    strategy_quality_blocks = {
        "TREND_CLEAR",
        "SIDEWAYS_FILTER",
        "WICK_REJECTION",
        "CANDLE_STRENGTH",
        "DOJI_FILTER",
        "PRICE_ACTION_SETUP",
        "REVERSAL_AGAINST",
        "LEVEL_CONFLICT",
        "LEVEL_REJECTION",
        "SR_ZONE",
        "LAST_3_ALIGNMENT",
        "CONTINUATION_DEAD_RSI",
        "WEAK_CONTINUATION_PUT",
        "EMA_TREND",
        "RSI_RANGE",
        "NO_ALTERNATING_LAST_3",
        "LAST_5_CONFIRMATION",
        "GLOBAL_LOSS_COOLDOWN",
        "ASSET_COOLDOWN",
        PATTERN_MEMORY_BLOCK,
    }
    if blocked_set & strategy_quality_blocks:
        return "NO_PATTERN_FOUND"
    if "CANDLES_UNAVAILABLE" in blocked_set:
        return "CANDLES_UNAVAILABLE"
    if STATUS_ACCOUNT_DISCONNECTED in blocked_set:
        return STATUS_ACCOUNT_DISCONNECTED
    if "MTF_DATA_UNAVAILABLE" in blocked_set:
        return "MTF_DATA_UNAVAILABLE"
    if "MTF_HIGHER_TF_CONFLICT" in blocked_set:
        return "MTF_HIGHER_TF_CONFLICT"
    if blocked_set & {"ACTIVE_CLOSED", "PAYOUT_UNAVAILABLE"}:
        return "ACTIVE_CLOSED"
    if "STOP_WIN_HIT" in blocked_set:
        return "STOP_WIN_HIT"
    if "STOP_LOSS_HIT" in blocked_set:
        return "STOP_LOSS_HIT"
    if "OPERATION_IN_PROGRESS" in blocked_set:
        return "OPERATION_IN_PROGRESS"
    return "NO_PATTERN_FOUND"


def global_loss_cooldown_reason(user_id: str) -> str | None:
    """Bloqueia novas entradas após LOSS(es) recentes para evitar streak tóxica.

    Regras (2026-08-04, calibrado 2026-08-04 tarde):
    - 1 LOSS → pausa de ``GLOBAL_LOSS_COOLDOWN_AFTER_ONE_SECONDS`` (60s).
    - 2 LOSSes consecutivos → pausa de ``GLOBAL_LOSS_COOLDOWN_AFTER_TWO_MINUTES`` (10).

    Args:
        user_id: Identificador autenticado do usuário.

    Returns:
        ``GLOBAL_LOSS_COOLDOWN`` durante a pausa ou ``None`` quando liberado.
    """
    consecutive_losses: list[datetime] = []
    for trade in auto_trader.history(user_id).get("trades", []):
        if str(trade.get("result") or "").upper() != "LOSS":
            break
        finished_at = parse_datetime(trade.get("finished_at"))
        if finished_at is None:
            break
        consecutive_losses.append(finished_at)
        if len(consecutive_losses) == 2:
            break
    if not consecutive_losses:
        return None
    now = utc_now()
    latest_loss = consecutive_losses[0]
    if len(consecutive_losses) >= 2:
        if now < latest_loss + timedelta(minutes=GLOBAL_LOSS_COOLDOWN_AFTER_TWO_MINUTES):
            return "GLOBAL_LOSS_COOLDOWN"
        return None
    if now < latest_loss + timedelta(seconds=GLOBAL_LOSS_COOLDOWN_AFTER_ONE_SECONDS):
        return "GLOBAL_LOSS_COOLDOWN"
    return None


def apply_strategy_guard(
    user_id: str,
    state: Any,
    signal: dict[str, Any],
    *,
    payout: float | None,
) -> tuple[bool, dict[str, Any], str | None]:
    selected = {
        **signal,
        "strategy_mode": signal.get("strategy_mode") or state.strategy_mode,
        "payout": payout,
    }
    blocked_filters = list(dict.fromkeys(selected.get("blocked_filters") or []))
    approved_filters = list(dict.fromkeys(selected.get("approved_filters") or []))

    def set_filter(name: str, passed: bool) -> None:
        if passed:
            if name in blocked_filters:
                blocked_filters.remove(name)
            if name not in approved_filters:
                approved_filters.append(name)
        else:
            if name in approved_filters:
                approved_filters.remove(name)
            if name not in blocked_filters:
                blocked_filters.append(name)

    confidence = int(selected.get("confidence") or 0)
    direction = str(selected.get("signal") or selected.get("direction") or "WAIT").upper()
    if direction not in {"CALL", "PUT"} and not any(
        reason in blocked_filters for reason in ("CANDLES_UNAVAILABLE", "INSUFFICIENT_CANDLES")
    ):
        trend = str(selected.get("trend") or "").upper()
        ema9 = float(selected.get("ema9") or 0)
        ema21 = float(selected.get("ema21") or 0)
        direction = "PUT" if trend in {"DOWN", "BEARISH"} or (ema9 and ema21 and ema9 < ema21) else "CALL"
    if payout is None:
        set_filter("PAYOUT_UNAVAILABLE", False)
    else:
        set_filter("PAYOUT_UNAVAILABLE", True)
        set_filter("MIN_PAYOUT", float(payout) >= state.min_payout)
    set_filter("MIN_CONFIDENCE", confidence >= state.min_confidence)
    if direction in {"CALL", "PUT"}:
        if "SIGNAL_WAIT" in blocked_filters:
            blocked_filters.remove("SIGNAL_WAIT")
        if "SIGNAL_WAIT" in approved_filters:
            approved_filters.remove("SIGNAL_WAIT")
        if "DIRECTION_VALID" not in approved_filters:
            approved_filters.append("DIRECTION_VALID")
    else:
        if "CANDLES_UNAVAILABLE" not in blocked_filters:
            blocked_filters.append("CANDLES_UNAVAILABLE")
    if "INSUFFICIENT_CANDLES" in blocked_filters:
        blocked_filters.remove("INSUFFICIENT_CANDLES")
        if "CANDLES_UNAVAILABLE" not in blocked_filters:
            blocked_filters.append("CANDLES_UNAVAILABLE")
    set_filter(
        "TREND_CLEAR",
        str(selected.get("price_action_setup") or "").upper() == "REVERSAL"
        or selected.get("trend") != "SIDEWAYS",
    )
    # Força fraca (UP/DOWN com strength baixa) só penaliza — não zera a sessão.
    profile_strength = int(
        (STRATEGY_PROFILES.get(str(selected.get("strategy_mode") or state.strategy_mode) or {}) or {}).get(
            "strength", 15
        )
    )
    set_filter(
        "TREND_STRENGTH",
        str(selected.get("price_action_setup") or "").upper() == "REVERSAL"
        or int(selected.get("strength") or 0) >= profile_strength,
    )
    set_filter("SIDEWAYS_FILTER", selected.get("trend") != "SIDEWAYS")

    if {"ema9", "ema21"}.issubset(selected):
        ema9 = float(selected.get("ema9") or 0)
        ema21 = float(selected.get("ema21") or 0)
        has_reversal_setup = str(selected.get("price_action_setup") or "").upper() == "REVERSAL"
        ema_ok = (
            (direction == "CALL" and ema9 > ema21)
            or (direction == "PUT" and ema9 < ema21)
            or has_reversal_setup
        )
        set_filter("EMA_TREND", ema_ok)
    if "rsi" in selected:
        rsi = float(selected.get("rsi") or 50)
        rsi_ok = (direction == "CALL" and 55 <= rsi <= 75) or (direction == "PUT" and 25 <= rsi <= 45)
        set_filter("RSI_RANGE", rsi_ok)
    if "body_ratio" in selected and float(selected.get("body_ratio") or 0) < float(
        (STRATEGY_PROFILES.get(str(selected.get("strategy_mode") or state.strategy_mode) or {}) or {}).get(
            "body_ratio", 0.40
        )
    ):
        set_filter("CANDLE_STRENGTH", False)
    if direction == "CALL" and "upper_wick_ratio" in selected and float(selected.get("upper_wick_ratio") or 0) > 0.45:
        set_filter("WICK_REJECTION", False)
    if direction == "PUT" and "lower_wick_ratio" in selected and float(selected.get("lower_wick_ratio") or 0) > 0.45:
        set_filter("WICK_REJECTION", False)
    if "atr_pct" in selected and float(selected.get("atr_pct") or 0) < 0.0001:
        set_filter("VOLATILITY", False)
    if "directional_candles_5" in selected and int(selected.get("directional_candles_5") or 0) < 3:
        set_filter("LAST_5_CONFIRMATION", str(selected.get("price_action_setup") or "").upper() == "REVERSAL")
    if selected.get("alternating_last_3") and "NO_ALTERNATING_LAST_3" not in blocked_filters:
        set_filter("NO_ALTERNATING_LAST_3", False)
    if str(selected.get("price_action_setup") or "").upper() not in {"CONTINUATION", "REVERSAL", "SUPPORT_RESISTANCE"}:
        set_filter("PRICE_ACTION_SETUP", False)
    if bool(selected.get("level_conflict")) or bool((selected.get("metrics") or {}).get("level_conflict")):
        set_filter("LEVEL_CONFLICT", False)
    if (
        str(selected.get("price_action_setup") or "").upper() in {"REVERSAL", "SUPPORT_RESISTANCE"}
        and not (
            bool(selected.get("level_rejection_confirmed"))
            or bool((selected.get("metrics") or {}).get("level_rejection_confirmed"))
        )
    ):
        set_filter("LEVEL_REJECTION", False)
    if bool(selected.get("reversal_against")):
        set_filter("REVERSAL_AGAINST", False)

    price_action_setup = str(selected.get("price_action_setup") or "").upper()
    is_continuation = price_action_setup == "CONTINUATION"
    last_3_direction = str(
        selected.get("last_3_direction")
        or (selected.get("metrics") or {}).get("last_3_direction")
        or ""
    ).upper()
    wanted_last_3 = "UP" if direction == "CALL" else "DOWN" if direction == "PUT" else ""
    if LAST_3_ALIGNMENT_HARD_BLOCK:
        # Sem last_3 no payload (sinais legados/teste) → não inventa bloqueio aqui;
        # analyze_signal sempre preenche o campo a partir das velas.
        if is_continuation and last_3_direction:
            set_filter("LAST_3_ALIGNMENT", last_3_direction == wanted_last_3)
        else:
            set_filter("LAST_3_ALIGNMENT", True)
    if CONTINUATION_DEAD_RSI_HARD_BLOCK and selected.get("rsi") is not None:
        rsi_value = float(selected.get("rsi") or 50)
        dead_rsi = is_continuation and CONTINUATION_DEAD_RSI_MIN <= rsi_value < CONTINUATION_DEAD_RSI_MAX
        set_filter("CONTINUATION_DEAD_RSI", not dead_rsi)
    symbol = normalize_binary_active(str(selected.get("symbol") or ""))
    if WEAK_CONTINUATION_PUT_HARD_BLOCK:
        set_filter(
            "WEAK_CONTINUATION_PUT",
            not (is_continuation and direction == "PUT" and is_weak_continuation_put_asset(symbol)),
        )

    cooldown = asset_cooldown_reason(user_id, symbol)
    if cooldown is not None:
        if cooldown not in blocked_filters:
            blocked_filters.append(cooldown)
        logger.warning("[ASSET_COOLDOWN] user_id=%s symbol=%s", user_id, symbol)

    loss_cooldown = global_loss_cooldown_reason(user_id)
    if loss_cooldown is not None:
        if loss_cooldown not in blocked_filters:
            blocked_filters.append(loss_cooldown)
        logger.warning("[GLOBAL_LOSS_COOLDOWN] user_id=%s", user_id)

    penalties = {
        "TREND_CLEAR": 10,
        "TREND_STRENGTH": 8,
        "SIDEWAYS_FILTER": 10,
        "EMA_TREND": 8,
        "RSI_RANGE": 8,
        "WICK_REJECTION": 8,
        "CANDLE_STRENGTH": 8,
        "DOJI_FILTER": 5,
        "VOLATILITY": 5,
        "LAST_5_CONFIRMATION": 5,
        "NO_ALTERNATING_LAST_3": 5,
        "ASSET_COOLDOWN": 10,
        "GLOBAL_LOSS_COOLDOWN": 15,
        "PRICE_ACTION_SETUP": 12,
        "REVERSAL_AGAINST": 15,
        "LEVEL_CONFLICT": 18,
        "LEVEL_REJECTION": 12,
        "LAST_3_ALIGNMENT": 20,
        "CONTINUATION_DEAD_RSI": 18,
        "WEAK_CONTINUATION_PUT": 20,
    }
    recovery = frequency_recovery_active(user_id) or bool(selected.get("frequency_recovery"))
    penalty_names = set(blocked_filters)
    if recovery:
        penalty_names -= set(FREQUENCY_RECOVERY_SOFT_BLOCKS)
    strategy_score = max(
        0,
        confidence - sum(penalties.get(name, 0) for name in penalty_names),
    )
    hard_blocks = [
        name
        for name in blocked_filters
        if name in effective_critical_trade_blocks(frequency_recovery=recovery)
    ]
    trade_allowed = not hard_blocks
    reason = str(selected.get("reason") or selected.get("signal_explanation") or "").strip()
    if blocked_filters:
        reason = f"{reason} Penalizacoes/bloqueios: {', '.join(blocked_filters)}.".strip()
    selected["blocked_filters"] = blocked_filters
    selected["approved_filters"] = approved_filters
    selected["trade_allowed"] = trade_allowed
    selected["direction"] = direction
    selected["signal"] = direction
    selected["strategy_score"] = strategy_score
    selected["quality_score"] = strategy_score
    selected["score"] = strategy_score
    selected["block_reasons"] = list(blocked_filters)
    selected["reason"] = reason
    selected["entry_reason"] = selected.get("entry_reason") or reason
    selected["quality_reason"] = "OK" if trade_allowed else ",".join(hard_blocks)
    selected["frequency_recovery"] = bool(
        frequency_recovery_active(user_id) or selected.get("frequency_recovery")
    )
    if trade_allowed:
        logger.info(
            "[STRATEGY_FILTER_PASS] user_id=%s symbol=%s mode=%s strategy_score=%s recovery=%s",
            user_id,
            symbol,
            state.strategy_mode,
            strategy_score,
            selected["frequency_recovery"],
        )
        return True, selected, None

    logger.info(
        "[STRATEGY_FILTER_BLOCK] user_id=%s symbol=%s mode=%s blocked_filters=%s",
        user_id,
        symbol,
        state.strategy_mode,
        blocked_filters,
    )
    return False, selected, hard_blocks[0] if hard_blocks else LOW_QUALITY_SIGNAL


def robot_stop_reason(state: Any) -> str | None:
    if state.operation_in_progress:
        return "OPERATION_IN_PROGRESS"
    return resolve_robot_stop_reason(state, profit=float(getattr(state, "profit", 0) or 0))


async def pause_robot_by_stop(user_id: str, reason: str) -> Any:
    state = auto_trader.pause_by_stop(user_id, reason)
    if reason == STATUS_STOP_WIN_HIT:
        logger.warning("[STOP_WIN_HIT] user_id=%s profit=%s", user_id, state.profit)
    else:
        logger.warning("[STOP_LOSS_HIT] user_id=%s profit=%s", user_id, state.profit)
    logger.warning("[ROBOT_PAUSED_BY_STOP] user_id=%s reason=%s", user_id, reason)
    persist_robot(user_id)
    await stop_robot_worker(user_id)
    return state


def robot_connection_unavailable(connected: bool, active_mode: str | None) -> bool:
    return not connected or active_mode is None


def is_stop_status(status: Any) -> bool:
    return str(status or "").strip().upper() in {
        STATUS_STOP_WIN_HIT,
        STATUS_STOP_LOSS_HIT,
    }


def get_user_account_snapshot(user_id: str | None) -> dict[str, Any]:
    if not user_id:
        return {
            "email": None,
            "balance": None,
            "currency": None,
            "mode": None,
            "connected": None,
            "credentials_saved": False,
        }
    try:
        record = user_store.get_user(str(user_id))
    except Exception:
        logger.warning("[ACCOUNT_SNAPSHOT_LOOKUP_FAILED] user_id=%s", user_id, exc_info=True)
        return {
            "email": None,
            "balance": None,
            "currency": None,
            "mode": None,
            "connected": None,
            "credentials_saved": False,
        }
    if record is None:
        return {
            "email": None,
            "balance": None,
            "currency": None,
            "mode": None,
            "connected": None,
            "credentials_saved": False,
        }
    balance = None
    if record.last_balance is not None:
        try:
            balance = float(record.last_balance)
        except (TypeError, ValueError):
            balance = None
    mode = str(record.account_mode).strip().upper() if record.account_mode else None
    return {
        "email": record.bullex_email,
        "balance": balance,
        "currency": record.currency,
        "mode": mode,
        "connected": record.connected,
        "credentials_saved": bool(record.has_saved_credentials or record.credentials_saved_at),
    }


def get_cached_account_snapshot(user_id: str) -> dict[str, Any]:
    cached = cached_successful_response(user_id, "/account")
    data = cached.payload.get("data") if cached is not None else None
    if not isinstance(data, dict):
        if isinstance(user_store, InMemoryUserStore):
            return get_user_account_snapshot(user_id)
        return {
            "email": None,
            "balance": None,
            "currency": None,
            "mode": None,
            "connected": None,
        }
    mode = data.get("active_mode_from_bullex") or data.get("active_mode") or data.get("mode")
    return {
        "email": data.get("email"),
        "balance": data.get("balance"),
        "currency": data.get("currency"),
        "mode": str(mode).strip().upper() if mode else None,
        "connected": data.get("connected"),
    }


def robot_has_recent_real_cache(user_id: str, state: Any, *, max_age_seconds: int = ROBOT_VALID_CACHE_SECONDS) -> bool:
    """Indica se o robô pode seguir analisando com evidência REAL recente.

    Sob carga o poll marca ``connected=False`` / ``active_mode=None`` por
    timeout no bullex-service. Exigir ``state.connected`` aqui tornava o
    cache inútil exatamente quando o worker precisa dele → loop em
    ``ROBOT_WORKER_BLOCKED_DISCONNECTED`` sem análise.
    """
    account_mode = str(getattr(state, "account_mode", "") or "").strip().upper()
    active_mode = str(getattr(state, "active_mode", "") or "").strip().upper()
    snapshot = get_cached_account_snapshot(user_id)
    snapshot_mode = str(snapshot.get("mode") or "").strip().upper() or None
    if "REAL" not in {account_mode, active_mode, snapshot_mode}:
        user_snap = get_user_account_snapshot(user_id)
        snapshot_mode = str(user_snap.get("mode") or "").strip().upper() or None
        if snapshot_mode != "REAL":
            return False
        snapshot = {
            "mode": "REAL",
            "balance": user_snap.get("balance"),
            "connected": user_snap.get("connected"),
        }

    balance = None
    try:
        if snapshot.get("balance") is not None:
            balance = float(snapshot["balance"])
    except (TypeError, ValueError):
        balance = None
    if balance is None or balance <= 0:
        return False

    checked_at = getattr(state, "connection_checked_at", None)
    if checked_at is not None:
        age = (utc_now() - checked_at).total_seconds()
        if 0 <= age <= max_age_seconds:
            return True

    # Sem checked_at fresco: aceita snapshot/cache REAL com saldo (flap de carga).
    if snapshot.get("connected") is True or snapshot_mode == "REAL":
        return True
    return bool(getattr(state, "enabled", False) and account_mode == "REAL")


def resume_robot_connection_from_real_cache(user_id: str, state: Any) -> Any | None:
    """Reativa connected/REAL no estado a partir do cache quando o poll falhou.

    Args:
        user_id: Usuário do robô.
        state: Estado atual do auto_trader.

    Returns:
        Estado sincronizado ou ``None`` se não houver evidência REAL.
    """
    if not robot_has_recent_real_cache(user_id, state):
        return None
    snapshot = get_cached_account_snapshot(user_id)
    mode = str(
        snapshot.get("mode")
        or getattr(state, "active_mode", None)
        or getattr(state, "account_mode", None)
        or "REAL"
    ).strip().upper()
    if mode != "REAL":
        mode = "REAL"
    logger.warning(
        "[ROBOT_WORKER_USING_RECENT_REAL_CACHE] user_id=%s previous_connected=%s previous_mode=%s",
        user_id,
        getattr(state, "connected", None),
        getattr(state, "active_mode", None),
    )
    return auto_trader.sync_connection(
        user_id,
        connected=True,
        active_mode=mode,
        source="recent_real_cache_resume",
        align_status=True,
    )


def recent_real_account_connection_payload(user_id: str) -> dict[str, Any] | None:
    cache = get_session_cache(user_id)
    cached = cache.responses.get("/account")
    if cached is None or utc_now() >= cached.expires_at:
        cached = cache.last_successful_responses.get("/account")
    if cached is None:
        return None
    data = cached.payload.get("data")
    if not isinstance(data, dict):
        return None
    active_mode = str(
        data.get("active_mode_from_bullex")
        or data.get("active_mode")
        or data.get("mode")
        or ""
    ).strip().upper()
    if data.get("connected") is not True or active_mode != "REAL" or data.get("balance") is None:
        return None
    clear_session_backoff(user_id)
    state = auto_trader.sync_connection(
        user_id,
        connected=True,
        active_mode="REAL",
        source="account_cache_grace",
        align_status=True,
    )
    return build_success(
        {
            "connected": True,
            "active_mode": "REAL",
            "server_time": None,
            "connection_status_source": state.connection_status_source,
            "from_cache": True,
        }
    )


def memory_account_fallback(user_id: str) -> dict[str, Any] | None:
    state = auto_trader.get(user_id)
    snapshot = get_cached_account_snapshot(user_id)
    connected = bool(state.connected or snapshot.get("connected") is True)
    active_mode = state.active_mode or snapshot.get("mode")
    if not connected and active_mode is None:
        return None
    return add_stale_warning(
        build_success(
            {
                "connected": connected,
                "active_mode": active_mode,
                "mode": active_mode,
                "balance": snapshot.get("balance"),
                "currency": snapshot.get("currency"),
                "email": snapshot.get("email"),
            }
        )
    )


def memory_status_fallback(user_id: str) -> dict[str, Any] | None:
    payload = memory_account_fallback(user_id)
    if payload is None:
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    state = auto_trader.get(user_id)
    data["connection_status_source"] = state.connection_status_source or "memory"
    data["robot"] = build_robot_payload(
        state,
        connected=bool(data.get("connected")),
        active_mode=data.get("active_mode"),
        connection_checked_at=state.connection_checked_at.isoformat()
        if state.connection_checked_at is not None
        else None,
        connection_status_source=data["connection_status_source"],
    )["data"]
    return payload


# Bug real corrigido em 2026-08-07: o ramo `elif data.get("pending_signal")`
# abaixo promovia QUALQUER status para STATUS_WAITING_ENTRY só por existir um
# pending_signal (às vezes só resquício/stale) em memória — inclusive estados
# de bloqueio/recuperação de conexão, escondendo do usuário que o robô estava
# em WAITING_RECOVERY, CONNECTION_BACKOFF, DISCONNECTED, ORDER_REJECTED etc.
# Esses status "de espera especial" nunca devem ser mascarados como
# "Melhor ativo encontrado".
PENDING_SIGNAL_STATUS_OVERRIDE_BLOCKLIST = frozenset(
    {
        STATUS_WAITING_RECOVERY,
        STATUS_CONNECTION_BACKOFF,
        STATUS_ACCOUNT_DISCONNECTED,
        STATUS_STOPPED,
        STATUS_ORDER_REJECTED,
        STATUS_INSUFFICIENT_BALANCE,
        STATUS_BULLEX_ACTIVE_MODE_NOT_REAL,
        STATUS_ACTIVE_COOLDOWN,
        STATUS_PAYOUT_COOLDOWN,
        STATUS_SYNCING,
        STATUS_ERROR,
        STATUS_ANALYSIS_ERROR,
        STATUS_ANALYSIS_TIMEOUT,
    }
)


def build_robot_payload(state: Any, **extra: Any) -> dict[str, Any]:
    data = strip_ai_fields(state.to_dict())
    data["status"] = normalize_robot_status(data.get("status"))
    data["account_mode"] = "REAL"
    data["allow_real"] = True
    data["confirm_real"] = True
    selectable_mode = coerce_selectable_market_mode(data.get("market_mode"))
    data["market_mode"] = selectable_mode
    data["market_mode_effective"] = effective_market_mode(selectable_mode)
    data["open_market_available"] = is_forex_open_market_open()
    data["open_market_hours_until"] = hours_until_forex_open_market()
    if str(data.get("active_mode") or "").strip().upper() == "DEMO":
        data["active_mode"] = "REAL"
    user_id = extra.pop("user_id", None)
    if user_id is not None:
        data["management"] = build_management_summary(str(user_id), state)
        data["management_stop_reason"] = data["management"]["stop_reason"]
        data["management_gross_profit"] = data["management"]["gross_profit"]
        data["management_gross_loss"] = data["management"]["gross_loss"]
        worker_task = robot_tasks.get(str(user_id))
        last_tick_at = robot_worker_last_tick_at.get(str(user_id))
        if (
            worker_task is not None
            and not worker_task.done()
            and last_tick_at is not None
            and (utc_now() - last_tick_at).total_seconds() > ROBOT_WORKER_STALE_SECONDS
            and data.get("enabled")
            and str(data.get("analysis_result") or "").strip().upper() != "RUNNING"
        ):
            logger.warning("[WORKER_STALE_RESTART] user_id=%s last_tick_at=%s", user_id, last_tick_at)
            worker_task.cancel()
            try:
                asyncio.get_running_loop()
                robot_tasks[str(user_id)] = asyncio.create_task(robot_worker(str(user_id)))
            except RuntimeError:
                robot_tasks.pop(str(user_id), None)
            worker_task = robot_tasks.get(str(user_id))
            last_tick_at = robot_worker_last_tick_at.get(str(user_id))
        data["worker_running"] = bool(worker_task is not None and not worker_task.done())
        data["worker_last_tick_at"] = last_tick_at.isoformat() if last_tick_at is not None else None
        # Só pending_signal trava entrada. best_candidate é telemetria de análise
        # e não deve segurar o worker como se houvesse ordem preparada.
        has_locked_signal = bool(data.get("pending_signal"))
        has_open_operation = bool(data.get("operation_in_progress") or data.get("result_waiting"))
        has_result_display = data.get("status") in {"WIN", "LOSS"} or (
            str(data.get("cycle_result") or "").upper() in {"WIN", "LOSS"}
            and data.get("result_display_until")
        )
        if data.get("enabled") and data["worker_running"] and data.get("status") in {
            STATUS_STOPPED,
        } and not (has_locked_signal or has_open_operation or has_result_display):
            data["status"] = STATUS_ANALYZING
    data.update(strip_ai_fields(extra))
    data["status"] = normalize_robot_status(data.get("status"))
    visible_result_status = str(data.get("status") or "").upper()
    visible_cycle_result = str(data.get("cycle_result") or "").upper()
    if data.get("operation_in_progress") or data.get("result_waiting"):
        data["status"] = STATUS_WAITING_RESULT
        data["analysis_message"] = None
        data["operation_message"] = data.get("operation_message") or "Operação aberta"
    elif data.get("status") in {STATUS_SENDING_ORDER, STATUS_SENDING_GALE_ORDER, STATUS_BUYING}:
        data["status"] = STATUS_BUYING
        data["analysis_message"] = None
        data["operation_message"] = data.get("operation_message") or "Executando ordem"
    elif visible_result_status in {"WIN", "LOSS"} or (
        visible_cycle_result in {"WIN", "LOSS"} and data.get("result_display_until")
    ):
        data["status"] = visible_result_status if visible_result_status in {"WIN", "LOSS"} else visible_cycle_result
        data["analysis_message"] = None
        data["operation_message"] = data.get("operation_message") or data["status"]
    elif data.get("status") == STATUS_WAITING_NEXT_CYCLE:
        data["pending_signal"] = None
        data["last_signal"] = None
        data["best_candidate"] = None
        data["cycle_best_candidate"] = None
        data["cycle_best_trade_candidate"] = None
        data["best_candidate_summary"] = None
        data["analysis_message"] = "Buscando melhor oportunidade"
        data["status_message"] = "Buscando melhor oportunidade"
        data["display_countdown_label"] = "Buscando melhor oportunidade"
        data["display_countdown_seconds"] = 0
        if not data.get("voice_message"):
            data["voice_message"] = (
                "El Capo está analisando o mercado. "
                "Identificando uma oportunidade de operação lucrativa."
            )
    elif data.get("pending_signal") and data.get("status") not in PENDING_SIGNAL_STATUS_OVERRIDE_BLOCKLIST:
        data["status"] = STATUS_WAITING_ENTRY
        data["analysis_message"] = None
        data["status_message"] = data.get("status_message") or "Melhor ativo encontrado"
    # NÃO promover best_candidate → SIGNAL_FOUND durante ANALYZING.
    # Candidatos de painel (trade_allowed=False / canal fechado) geravam o
    # visual "Melhor ativo encontrado" sem nunca abrir ordem.
    data["account_mode"] = "REAL"
    data["allow_real"] = True
    data["confirm_real"] = True
    if str(data.get("active_mode") or "").strip().upper() == "DEMO":
        data["active_mode"] = "REAL"
    connected = bool(data.get("connected"))
    active_mode = str(data.get("active_mode") or "").strip().upper() or None
    if connected and active_mode == "REAL":
        data["connected"] = True
        data["active_mode"] = "REAL"
        data["session_status"] = "connected"
        data["session"] = "connected"
        if data.get("status") == STATUS_ACCOUNT_DISCONNECTED:
            data["status"] = STATUS_WAITING_NEXT_CYCLE if data.get("enabled") else STATUS_STOPPED
            data["rejection_reason"] = None
            data["last_rejection_reason"] = None
            data["operation_message"] = None
    else:
        data["session_status"] = "disconnected"
        data["session"] = "disconnected"
    if data.get("worker_running") and data.get("status") == STATUS_STOPPED:
        data["status"] = "RUNNING"
    if data.get("status") == STATUS_ACCOUNT_DISCONNECTED:
        data["connected"] = False
        data["enabled"] = False
        data["active_mode"] = None
        data["session_status"] = "disconnected"
        data["session"] = "disconnected"
        data["worker_running"] = False
        data["operation_in_progress"] = False
        data["result_waiting"] = False
        data["operation_message"] = "Conta BullEx desconectada"
        data["analysis_message"] = None
        data["display_countdown_label"] = None
        data["display_countdown_seconds"] = 0
        data["best_candidate_summary"] = None
        data["pending_signal"] = None
        data["last_signal"] = None
        data["last_trade"] = None
    if data.get("status") == STATUS_INSUFFICIENT_BALANCE:
        data["enabled"] = False
        data["worker_running"] = False
        data["operation_in_progress"] = False
        data["result_waiting"] = False
        data["analysis_message"] = None
        data["pending_signal"] = None
        data["operation_message"] = INSUFFICIENT_BALANCE_START_MESSAGE
        data["status_message"] = INSUFFICIENT_BALANCE_START_MESSAGE
        data["real_ready"] = False
    if data.get("status") == STATUS_BULLEX_ACTIVE_MODE_NOT_REAL:
        data["enabled"] = False
        data["worker_running"] = False
        data["operation_in_progress"] = False
        data["result_waiting"] = False
        data["analysis_message"] = None
        data["pending_signal"] = None
        data["operation_message"] = "Entre na conta REAL da BullEx para iniciar o robô."
        data["status_message"] = "Entre na conta REAL da BullEx para iniciar o robô."
    if data.get("status") == STATUS_BULLEX_ACTIVE_MODE_NOT_REAL:
        data["real_ready"] = False
    if is_stop_status(data.get("status")):
        data["enabled"] = False
        data["worker_running"] = False
        data["operation_in_progress"] = False
        data["result_waiting"] = False
    if data.get("operation_in_progress"):
        logger.info(
            "[EXPIRATION_COUNTDOWN] status=%s order_id=%s expiration_seconds=%s result_waiting=%s",
            data.get("status"),
            (data.get("last_trade") or {}).get("order_id"),
            data.get("expiration_seconds"),
            data.get("result_waiting"),
        )
        if data.get("result_waiting"):
            logger.info(
                "[RESULT_WAITING] order_id=%s",
                (data.get("last_trade") or {}).get("order_id"),
            )
    return build_success(data)


def build_robot_state_fallback_payload(user_id: str | None, exc: Exception) -> dict[str, Any]:
    state = auto_trader.get(str(user_id)) if user_id else None
    if state is not None:
        try:
            state.status = normalize_robot_status(getattr(state, "status", None))
            return build_robot_payload(
                state,
                user_id=user_id,
                real_ready=False,
                real_block_reason=exc.__class__.__name__,
            )
        except Exception:
            logger.info(
                "[ROBOT_STATE_FALLBACK_PAYLOAD] user_id=%s reason=minimal_payload",
                user_id,
                exc_info=True,
            )
    return build_success(
        {
            "status": STATUS_STOPPED,
            "enabled": False,
            "connected": False,
            "active_mode": None,
            "worker_running": False,
            "operation_in_progress": False,
            "result_waiting": False,
            "account_mode": "REAL",
            "allow_real": True,
            "confirm_real": True,
            "real_ready": False,
            "real_block_reason": exc.__class__.__name__,
        }
    )


def robot_config_locked(user_id: str, state: Any) -> bool:
    """Trava config só com robô ativo (enabled ou worker). Parado libera o pop-up de início."""
    worker_task = robot_tasks.get(user_id)
    worker_running = bool(worker_task is not None and not worker_task.done())
    return bool(getattr(state, "enabled", False) or worker_running)


def clear_stale_open_operation_if_stopped(user_id: str, state: Any) -> Any:
    """
    Remove 'operação aberta' fantasma quando o robô já está parado.

    Evita travar Iniciar Operação após stop com PENDING_RESULT órfão.
    """
    worker_task = robot_tasks.get(user_id)
    worker_running = bool(worker_task is not None and not worker_task.done())
    if getattr(state, "enabled", False) or worker_running:
        return state

    status = str(getattr(state, "status", "") or "").upper()
    sticky_status = status in {
        STATUS_WAITING_RESULT,
        STATUS_PENDING_RESULT,
        STATUS_PENDING_GALE_RESULT,
        STATUS_BUYING,
        STATUS_SENDING_ORDER,
        STATUS_SENDING_GALE_ORDER,
    }
    if not getattr(state, "operation_in_progress", False) and not sticky_status and not state.gale_pending:
        return state

    trade_result = str(((getattr(state, "last_trade", None) or {}).get("result") or "")).strip().upper()
    state.operation_in_progress = False
    state.gale_pending = False
    if sticky_status:
        state.status = STATUS_STOPPED
    logger.warning(
        "[STALE_OPEN_OPERATION_CLEARED] user_id=%s previous_status=%s last_trade_result=%s",
        user_id,
        status or None,
        trade_result or None,
    )
    persist_robot(user_id)
    return state


def recover_sync_timeout_if_needed(user_id: str) -> Any:
    recovered, state = auto_trader.recover_sync_timeout(user_id)
    if recovered:
        logger.warning(
            "[SYNC_TIMEOUT_RECOVERED] user_id=%s status=%s connected=%s enabled=%s",
            user_id,
            state.status,
            state.connected,
            state.enabled,
        )
        persist_robot(user_id)
    return state


def get_real_balance_warning(
    user_id: str | None,
    state: Any,
    active_mode: str | None,
    *,
    snapshot: dict[str, Any] | None = None,
) -> str | None:
    if user_id is None or getattr(state, "account_mode", None) != "REAL" or active_mode != "REAL":
        return None
    snapshot = snapshot if snapshot is not None else get_user_account_snapshot(user_id)
    if snapshot.get("balance") is None:
        return None
    balance = float(snapshot["balance"])
    if balance <= 0:
        logger.warning("[INSUFFICIENT_BALANCE_REAL] user_id=%s balance=%s", user_id, balance)
    return "BALANCE_ZERO" if balance <= 0 else None


async def stop_real_robot_for_insufficient_balance(
    user_id: str,
    *,
    balance: float | None,
    entry_value: float | None = None,
) -> Any:
    state = auto_trader.insufficient_balance(user_id)
    persist_robot(user_id)
    logger.warning(
        "[ROBOT_STOPPED_BALANCE_ZERO] user_id=%s balance=%s entry_value=%s",
        user_id,
        balance,
        entry_value if entry_value is not None else state.entry_value,
    )
    logger.warning(
        "[INSUFFICIENT_BALANCE_REAL] user_id=%s balance=%s entry_value=%s",
        user_id,
        balance,
        entry_value if entry_value is not None else state.entry_value,
    )
    await stop_robot_worker(user_id)
    return state


def recover_timed_out_analysis_if_needed(user_id: str) -> Any:
    recovered, state = auto_trader.recover_timed_out_analysis(user_id)
    if recovered:
        logger.warning("[ANALYSIS_TIMEOUT] user_id=%s", user_id)
        logger.info("[ANALYSIS_RECOVERED] user_id=%s reason=%s", user_id, state.rejection_reason)
        logger.info("[NEXT_CYCLE_SCHEDULED] user_id=%s next_cycle_at=%s", user_id, state.next_cycle_at)
        persist_robot(user_id)
    return state


def recover_running_analysis_if_needed(user_id: str, window: dict[str, Any]) -> tuple[str | None, Any]:
    reason, state = auto_trader.recover_running_analysis(user_id, window)
    if reason is None:
        return None, state
    if reason == "ANALYSIS_TIMEOUT":
        logger.warning("[ANALYSIS_TIMEOUT] user_id=%s", user_id)
    logger.info(
        "[WAITING_ANALYSIS_WINDOW] user_id=%s timeframe=%s current_candle_seconds=%s "
        "analysis_result=%s seconds_until_analysis_window=%s",
        user_id,
        state.timeframe,
        window["current_candle_seconds"],
        state.analysis_result,
        state.seconds_until_analysis_window,
    )
    logger.info("[ANALYSIS_STATE_RECOVERED] user_id=%s reason=%s", user_id, reason)
    persist_robot(user_id)
    return reason, state


def recover_analysis_error_to_window(
    user_id: str,
    error: Any,
    window: dict[str, Any] | None = None,
) -> Any:
    state = auto_trader.get(user_id)
    if window is None:
        window = get_entry_window(
            state.timeframe,
            utc_now().timestamp(),
            server_time_source="vps_fallback",
        )
        logger.warning(
            "[SERVER_TIME_FALLBACK] user_id=%s reason=analysis_recovery current_candle_seconds=%s",
            user_id,
            window["current_candle_seconds"],
        )
    friendly_error = readable_order_error(error)
    state = auto_trader.wait_analysis_window(
        user_id,
        window,
        clear_pending=True,
        analysis_result="ANALYSIS_ERROR",
        rejection_reason="ANALYSIS_ERROR",
        last_rejection_reason=friendly_error,
        force_next=True,
    )
    state.last_order_error = friendly_error
    logger.info(
        "[ANALYSIS_RECOVERED] user_id=%s error=%s next_cycle_at=%s",
        user_id,
        friendly_error,
        state.next_cycle_at,
    )
    return state


def readable_order_error(error: Any) -> str:
    raw_error = str(error or "ORDER_FAILED").strip() or "ORDER_FAILED"
    normalized = raw_error.lower().replace("_", " ").replace("-", " ")
    if "asset is not available" in normalized or "cannot purchase" in normalized:
        return "Ativo indisponivel no momento da compra"
    if "active suspended" in normalized or "ativo suspenso" in normalized:
        return "Ativo suspenso pela BullEx"
    if "payout" in normalized and any(term in normalized for term in ("low", "baixo", "minimum", "minimo")):
        return "Payout abaixo do minimo permitido"
    if "active not found" in normalized or "ativo nao encontrado" in normalized:
        return "Ativo nao encontrado na BullEx"
    if "account mismatch" in normalized or "mode mismatch" in normalized or "modo da conta" in normalized:
        return "Conta BullEx incompativel com o modo selecionado"
    if "field required" in normalized or "payload required fields" in normalized or "buy real payload required fields" in normalized:
        return "Compra REAL bloqueada por payload incompleto. Atualize backend-gateway e bullex-service no VPS."
    return raw_error


def is_order_availability_error(error: Any) -> bool:
    normalized = str(error or "").strip().lower().replace("_", " ").replace("-", " ")
    return any(term in normalized for term in ORDER_AVAILABILITY_ERROR_TERMS)


def candidate_pre_order_block_reason(candidate: dict[str, Any]) -> str | None:
    symbol = normalize_binary_active(str(candidate.get("symbol") or ""))
    if not symbol or not is_binary_asset_allowed(symbol):
        return "ACTIVE_CLOSED"
    active_status = str(candidate.get("active_status") or candidate.get("status") or "").upper()
    blocked_filters = set(str(item) for item in (candidate.get("blocked_filters") or []))
    if candidate.get("suspended") or "SUSPEND" in active_status or "ACTIVE_SUSPENDED" in blocked_filters:
        return "ACTIVE_SUSPENDED"
    if "ACTIVE_COOLDOWN" in blocked_filters or "ASSET_COOLDOWN" in blocked_filters:
        return "ACTIVE_COOLDOWN"
    if candidate.get("is_open") is False or active_status in {"CLOSED", "INACTIVE"} or "ACTIVE_CLOSED" in blocked_filters:
        return "ACTIVE_CLOSED"
    return None


def resolve_entry_validation_reason(
    candidate: dict[str, Any] | None,
    state: Any,
    *,
    minimum_confidence: int,
    user_id: str | None = None,
) -> str | None:
    """Motivo bloqueador na hora da compra, ou ``None`` se a ordem pode sair.

    Ordem importa: canal fechado / suspenso vem **antes** do rótulo genérico
    ``LOW_QUALITY_SIGNAL``. Sem isso, a revalidação pré-ordem marcava
    ``ACTIVE_CLOSED`` + ``trade_allowed=False`` e o loop logava só
    "baixa qualidade", escondendo que o turbo/binary fechou entre o lock e o buy
    (sintoma: IA anuncia a entrada e ``ORDER_REJECTED`` com ``NO_AVAILABLE_ASSET``).

    Args:
        candidate: Candidato já revalidado (ou gale).
        state: Estado do robô (mínimos de confiança/payout).
        minimum_confidence: Piso de confiança deste ciclo.
        user_id: Usuário autenticado (memória de padrões).

    Returns:
        Código do bloqueio, ou ``None`` quando o portão libera a compra.
    """
    if not isinstance(candidate, dict):
        return "NO_SIGNAL"
    pre_order_reason = candidate_pre_order_block_reason(candidate)
    if pre_order_reason is not None:
        return pre_order_reason
    if candidate_meets_cycle_threshold(
        candidate,
        state,
        minimum_confidence=minimum_confidence,
        user_id=user_id,
    ):
        return None
    blocked_filters = {str(item) for item in (candidate.get("blocked_filters") or [])}
    if PATTERN_MEMORY_BLOCK in blocked_filters:
        return PATTERN_MEMORY_BLOCK
    if (
        candidate.get("stale") is True
        or candidate.get("from_cache") is True
        or candidate.get("market_data_stale") is True
        or "STALE_MARKET_DATA" in blocked_filters
    ):
        return "STALE_MARKET_DATA"
    critical_hits = sorted(blocked_filters & CRITICAL_TRADE_BLOCKS)
    if critical_hits:
        return critical_hits[0]
    try:
        confidence = int(candidate.get("confidence") or 0)
        payout = float(candidate.get("payout") or 0)
    except (TypeError, ValueError):
        return LOW_QUALITY_SIGNAL
    if confidence < int(minimum_confidence):
        return "MIN_CONFIDENCE"
    if payout < float(state.min_payout):
        return "PAYOUT_TOO_LOW"
    if candidate.get("trade_allowed") is not True:
        return LOW_QUALITY_SIGNAL
    return LOW_QUALITY_SIGNAL


def cached_payout_age_seconds(user_id: str, symbol: str) -> float | None:
    """Há quantos segundos o payload de ``/payouts`` do ativo foi lido.

    Args:
        user_id: Identificador autenticado.
        symbol: Ativo consultado.

    Returns:
        Idade em segundos, ou ``None`` quando não há entrada válida em cache.
    """
    cache = get_session_cache(user_id)
    cache_key = build_cache_key("/payouts", {"active": normalize_binary_active(symbol)})
    entry = cache.responses.get(cache_key)
    if entry is None:
        return None
    remaining_seconds = (entry.expires_at - utc_now()).total_seconds()
    return max(0.0, PAYOUT_CACHE_TTL_SECONDS - remaining_seconds)


async def fresh_asset_open_for_active(
    user_id: str,
    symbol: str,
    timeframe: str,
    *,
    timeout_seconds: float,
) -> bool | None:
    """Consulta ``/payouts`` sem cache só para saber se o canal está aberto.

    Args:
        user_id: Identificador autenticado.
        symbol: Ativo a consultar.
        timeframe: Timeframe operacional (define turbo vs binary).
        timeout_seconds: Tempo máximo de espera — a janela de compra é curta.

    Returns:
        ``True``/``False`` quando a corretora respondeu a tempo; ``None`` quando
        não deu para saber (timeout, erro ou ativo fora da lista permitida).
    """
    if not is_binary_asset_allowed(symbol) or timeout_seconds <= 0:
        return None
    try:
        status_code, payload = await asyncio.wait_for(
            call_bullex_service(
                "GET",
                "/payouts",
                user_id,
                params={"active": symbol},
                force_refresh=True,
            ),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "[CHANNEL_REVALIDATION_TIMEOUT] user_id=%s symbol=%s timeout=%s",
            user_id,
            symbol,
            timeout_seconds,
        )
        return None
    except Exception as exc:  # degrada para o cache, mas nunca em silêncio
        logger.warning(
            "[CHANNEL_REVALIDATION_FAILED] user_id=%s symbol=%s error=%s",
            user_id,
            symbol,
            exc.__class__.__name__,
            exc_info=True,
        )
        return None
    if status_code >= 400 or not payload.get("ok"):
        return None
    return extract_asset_open(payload, symbol, timeframe)


async def refresh_candidate_execution_channel(
    user_id: str,
    candidate: dict[str, Any],
    timeframe: str,
    *,
    fresh_timeout_seconds: float = CHANNEL_REVALIDATION_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Revalida o canal turbo/binary imediatamente antes da compra.

    O sinal é travado na vela anterior à entrada, então o cache pode estar
    velho quando a ordem sai. Com cache antigo (ou ausente) busca dado fresco;
    se a corretora não responder dentro de ``fresh_timeout_seconds``, mantém o
    que o cache souber — nunca deixa de comprar por falta de resposta.

    Args:
        user_id: Identificador autenticado.
        candidate: Candidato selecionado para a ordem.
        timeframe: Timeframe operacional do robô.
        fresh_timeout_seconds: Orçamento de tempo para a consulta fresca.
            ``<= 0`` desliga a consulta e usa só o cache.

    Returns:
        Cópia do candidato com ``is_open`` atualizado quando foi possível saber.
    """
    refreshed = dict(candidate)
    symbol = normalize_binary_active(str(refreshed.get("symbol") or ""))
    if not symbol:
        return refreshed
    channel_open = cached_asset_open_for_active(user_id, symbol, timeframe)
    cache_age_seconds = cached_payout_age_seconds(user_id, symbol)
    cache_is_usable = (
        channel_open is not None
        and cache_age_seconds is not None
        and cache_age_seconds <= CHANNEL_CACHE_MAX_AGE_SECONDS
    )
    if not cache_is_usable:
        fresh_open = await fresh_asset_open_for_active(
            user_id,
            symbol,
            timeframe,
            timeout_seconds=fresh_timeout_seconds,
        )
        if fresh_open is not None:
            logger.info(
                "[CHANNEL_REVALIDATED] user_id=%s symbol=%s timeframe=%s open=%s cached_open=%s cache_age=%s",
                user_id,
                symbol,
                timeframe,
                fresh_open,
                channel_open,
                round(cache_age_seconds, 1) if cache_age_seconds is not None else None,
            )
            channel_open = fresh_open
    if channel_open is None:
        return refreshed
    apply_execution_channel_open(
        refreshed,
        channel_open=channel_open,
        timeframe=timeframe,
    )
    return refreshed


def pattern_memory_history_loader(user_id: str, days: int) -> list[dict[str, Any]]:
    """Carrega histórico para hidratar a memória de padrões sob demanda."""
    return robot_persistence.load_trade_history(user_id, days)


def order_attempt_candidates(
    state: Any,
    selected: dict[str, Any],
    *,
    user_id: str | None = None,
) -> list[dict[str, Any]]:
    if state.gale_pending or bool(selected.get("is_gale")):
        return [dict(selected)]
    candidates = [dict(selected)]
    ranked_candidates = sorted(
        [
            candidate
            for candidate in state.candidates
            if isinstance(candidate, dict)
            and candidate_meets_cycle_threshold(
                candidate,
                state,
                minimum_confidence=int(state.min_confidence),
                user_id=user_id,
            )
        ],
        key=lambda item: (
            int(item.get("strategy_score") or item.get("score") or 0),
            int(item.get("confidence") or 0),
            float(item.get("payout") or 0),
        ),
        reverse=True,
    )
    candidates.extend(ranked_candidates)
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for candidate in candidates:
        symbol = normalize_binary_active(str(candidate.get("symbol") or ""))
        direction = str(candidate.get("signal") or candidate.get("direction") or "").upper()
        key = (symbol, direction)
        if key in seen:
            continue
        seen.add(key)
        normalized = {**candidate, "symbol": symbol, "signal": direction, "direction": direction}
        unique.append(normalized)
    return unique


def build_strategy_narration(candidate: dict[str, Any]) -> tuple[str, str, list[str]]:
    approved = set(candidate.get("approved_filters") or [])
    used: list[str] = []
    named_label = str(candidate.get("strategy_name") or "").strip()
    strategy_key = str(candidate.get("strategy_key") or "").strip().upper()
    named_keys = [
        str(item.get("key") if isinstance(item, dict) else item)
        for item in (candidate.get("named_strategies") or candidate.get("named_strategy_keys") or [])
    ]
    if strategy_key:
        used.append(strategy_key)
    for key in named_keys:
        if key and key not in used:
            used.append(key)
    if "EMA_TREND" in approved or {"ema9", "ema21"}.issubset(candidate):
        used.append("EMA9/EMA21")
    if "RSI_RANGE" in approved or "rsi" in candidate:
        used.append("RSI")
    if "CANDLE_STRENGTH" in approved or "body_ratio" in candidate:
        used.append("Candle Force")
    if "WICK_REJECTION" in approved or "upper_wick_ratio" in candidate or "lower_wick_ratio" in candidate:
        used.append("Pavios")
    if "PRICE_ACTION_SETUP" in approved or "price_action_setup" in candidate:
        setup = str(candidate.get("price_action_setup") or "").upper()
        if setup == "REVERSAL":
            used.append("ZigZag/Reversao")
        elif setup == "SUPPORT_RESISTANCE":
            used.append("Suporte/Resistencia")
        else:
            used.append("Price Action")
    if "LAST_5_CONFIRMATION" in approved or "directional_candles_5" in candidate:
        used.append("Últimos Candles")
    if "VOLATILITY" in approved or "atr_pct" in candidate:
        used.append("Volatilidade")
    if candidate.get("payout") is not None:
        used.append("Payout")
    if not used:
        used = ["Score de Estratégias", "Payout"]

    # Preferência: rótulo da estratégia nomeada (retração / exaustão / fluxo).
    if named_label and strategy_key in {
        "RETRACEMENT_SR",
        "EXHAUSTION_REVERSAL",
        "CANDLE_FLOW",
        "CONTINUATION",
    }:
        strategy_name = named_label
    elif named_label and not named_label.lower().startswith("confluência"):
        strategy_name = named_label
    else:
        strategy_name = "Confluência " + " + ".join(used)
    strategy_reason = str(
        candidate.get("analysis_detail")
        or candidate.get("strategy_reason")
        or candidate.get("entry_reason")
        or candidate.get("reason")
        or candidate.get("signal_explanation")
        or ""
    ).strip()
    if not strategy_reason:
        symbol = normalize_binary_active(str(candidate.get("symbol") or ""))
        direction = str(candidate.get("direction") or candidate.get("signal") or "").strip().upper()
        confidence = int(candidate.get("confidence") or candidate.get("strategy_score") or candidate.get("score") or 0)
        payout = candidate.get("payout")
        direction_text = "CALL" if direction == "CALL" else "PUT" if direction == "PUT" else "direção definida pela estratégia"
        payout_text = f" payout de {float(payout):.0f}%" if payout is not None else " payout confirmado"
        strategy_reason = (
            f"{symbol or 'Ativo selecionado'} com entrada {direction_text}, "
            f"confiança {confidence} e{payout_text}. "
            f"Leitura baseada em {', '.join(used)}."
        )
    return strategy_name, strategy_reason, used

async def submit_bullex_order(
    user_id: str,
    endpoint: str,
    body: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    service_paths = {
        "/bullex/buy-demo": "/orders/buy-demo",
        "/bullex/buy-real": "/orders/buy-real",
    }
    return await call_bullex_service(
        "POST",
        service_paths[endpoint],
        user_id,
        json_body=body,
    )


def opposite_execution_direction(analyzed_direction: str) -> str:
    """Retorna a direção operacional oposta à direção analisada.

    Args:
        analyzed_direction: Direção produzida pela análise técnica (`CALL` ou
            `PUT`), sem distinção entre maiúsculas e minúsculas.

    Returns:
        `PUT` para uma análise `CALL`, ou `CALL` para uma análise `PUT`.

    Raises:
        ValueError: Se a direção não representar uma operação válida.
    """
    normalized_direction = str(analyzed_direction or "").strip().upper()
    opposite_directions = {"CALL": "PUT", "PUT": "CALL"}
    if normalized_direction not in opposite_directions:
        raise ValueError(f"INVALID_TRADE_DIRECTION:{normalized_direction or 'EMPTY'}")
    return opposite_directions[normalized_direction]


def resolve_robot_execution_direction(
    analyzed_direction: str,
    *,
    is_gale_order: bool,
) -> str:
    """Resolve a direção enviada à corretora para uma ordem do robô.

    A ordem segue a mesma direção aprovada pela análise técnica. Gale repete
    a direção da entrada anterior (já alinhada à análise).

    Args:
        analyzed_direction: Direção original aprovada pela análise técnica
            (entrada inicial) ou direção da ordem anterior (gale).
        is_gale_order: Indica se a ordem repete uma entrada anterior via gale.
            Mantido na assinatura para compatibilidade com o loop de ordens;
            não altera a direção resolvida.

    Returns:
        A mesma direção analisada (`CALL` ou `PUT`), normalizada em maiúsculas.

    Raises:
        ValueError: Se a direção analisada não for `CALL` nem `PUT`.
    """
    _ = is_gale_order  # API estável; política atual não diferencia gale.
    normalized_direction = str(analyzed_direction or "").strip().upper()
    if normalized_direction not in {"CALL", "PUT"}:
        raise ValueError(
            f"INVALID_TRADE_DIRECTION:{normalized_direction or 'EMPTY'}"
        )
    return normalized_direction


def validate_buy_real_order_payload(body: dict[str, Any]) -> str | None:
    required = ("active", "action", "amount", "expiration", "confirm_real")
    missing = [field for field in required if field not in body or body.get(field) in {None, ""}]
    if missing:
        return "BUY_REAL_PAYLOAD_MISSING_" + "_".join(missing).upper()
    if str(body.get("action") or "").strip().lower() not in {"call", "put"}:
        return "BUY_REAL_PAYLOAD_INVALID_ACTION"
    try:
        amount = float(body.get("amount"))
    except (TypeError, ValueError):
        return "BUY_REAL_PAYLOAD_INVALID_AMOUNT"
    if amount <= 0:
        return "BUY_REAL_PAYLOAD_INVALID_AMOUNT"
    try:
        expiration = int(body.get("expiration"))
    except (TypeError, ValueError):
        return "BUY_REAL_PAYLOAD_INVALID_EXPIRATION"
    if expiration <= 0:
        return "BUY_REAL_PAYLOAD_INVALID_EXPIRATION"
    if body.get("confirm_real") is not True:
        return "BUY_REAL_PAYLOAD_CONFIRM_REAL_REQUIRED"
    return None


def is_order_result_path(path: str) -> bool:
    return path.startswith("/orders/") and path.endswith("/result")


# Gargalo real corrigido em 2026-08-07: `persist_robot` é chamado (~50 pontos
# do arquivo, síncronos e assíncronos) toda vez que o estado do robô muda —
# inclusive a cada ciclo de análise (cadência contínua, por candle) de CADA
# usuário ativo. `robot_persistence.save_state/save_trade` usam `httpx.Client`
# SÍNCRONO contra o PostgREST do Supabase. Como o backend-gateway roda com um
# único worker uvicorn/asyncio (obrigatório: o estado do robô vive em memória
# no processo — múltiplos workers fragmentariam esse estado), cada chamada
# síncrona bloqueava o ÚNICO event loop pela duração da requisição HTTP. Com
# dezenas de usuários reais operando simultaneamente, isso empilhava e travava
# TODO o backend por vários segundos (login, `/admin/dashboard`, qualquer
# rota) — reproduzido com Playwright: login demorou 14–33s e
# `GET /admin/dashboard` teve resposta em ~19s, ambos sem relação direta com o
# endpoint em si (confirmado via curl direto: <2.5s isolado).
#
# Correção: as escritas (best-effort, já eram só logadas em warning se
# falhassem — nenhuma rota depende do retorno) passam a rodar numa
# ThreadPoolExecutor dedicada, fora do event loop. Um lock por usuário garante
# que as escritas do MESMO usuário continuem em ordem de submissão (evita
# `save_state` mais antigo sobrescrever um mais novo). `persist_robot`
# continua síncrona de propósito — trocar a assinatura para `async def`
# exigiria `await` em ~50 call sites, vários deles dentro de funções síncronas
# (ex.: `ensure_robot_worker`), tornando o refactor muito mais arriscado do
# que o ganho aqui. Ver docs/PERFORMANCE_SISTEMA.md.
_ROBOT_PERSIST_EXECUTOR = ThreadPoolExecutor(
    max_workers=8, thread_name_prefix="robot-persist"
)
_robot_persist_locks: dict[str, threading.Lock] = {}
_robot_persist_locks_guard = threading.Lock()


def _get_robot_persist_lock(user_id: str) -> threading.Lock:
    """Lock dedicado por usuário para serializar escritas em background."""
    with _robot_persist_locks_guard:
        lock = _robot_persist_locks.get(user_id)
        if lock is None:
            lock = threading.Lock()
            _robot_persist_locks[user_id] = lock
        return lock


def persist_robot(user_id: str) -> Future | None:
    """
    Agenda a persistência do robô em background (ver nota acima).

    Returns:
        O ``Future`` da escrita em background, ou ``None`` se não houve nada
        para persistir (ex.: leitura do estado em memória falhou). Nenhum dos
        ~50 call sites do arquivo usa o retorno — existe só para os testes
        sincronizarem com a escrita assíncrona via ``future.result()``.
    """
    state_payload: dict[str, Any] | None = None
    last_trade: dict[str, Any] | None = None
    try:
        state = auto_trader.get(user_id)
        state.account_mode = "REAL"
        state.allow_real = True
        state.confirm_real = True
        state_payload = strip_ai_fields(state.to_dict())
        state_payload.update(
            {
                "account_mode": "REAL",
                "allow_real": True,
                "confirm_real": True,
            }
        )
        last_trade = state.last_trade
        robot_state_hydrated_users.add(user_id)
        restorable_robot_states[user_id] = deepcopy(state_payload)
        if robot_state_ws_hub.has_connections(user_id):
            robot_state_ws_hub.schedule_publish(user_id)
        if robot_runtime_mode() == "worker":
            try:
                robot_bus.publish_snapshot(
                    user_id, build_robot_state_snapshot_payload(user_id)
                )
            except Exception:
                logger.warning(
                    "[ROBOT_BUS_SNAPSHOT_FAILED] user_id=%s",
                    user_id,
                    exc_info=True,
                )
    except Exception:
        logger.warning("[ROBOT_PERSISTENCE_WARNING] user_id=%s step=read_state", user_id, exc_info=True)
        return None

    def _write_to_supabase(payload: dict[str, Any], trade: dict[str, Any] | None) -> None:
        with _get_robot_persist_lock(user_id):
            try:
                robot_persistence.save_state(user_id, payload)
            except Exception:
                logger.warning(
                    "[ROBOT_PERSISTENCE_WARNING] user_id=%s step=save_state", user_id, exc_info=True
                )

            try:
                save_settings = getattr(robot_persistence, "save_settings", None)
                if callable(save_settings):
                    save_settings(user_id, payload)
            except Exception:
                logger.warning(
                    "[ROBOT_PERSISTENCE_WARNING] user_id=%s step=save_settings", user_id, exc_info=True
                )

            if trade:
                try:
                    robot_persistence.save_trade(user_id, trade)
                except Exception:
                    logger.warning(
                        "[ROBOT_PERSISTENCE_WARNING] user_id=%s step=save_trade", user_id, exc_info=True
                    )

    try:
        return _ROBOT_PERSIST_EXECUTOR.submit(_write_to_supabase, state_payload, last_trade)
    except RuntimeError:
        # Executor derrubado (shutdown do processo) — melhor esforço síncrono.
        _write_to_supabase(state_payload, last_trade)
        return None


def robot_persistence_source() -> str:
    if robot_persistence.__class__.__name__ == "SupabaseRobotPersistence":
        return "supabase"
    return "memory"


def get_user_robot_state(user_id: str) -> Any:
    """
    Devolve o estado do robô em memória, hidratando do disco só a frio.

    Importante: se já existe estado em memória, **não** rehidratar forçando
    ``enabled=False`` / ``STOPPED``. Esse clobber matava o robô logo após
    ``POST /robot/start`` (UI voltava para “parado” mesmo com start 200).
    """
    if auto_trader.has_state(user_id) and user_id in robot_state_hydrated_users:
        state = auto_trader.get(user_id)
        state.account_mode = "REAL"
        state.allow_real = True
        state.confirm_real = True
        if state.active_mode is None or str(state.active_mode).strip().upper() == "DEMO":
            state.active_mode = "REAL"
        return state
    if auto_trader.has_state(user_id):
        # Memória já populada (ex.: start acabou de ligar) — só marca hydrated.
        state = auto_trader.get(user_id)
        state.account_mode = "REAL"
        state.allow_real = True
        state.confirm_real = True
        if state.active_mode is None or str(state.active_mode).strip().upper() == "DEMO":
            state.active_mode = "REAL"
        robot_state_hydrated_users.add(user_id)
        return state
    robot_state_hydrated_users.discard(user_id)
    try:
        load_settings = getattr(robot_persistence, "load_settings", None)
        settings = load_settings(user_id) if callable(load_settings) else None
        payload = restorable_robot_states.get(user_id)
        if payload is None:
            payload = robot_persistence.load_state(user_id)
        if payload is not None:
            # Força parado até /robot/start — e status STOPPED para a UI não
            # mostrar "analisando" fantasma com enabled=False no banco ligado.
            payload = {
                **payload,
                "enabled": False,
                # A trava acima continua: robô só volta com /robot/start do
                # cliente. Este sinal apenas conta ao painel que a parada foi
                # manutenção/restart, não decisão do usuário — para a UI oferecer
                # "religar" em vez de fingir que ele mesmo desligou.
                "paused_by_maintenance": bool(payload.get("was_running")),
                "connected": False,
                "active_mode": None,
                "connection_checked_at": None,
                "connection_status_source": "restorable",
                "status": STATUS_STOPPED,
                "analysis_message": None,
                "status_message": None,
                "display_countdown_label": None,
                "voice_message": None,
            }
            if settings is not None:
                payload = {**payload, **settings}
            payload["account_mode"] = "REAL"
            payload["allow_real"] = True
            payload["confirm_real"] = True
            if payload.get("active_mode") is None or str(payload.get("active_mode")).strip().upper() == "DEMO":
                payload["active_mode"] = "REAL"
            trades = robot_persistence.load_trades(user_id)
            if not trades:
                try:
                    trades = [
                        item
                        for item in robot_persistence.load_trade_history(user_id, 30)
                        if str(item.get("result") or "").upper() in {"WIN", "LOSS", "TIMEOUT", "DRAW"}
                    ]
                except Exception:
                    logger.exception("[ON_DEMAND_HISTORY_HYDRATE_FAILED] user_id=%s", user_id)
                    trades = []
            state = auto_trader.restore(
                user_id,
                payload,
                trades,
                source=robot_persistence_source(),
            )
            robot_state_hydrated_users.add(user_id)
            logger.info("[USER_STATE_LOADED_NO_WORKER] user_id=%s source=%s", user_id, robot_persistence_source())
            return state
        if settings is not None:
            state = auto_trader.get(user_id)
            for field, value in settings.items():
                if field != "account_mode" and hasattr(state, field):
                    setattr(state, field, value)
            state.account_mode = "REAL"
            state.allow_real = True
            state.confirm_real = True
            if state.active_mode is None or str(state.active_mode).strip().upper() == "DEMO":
                state.active_mode = "REAL"
            state.enabled = False
            auto_trader.mark_source(user_id, robot_persistence_source())
            robot_state_hydrated_users.add(user_id)
            logger.info("[USER_STATE_LOADED_NO_WORKER] user_id=%s source=%s", user_id, robot_persistence_source())
            return state
    except Exception:
        logger.exception("[ROBOT USER LOAD ERROR] user_id=%s", user_id)
    robot_state_hydrated_users.add(user_id)
    state = auto_trader.get(user_id)
    state.account_mode = "REAL"
    state.allow_real = True
    state.confirm_real = True
    if state.active_mode is None or str(state.active_mode).strip().upper() == "DEMO":
        state.active_mode = "REAL"
    return state


async def load_candles_for_active(
    user_id: str,
    symbol: str,
    timeframe: str,
    endtime: int | None = None,
) -> tuple[list[dict[str, Any]] | None, bool, str | None]:
    """
    Carrega candles de um ativo/timeframe via cache ou Bullex.

    Returns:
        Tupla (candles, used_cache, error_code). ``error_code`` pode ser
        ``SESSION_DISCONNECTED`` quando a sessao Bullex caiu.
    """
    cached_candles = cached_candles_for_active(user_id, symbol, timeframe, endtime=endtime)
    if cached_candles:
        return cached_candles, True, None

    interval = TIMEFRAME_SECONDS[timeframe]
    candle_params: dict[str, Any] = {
        "active": symbol,
        "interval": interval,
        "count": ROBOT_CANDLE_COUNT,
    }
    if endtime is not None:
        candle_params["endtime"] = endtime

    try:
        status_code, payload = await asyncio.wait_for(
            call_bullex_service(
                "GET",
                "/candles",
                user_id,
                params=candle_params,
            ),
            timeout=ACTIVE_DATA_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        cached_candles = cached_candles_for_active(user_id, symbol, timeframe, endtime=endtime)
        if cached_candles:
            logger.info(
                "[ACTIVE_CACHE] user_id=%s symbol=%s timeframe=%s path=/candles reason=CANDLES_TIMEOUT",
                user_id,
                symbol,
                timeframe,
            )
            return cached_candles, True, None
        return None, False, "CANDLES_TIMEOUT"

    log_ignored_disconnect(user_id, "/candles", payload)
    if payload.get("ok"):
        candles = extract_candles(payload)
        return candles, False, None

    if is_session_disconnected(payload):
        return None, False, SESSION_DISCONNECTED

    cached_candles = cached_candles_for_active(user_id, symbol, timeframe, endtime=endtime)
    if cached_candles:
        logger.info(
            "[ACTIVE_CACHE] user_id=%s symbol=%s timeframe=%s path=/candles reason=CANDLES_UNAVAILABLE",
            user_id,
            symbol,
            timeframe,
        )
        return cached_candles, True, None
    _ = status_code
    return None, False, str(payload.get("error") or "CANDLES_UNAVAILABLE")


async def analyze_active_signal(
    user_id: str,
    symbol: str,
    timeframe: str = "M1",
    endtime: int | None = None,
    strategy_mode: str = "conservative",
) -> tuple[int, dict[str, Any]]:
    """
    Analisa o ativo no grafico do timeframe da operacao.

    Operacao M1 → candles M1; M5 → M5; M15 → M15.
    """
    operation_timeframe = str(timeframe or "M1").strip().upper()
    if operation_timeframe not in TIMEFRAME_SECONDS:
        operation_timeframe = "M1"
    reference_endtime = int(endtime if endtime is not None else utc_now().timestamp())
    endtime = closed_candle_endtime(reference_endtime, operation_timeframe)

    cached_candles = cached_candles_for_active(
        user_id, symbol, operation_timeframe, endtime=endtime
    )
    cached_payout = cached_payout_for_active(user_id, symbol)
    used_cache = bool(cached_candles or cached_payout is not None)

    if active_cooldown_remaining(user_id, symbol) is not None:
        # Cooldown duro: nunca analisar/relançar o ativo (nem com candles em
        # cache). Antes o cache furava o skip e o M1 recomprava o mesmo símbolo
        # rejeitado pela corretora a cada vela.
        logger.warning("[ACTIVE_SKIPPED] user_id=%s symbol=%s reason=ACTIVE_COOLDOWN", user_id, symbol)
        return 200, build_success(
            {
                "symbol": symbol,
                "signal": "WAIT",
                "confidence": 0,
                "trade_allowed": False,
                "quality_reason": "ACTIVE_COOLDOWN",
                "blocked_filters": ["ACTIVE_COOLDOWN"],
                "approved_filters": [],
            }
        )

    if payout_cooldown_remaining(user_id, symbol) is not None:
        if cached_payout is not None:
            logger.info("[ACTIVE_CACHE] user_id=%s symbol=%s path=/payouts reason=PAYOUT_COOLDOWN", user_id, symbol)
        else:
            logger.warning("[ACTIVE_SKIPPED] user_id=%s symbol=%s reason=PAYOUT_COOLDOWN", user_id, symbol)
            return 200, build_success(
                {
                    "symbol": symbol,
                    "signal": "WAIT",
                    "confidence": 0,
                    "trade_allowed": False,
                    "quality_reason": "PAYOUT_COOLDOWN",
                    "blocked_filters": ["PAYOUT_COOLDOWN"],
                    "approved_filters": [],
                }
            )

    candles, from_cache, error_code = await load_candles_for_active(
        user_id,
        symbol,
        operation_timeframe,
        endtime=endtime,
    )
    used_cache = used_cache or from_cache
    if candles is None:
        if error_code == SESSION_DISCONNECTED:
            return 409, build_error(SESSION_DISCONNECTED)
        set_named_cooldown(
            active_cooldowns,
            user_id,
            symbol,
            seconds=ACTIVE_COOLDOWN_SECONDS,
            log_label="ACTIVE_SKIPPED",
            status=STATUS_ACTIVE_COOLDOWN,
            reason=str(error_code or "CANDLES_UNAVAILABLE"),
        )
        return 200, build_success(
            {
                "symbol": symbol,
                "signal": "WAIT",
                "confidence": 0,
                "trade_allowed": False,
                "quality_reason": str(error_code or "CANDLES_UNAVAILABLE"),
                "blocked_filters": [str(error_code or "CANDLES_UNAVAILABLE")],
                "approved_filters": [],
            }
        )

    if not candles:
        return 200, build_success(
            {
                "symbol": symbol,
                "signal": "WAIT",
                "confidence": 0,
                "trade_allowed": False,
                "quality_reason": "CANDLES_UNAVAILABLE",
                "blocked_filters": ["CANDLES_UNAVAILABLE"],
                "approved_filters": [],
            }
        )

    payout_status = 200
    payout = None
    payout_payload: dict[str, Any] | None = None
    cached_payout_payload = cached_payout_payload_for_active(user_id, symbol)
    if cached_payout_payload is not None:
        # Usa o payload completo (com open_turbo/open_binary). Antes só o float
        # do payout era reaproveitado e o canal fechado passava como "aberto".
        payout_payload = cached_payout_payload
        payout = extract_payout(payout_payload, symbol)
        used_cache = True
        logger.info(
            "[ACTIVE_CACHE] user_id=%s symbol=%s path=/payouts reason=FULL_PAYLOAD",
            user_id,
            symbol,
        )
    if payout_payload is None:
        try:
            payout_status, payout_payload = await asyncio.wait_for(
                call_bullex_service(
                    "GET",
                    "/payouts",
                    user_id,
                    params={"active": symbol},
                ),
                timeout=ACTIVE_DATA_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            cached_payout_payload = cached_payout_payload_for_active(user_id, symbol)
            if cached_payout_payload is not None:
                logger.info("[ACTIVE_CACHE] user_id=%s symbol=%s path=/payouts reason=PAYOUT_TIMEOUT", user_id, symbol)
                used_cache = True
                payout_status = 200
                payout_payload = cached_payout_payload
            else:
                set_named_cooldown(
                    payout_cooldowns,
                    user_id,
                    symbol,
                    seconds=PAYOUT_COOLDOWN_SECONDS,
                    log_label="PAYOUT_TIMEOUT",
                    status=STATUS_PAYOUT_COOLDOWN,
                    reason="PAYOUT_TIMEOUT",
                )
                payout_status = 200
                payout_payload = build_success([])
        log_ignored_disconnect(user_id, "/payouts", payout_payload)
    payout = extract_payout(payout_payload, symbol) if payout_payload and payout_payload.get("ok") else None
    if payout is None and payout_status >= 400:
        set_named_cooldown(
            payout_cooldowns,
            user_id,
            symbol,
            seconds=PAYOUT_COOLDOWN_SECONDS,
            log_label="PAYOUT_COOLDOWN",
            status=STATUS_PAYOUT_COOLDOWN,
            reason="PAYOUT_COOLDOWN",
        )

    signal = analyze_signal(
        symbol,
        candles,
        timeframe=operation_timeframe,
        strategy_mode=strategy_mode,
        payout=payout,
        frequency_recovery=frequency_recovery_active(user_id),
    )
    signal["operation_timeframe"] = operation_timeframe
    signal["analysis_timeframe"] = operation_timeframe
    channel_open = (
        extract_asset_open(payout_payload, symbol, operation_timeframe)
        if isinstance(payout_payload, dict)
        else None
    )
    apply_execution_channel_open(
        signal,
        channel_open=channel_open,
        timeframe=operation_timeframe,
    )
    if used_cache:
        signal["from_cache"] = True
    if payout_status >= 400 and payout is None:
        signal["blocked_filters"] = list(signal.get("blocked_filters") or []) + ["PAYOUT_UNAVAILABLE"]
        signal["trade_allowed"] = False
        signal["quality_reason"] = LOW_QUALITY_SIGNAL
    logger.info(
        "[OPERATION_TF_ANALYSIS] user_id=%s symbol=%s timeframe=%s signal=%s confidence=%s",
        user_id,
        symbol,
        operation_timeframe,
        signal.get("signal"),
        signal.get("confidence"),
    )
    logger.info("[ACTIVE_OK] user_id=%s symbol=%s payout=%s confidence=%s", user_id, symbol, payout, signal.get("confidence"))
    logger.info("[SIGNAL ANALYZE] %s %s %s", symbol, signal["signal"], signal["confidence"])
    return 200, build_success(signal)


def relax_signal_confidence_only(
    signal: dict[str, Any],
    *,
    minimum_confidence: int = RECOVERY_MIN_CONFIDENCE,
) -> dict[str, Any]:
    """Libera confiança e ausência de padrão somente para confirmação MTF.

    Args:
        signal: Sinal local com filtros e estratégias já calculados.
        minimum_confidence: Piso reduzido aplicado no ciclo de recuperação.

    Returns:
        Cópia do sinal, liberada apenas quando nenhum outro bloqueio crítico existe.
    """
    normalized = dict(signal)
    confidence = int(normalized.get("confidence") or 0)
    blocked = list(dict.fromkeys(str(item) for item in (normalized.get("blocked_filters") or [])))
    matched = [
        str(item)
        for item in (normalized.get("matched_strategies") or [])
        if str(item).strip()
    ]
    remaining_critical = {
        item
        for item in blocked
        if item in CRITICAL_TRADE_BLOCKS
        and item not in {"MIN_CONFIDENCE", "NO_PATTERN_FOUND"}
    }
    direction = str(normalized.get("signal") or normalized.get("direction") or "").upper()
    pattern_can_be_relaxed = bool(matched) or "NO_PATTERN_FOUND" in blocked
    if (
        confidence < minimum_confidence
        or remaining_critical
        or not pattern_can_be_relaxed
        or direction not in {"CALL", "PUT"}
        or normalized.get("fallback_candidate_used")
    ):
        return normalized

    normalized["blocked_filters"] = [
        item
        for item in blocked
        if item not in {"MIN_CONFIDENCE", "NO_PATTERN_FOUND"}
    ]
    normalized["block_reasons"] = [
        str(item)
        for item in (normalized.get("block_reasons") or normalized["blocked_filters"])
        if str(item) not in {"MIN_CONFIDENCE", "NO_PATTERN_FOUND"}
    ]
    approved = list(dict.fromkeys(str(item) for item in (normalized.get("approved_filters") or [])))
    if "MIN_CONFIDENCE_RECOVERY" not in approved:
        approved.append("MIN_CONFIDENCE_RECOVERY")
    if not matched and "PATTERN_RECOVERY_MTF_REQUIRED" not in approved:
        approved.append("PATTERN_RECOVERY_MTF_REQUIRED")
    normalized["approved_filters"] = approved
    normalized["trade_allowed"] = True
    normalized["confidence_relaxed_after_skip"] = True
    normalized["pattern_relaxed_after_skip"] = not bool(matched)
    normalized["effective_min_confidence"] = minimum_confidence
    normalized["quality_reason"] = "OK"
    return normalized


async def confirm_candidate_multi_timeframe(
    user_id: str,
    candidate: dict[str, Any],
    *,
    primary_timeframe: str,
    endtime: int | None,
    strategy_mode: str,
) -> dict[str, Any]:
    """Confirma um candidato nos gráficos M1, M5 e M15 antes de liberá-lo."""
    symbol = normalize_binary_active(str(candidate.get("symbol") or ""))
    normalized_primary = str(primary_timeframe or "M1").strip().upper()
    confidence_recovery_active = bool(candidate.get("confidence_relaxed_after_skip"))
    primary_candidate = (
        relax_signal_confidence_only(candidate)
        if confidence_recovery_active
        else dict(candidate)
    )
    signals_by_timeframe: dict[str, dict[str, Any]] = {
        normalized_primary: primary_candidate
    }

    for timeframe in ANALYSIS_TIMEFRAMES:
        if timeframe == normalized_primary:
            continue
        try:
            status_code, payload = await analyze_active_signal(
                user_id,
                symbol,
                timeframe=timeframe,
                endtime=endtime,
                strategy_mode=strategy_mode,
            )
        except Exception:
            logger.warning(
                "[MTF_ANALYSIS_FAILED] user_id=%s symbol=%s timeframe=%s",
                user_id,
                symbol,
                timeframe,
                exc_info=True,
            )
            continue
        data = payload.get("data") if status_code < 400 and payload.get("ok") else None
        if isinstance(data, dict):
            signals_by_timeframe[timeframe] = (
                relax_signal_confidence_only(data)
                if confidence_recovery_active
                else data
            )

    merged = merge_multi_timeframe_signals(
        normalized_primary,
        signals_by_timeframe,
        minimum_vote_confidence=(
            RECOVERY_MIN_CONFIDENCE
            if confidence_recovery_active
            else MTF_VOTE_CONFIDENCE_MIN
        ),
    )
    if len(signals_by_timeframe) < 2:
        blocked = list(merged.get("blocked_filters") or [])
        if "MTF_DATA_UNAVAILABLE" not in blocked:
            blocked.append("MTF_DATA_UNAVAILABLE")
        merged["blocked_filters"] = blocked
        merged["block_reasons"] = blocked
        merged["trade_allowed"] = False
        merged["mtf_ready"] = False
        merged["quality_reason"] = "Dados multi-timeframe insuficientes."
    logger.info(
        "[MTF_CONFIRMATION] user_id=%s symbol=%s primary=%s votes=%s ready=%s",
        user_id,
        symbol,
        normalized_primary,
        merged.get("mtf_votes"),
        merged.get("mtf_ready"),
    )
    return {**candidate, **merged, "symbol": symbol}


async def confirm_ranked_candidates_multi_timeframe(
    user_id: str,
    candidates: list[dict[str, Any]],
    *,
    primary_timeframe: str,
    endtime: int | None,
    strategy_mode: str,
) -> list[dict[str, Any]]:
    """Estratégia clássica do backup: opera no timeframe da operação sem gate MTF.

    Mantém os campos MTF preenchidos para telemetria, mas não bloqueia nem
    reordena candidatos com base em confluência multi-timeframe.
    """
    confirmed: list[dict[str, Any]] = []
    for candidate in candidates:
        allowed = candidate.get("trade_allowed") is True
        confirmed.append(
            {
                **candidate,
                "mtf_ready": True,
                "mtf_confirmed": True,
                "mtf_skipped": True,
                "mtf_confluence": 2 if allowed else int(candidate.get("mtf_confluence") or 0),
                "mtf_primary_timeframe": primary_timeframe,
                "mtf_strategy_mode": strategy_mode,
            }
        )
    logger.info(
        "[MTF_SKIPPED_CLASSIC_STRATEGY] user_id=%s candidates=%s primary=%s",
        user_id,
        len(confirmed),
        primary_timeframe,
    )
    return confirmed

async def scan_local_signals(
    user_id: str,
    limit: int = 5,
    include_wait: bool = False,
    timeframe: str = "M1",
    endtime: int | None = None,
    strategy_mode: str = "conservative",
    max_assets: int | None = None,
    asset_sleep_seconds: float = 0.0,
    market_mode: str | None = None,
) -> tuple[int, dict[str, Any]]:
    logger.info("[SIGNAL SCAN START]")
    signals = []
    resolved_mode = effective_market_mode(market_mode)
    analysis_assets = select_analysis_assets_for_cycle(
        user_id,
        max_assets=max_assets,
        market_mode=resolved_mode,
    )
    logger.info(
        "[ANALYSIS_FILTER] market_mode=%s requested=%s total_allowed=%s filtered_assets=%s",
        resolved_mode,
        normalize_market_mode(market_mode),
        len(BINARY_ALLOWED_ASSETS),
        len(analysis_assets),
    )
    logger.info("[ANALYSIS_FILTER_ASSETS] assets=%s", ",".join(analysis_assets))

    async def analyze_one(symbol: str) -> tuple[str, int, dict[str, Any]]:
        try:
            logger.info("[ANALYZING_ASSET] user_id=%s asset=%s", user_id, symbol)
            logger.info("[ANALYZING_ASSET] symbol=%s", symbol)
            status_code, payload = await asyncio.wait_for(
                analyze_active_signal(
                    user_id,
                    symbol,
                    timeframe=timeframe,
                    endtime=endtime,
                    strategy_mode=strategy_mode,
                ),
                timeout=ROBOT_ANALYSIS_ASSET_TIMEOUT_SECONDS,
            )
            return symbol, status_code, payload
        except asyncio.TimeoutError:
            cached_candles = cached_candles_for_active(user_id, symbol, timeframe, endtime=endtime)
            if cached_candles:
                cached_payout = cached_payout_for_active(user_id, symbol)
                cached_signal = analyze_signal(
                    symbol,
                    cached_candles,
                    timeframe=timeframe,
                    strategy_mode=strategy_mode,
                    payout=cached_payout,
                    frequency_recovery=frequency_recovery_active(user_id),
                )
                cached_signal["from_cache"] = True
                cached_signal["market_data_stale"] = True
                cached_signal["stale"] = True
                cached_signal["trade_allowed"] = False
                blocked = list(cached_signal.get("blocked_filters") or [])
                if "STALE_MARKET_DATA" not in blocked:
                    blocked.append("STALE_MARKET_DATA")
                cached_signal["blocked_filters"] = blocked
                cached_signal["block_reasons"] = list(blocked)
                cached_signal["quality_reason"] = "STALE_MARKET_DATA"
                logger.info("[ACTIVE_CACHE] user_id=%s symbol=%s path=/candles reason=ANALYSIS_TIMEOUT", user_id, symbol)
                return symbol, 200, build_success(cached_signal)
            logger.warning("[ACTIVE_TIMEOUT] user_id=%s symbol=%s phase=analysis", user_id, symbol)
            return symbol, 200, build_success(
                {
                    "symbol": symbol,
                    "signal": "WAIT",
                    "confidence": 0,
                    "trade_allowed": False,
                    "quality_reason": "ACTIVE_TIMEOUT",
                    "blocked_filters": ["ACTIVE_TIMEOUT"],
                    "approved_filters": [],
                    "_analysis_timeout": True,
                }
            )
        except Exception as exc:
            logger.warning("[ACTIVE_SKIPPED] user_id=%s symbol=%s reason=%s", user_id, symbol, exc.__class__.__name__)
            return symbol, 200, build_success(
                {
                    "symbol": symbol,
                    "signal": "WAIT",
                    "confidence": 0,
                    "trade_allowed": False,
                    "quality_reason": exc.__class__.__name__,
                    "blocked_filters": [exc.__class__.__name__],
                    "approved_filters": [],
                }
            )

    # Sequencial: Bullex serializa por usuário (lock). gather paralelo gerava
    # ACTIVE_TIMEOUT em massa e o ciclo fechava em NO_OPPORTUNITY sem rodar estratégias.
    results: list[tuple[str, int, dict[str, Any]]] = []
    for index, symbol in enumerate(analysis_assets):
        robot_worker_last_tick_at[user_id] = utc_now()
        result = await analyze_one(symbol)
        results.append(result)
        advance_analysis_asset_cursor(user_id, market_mode=resolved_mode)
        robot_worker_last_tick_at[user_id] = utc_now()
        if asset_sleep_seconds > 0 and index + 1 < len(analysis_assets):
            await asyncio.sleep(asset_sleep_seconds)
    timed_out_symbols = [
        symbol
        for symbol, _status_code, payload in results
        if isinstance(payload, dict)
        and payload.get("ok")
        and isinstance(payload.get("data"), dict)
        and payload["data"].get("_analysis_timeout")
    ]
    all_assets_timed_out = bool(analysis_assets) and len(timed_out_symbols) == len(analysis_assets)
    if all_assets_timed_out:
        logger.warning(
            "[ANALYSIS_BATCH_TIMEOUT] user_id=%s assets=%s action=retry_next_tick",
            user_id,
            ",".join(timed_out_symbols),
        )
    else:
        for symbol in timed_out_symbols:
            set_named_cooldown(
                active_cooldowns,
                user_id,
                symbol,
                seconds=ACTIVE_COOLDOWN_SECONDS,
                log_label="ACTIVE_TIMEOUT",
                status=STATUS_ACTIVE_COOLDOWN,
                reason="ACTIVE_TIMEOUT",
            )

    for symbol, status_code, payload in results:
        try:
            if not payload.get("ok"):
                if is_session_disconnected(payload):
                    logger.warning("[SIGNAL ERROR] %s %s", symbol, payload.get("error"))
                    logger.warning("[ACTIVE_SKIPPED] user_id=%s symbol=%s reason=%s", user_id, symbol, payload.get("error"))
                    continue
                logger.warning("[SIGNAL ERROR] %s %s", symbol, payload.get("error"))
                logger.warning("[ACTIVE_SKIPPED] user_id=%s symbol=%s reason=%s", user_id, symbol, payload.get("error"))
                continue

            signal = payload["data"]
            logger.info(
                "[ASSET_SCORE] user_id=%s symbol=%s signal=%s confidence=%s score=%s payout=%s allowed=%s",
                user_id,
                symbol,
                signal.get("signal"),
                signal.get("confidence"),
                signal.get("strategy_score") or signal.get("score") or 0,
                signal.get("payout"),
                signal.get("trade_allowed"),
            )
            if signal["confidence"] < 70 and not include_wait:
                continue
            if (signal["signal"] == "WAIT" or not signal.get("trade_allowed", True)) and not include_wait:
                continue
            signals.append(signal)
        except Exception as exc:
            logger.warning("[ACTIVE_SKIPPED] user_id=%s symbol=%s reason=%s", user_id, symbol, exc.__class__.__name__)
            continue

    signals.sort(key=lambda item: item["confidence"], reverse=True)
    limited_signals = signals[:limit]
    timed_out_count = len(timed_out_symbols)
    scored_count = sum(
        1
        for _symbol, _status, payload in results
        if isinstance(payload, dict)
        and payload.get("ok")
        and isinstance(payload.get("data"), dict)
        and not payload["data"].get("_analysis_timeout")
        and str(payload["data"].get("quality_reason") or "") not in {"ACTIVE_TIMEOUT", "ACTIVE_COOLDOWN", "PAYOUT_COOLDOWN"}
    )
    allowed_count = sum(1 for item in limited_signals if item.get("trade_allowed"))
    logger.info("[SIGNAL SCAN RESULT] count=%s", len(limited_signals))
    logger.warning(
        "[SIGNAL SCAN SUMMARY] user_id=%s market_mode=%s assets=%s scored=%s timed_out=%s "
        "allowed=%s returned=%s recovery=%s",
        user_id,
        resolved_mode,
        len(analysis_assets),
        scored_count,
        timed_out_count,
        allowed_count,
        len(limited_signals),
        frequency_recovery_active(user_id),
    )
    if frequency_recovery_active(user_id) and allowed_count == 0:
        logger.warning(
            "[FREQUENCY_RECOVERY_ACTIVE] user_id=%s consecutive=%s soft_blocks=%s "
            "still_zero_allowed=1",
            user_id,
            getattr(auto_trader.get(user_id), "consecutive_no_opportunity_cycles", 0),
            sorted(FREQUENCY_RECOVERY_SOFT_BLOCKS),
        )
    return 200, build_success(limited_signals)


def simple_candle_direction(candles: list[dict[str, Any]]) -> str:
    prices: list[float] = []
    for candle in candles:
        value = candle.get("close", candle.get("close_price", candle.get("c")))
        try:
            prices.append(float(value))
        except (TypeError, ValueError):
            continue
    if len(prices) < 2:
        return "CALL"
    return "CALL" if prices[-1] >= prices[0] else "PUT"


def candidate_rank(candidate: dict[str, Any] | None) -> tuple[int, float, int]:
    """Ordena candidatos por qualidade estrutural, não por confiança bruta.

    Em opções binárias OTC a confiança alta do score técnico não implica edge
    (amostra real: conf ≥95 com WR 37,8%). O ranking prioriza ``strategy_score``
    (já penalizado pelos filtros) e payout; a confiança fica por último só como
    desempate fraco.

    Args:
        candidate: Candidato de entrada ou ``None``.

    Returns:
        Tupla ordenável ``(strategy_score, payout, confidence)``.
    """
    if not isinstance(candidate, dict):
        return (-1, -1.0, -1)
    return (
        int(candidate.get("strategy_score") or candidate.get("score") or 0),
        float(candidate.get("payout") or 0),
        int(candidate.get("confidence") or 0),
    )

def candidate_meets_cycle_threshold(
    candidate: dict[str, Any] | None,
    state: Any,
    *,
    minimum_confidence: int,
    user_id: str | None = None,
) -> bool:
    if not isinstance(candidate, dict):
        return False
    if str(candidate.get("direction") or candidate.get("signal") or "").upper() not in {"CALL", "PUT"}:
        return False
    if candidate_pre_order_block_reason(candidate) is not None:
        return False
    # Dados de candles/payout vindos de cache stale não podem abrir ordem —
    # eram a origem de "asset not available" e setups atrasados.
    if (
        candidate.get("stale") is True
        or candidate.get("from_cache") is True
        or candidate.get("market_data_stale") is True
        or "STALE_MARKET_DATA" in {
            str(item) for item in (candidate.get("blocked_filters") or [])
        }
    ):
        return False
    # Sinal reprovado pelos filtros da estratégia nunca vira entrada, mesmo com
    # confiança/payout altos. Sem isso, candidatos com bloqueios críticos
    # (LEVEL_CONFLICT, DOJI_FILTER etc.) eram executados em produção.
    if candidate.get("trade_allowed") is not True:
        return False
    blocked_filters = {str(item) for item in (candidate.get("blocked_filters") or [])}
    recovery = bool(candidate.get("frequency_recovery")) or frequency_recovery_active(user_id)
    if blocked_filters & effective_critical_trade_blocks(frequency_recovery=recovery):
        return False
    try:
        payout = float(candidate.get("payout") or 0)
        # Portão usa strategy_score (após penalidades), não confiança bruta —
        # confiança alta em OTC binário não prova qualidade do setup.
        effective_score = int(
            candidate.get("strategy_score")
            if candidate.get("strategy_score") is not None
            else candidate.get("score")
            if candidate.get("score") is not None
            else candidate.get("confidence")
            or 0
        )
    except (TypeError, ValueError):
        return False
    score_floor = int(minimum_confidence)
    if recovery:
        score_floor = min(score_floor, FREQUENCY_RECOVERY_MIN_SCORE)
    if not (payout >= float(state.min_payout) and effective_score >= score_floor):
        return False
    # Memória de padrões: vetar contextos com histórico fraco do próprio usuário.
    if user_id:
        if "timeframe" not in candidate or not candidate.get("timeframe"):
            candidate["timeframe"] = getattr(state, "timeframe", "M1")
        decision = pattern_memory.apply_to_candidate(
            user_id,
            candidate,
            default_timeframe=str(getattr(state, "timeframe", "M1") or "M1"),
            history_loader=pattern_memory_history_loader,
        )
        if not decision.allowed:
            return False
    return True

def choose_better_candidate(
    current: dict[str, Any] | None,
    incoming: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if incoming is None:
        return current
    if current is None:
        return dict(incoming)
    return dict(incoming) if candidate_rank(incoming) > candidate_rank(current) else current


def resolve_cycle_entry_candidate(
    state: Any,
    *,
    user_id: str | None = None,
) -> dict[str, Any] | None:
    strict = state.cycle_best_trade_candidate
    if candidate_meets_cycle_threshold(
        strict,
        state,
        minimum_confidence=int(state.min_confidence),
        user_id=user_id,
    ):
        return dict(strict)
    fallback = state.cycle_best_candidate
    if candidate_meets_cycle_threshold(
        fallback,
        state,
        minimum_confidence=70,
        user_id=user_id,
    ):
        candidate = dict(fallback)
        candidate["fallback_candidate_used"] = True
        return candidate
    return None

async def select_fallback_candidate(
    user_id: str,
    state: Any,
    *,
    endtime: int | None = None,
    max_assets: int | None = ROBOT_MAX_ASSETS_PER_CYCLE,
) -> dict[str, Any] | None:
    assets = select_analysis_assets_for_cycle(
        user_id,
        max_assets=max_assets,
        market_mode=getattr(state, "market_mode", "OTC"),
    )
    for index, symbol in enumerate(assets):
        if index > 0:
            await asyncio.sleep(ROBOT_ASSET_QUEUE_SLEEP_SECONDS)
        cached_candles = cached_candles_for_active(user_id, symbol, state.timeframe, endtime=endtime)
        cached_payout = cached_payout_for_active(user_id, symbol)
        if active_cooldown_remaining(user_id, symbol) is not None:
            logger.warning("[ACTIVE_COOLDOWN] user_id=%s symbol=%s", user_id, symbol)
            continue
        if payout_cooldown_remaining(user_id, symbol) is not None:
            if cached_payout is not None:
                logger.info("[ACTIVE_CACHE] user_id=%s symbol=%s path=/payouts reason=FALLBACK_PAYOUT_COOLDOWN", user_id, symbol)
            else:
                logger.warning("[PAYOUT_COOLDOWN] user_id=%s symbol=%s", user_id, symbol)
                continue
        try:
            payout = cached_payout
            if payout is None:
                try:
                    payout_status, payout_payload = await asyncio.wait_for(
                        call_bullex_service(
                            "GET",
                            "/payouts",
                            user_id,
                            params={"active": symbol},
                        ),
                        timeout=ACTIVE_DATA_TIMEOUT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    logger.warning("[ACTIVE_SKIPPED] user_id=%s symbol=%s reason=FALLBACK_PAYOUT_TIMEOUT", user_id, symbol)
                    continue
                log_ignored_disconnect(user_id, "/payouts", payout_payload)
                payout = (
                    extract_payout(payout_payload, symbol)
                    if payout_status < 400 and payout_payload.get("ok")
                    else None
                )
            if payout is None:
                set_named_cooldown(
                    payout_cooldowns,
                    user_id,
                    symbol,
                    seconds=PAYOUT_COOLDOWN_SECONDS,
                    log_label="PAYOUT_COOLDOWN",
                    status=STATUS_PAYOUT_COOLDOWN,
                    reason="PAYOUT_COOLDOWN",
                )
                continue

            candles = cached_candles
            if not candles:
                candle_params: dict[str, Any] = {
                    "active": symbol,
                    "interval": TIMEFRAME_SECONDS[state.timeframe],
                    "count": ROBOT_CANDLE_COUNT,
                }
                if endtime is not None:
                    candle_params["endtime"] = endtime
                try:
                    candle_status, candle_payload = await asyncio.wait_for(
                        call_bullex_service(
                            "GET",
                            "/candles",
                            user_id,
                            params=candle_params,
                        ),
                        timeout=ACTIVE_DATA_TIMEOUT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    logger.warning("[ACTIVE_SKIPPED] user_id=%s symbol=%s reason=FALLBACK_CANDLES_TIMEOUT", user_id, symbol)
                    continue
                log_ignored_disconnect(user_id, "/candles", candle_payload)
                candles = extract_candles(candle_payload) if candle_status < 400 else []
            if not candles:
                continue
        except Exception as exc:
            logger.debug(
                "[FALLBACK_CANDIDATE_SKIPPED] user_id=%s symbol=%s error=%s",
                user_id,
                symbol,
                exc,
            )
            continue

        direction = simple_candle_direction(candles)
        candidate = {
            "symbol": symbol,
            "direction": direction,
            "signal": direction,
            "strategy_score": 70,
            "score": 70,
            "quality_score": 70,
            "confidence": 70,
            "payout": payout,
            "reason": "Fallback operacional pelo movimento simples das últimas velas.",
            "entry_reason": "Fallback operacional pelo movimento simples das últimas velas.",
            "candle_reading": "Leitura simplificada por fallback operacional.",
            "block_reasons": [],
            "metrics": {
                "symbol": symbol,
                "timeframe": state.timeframe,
                "candles_count": len(candles),
                "fallback": True,
            },
            "approved_filters": ["FALLBACK_OPEN_ASSET"],
            "blocked_filters": [],
            "trade_allowed": False,
            "fallback_candidate_used": True,
            "strategy_mode": state.strategy_mode,
            "target_entry_second": state.buy_target_second,
            "entry_window_start_second": state.entry_window_start_second,
            "entry_window_end_second": state.entry_window_end_second,
        }
        strategy_name, strategy_reason, used_strategies = build_strategy_narration(candidate)
        candidate.update(
            {
                "strategy_name": strategy_name,
                "strategy_reason": strategy_reason,
                "used_strategies": used_strategies,
            }
        )
        logger.info(
            "[FALLBACK_CANDIDATE_SELECTED] user_id=%s symbol=%s direction=%s payout=%s",
            user_id,
            symbol,
            direction,
            payout,
        )
        return candidate
    return None


async def update_cycle_analysis(
    user_id: str,
    state: Any,
    entry_window: dict[str, Any],
    *,
    force: bool = False,
) -> Any:
    now = utc_now()
    if force:
        state.best_candidate = None
        state.cycle_best_candidate = None
        state.cycle_best_trade_candidate = None
        state.candidates = []
        state.candidates_count = 0
    if not force and state.last_analysis_at is not None:
        elapsed = (now - state.last_analysis_at).total_seconds()
        if elapsed < 3:
            return state

    logger.info(
        "[CYCLE_ANALYSIS] user_id=%s cycle_id=%s seconds_until_next_cycle=%s",
        user_id,
        state.cycle_id,
        state.to_dict()["seconds_until_next_cycle"],
    )
    logger.info("[WORKER_ANALYSIS_STARTED] user_id=%s cycle_id=%s", user_id, state.cycle_id)
    cycle_started_at = monotonic()
    scan_status, scan_payload = await scan_local_signals(
        user_id,
        limit=ROBOT_MAX_ASSETS_PER_CYCLE,
        include_wait=True,
        timeframe=state.timeframe,
        endtime=int(entry_window["server_timestamp"]),
        strategy_mode=state.strategy_mode,
        max_assets=len(resolve_analysis_assets(state.market_mode)),
        asset_sleep_seconds=ROBOT_ASSET_QUEUE_SLEEP_SECONDS,
        market_mode=state.market_mode,
    )
    if scan_payload.get("ok"):
        signals = [item for item in scan_payload.get("data", []) if isinstance(item, dict)]
    else:
        logger.warning(
            "[ANALYSIS_RECOVERED] user_id=%s error=%s action=FALLBACK_CANDIDATE",
            user_id,
            scan_payload.get("error") or "SIGNAL_SCAN_FAILED",
        )
        signals = []

    if not signals:
        fallback = await select_fallback_candidate(
            user_id,
            state,
            endtime=int(entry_window["server_timestamp"]),
        )
        signals = [fallback] if fallback is not None else []

    candidates: list[dict[str, Any]] = []
    for raw_signal in signals:
        symbol = normalize_binary_active(str(raw_signal.get("symbol") or ""))
        if not symbol:
            continue
        if active_cooldown_remaining(user_id, symbol) is not None:
            logger.warning("[ACTIVE_COOLDOWN] user_id=%s symbol=%s", user_id, symbol)
            continue
        payout = raw_signal.get("payout")
        if payout is None and payout_cooldown_remaining(user_id, symbol) is not None:
            payout = cached_payout_for_active(user_id, symbol)
            if payout is not None:
                logger.info("[ACTIVE_CACHE] user_id=%s symbol=%s path=/payouts reason=CANDIDATE_PAYOUT_COOLDOWN", user_id, symbol)
            elif not raw_signal.get("from_cache"):
                logger.warning("[PAYOUT_COOLDOWN] user_id=%s symbol=%s", user_id, symbol)
                continue
        if payout is None and is_binary_asset_allowed(symbol):
            payout_status, payout_payload = await call_bullex_service(
                "GET",
                "/payouts",
                user_id,
                params={"active": symbol},
            )
            log_ignored_disconnect(user_id, "/payouts", payout_payload)
            if payout_status < 400 and payout_payload.get("ok"):
                payout = extract_payout(payout_payload, symbol)
                # Canal turbo/binary (execução) pode estar fechado mesmo com
                # payout digital > 0. Marca is_open para o gate de candidato
                # não escolher um ativo que a corretora recusaria no buy.
                channel_open = extract_asset_open(payout_payload, symbol, state.timeframe)
                if channel_open is False:
                    raw_signal["is_open"] = False
                    logger.warning(
                        "[BINARY_CHANNEL_CLOSED] user_id=%s symbol=%s timeframe=%s",
                        user_id,
                        symbol,
                        state.timeframe,
                    )
                elif channel_open is True:
                    raw_signal["is_open"] = True
            else:
                payout = None
            if payout is None and payout_status >= 400:
                set_named_cooldown(
                    payout_cooldowns,
                    user_id,
                    symbol,
                    seconds=PAYOUT_COOLDOWN_SECONDS,
                    log_label="PAYOUT_COOLDOWN",
                    status=STATUS_PAYOUT_COOLDOWN,
                    reason="PAYOUT_COOLDOWN",
                )
        elif not isinstance(raw_signal.get("is_open"), bool):
            # Payout já vinha do scan/cache: ainda assim precisamos do canal
            # de execução. Sem isso, ativos fechados no turbo viravam
            # "Melhor ativo encontrado" e falhavam no buy.
            channel_open = cached_asset_open_for_active(user_id, symbol, state.timeframe)
            if channel_open is None and is_binary_asset_allowed(symbol):
                try:
                    payout_status, payout_payload = await call_bullex_service(
                        "GET",
                        "/payouts",
                        user_id,
                        params={"active": symbol},
                    )
                    log_ignored_disconnect(user_id, "/payouts", payout_payload)
                    if payout_status < 400 and payout_payload.get("ok"):
                        channel_open = extract_asset_open(
                            payout_payload,
                            symbol,
                            state.timeframe,
                        )
                except Exception:
                    # Falha operacional no refresh do canal não deve abortar
                    # o ciclo — deixa is_open desconhecido e segue o portão.
                    logger.warning(
                        "[BINARY_CHANNEL_REFRESH_FAILED] user_id=%s symbol=%s",
                        user_id,
                        symbol,
                        exc_info=True,
                    )
                    channel_open = None
            if channel_open is False:
                raw_signal["is_open"] = False
                logger.warning(
                    "[BINARY_CHANNEL_CLOSED] user_id=%s symbol=%s timeframe=%s",
                    user_id,
                    symbol,
                    state.timeframe,
                )
            elif channel_open is True:
                raw_signal["is_open"] = True

        allowed, ranked, _ = apply_strategy_guard(
            user_id,
            state,
            {**raw_signal, "symbol": symbol},
            payout=payout,
        )
        active_status = str(raw_signal.get("active_status") or raw_signal.get("status") or "").upper()
        real_block = None
        if not is_binary_asset_allowed(symbol):
            real_block = "ACTIVE_CLOSED"
        elif raw_signal.get("suspended") or "SUSPEND" in active_status:
            real_block = "ACTIVE_SUSPENDED"
        elif raw_signal.get("is_open") is False or active_status in {"CLOSED", "INACTIVE"}:
            real_block = "ACTIVE_CLOSED"

        blocked_filters = list(ranked.get("blocked_filters") or [])
        if real_block and real_block not in blocked_filters:
            blocked_filters.append(real_block)
        direction = ranked.get("direction") or ranked.get("signal") or "WAIT"
        candidate = {
            **{
                key: ranked[key]
                for key in ANALYSIS_DETAIL_FIELDS
                if key in ranked
            },
            "symbol": symbol,
            "direction": direction,
            "signal": direction,
            "strategy_score": int(ranked.get("strategy_score") or 0),
            "score": int(ranked.get("strategy_score") or 0),
            "quality_score": int(ranked.get("quality_score") or 0),
            "confidence": int(ranked.get("confidence") or 0),
            "payout": payout,
            "is_open": raw_signal.get("is_open"),
            "reason": ranked.get("reason") or ranked.get("signal_explanation"),
            "entry_reason": ranked.get("entry_reason") or ranked.get("reason") or ranked.get("signal_explanation"),
            "candle_reading": ranked.get("candle_reading"),
            "block_reasons": list(ranked.get("block_reasons") or ranked.get("blocked_filters") or []),
            "metrics": dict(ranked.get("metrics") or {}),
            "approved_filters": list(ranked.get("approved_filters") or []),
            "blocked_filters": blocked_filters,
            "trade_allowed": bool(allowed and real_block is None and direction in {"CALL", "PUT"}),
            "strategy_mode": ranked.get("strategy_mode") or state.strategy_mode,
            "target_entry_second": state.buy_target_second,
            "entry_window_start_second": state.entry_window_start_second,
            "entry_window_end_second": state.entry_window_end_second,
        }
        strategy_name, strategy_reason, used_strategies = build_strategy_narration(candidate)
        candidate.update(
            {
                "strategy_name": strategy_name,
                "strategy_reason": strategy_reason,
                "used_strategies": used_strategies,
            }
        )
        candidates.append(candidate)
        logger.info(
            "[ASSET_SCORE] user_id=%s asset=%s score=%s payout=%s confidence=%s",
            user_id,
            symbol,
            candidate.get("strategy_score") or candidate.get("score") or 0,
            candidate.get("payout"),
            candidate.get("confidence"),
        )

    candidates = await confirm_ranked_candidates_multi_timeframe(
        user_id,
        candidates,
        primary_timeframe=state.timeframe,
        endtime=int(entry_window["server_timestamp"]),
        strategy_mode=state.strategy_mode,
    )
    strict_candidates = [
        candidate
        for candidate in candidates
        if candidate_meets_cycle_threshold(
            candidate,
            state,
            minimum_confidence=int(state.min_confidence),
            user_id=user_id,
        )
    ]
    visible_candidates = [
        candidate
        for candidate in candidates
        if candidate_meets_cycle_threshold(
            candidate,
            state,
            minimum_confidence=70,
            user_id=user_id,
        )
    ] or candidates
    current_best_candidate = max(visible_candidates, key=candidate_rank) if visible_candidates else None
    current_best_trade_candidate = max(strict_candidates, key=candidate_rank) if strict_candidates else None
    previous_symbol = (state.cycle_best_candidate or {}).get("symbol") if state.cycle_best_candidate else None
    state = auto_trader.set_analysis_candidates(
        user_id,
        candidates,
        current_best_candidate,
    )
    state.cycle_best_candidate = choose_better_candidate(state.cycle_best_candidate, current_best_candidate)
    state.cycle_best_trade_candidate = choose_better_candidate(
        state.cycle_best_trade_candidate,
        current_best_trade_candidate,
    )
    state.best_candidate = dict(state.cycle_best_candidate) if state.cycle_best_candidate is not None else None
    if state.cycle_best_candidate is not None:
        state.strategy_score = int((state.cycle_best_candidate or {}).get("strategy_score") or 0)
        state.strategy_name = (state.cycle_best_candidate or {}).get("strategy_name")
        state.strategy_reason = (state.cycle_best_candidate or {}).get("strategy_reason")
        state.used_strategies = list((state.cycle_best_candidate or {}).get("used_strategies") or [])
        state.candle_reading = (state.cycle_best_candidate or {}).get("candle_reading")
        state.entry_reason = (state.cycle_best_candidate or {}).get("entry_reason")
        state.block_reasons = list(
            (state.cycle_best_candidate or {}).get("block_reasons")
            or (state.cycle_best_candidate or {}).get("blocked_filters")
            or []
        )
        state.metrics = dict((state.cycle_best_candidate or {}).get("metrics") or {})
    if state.cycle_best_candidate is not None and state.cycle_best_candidate.get("symbol") != previous_symbol:
        logger.info(
            "[BEST_CANDIDATE] user_id=%s cycle_id=%s symbol=%s direction=%s confidence=%s payout=%s fallback=%s",
            user_id,
            state.cycle_id,
            state.cycle_best_candidate.get("symbol"),
            state.cycle_best_candidate.get("direction"),
            state.cycle_best_candidate.get("confidence"),
            state.cycle_best_candidate.get("payout"),
            not bool(state.cycle_best_trade_candidate),
        )
        logger.info(
            "[BEST_CANDIDATE] user_id=%s asset=%s direction=%s confidence=%s payout=%s",
            user_id,
            state.cycle_best_candidate.get("symbol"),
            state.cycle_best_candidate.get("direction"),
            state.cycle_best_candidate.get("confidence"),
            state.cycle_best_candidate.get("payout"),
        )
    if state.cycle_best_candidate is None:
        logger.info("[NO_SIGNAL_FOUND] user_id=%s reason=NO_CANDIDATES", user_id)
    logger.info(
        "[WORKER_ANALYSIS_FINISHED] user_id=%s cycle_id=%s candidates=%s",
        user_id,
        state.cycle_id,
        len(candidates),
    )
    logger.info(
        "[CYCLE_DURATION_MS] user_id=%s cycle_id=%s phase=analysis ms=%s",
        user_id,
        state.cycle_id,
        int((monotonic() - cycle_started_at) * 1000),
    )
    return state


async def fetch_trade_result(user_id: str, order_id: str) -> tuple[int, dict[str, Any]]:
    status_code, payload = await call_bullex_service("GET", f"/orders/{order_id}/result", user_id)
    mark_disconnected_from_payload(user_id, payload)
    return status_code, payload


# Backoff da reconciliação de TIMEOUT: sem isso, uma ordem cujo resultado
# nunca chega (ex.: sessão da corretora offline) era consultada em TODO
# `/robot/state` (poll a cada 2,5-8s) — para sempre, disputando o
# `_call_gate` do bullex-service junto com candles/payouts de todo mundo.
# Ver PERFORMANCE_SISTEMA.md ("CALL_GATE_TIMEOUT sob carga").
TIMEOUT_RECONCILE_RETRY_SECONDS = 30.0
TIMEOUT_RECONCILE_MAX_ATTEMPTS = 120  # ~1h de tentativas a cada 30s antes de desistir
_timeout_reconcile_backoff: dict[tuple[str, str], dict[str, Any]] = {}


def _timeout_reconcile_should_skip(user_id: str, order_id: str) -> bool:
    entry = _timeout_reconcile_backoff.get((user_id, order_id))
    if entry is None:
        return False
    if entry.get("given_up"):
        return True
    return monotonic() < float(entry.get("next_retry_at", 0.0))


def _timeout_reconcile_record_failure(user_id: str, order_id: str) -> None:
    key = (user_id, order_id)
    entry = _timeout_reconcile_backoff.setdefault(key, {"attempts": 0})
    entry["attempts"] = int(entry.get("attempts", 0)) + 1
    if entry["attempts"] >= TIMEOUT_RECONCILE_MAX_ATTEMPTS:
        entry["given_up"] = True
        logger.warning(
            "[TIMEOUT_RECONCILE_GIVEN_UP] user_id=%s order_id=%s attempts=%s",
            user_id,
            order_id,
            entry["attempts"],
        )
        return
    entry["next_retry_at"] = monotonic() + TIMEOUT_RECONCILE_RETRY_SECONDS


def _timeout_reconcile_clear(user_id: str, order_id: str) -> None:
    _timeout_reconcile_backoff.pop((user_id, order_id), None)


def reset_cycle_after_finish(user_id: str) -> Any:
    logger.info("[CYCLE_RESET_STARTED] user_id=%s", user_id)
    state = auto_trader.reset_cycle_after_result(user_id)
    logger.info(
        "[CYCLE_RESET_DONE] user_id=%s cycle_id=%s next_cycle_at=%s",
        user_id,
        state.cycle_id,
        state.next_cycle_at,
    )
    logger.info("[NEXT_CYCLE_SCHEDULED] user_id=%s next_cycle_at=%s", user_id, state.next_cycle_at)
    logger.info(
        "[WAITING_NEXT_CYCLE] user_id=%s cycle_id=%s next_cycle_at=%s",
        user_id,
        state.cycle_id,
        state.next_cycle_at,
    )
    logger.info("[READY_NEXT_CYCLE] user_id=%s cycle_id=%s", user_id, state.cycle_id)
    persist_robot(user_id)
    return state


def reset_cycle_after_result(user_id: str) -> Any:
    return reset_cycle_after_finish(user_id)


def result_display_expired(state: Any) -> bool:
    if getattr(state, "result_display_until", None) is None:
        return False
    if str(getattr(state, "status", "") or "").upper() not in {"WIN", "LOSS", "RESULT_RECEIVED", "GALE_RESULT_RECEIVED"}:
        return False
    return utc_now() >= state.result_display_until


def waiting_result_stale(state: Any) -> bool:
    """Detecta 'Operação aberta' fantasma que trava o ciclo do robô.

    Antes, ``operation_in_progress=True`` fazia esta função retornar sempre
    ``False`` — a UI ficava em "Operação aberta" / "Aguardando resultado"
    infinitamente e o worker não voltava a analisar (mesmo sem ordem viva).
    """
    status = str(getattr(state, "status", "") or "").upper()
    sticky = status in {
        STATUS_WAITING_RESULT,
        STATUS_PENDING_RESULT,
        STATUS_PENDING_GALE_RESULT,
        STATUS_OPERATION_OPEN,
    }
    operation_open = bool(
        getattr(state, "operation_in_progress", False)
        or getattr(state, "result_waiting", False)
    )
    if not sticky and not operation_open:
        return False

    trade = getattr(state, "last_trade", None) or {}
    trade_result = str(trade.get("result") or "").strip().upper()
    # Resultado já conhecido: não faz sentido continuar em "operação aberta".
    if trade_result in {"WIN", "LOSS", "TIMEOUT", "DRAW"}:
        return True

    base = (
        getattr(state, "last_entry_at", None)
        or getattr(state, "current_cycle_started_at", None)
    )
    cycle_minutes = int(getattr(state, "cycle_minutes", None) or 1)
    # Vela + margem para o monitor de resultado; M1 ≈ 2,5 min, M5 ≈ 6,5 min.
    max_wait_seconds = max(150, cycle_minutes * 60 + 90)
    if base is None:
        order_id = str(trade.get("order_id") or "").strip()
        return sticky and operation_open and not order_id
    return (utc_now() - base).total_seconds() > max_wait_seconds


async def reconcile_timeout_last_trade(user_id: str) -> bool:
    """
    Se a última ordem ficou TIMEOUT no painel mas a Bullex já tem WIN/LOSS,
    corrige placar/histórico. Retorna True se recuperou.
    """
    state = auto_trader.get(user_id)
    trade = state.last_trade or {}
    if str(trade.get("result") or "").strip().upper() != "TIMEOUT":
        return False
    order_id = str(trade.get("order_id") or "").strip()
    if not order_id:
        return False
    if _timeout_reconcile_should_skip(user_id, order_id):
        return False
    try:
        _, payload = await fetch_trade_result(user_id, order_id)
        normalized = normalize_trade_result(payload)
    except Exception as exc:
        logger.warning(
            "[TIMEOUT_RECONCILE_FAILED] user_id=%s order_id=%s error=%s",
            user_id,
            order_id,
            exc.__class__.__name__,
        )
        _timeout_reconcile_record_failure(user_id, order_id)
        return False
    if normalized is None or normalized[0] not in {"WIN", "LOSS"}:
        _timeout_reconcile_record_failure(user_id, order_id)
        return False
    _timeout_reconcile_clear(user_id, order_id)
    result, profit = normalized
    await finish_monitored_trade(user_id, order_id, result, profit)
    logger.info(
        "[TIMEOUT_RECONCILED] user_id=%s order_id=%s result=%s profit=%s",
        user_id,
        order_id,
        result,
        profit,
    )
    return True


async def finish_monitored_trade(user_id: str, order_id: str, result: str, profit: float) -> None:
    should_pause_worker = False
    result, profit = await apply_marketing_result_override(user_id, result, profit)
    async with auto_trader.result_lock(user_id):
        async with auto_trader.cycle_lock(user_id):
            finalized, state = auto_trader.finish_trade(user_id, order_id, result, profit)
            if finalized:
                # Com a aba fechada o flash de 5s some; guarda para o placar ao voltar.
                # A narração do placar não depende disso: usa `state.result_voice`.
                if not is_panel_online(user_id):
                    state.unseen_result = True
                    state.result_client_seen_at = None
                    logger.info(
                        "[UNSEEN_RESULT_MARKED] user_id=%s order_id=%s result=%s",
                        user_id,
                        order_id,
                        result,
                    )
                else:
                    state.unseen_result = False
                    state.result_client_seen_at = None
        if not finalized and state.gale_pending:
            logger.info(
                "[TRADE_RESULT] user_id=%s order_id=%s result=LOSS profit=%s",
                user_id,
                order_id,
                (state.last_trade or {}).get("profit"),
            )
            logger.info(
                "[GALE_TRIGGERED] user_id=%s order_id=%s gale_amount=%s multiplier=%s",
                user_id,
                order_id,
                state.gale_amount,
                state.martingale_multiplier,
            )
            logger.info(
                "[TRADE_LOSS_GALE_TRIGGERED] user_id=%s order_id=%s gale_amount=%s multiplier=%s",
                user_id,
                order_id,
                state.gale_amount,
                state.martingale_multiplier,
            )
            logger.info(
                "[WAITING_GALE_ENTRY] user_id=%s order_id=%s active=%s direction=%s",
                user_id,
                order_id,
                (state.pending_signal or {}).get("symbol"),
                state.gale_direction,
            )
            if state.last_trade:
                try:
                    robot_persistence.save_trade_history(user_id, state.last_trade)
                    invalidate_daily_history_cache(user_id)
                    # Espelha também em robot_trades para restore do placar/memória.
                    robot_persistence.save_trade(user_id, state.last_trade)
                    try:
                        trade_for_memory = dict(state.last_trade)
                        if not trade_for_memory.get("timeframe"):
                            trade_for_memory["timeframe"] = getattr(state, "timeframe", "M1")
                        pattern_memory.record_outcome(user_id, trade_for_memory)
                    except Exception:
                        logger.warning(
                            "[PATTERN_MEMORY_RECORD_FAILED] user_id=%s order_id=%s",
                            user_id,
                            order_id,
                            exc_info=True,
                        )
                    logger.info(
                        "[HISTORY_SAVED] user_id=%s order_id=%s result=%s final_result=%s",
                        user_id,
                        order_id,
                        state.last_trade.get("result"),
                        state.last_trade.get("final_result"),
                    )
                except Exception:
                    logger.exception(
                        "[ROBOT HISTORY ERROR] user_id=%s order_id=%s",
                        user_id,
                        order_id,
                    )
        if finalized and state.last_trade:
            cycle_profit = float(state.last_trade.get("profit") or 0)
            if state.last_trade.get("is_gale"):
                cycle_profit = round(
                    float((state.gale_parent_trade or {}).get("profit") or 0) + cycle_profit,
                    2,
                )
            logger.info(
                "[RESULT_RECEIVED] user_id=%s order_id=%s result=%s profit=%s",
                user_id,
                order_id,
                result,
                state.last_trade.get("profit"),
            )
            logger.info(
                "[CYCLE_RESULT_%s] user_id=%s order_id=%s profit=%s",
                str(state.cycle_result or result).upper(),
                user_id,
                order_id,
                state.profit,
            )
            logger.info(
                "[STATE] RESULT_%s user_id=%s order_id=%s",
                str(result).upper(),
                user_id,
                order_id,
            )
            logger.info(
                "[STATE] SHOW_RESULT user_id=%s order_id=%s result=%s result_display_until=%s",
                user_id,
                order_id,
                result,
                state.result_display_until,
            )
            logger.info(
                "[TRADE_RESULT] user_id=%s order_id=%s result=%s profit=%s",
                user_id,
                order_id,
                result,
                state.last_trade.get("profit"),
            )
            logger.info(
                "[ORDER_RESULT] user_id=%s order_id=%s result=%s profit=%s",
                user_id,
                order_id,
                result,
                state.last_trade.get("profit"),
            )
            if state.last_trade.get("is_gale"):
                logger.info(
                    "[GALE_RESULT] user_id=%s order_id=%s parent_order_id=%s result=%s profit=%s",
                    user_id,
                    order_id,
                    state.last_trade.get("parent_order_id"),
                    result,
                    state.last_trade.get("profit"),
                )
            if state.cycle_result == "LOSS":
                logger.info(
                    "[LOSS_COUNTED] user_id=%s order_id=%s losses=%s profit=%s",
                    user_id,
                    order_id,
                    state.losses,
                    state.profit,
                )
            try:
                robot_persistence.save_trade_history(user_id, state.last_trade)
                invalidate_daily_history_cache(user_id)
                robot_persistence.save_trade(user_id, state.last_trade)
                try:
                    trade_for_memory = dict(state.last_trade)
                    if not trade_for_memory.get("timeframe"):
                        trade_for_memory["timeframe"] = getattr(state, "timeframe", "M1")
                    pattern_memory.record_outcome(user_id, trade_for_memory)
                except Exception:
                    logger.warning(
                        "[PATTERN_MEMORY_RECORD_FAILED] user_id=%s order_id=%s",
                        user_id,
                        order_id,
                        exc_info=True,
                    )
                logger.info(
                    "[HISTORY_SAVED] user_id=%s order_id=%s result=%s final_result=%s",
                    user_id,
                    order_id,
                    state.last_trade.get("result"),
                    state.last_trade.get("final_result"),
                )
            except Exception:
                logger.exception(
                    "[ROBOT HISTORY ERROR] user_id=%s order_id=%s",
                    user_id,
                    order_id,
                )
            logger.info(
                "[CYCLE_FINAL_RESULT] user_id=%s order_id=%s cycle_result=%s final_result=%s cycle_profit=%s",
                user_id,
                order_id,
                state.cycle_result,
                state.last_trade.get("final_result"),
                cycle_profit,
            )
            logger.info(
                "[SCORE_UPDATED] user_id=%s wins=%s losses=%s profit=%s cycle_result=%s",
                user_id,
                state.wins,
                state.losses,
                state.profit,
                state.cycle_result,
            )
            logger.info(
                "[RESULT_DISPLAY_UNTIL] user_id=%s result_display_until=%s",
                user_id,
                state.result_display_until,
            )
            if state.last_trade.get("is_gale"):
                logger.info(
                    "[%s] user_id=%s order_id=%s parent_order_id=%s",
                    state.cycle_result,
                    user_id,
                    order_id,
                    state.last_trade.get("parent_order_id"),
                )
            elif result == "LOSS" and not state.martingale_enabled:
                logger.info("[GALE_DISABLED_LOSS_FINAL] user_id=%s order_id=%s", user_id, order_id)
            if state.status in {STATUS_STOP_WIN_HIT, STATUS_STOP_LOSS_HIT}:
                should_pause_worker = True
                if state.status == STATUS_STOP_WIN_HIT:
                    logger.warning("[STOP_WIN_HIT] user_id=%s profit=%s", user_id, state.profit)
                else:
                    logger.warning("[STOP_LOSS_HIT] user_id=%s profit=%s", user_id, state.profit)
                logger.warning("[ROBOT_PAUSED_BY_STOP] user_id=%s reason=%s", user_id, state.status)
        persist_robot(user_id)
    if should_pause_worker:
        await stop_robot_worker(user_id)


async def timeout_monitored_trade(user_id: str, order_id: str) -> None:
    # Última leitura na Bullex antes de gravar TIMEOUT (sem placar).
    try:
        _, payload = await fetch_trade_result(user_id, order_id)
        normalized = normalize_trade_result(payload)
        if normalized is not None and normalized[0] in {"WIN", "LOSS"}:
            result, profit = normalized
            await finish_monitored_trade(user_id, order_id, result, profit)
            logger.info(
                "[TIMEOUT_AVOIDED_WITH_RESULT] user_id=%s order_id=%s result=%s profit=%s",
                user_id,
                order_id,
                result,
                profit,
            )
            return
    except Exception as exc:
        logger.warning(
            "[TIMEOUT_LAST_FETCH_FAILED] user_id=%s order_id=%s error=%s",
            user_id,
            order_id,
            exc.__class__.__name__,
        )

    async with auto_trader.lock(user_id):
        timed_out, state = auto_trader.timeout_trade(user_id, order_id)
        if timed_out and state.last_trade:
            try:
                robot_persistence.save_trade_history(user_id, state.last_trade)
                invalidate_daily_history_cache(user_id)
                logger.info(
                    "[HISTORY_SAVED] user_id=%s order_id=%s result=TIMEOUT final_result=TIMEOUT",
                    user_id,
                    order_id,
                )
            except Exception:
                logger.exception("[ROBOT HISTORY ERROR] user_id=%s order_id=%s", user_id, order_id)
        persist_robot(user_id)


trade_result_monitor = TradeResultMonitor(
    fetch_result=fetch_trade_result,
    finish_trade=finish_monitored_trade,
    timeout_trade=timeout_monitored_trade,
)


async def execute_robot_cycle(
    user_id: str,
    *,
    required_mode: str | None = None,
) -> tuple[int, dict[str, Any]]:
    async with auto_trader.cycle_lock(user_id):
        state = recover_sync_timeout_if_needed(user_id)
        if result_display_expired(state) or waiting_result_stale(state):
            state = reset_cycle_after_result(user_id)
        initial_stop_reason = daily_stop_reason(user_id, state) or robot_stop_reason(state)
        if initial_stop_reason in {STATUS_STOP_WIN_HIT, STATUS_STOP_LOSS_HIT}:
            state = await pause_robot_by_stop(user_id, initial_stop_reason)
            return 200, build_robot_payload(state, user_id=user_id)
        had_pending_signal = state.pending_signal is not None
        running_analysis = state.analysis_result == "RUNNING" or state.last_analysis_result == "RUNNING"
        if not had_pending_signal and not running_analysis:
            can_run, state = auto_trader.prepare_cycle(user_id)
            if not can_run:
                if state.status == STATUS_WAITING_NEXT_CYCLE and state.enabled:
                    logger.info(
                        "[WAITING_NEXT_CYCLE] user_id=%s cycle_id=%s next_cycle_at=%s seconds=%s",
                        user_id,
                        state.cycle_id,
                        state.next_cycle_at,
                        state.to_dict()["seconds_until_next_cycle"],
                    )
                    logger.info(
                        "[ANALYSIS_SKIPPED_NEXT_CYCLE] user_id=%s cycle_id=%s",
                        user_id,
                        state.cycle_id,
                    )
                    return 200, build_robot_payload(state, user_id=user_id)
                if state.status != STATUS_WAITING_NEXT_CYCLE or not state.enabled:
                    return 200, build_robot_payload(state)
            elif state.to_dict()["seconds_until_next_cycle"] <= 0:
                logger.info(
                    "[CYCLE_START] user_id=%s cycle_id=%s current_cycle_started_at=%s",
                    user_id,
                    state.cycle_id,
                    state.current_cycle_started_at,
                )

        logger.info("[ROBOT TICK] user_id=%s", user_id)
        try:
            result_waiting = bool(
                state.operation_in_progress
                and str((state.last_trade or {}).get("result") or "").upper() not in {"WIN", "LOSS", "TIMEOUT"}
            )
            if state.operation_in_progress or result_waiting:
                state.status = STATUS_PENDING_GALE_RESULT if state.gale_active else STATUS_WAITING_RESULT
                logger.info("[STATE] WAITING_RESULT user_id=%s order_id=%s", user_id, (state.last_trade or {}).get("order_id"))
                logger.info("[RESULT_WAIT_ONLY] user_id=%s status=%s", user_id, state.status)
                return 200, build_robot_payload(state)

            active_stop_reason = daily_stop_reason(user_id, state) or robot_stop_reason(state)
            if active_stop_reason is not None:
                if active_stop_reason in {STATUS_STOP_WIN_HIT, STATUS_STOP_LOSS_HIT}:
                    state = await pause_robot_by_stop(user_id, active_stop_reason)
                else:
                    if active_stop_reason.startswith("DAILY_STOP"):
                        state.enabled = False
                        logger.warning("[DAILY_STOP_HIT] user_id=%s reason=%s", user_id, active_stop_reason)
                    state = auto_trader.reject(user_id, active_stop_reason)
                    logger.info("[ROBOT SIGNAL REJECTED] user_id=%s reason=%s", user_id, active_stop_reason)
                return 200, build_robot_payload(state, user_id=user_id)

            if required_mode is not None and state.account_mode != required_mode:
                return 409, build_error(f"ACCOUNT_MODE_NOT_{required_mode}")

            if state.account_mode == "REAL":
                account_snapshot = get_cached_account_snapshot(user_id)
                snapshot_mode = account_snapshot.get("mode") or state.active_mode
                raw_balance = account_snapshot.get("balance")
                real_balance = float(raw_balance) if snapshot_mode == "REAL" and raw_balance is not None else None
                if real_balance is not None and real_balance <= 0:
                    state = await stop_real_robot_for_insufficient_balance(
                        user_id,
                        balance=real_balance,
                    )
                    return 200, build_robot_payload(
                        state,
                        user_id=user_id,
                        balance=real_balance,
                        balance_real=real_balance,
                    )
                if real_balance is not None and float(state.entry_value) > real_balance:
                    state = await stop_real_robot_for_insufficient_balance(
                        user_id,
                        balance=real_balance,
                        entry_value=float(state.entry_value),
                    )
                    return 200, build_robot_payload(
                        state,
                        user_id=user_id,
                        balance=real_balance,
                        balance_real=real_balance,
                        operation_message=ENTRY_VALUE_EXCEEDS_BALANCE_MESSAGE,
                        status_message=ENTRY_VALUE_EXCEEDS_BALANCE_MESSAGE,
                    )

            status_code, account_payload, entry_window = await refresh_entry_window(user_id, state)
            state, connected, active_mode, connection_source = await reconcile_robot_connection_from_payload(
                user_id,
                account_payload,
            )
            if robot_connection_unavailable(connected, active_mode):
                # Nunca desligar o robô aqui (disconnect_account → enabled=False).
                # Blips de sessão após 1–2 trades matavam a operação sem stop win.
                # Mantém enabled, agenda recuperação e tenta auto-reconnect.
                was_enabled = bool(state.enabled)
                state = auto_trader.defer_cycle(
                    user_id,
                    STATUS_WAITING_RECOVERY,
                    wait_seconds=SESSION_OFFLINE_TTL_SECONDS,
                    rejection_reason="WAITING_RECOVERY",
                    last_rejection_reason="WAITING_RECOVERY",
                    last_order_error="WAITING_RECOVERY",
                )
                if was_enabled:
                    state.enabled = True
                logger.warning(
                    "[ROBOT_CONNECTION_BLIP_RECOVER] user_id=%s failures=%s enabled=%s action=defer_and_reconnect",
                    user_id,
                    getattr(state, "connection_failure_count", 0),
                    state.enabled,
                )
                persist_robot(user_id)
                if was_enabled:
                    asyncio.create_task(_auto_reconnect_then_ensure_worker(user_id))
                return 200, build_robot_payload(
                    state,
                    user_id=user_id,
                    connected=bool(state.connected),
                    active_mode=state.active_mode,
                    connection_checked_at=state.connection_checked_at.isoformat()
                    if state.connection_checked_at is not None
                    else None,
                    connection_status_source=connection_source or "waiting_recovery",
                )
            if state.account_mode == "REAL":
                account_snapshot = get_cached_account_snapshot(user_id)
                snapshot_mode = account_snapshot.get("mode") or active_mode
                raw_balance = account_snapshot.get("balance")
                real_balance = float(raw_balance) if snapshot_mode == "REAL" and raw_balance is not None else None
                if active_mode == "REAL" and real_balance is not None and real_balance <= 0:
                    state = await stop_real_robot_for_insufficient_balance(
                        user_id,
                        balance=real_balance,
                    )
                    return 200, build_robot_payload(
                        state,
                        user_id=user_id,
                        balance=real_balance,
                        balance_real=real_balance,
                    )
                if active_mode == "REAL" and real_balance is not None and float(state.entry_value) > real_balance:
                    state = await stop_real_robot_for_insufficient_balance(
                        user_id,
                        balance=real_balance,
                        entry_value=float(state.entry_value),
                    )
                    return 200, build_robot_payload(
                        state,
                        user_id=user_id,
                        balance=real_balance,
                        balance_real=real_balance,
                        operation_message=ENTRY_VALUE_EXCEEDS_BALANCE_MESSAGE,
                        status_message=ENTRY_VALUE_EXCEEDS_BALANCE_MESSAGE,
                    )
                logger.info(
                    "[REAL MODE DETECTED] user_id=%s active_mode=%s connected=%s confirm_real=%s",
                    user_id,
                    active_mode,
                    connected,
                    state.confirm_real,
                )
                logger.info(
                    "[REAL BUY ATTEMPT] user_id=%s entry_value=%s",
                    user_id,
                    state.entry_value,
                )
                block_reason = real_block_reason(
                    state,
                    connected=connected,
                    active_mode=active_mode,
                    user_id=user_id,
                )
                if block_reason is not None:
                    auto_trader.lock_real(user_id, block_reason)
                    persist_robot(user_id)
                    logger.warning(
                        "[REAL BUY BLOCKED reason=%s] user_id=%s",
                        block_reason,
                        user_id,
                    )
                    return 403, build_error(block_reason)

            expected_bullex_mode = "REAL"
            if active_mode != expected_bullex_mode:
                state = auto_trader.reject(user_id, f"ACCOUNT_MODE_MUST_BE_{expected_bullex_mode}")
                logger.info(
                    "[ROBOT SIGNAL REJECTED] user_id=%s reason=ACCOUNT_MODE_MUST_BE_%s",
                    user_id,
                    expected_bullex_mode,
                )
                return 200, build_robot_payload(state)
            if entry_window is None:
                state = recover_analysis_error_to_window(user_id, "SERVER_TIME_UNAVAILABLE")
                return status_code, build_robot_payload(state)
            recovered_reason, state = recover_running_analysis_if_needed(user_id, entry_window)
            if recovered_reason is not None:
                return 200, build_robot_payload(state)
            selected = dict(state.pending_signal) if state.pending_signal else None
            if selected is not None and not state.operation_in_progress:
                state.status = STATUS_WAITING_GALE_ENTRY if state.gale_pending else STATUS_WAITING_ENTRY
                state.rejection_reason = None
            seconds_until_next_cycle = state.to_dict()["seconds_until_next_cycle"]
            if (
                selected is None
                and state.enabled
                and state.status == STATUS_WAITING_NEXT_CYCLE
                and not state.operation_in_progress
            ):
                if seconds_until_next_cycle > 0:
                    logger.info(
                        "[ENTRY_WINDOW] user_id=%s cycle_id=%s next_cycle_at=%s phase=waiting_next_cycle",
                        user_id,
                        state.cycle_id,
                        state.next_cycle_at,
                    )
                    return 200, build_robot_payload(state)

                logger.info(
                    "[CYCLE_END] user_id=%s cycle_id=%s phase=selection",
                    user_id,
                    state.cycle_id,
                )
                state = await update_cycle_analysis(user_id, state, entry_window, force=True)
                entry_window = refresh_cycle_entry_window(
                    user_id,
                    state,
                    entry_window,
                )
                selected = resolve_cycle_entry_candidate(state, user_id=user_id)
                if selected is None:
                    logger.info(
                        "[NO_TRADE] user_id=%s cycle_id=%s best_candidate=%s confidence=%s payout=%s",
                        user_id,
                        state.cycle_id,
                        (state.cycle_best_candidate or {}).get("symbol"),
                        (state.cycle_best_candidate or {}).get("confidence"),
                        (state.cycle_best_candidate or {}).get("payout"),
                    )
                    logger.info("[NO_SIGNAL_FOUND] user_id=%s reason=NO_TRADE", user_id)
                    blocked = {
                        str(item)
                        for item in (
                            (state.cycle_best_candidate or {}).get("blocked_filters")
                            or (state.cycle_best_candidate or {}).get("block_reasons")
                            or []
                        )
                    }
                    rejection_reason = classify_no_opportunity_reason(blocked)
                    state = auto_trader.schedule_next_analysis_session(
                        user_id,
                        analysis_result="NO_OPPORTUNITY_FOUND",
                        last_rejection_reason=rejection_reason,
                    )
                    state.blocked_filters = sorted(blocked)
                    state.block_reasons = sorted(blocked)
                    state.analysis_message = None
                    logger.info(
                        "[NEXT_ANALYSIS_RETRY_SCHEDULED] user_id=%s cycle_id=%s reason=NO_OPPORTUNITY next_cycle_at=%s",
                        user_id,
                        state.cycle_id,
                        state.next_cycle_at,
                    )
                    return 200, build_robot_payload(state)
                logger.info(
                    "[BEST_CANDIDATE] user_id=%s cycle_id=%s symbol=%s direction=%s confidence=%s payout=%s fallback=%s",
                    user_id,
                    state.cycle_id,
                    selected.get("symbol"),
                    selected.get("direction") or selected.get("signal"),
                    selected.get("confidence"),
                    selected.get("payout"),
                    bool(selected.get("fallback_candidate_used")),
                )
                logger.info(
                    "[BEST_CANDIDATE_FOUND] user_id=%s cycle_id=%s symbol=%s direction=%s confidence=%s payout=%s",
                    user_id,
                    state.cycle_id,
                    selected.get("symbol"),
                    selected.get("direction") or selected.get("signal"),
                    selected.get("confidence"),
                    selected.get("payout"),
                )
                state = auto_trader.set_pending_signal(user_id, selected)
                if selected.get("fallback_candidate_used"):
                    state.fallback_candidate_used = True
                selected = dict(state.pending_signal or {})
                logger.info(
                    "[SIGNAL_FOUND] user_id=%s cycle_id=%s symbol=%s direction=%s confidence=%s payout=%s",
                    user_id,
                    state.cycle_id,
                    selected.get("symbol"),
                    selected.get("signal") or selected.get("direction"),
                    selected.get("confidence"),
                    selected.get("payout"),
                )
                logger.info("[STATE] SIGNAL_FOUND user_id=%s cycle_id=%s", user_id, state.cycle_id)
                logger.info(
                    "[SIGNAL_PREPARED] user_id=%s cycle_id=%s symbol=%s direction=%s seconds_until_entry=%s",
                    user_id,
                    state.cycle_id,
                    selected.get("symbol"),
                    selected.get("signal") or selected.get("direction"),
                    state.seconds_until_entry_window,
                )
            if selected is None and False:
                if not entry_window["analysis_window_open"]:
                    state = auto_trader.wait_analysis_window(user_id, entry_window)
                    logger.info(
                        "[WAITING_ANALYSIS_WINDOW] user_id=%s timeframe=%s "
                        "current_candle_seconds=%s analysis_window_start=%s "
                        "analysis_window_end=%s seconds_until_analysis_window=%s",
                        user_id,
                        state.timeframe,
                        entry_window["current_candle_seconds"],
                        entry_window["analysis_window_start_second"],
                        entry_window["analysis_window_end_second"],
                        entry_window["seconds_until_analysis_window"],
                    )
                    logger.info(
                        "[NEXT_CYCLE_SCHEDULED] user_id=%s next_cycle_at=%s",
                        user_id,
                        state.next_cycle_at,
                    )
                    return 200, build_robot_payload(state)
                logger.info(
                    "[ANALYSIS_WINDOW_OPEN] user_id=%s timeframe=%s "
                    "current_candle_seconds=%s analysis_window_start=%s analysis_window_end=%s",
                    user_id,
                    state.timeframe,
                    entry_window["current_candle_seconds"],
                    entry_window["analysis_window_start_second"],
                    entry_window["analysis_window_end_second"],
                )
                state = auto_trader.start_analysis(user_id)
                if state.status != STATUS_ANALYZING:
                    state = auto_trader.wait_analysis_window(user_id, entry_window)
                    return 200, build_robot_payload(state)
                logger.info(
                    "[ANALYSIS_STARTED] user_id=%s cycle_id=%s",
                    user_id,
                    state.cycle_id,
                )
                logger.info("[STATE] ANALYZING user_id=%s cycle_id=%s", user_id, state.cycle_id)
                logger.info(
                    "[WORKER_ANALYSIS_STARTED] user_id=%s cycle_id=%s",
                    user_id,
                    state.cycle_id,
                )
                scan_status, scan_payload = await scan_local_signals(
                    user_id,
                    limit=len(resolve_analysis_assets(state.market_mode)),
                    include_wait=True,
                    timeframe=state.timeframe,
                    endtime=int(entry_window["server_timestamp"]),
                    strategy_mode=state.strategy_mode,
                    market_mode=state.market_mode,
                )
                scan_error = None
                if not scan_payload.get("ok"):
                    scan_error = str(scan_payload.get("error") or "SIGNAL_SCAN_FAILED")
                    logger.warning(
                        "[ANALYSIS_RECOVERED] user_id=%s error=%s action=FALLBACK_CANDIDATE",
                        user_id,
                        scan_error,
                    )
                    signals = []
                else:
                    signals = [item for item in scan_payload.get("data", []) if isinstance(item, dict)]
                if not signals:
                    fallback = await select_fallback_candidate(
                        user_id,
                        state,
                        endtime=int(entry_window["server_timestamp"]),
                    )
                    signals = [fallback] if fallback is not None else []
                logger.info(
                    "[ANALYSIS_FINISHED] user_id=%s cycle_id=%s candidates=%s",
                    user_id,
                    state.cycle_id,
                    len(signals),
                )
                if not signals:
                    state = auto_trader.schedule_next_analysis_session(
                        user_id,
                        analysis_result="NO_OPPORTUNITY_FOUND",
                        last_rejection_reason="CANDLES_UNAVAILABLE",
                    )
                    if scan_error is not None:
                        state.last_order_error = readable_order_error(scan_error)
                    auto_trader.set_analysis_candidates(user_id, [], None)
                    logger.info(
                        "[NO_CANDIDATE_THIS_CANDLE] user_id=%s reason=%s",
                        user_id,
                        state.last_rejection_reason,
                    )
                    logger.info(
                        "[NO_SIGNAL_FOUND] user_id=%s cycle_id=%s reason=%s",
                        user_id,
                        state.cycle_id,
                        state.last_rejection_reason,
                    )
                    return 200, build_robot_payload(state)

                candidates: list[dict[str, Any]] = []
                for raw_signal in signals:
                    symbol = normalize_binary_active(str(raw_signal.get("symbol") or ""))
                    payout = raw_signal.get("payout")
                    if payout is None and is_binary_asset_allowed(symbol):
                        payout_status, payout_payload = await call_bullex_service(
                            "GET",
                            "/payouts",
                            user_id,
                            params={"active": symbol},
                        )
                        log_ignored_disconnect(user_id, "/payouts", payout_payload)
                        payout = (
                            extract_payout(payout_payload, symbol)
                            if payout_status < 400 and payout_payload.get("ok")
                            else None
                        )

                    allowed, ranked, _ = apply_strategy_guard(
                        user_id,
                        state,
                        {**raw_signal, "symbol": symbol},
                        payout=payout,
                    )
                    if not is_binary_asset_allowed(symbol):
                        allowed = False
                        ranked["trade_allowed"] = False
                        ranked["blocked_filters"] = list(
                            dict.fromkeys(
                                [*ranked.get("blocked_filters", []), "ACTIVE_CLOSED"]
                            )
                        )
                        ranked["quality_reason"] = "ACTIVE_CLOSED"
                    active_status = str(
                        raw_signal.get("active_status")
                        or raw_signal.get("status")
                        or ""
                    ).upper()
                    if raw_signal.get("suspended") or "SUSPEND" in active_status:
                        allowed = False
                        ranked["trade_allowed"] = False
                        ranked["blocked_filters"] = list(
                            dict.fromkeys(
                                [*ranked.get("blocked_filters", []), "ACTIVE_CLOSED"]
                            )
                        )
                        ranked["quality_reason"] = "ACTIVE_CLOSED"
                    elif raw_signal.get("is_open") is False or active_status in {
                        "CLOSED",
                        "INACTIVE",
                    }:
                        allowed = False
                        ranked["trade_allowed"] = False
                        ranked["blocked_filters"] = list(
                            dict.fromkeys(
                                [*ranked.get("blocked_filters", []), "ACTIVE_CLOSED"]
                            )
                        )
                        ranked["quality_reason"] = "ACTIVE_CLOSED"
                    candidate = {
                        **{
                            key: ranked[key]
                            for key in ANALYSIS_DETAIL_FIELDS
                            if key in ranked
                        },
                        "symbol": symbol,
                        "direction": ranked.get("direction") or ranked.get("signal") or "WAIT",
                        "signal": ranked.get("direction") or ranked.get("signal") or "WAIT",
                        "strategy_score": int(ranked.get("strategy_score") or 0),
                        "score": int(ranked.get("strategy_score") or 0),
                        "quality_score": int(ranked.get("quality_score") or 0),
                        "confidence": int(ranked.get("confidence") or 0),
                        "payout": payout,
                        "reason": ranked.get("reason") or ranked.get("signal_explanation"),
                        "entry_reason": ranked.get("entry_reason")
                        or ranked.get("reason")
                        or ranked.get("signal_explanation"),
                        "candle_reading": ranked.get("candle_reading"),
                        "block_reasons": list(ranked.get("block_reasons") or ranked.get("blocked_filters") or []),
                        "metrics": dict(ranked.get("metrics") or {}),
                        "approved_filters": list(ranked.get("approved_filters") or []),
                        "blocked_filters": list(ranked.get("blocked_filters") or []),
                        "trade_allowed": bool(allowed and ranked.get("trade_allowed")),
                        "strategy_mode": ranked.get("strategy_mode") or state.strategy_mode,
                        "target_entry_second": state.buy_target_second,
                        "entry_window_start_second": state.entry_window_start_second,
                        "entry_window_end_second": state.entry_window_end_second,
                    }
                    strategy_name, strategy_reason, used_strategies = (
                        build_strategy_narration(candidate)
                    )
                    candidate.update(
                        {
                            "strategy_name": strategy_name,
                            "strategy_reason": strategy_reason,
                            "used_strategies": used_strategies,
                        }
                    )
                    candidates.append(candidate)

                candidates = await confirm_ranked_candidates_multi_timeframe(
                    user_id,
                    candidates,
                    primary_timeframe=state.timeframe,
                    endtime=int(entry_window["server_timestamp"]),
                    strategy_mode=state.strategy_mode,
                )
                approved_candidates = [
                    candidate
                    for candidate in candidates
                    if candidate.get("trade_allowed")
                    and candidate.get("payout") is not None
                    and candidate.get("direction") in {"CALL", "PUT"}
                ]
                selected = (
                    max(approved_candidates, key=candidate_rank)
                    if approved_candidates
                    else None
                )
                auto_trader.set_analysis_candidates(user_id, candidates, selected)
                logger.info(
                    "[ANALYSIS_CANDIDATES] user_id=%s cycle_id=%s count=%s candidates=%s",
                    user_id,
                    state.cycle_id,
                    len(candidates),
                    candidates,
                )

                if selected is None:
                    fallback = await select_fallback_candidate(
                        user_id,
                        state,
                        endtime=int(entry_window["server_timestamp"]),
                    )
                    if fallback is not None:
                        candidates.append(fallback)
                        selected = fallback
                        auto_trader.set_analysis_candidates(user_id, candidates, selected)

                if selected is None:
                    highest_rejected = max(
                        candidates,
                        key=lambda item: (
                            int(item["strategy_score"]),
                            int(item["confidence"]),
                        ),
                        default=None,
                    )
                    blocked_reasons = list((highest_rejected or {}).get("blocked_filters", []))
                    last_rejection_reason = classify_no_opportunity_reason(blocked_reasons)
                    state = auto_trader.schedule_next_analysis_session(
                        user_id,
                        analysis_result="NO_OPPORTUNITY_FOUND",
                        last_rejection_reason=last_rejection_reason
                        if last_rejection_reason
                        in {
                            "CANDLES_UNAVAILABLE",
                            STATUS_ACCOUNT_DISCONNECTED,
                            "STOP_WIN_HIT",
                            "STOP_LOSS_HIT",
                            "OPERATION_IN_PROGRESS",
                            "ACTIVE_CLOSED",
                            "NO_PATTERN_FOUND",
                            "MTF_DATA_UNAVAILABLE",
                            "MTF_HIGHER_TF_CONFLICT",
                        }
                        else "NO_PATTERN_FOUND",
                    )
                    state.blocked_filters = list((highest_rejected or {}).get("blocked_filters") or [])
                    state.approved_filters = list((highest_rejected or {}).get("approved_filters") or [])
                    state.quality_score = int((highest_rejected or {}).get("quality_score") or 0)
                    auto_trader.set_analysis_candidates(user_id, candidates, None)
                    logger.info(
                        "[NO_CANDIDATE_THIS_CANDLE] user_id=%s reason=%s candidates=%s",
                        user_id,
                        state.last_rejection_reason,
                        len(candidates),
                    )
                    logger.info(
                        "[NO_SIGNAL_FOUND] user_id=%s cycle_id=%s reason=%s candidates=%s",
                        user_id,
                        state.cycle_id,
                        last_rejection_reason,
                        len(candidates),
                    )
                    return 200, build_robot_payload(state)

                logger.info(
                    "[BEST_CANDIDATE] user_id=%s cycle_id=%s symbol=%s direction=%s confidence=%s payout=%s score=%s",
                    user_id,
                    state.cycle_id,
                    selected["symbol"],
                    selected["direction"],
                    selected["confidence"],
                    selected["payout"],
                    selected["strategy_score"],
                )
                logger.info(
                    "[BEST_CANDIDATE_FOUND] user_id=%s cycle_id=%s symbol=%s direction=%s confidence=%s payout=%s",
                    user_id,
                    state.cycle_id,
                    selected["symbol"],
                    selected["direction"],
                    selected["confidence"],
                    selected["payout"],
                )
                logger.info(
                    "[BEST_CANDIDATE_SELECTED] user_id=%s symbol=%s direction=%s strategy_score=%s confidence=%s payout=%s strategy_name=%s strategy_reason=%s used_strategies=%s",
                    user_id,
                    selected["symbol"],
                    selected["direction"],
                    selected["strategy_score"],
                    selected["confidence"],
                    selected["payout"],
                    selected["strategy_name"],
                    selected["strategy_reason"],
                    selected["used_strategies"],
                )

                state = auto_trader.set_pending_signal(user_id, selected)
                selected = dict(state.pending_signal or {})
                entry_window = refresh_entry_window_after_analysis(entry_window)
                state = auto_trader.update_entry_window(user_id, entry_window)
                logger.info(
                    "[SIGNAL_FOUND] user_id=%s cycle_id=%s symbol=%s direction=%s confidence=%s payout=%s",
                    user_id,
                    state.cycle_id,
                    selected.get("symbol"),
                    selected.get("signal") or selected.get("direction"),
                    selected.get("confidence"),
                    selected.get("payout"),
                )
                logger.info("[STATE] SIGNAL_FOUND user_id=%s cycle_id=%s", user_id, state.cycle_id)
                logger.info(
                    "[SIGNAL_PREPARED] user_id=%s cycle_id=%s symbol=%s direction=%s seconds_until_entry=%s",
                    user_id,
                    state.cycle_id,
                    selected.get("symbol"),
                    selected.get("signal") or selected.get("direction"),
                    state.seconds_until_entry_window,
                )
                logger.info(
                    "[CYCLE_FINISHED_SIGNAL_LOCKED] user_id=%s symbol=%s signal=%s confidence=%s "
                    "payout=%s timeframe=%s",
                    user_id,
                    selected.get("symbol"),
                    selected.get("signal"),
                    selected.get("confidence"),
                    selected.get("payout"),
                    selected.get("timeframe"),
                )

            if selected is not None and not entry_window["entry_window_open"]:
                if entry_window["missed_entry_window"]:
                    target_timestamp = selected.get("target_entry_timestamp")
                    if target_timestamp is None:
                        target_timestamp = (
                            float(entry_window["server_timestamp"])
                            + float(entry_window["seconds_until_entry_window"])
                        )
                        selected["target_entry_timestamp"] = target_timestamp
                        selected["entry_target"] = "NEXT_CANDLE_OPEN"
                        selected["target_entry_second"] = int(entry_window["buy_target_second"])
                        state.pending_signal = dict(selected)
                        state.last_signal = dict(selected)
                        state.best_candidate = dict(selected)
                        state.cycle_best_candidate = dict(selected)
                        state.cycle_best_trade_candidate = dict(selected)
                        state.status = STATUS_WAITING_ENTRY
                        state.rejection_reason = None
                        state.seconds_until_entry_window = int(entry_window["seconds_until_entry_window"])
                        logger.info(
                            "[ENTRY_SCHEDULED_NEXT_CANDLE] user_id=%s cycle_id=%s symbol=%s seconds_until_entry=%s target_entry_timestamp=%s",
                            user_id,
                            state.cycle_id,
                            selected.get("symbol"),
                            state.seconds_until_entry_window,
                            target_timestamp,
                        )
                        logger.info(
                            "[ENTRY_SCHEDULED] user_id=%s cycle_id=%s symbol=%s seconds_until_entry=%s target=NEXT_CANDLE_OPEN",
                            user_id,
                            state.cycle_id,
                            selected.get("symbol"),
                            state.seconds_until_entry_window,
                        )
                        logger.info("[STATE] WAITING_ENTRY user_id=%s cycle_id=%s", user_id, state.cycle_id)
                    elif float(entry_window["server_timestamp"]) > (
                        float(target_timestamp) + float(entry_window["entry_window_end_second"])
                    ):
                        logger.warning(
                            "[ENTRY_WINDOW_MISSED] user_id=%s symbol=%s server_time=%s "
                            "timeframe=%s current_candle_seconds=%s window_end=%s",
                            user_id,
                            selected.get("symbol"),
                            entry_window["server_time"],
                            state.timeframe,
                            entry_window["current_candle_seconds"],
                            entry_window["entry_window_end_second"],
                        )
                        state = auto_trader.expire_pending_signal(
                            user_id,
                            reason="ENTRY_WINDOW_MISSED",
                            wait_seconds=max(1, int(entry_window["seconds_until_entry_window"])),
                        )
                        logger.warning(
                            "[SIGNAL_EXPIRED] user_id=%s cycle_id=%s reason=ENTRY_WINDOW_MISSED",
                            user_id,
                            state.cycle_id,
                        )
                        return 200, build_robot_payload(state, user_id=user_id)
                    else:
                        state.status = STATUS_WAITING_ENTRY
                        state.rejection_reason = None
                        state.seconds_until_entry_window = int(entry_window["seconds_until_entry_window"])
                waiting_log = "[WAITING_GALE_ENTRY]" if state.gale_pending else "[WAITING_NEXT_CANDLE_ENTRY]"
                logger.info("[STATE] WAITING_ENTRY user_id=%s cycle_id=%s", user_id, state.cycle_id)
                logger.info(
                    "%s user_id=%s symbol=%s server_time=%s timeframe=%s current_candle_seconds=%s "
                    "seconds_until_entry=%s window_start=%s window_end=%s",
                    waiting_log,
                    user_id,
                    selected.get("symbol"),
                    entry_window["server_time"],
                    state.timeframe,
                    entry_window["current_candle_seconds"],
                    entry_window["seconds_until_entry_window"],
                    entry_window["entry_window_start_second"],
                    entry_window["entry_window_end_second"],
                )
                logger.info(
                    "[ENTRY_WINDOW] user_id=%s cycle_id=%s symbol=%s seconds_until_entry=%s window_start=%s window_end=%s",
                    user_id,
                    state.cycle_id,
                    selected.get("symbol"),
                    entry_window["seconds_until_entry_window"],
                    entry_window["entry_window_start_second"],
                    entry_window["entry_window_end_second"],
                )
                return 200, build_robot_payload(state)

            logger.info(
                "[ENTRY_COUNTDOWN_ZERO] user_id=%s cycle_id=%s symbol=%s current_candle_seconds=%s",
                user_id,
                state.cycle_id,
                selected.get("symbol") if isinstance(selected, dict) else None,
                entry_window["current_candle_seconds"],
            )
            logger.info(
                "[NEXT_CANDLE_ENTRY_WINDOW_OPEN] user_id=%s server_time=%s timeframe=%s "
                "seconds_in_candle=%s window_start=%s window_end=%s buy_target_second=%s",
                user_id,
                entry_window["server_time"],
                state.timeframe,
                entry_window["current_candle_seconds"],
                entry_window["entry_window_start_second"],
                entry_window["entry_window_end_second"],
                entry_window["buy_target_second"],
            )
            logger.info(
                "[ENTRY_WINDOW] user_id=%s cycle_id=%s symbol=%s state=OPEN current_candle_seconds=%s window_start=%s window_end=%s",
                user_id,
                state.cycle_id,
                selected.get("symbol") if isinstance(selected, dict) else None,
                entry_window["current_candle_seconds"],
                entry_window["entry_window_start_second"],
                entry_window["entry_window_end_second"],
            )
            if not state.enabled:
                state.status = STATUS_STOPPED
                state.pending_signal = None
                state.gale_pending = False
                state.gale_active = False
                return 200, build_robot_payload(state)
            if not isinstance(selected, dict) or not selected.get("symbol"):
                # Estado inconsistente (ex.: pending_signal limpo por recovery
                # após restart do bullex-service) chegava aqui com selected=None
                # e explodia em order_attempt_candidates. Reagenda a análise.
                logger.warning(
                    "[ORDER_SKIPPED_NO_SIGNAL] user_id=%s cycle_id=%s status=%s",
                    user_id,
                    state.cycle_id,
                    state.status,
                )
                state = auto_trader.schedule_next_analysis_session(
                    user_id,
                    analysis_result="NO_OPPORTUNITY_FOUND",
                    last_rejection_reason="NO_PATTERN_FOUND",
                )
                return 200, build_robot_payload(state)
            order_path = "/bullex/buy-real"

            skipped_candidates = 0
            last_order_status = 409
            last_order_reason = "NO_AVAILABLE_ASSET"
            last_friendly_error = NO_AVAILABLE_ASSET_ERROR
            attempted_unavailable = False
            # Orçamento compartilhado da revalidação de canal: a janela de
            # compra é 0-5s, então o conjunto de candidatos não pode gastar
            # mais que isso consultando a corretora.
            revalidation_deadline = monotonic() + CHANNEL_REVALIDATION_BUDGET_SECONDS
            for candidate in order_attempt_candidates(state, selected, user_id=user_id):
                is_gale_order = bool(state.gale_pending or candidate.get("is_gale"))
                if not state.enabled:
                    state.status = STATUS_STOPPED
                    state.pending_signal = None
                    state.gale_pending = False
                    state.gale_active = False
                    return 200, build_robot_payload(state)
                if state.order_attempts >= MAX_ORDER_ATTEMPTS_PER_CYCLE:
                    break
                candidate_symbol = normalize_binary_active(str(candidate.get("symbol") or ""))
                if (
                    not is_gale_order
                    and candidate_symbol
                    and active_cooldown_remaining(user_id, candidate_symbol) is not None
                ):
                    skipped_candidates += 1
                    attempted_unavailable = True
                    logger.warning(
                        "[ORDER_FALLBACK_NEXT_CANDIDATE] user_id=%s skipped_active=%s "
                        "reason=ACTIVE_COOLDOWN attempts=%s",
                        user_id,
                        candidate_symbol,
                        state.order_attempts,
                    )
                    continue
                if not is_gale_order:
                    candidate = await refresh_candidate_execution_channel(
                        user_id,
                        candidate,
                        state.timeframe,
                        fresh_timeout_seconds=min(
                            CHANNEL_REVALIDATION_TIMEOUT_SECONDS,
                            revalidation_deadline - monotonic(),
                        ),
                    )
                validation_reason = None
                if not is_gale_order:
                    validation_reason = resolve_entry_validation_reason(
                        candidate,
                        state,
                        minimum_confidence=int(state.min_confidence),
                        user_id=user_id,
                    )
                stop_reason = daily_stop_reason(user_id, state) or robot_stop_reason(state)
                if stop_reason in {STATUS_STOP_WIN_HIT, STATUS_STOP_LOSS_HIT}:
                    state = await pause_robot_by_stop(user_id, stop_reason)
                    return 200, build_robot_payload(state)
                if validation_reason is not None:
                    logger.info(
                        "[ENTRY_BLOCKED] user_id=%s reason=%s asset=%s "
                        "trade_allowed=%s is_open=%s confidence=%s payout=%s blocked=%s",
                        user_id,
                        validation_reason,
                        candidate.get("symbol"),
                        candidate.get("trade_allowed"),
                        candidate.get("is_open"),
                        candidate.get("confidence"),
                        candidate.get("payout"),
                        list(candidate.get("blocked_filters") or []),
                    )
                    if validation_reason == "PAYOUT_TOO_LOW":
                        state = auto_trader.reject_strategy(
                            user_id,
                            "SIGNAL_REJECTED",
                            last_rejection_reason="PAYOUT_TOO_LOW",
                            blocked_filters=["PAYOUT_TOO_LOW"],
                        )
                        state.last_order_error = "PAYOUT_TOO_LOW"
                        state.pending_signal = None
                        logger.warning(
                            "[SIGNAL_REJECTED] user_id=%s symbol=%s reason=PAYOUT_TOO_LOW",
                            user_id,
                            candidate.get("symbol"),
                        )
                        return 200, build_robot_payload(state, user_id=user_id)
                    skipped_candidates += 1
                    logger.warning(
                        "[ORDER_FALLBACK_NEXT_CANDIDATE] user_id=%s skipped_active=%s reason=%s attempts=%s",
                        user_id,
                        candidate.get("symbol"),
                        validation_reason,
                        state.order_attempts,
                    )
                    continue

                next_attempt = state.order_attempts + 1
                state = auto_trader.set_order_attempt(user_id, candidate, next_attempt)
                selected = dict(state.pending_signal or candidate)
                symbol = str(selected["symbol"])
                analyzed_direction = str(
                    selected.get("signal") or selected.get("direction") or ""
                ).strip().upper()
                direction = resolve_robot_execution_direction(
                    analyzed_direction,
                    is_gale_order=is_gale_order,
                )
                payout = selected.get("payout")
                order_amount = state.gale_amount if is_gale_order else state.entry_value
                logger.info(
                    "[ENTRY_ALLOWED] user_id=%s cycle_id=%s symbol=%s direction=%s amount=%s payout=%s",
                    user_id,
                    state.cycle_id,
                    symbol,
                    direction,
                    order_amount,
                    payout,
                )
                order_body = {
                    "active": symbol,
                    "action": direction.lower(),
                    "amount": order_amount,
                    "expiration": entry_window["expiration_minutes"],
                }
                if state.account_mode == "REAL":
                    order_body["confirm_real"] = True
                    payload_reason = validate_buy_real_order_payload(order_body)
                    if payload_reason is not None:
                        logger.error(
                            "[BUY_REAL_PAYLOAD_INVALID] user_id=%s reason=%s payload=%s",
                            user_id,
                            payload_reason,
                            strip_ai_fields(order_body),
                        )
                        state.last_order_error = payload_reason
                        state = reset_cycle_after_finish(user_id)
                        logger.info(
                            "[NEXT_CYCLE_SCHEDULED] user_id=%s next_cycle_at=%s",
                            user_id,
                            state.next_cycle_at,
                        )
                        return 200, build_robot_payload(state, user_id=user_id)
                    logger.info("[BUY_REAL_PAYLOAD] user_id=%s payload=%s", user_id, strip_ai_fields(order_body))
                    logger.info(
                        "[REAL BUY ATTEMPT] user_id=%s active=%s direction=%s amount=%s expiration=%s",
                        user_id,
                        symbol,
                        direction,
                        order_amount,
                        entry_window["expiration_minutes"],
                    )

                logger.info(
                    "[ENTRY_ALLOWED] user_id=%s asset=%s direction=%s amount=%s payout=%s confidence=%s",
                    user_id,
                    symbol,
                    direction,
                    order_amount,
                    payout,
                    selected.get("confidence"),
                )
                state = auto_trader.start_sending_order(user_id)
                logger.info("[STATE] BUYING user_id=%s cycle_id=%s symbol=%s direction=%s", user_id, state.cycle_id, symbol, direction)
                logger.info(
                    "[%s] user_id=%s symbol=%s direction=%s",
                    "SENDING_GALE_ORDER" if is_gale_order else "BUYING",
                    user_id,
                    symbol,
                    direction,
                )
                logger.info(
                    "[ORDER_ATTEMPT] user_id=%s cycle_id=%s path=%s active=%s direction=%s amount=%s expiration=%s attempt=%s gale=%s",
                    user_id,
                    state.cycle_id,
                    order_path,
                    symbol,
                    direction,
                    order_amount,
                    entry_window["expiration_minutes"],
                    state.order_attempts,
                    is_gale_order,
                )
                try:
                    logger.info(
                        "[ORDER_SENT] user_id=%s asset=%s direction=%s amount=%s",
                        user_id,
                        symbol,
                        direction,
                        order_amount,
                    )
                    order_status, order_payload = await submit_bullex_order(
                        user_id,
                        order_path,
                        order_body,
                    )
                except Exception as exc:
                    reason = str(exc).strip() or type(exc).__name__
                    friendly_error = readable_order_error(reason)
                    last_order_status = 502
                    last_order_reason = reason
                    last_friendly_error = friendly_error
                    logger.exception("[ORDER_SEND_FAILED] user_id=%s active=%s error=%s", user_id, symbol, reason)
                    if is_order_availability_error(reason):
                        attempted_unavailable = True
                        mark_execution_channel_unavailable(
                            user_id,
                            symbol,
                            state.timeframe,
                            seconds=UNAVAILABLE_ASSET_COOLDOWN_SECONDS,
                        )
                        logger.info(
                            "[ORDER_FALLBACK_NEXT_CANDIDATE] user_id=%s failed_active=%s attempts=%s",
                            user_id,
                            symbol,
                            state.order_attempts,
                        )
                        continue
                    state = auto_trader.reject_order(
                        user_id,
                        reason,
                        last_order_error=friendly_error,
                    )
                    logger.error(
                        "[ORDER_REJECTED] user_id=%s reason=%s last_order_error=%s",
                        user_id,
                        reason,
                        friendly_error,
                    )
                    logger.info(
                        "[NEXT_CYCLE_SCHEDULED] user_id=%s next_cycle_at=%s",
                        user_id,
                        state.next_cycle_at,
                    )
                    if state.account_mode == "REAL":
                        persist_robot(user_id)
                        logger.warning("[REAL BUY BLOCKED reason=%s] user_id=%s", reason, user_id)
                        return 502, build_robot_payload(state, user_id=user_id)
                    return 502, build_robot_payload(state)
                mark_disconnected_from_payload(user_id, order_payload)
                if not order_payload.get("ok"):
                    reason = str(order_payload.get("error") or "ORDER_FAILED")
                    friendly_error = readable_order_error(reason)
                    last_order_status = order_status
                    last_order_reason = reason
                    last_friendly_error = friendly_error
                    logger.error("[ORDER_SEND_FAILED] user_id=%s active=%s error=%s", user_id, symbol, reason)
                    if is_order_availability_error(reason):
                        attempted_unavailable = True
                        mark_execution_channel_unavailable(
                            user_id,
                            symbol,
                            state.timeframe,
                            seconds=UNAVAILABLE_ASSET_COOLDOWN_SECONDS,
                        )
                        logger.info(
                            "[ORDER_FALLBACK_NEXT_CANDIDATE] user_id=%s failed_active=%s attempts=%s",
                            user_id,
                            symbol,
                            state.order_attempts,
                        )
                        continue
                    state = auto_trader.reject_order(
                        user_id,
                        reason,
                        last_order_error=friendly_error,
                    )
                    logger.error(
                        "[ORDER_REJECTED] user_id=%s reason=%s last_order_error=%s",
                        user_id,
                        reason,
                        friendly_error,
                    )
                    logger.info(
                        "[NEXT_CYCLE_SCHEDULED] user_id=%s next_cycle_at=%s",
                        user_id,
                        state.next_cycle_at,
                    )
                    if state.account_mode == "REAL":
                        persist_robot(user_id)
                        logger.warning("[REAL BUY BLOCKED reason=%s] user_id=%s", reason, user_id)
                        return order_status, build_robot_payload(state, user_id=user_id)
                    return order_status, build_robot_payload(state)

                order_data = order_payload.get("data") if isinstance(order_payload.get("data"), dict) else {}
                order_id = order_data.get("order_id")
                if order_id is None or not str(order_id).strip():
                    state = auto_trader.reject_order(
                        user_id,
                        "ORDER_ID_MISSING",
                        last_order_error="BullEx nao retornou o identificador da ordem",
                    )
                    logger.error("[ORDER_SEND_FAILED] user_id=%s active=%s error=ORDER_ID_MISSING", user_id, symbol)
                    logger.error(
                        "[ORDER_REJECTED] user_id=%s reason=ORDER_ID_MISSING last_order_error=%s",
                        user_id,
                        state.last_order_error,
                    )
                    logger.info(
                        "[NEXT_CYCLE_SCHEDULED] user_id=%s next_cycle_at=%s",
                        user_id,
                        state.next_cycle_at,
                    )
                    if state.account_mode == "REAL":
                        persist_robot(user_id)
                        logger.warning("[REAL BUY BLOCKED reason=ORDER_ID_MISSING] user_id=%s", user_id)
                        return 502, build_error("ORDER_ID_MISSING")
                    return 502, build_robot_payload(state)
                if state.account_mode == "REAL":
                    logger.info("[REAL BUY SUCCESS order_id=%s] user_id=%s", order_id, user_id)
                logger.info(
                    "[ORDER_ACCEPTED] user_id=%s cycle_id=%s order_id=%s symbol=%s direction=%s",
                    user_id,
                    state.cycle_id,
                    order_id,
                    symbol,
                    direction,
                )
                logger.info(
                    "[ENTRY_SENT] user_id=%s cycle_id=%s order_id=%s symbol=%s direction=%s amount=%s payout=%s",
                    user_id,
                    state.cycle_id,
                    order_id,
                    symbol,
                    direction,
                    order_amount,
                    payout,
                )

                sent_at = datetime.now(timezone.utc)
                expiration_window = entry_window
                try:
                    fresh_timestamp = estimate_state_server_timestamp(state)
                    if fresh_timestamp is None:
                        fresh_status, fresh_payload = await call_bullex_service("GET", "/sessions/status", user_id)
                        fresh_timestamp = extract_server_timestamp(fresh_payload)
                    else:
                        fresh_status = 200
                    if fresh_status < 500 and fresh_timestamp is not None:
                        expiration_window = get_entry_window(state.timeframe, fresh_timestamp, server_time_source="bullex")
                except Exception as exc:
                    logger.warning(
                        "[EXPIRATION_SERVER_TIME_REFRESH_FAILED] user_id=%s error=%s",
                        user_id,
                        str(exc).strip() or type(exc).__name__,
                    )
                expected_expire_at, expiration_source = calculate_expected_expire_at(
                    state.timeframe,
                    order_data,
                    expiration_window,
                    sent_at,
                )
                trade = {
                    **order_data,
                    "mode": state.account_mode,
                    "active": symbol,
                    "direction": direction,
                    "analyzed_direction": selected.get("analyzed_direction")
                    or analyzed_direction,
                    "execution_direction_inverted": (
                        str(direction).strip().upper()
                        != str(
                            selected.get("analyzed_direction") or analyzed_direction
                        ).strip().upper()
                    ),
                    "amount": order_amount,
                    "confidence": selected["confidence"],
                    "payout": payout,
                    "expiration": state.timeframe,
                    "timeframe": state.timeframe,
                    "result": STATUS_PENDING_RESULT,
                    "sent_at": sent_at.isoformat(),
                    "expected_expire_at": expected_expire_at.isoformat(),
                    "expires_at": expected_expire_at.isoformat(),
                    "expiration_source": expiration_source,
                    "server_time_at_send": expiration_window.get("server_time"),
                    "server_timestamp_at_send": expiration_window.get("server_timestamp"),
                    "cycle_id": state.cycle_id,
                    "order_attempts": state.order_attempts,
                    "fallback_candidate_used": state.fallback_candidate_used,
                    "market_mode": state.market_mode,
                    "strategy_mode": state.strategy_mode,
                    "strategy_name": selected.get("strategy_name"),
                    "strategy_key": selected.get("strategy_key"),
                    "strategy_summary": selected.get("strategy_summary"),
                    "analysis_detail": selected.get("analysis_detail")
                    or selected.get("entry_reason"),
                    "speech_preview": selected.get("speech_preview")
                    or selected.get("narrator_text"),
                    "named_strategies": list(selected.get("named_strategies") or []),
                    "named_strategy_keys": list(selected.get("named_strategy_keys") or []),
                    "strategy_setup": selected.get("strategy_setup"),
                    "strategy_setups": dict(selected.get("strategy_setups") or {}),
                    "matched_strategies": list(selected.get("matched_strategies") or []),
                    "strategy_reason": selected.get("strategy_reason")
                    or selected.get("analysis_detail"),
                    "entry_reason": selected.get("entry_reason")
                    or selected.get("analysis_detail"),
                    "used_strategies": list(selected.get("used_strategies") or []),
                    "candle_reading": selected.get("candle_reading"),
                    "near_support": bool(selected.get("near_support")),
                    "near_resistance": bool(selected.get("near_resistance")),
                    "price_action_setup": selected.get("price_action_setup"),
                    "strategy_score": int(selected.get("strategy_score") or selected.get("score") or 0),
                    "quality_score": int(selected.get("quality_score") or 0),
                    "confidence_model_version": selected.get("confidence_model_version"),
                    "raw_direction_score": int(selected.get("raw_direction_score") or 0),
                    "direction_score_edge": int(selected.get("direction_score_edge") or 0),
                    "mtf_votes": dict(selected.get("mtf_votes") or {}),
                    "mtf_qualified_votes": dict(selected.get("mtf_qualified_votes") or {}),
                    "mtf_analysis": dict(selected.get("mtf_analysis") or {}),
                    "block_reasons": list(selected.get("block_reasons") or selected.get("blocked_filters") or []),
                    "metrics": dict(selected.get("metrics") or {}),
                    "is_gale": is_gale_order,
                    "gale_step": int(selected.get("gale_step") or (1 if is_gale_order else 0)),
                    "parent_order_id": selected.get("parent_order_id") or state.gale_original_order_id,
                    "cycle_result": None,
                    "final_result": None,
                    "original_amount": float(selected.get("original_amount") or state.entry_value),
                    "gale_amount": float(selected.get("gale_amount") or order_amount),
                }
                trade["timestamp"] = trade["sent_at"]
                state = auto_trader.record_trade(user_id, trade)
                logger.info("[STATE] ORDER_OPEN user_id=%s cycle_id=%s order_id=%s", user_id, state.cycle_id, order_id)
                logger.info("[STATE] WAITING_RESULT user_id=%s cycle_id=%s order_id=%s", user_id, state.cycle_id, order_id)
                logger.info("[WAITING_RESULT] user_id=%s cycle_id=%s order_id=%s", user_id, state.cycle_id, order_id)
                invalidate_account_cache(user_id)
                state.entry_window_open = False
                logger.info(
                    "[EXPIRATION_SET] user_id=%s order_id=%s timeframe=%s expected_expire_at=%s source=%s server_time_at_send=%s",
                    user_id,
                    order_id,
                    state.timeframe,
                    trade["expected_expire_at"],
                    expiration_source,
                    trade["server_time_at_send"],
                )
                logger.info(
                    "[%s] user_id=%s order_id=%s status=%s",
                    "GALE_ORDER_SEND_SUCCESS" if is_gale_order else "ORDER_SEND_SUCCESS",
                    user_id,
                    order_id,
                    state.status,
                )
                logger.info(
                    "[ORDER_SENT] user_id=%s cycle_id=%s order_id=%s symbol=%s direction=%s fallback=%s",
                    user_id,
                    state.cycle_id,
                    order_id,
                    symbol,
                    direction,
                    state.fallback_candidate_used,
                )
                if is_gale_order:
                    logger.info(
                        "[GALE_ORDER_SENT] user_id=%s cycle_id=%s order_id=%s parent_order_id=%s amount=%s",
                        user_id,
                        state.cycle_id,
                        order_id,
                        trade.get("parent_order_id"),
                        order_amount,
                    )
                logger.info(
                    "[TRADE_EXECUTED] user_id=%s order_id=%s symbol=%s direction=%s amount=%s",
                    user_id,
                    order_id,
                    symbol,
                    direction,
                    order_amount,
                )
                logger.info(
                    "[%s] user_id=%s order_id=%s",
                    "PENDING_GALE_RESULT" if is_gale_order else "PENDING_RESULT",
                    user_id,
                    order_id,
                )
                logger.info(
                    "[TRADE_SENT_AT] user_id=%s server_time=%s timeframe=%s "
                    "seconds_in_candle=%s seconds_until_close=%s expiration=%s",
                    user_id,
                    entry_window["server_time"],
                    state.timeframe,
                    entry_window["current_candle_seconds"],
                    entry_window["seconds_until_close"],
                    entry_window["expiration"],
                )
                logger.info(
                    "[REAL_TRADE_SENT] user_id=%s order_id=%s",
                    user_id,
                    trade.get("order_id"),
                )
                logger.info(
                    "[CYCLE_END] user_id=%s cycle_id=%s result=ORDER_SENT order_id=%s",
                    user_id,
                    state.cycle_id,
                    order_id,
                )
                trade_result_monitor.start(user_id, order_id, trade.get("expires_at"))
                return 200, build_robot_payload(state)

            final_error = NO_AVAILABLE_ASSET_ERROR if attempted_unavailable or skipped_candidates else last_friendly_error
            state = auto_trader.reject_order(
                user_id,
                last_order_reason,
                last_order_error=final_error,
            )
            logger.error(
                "[ORDER_REJECTED] user_id=%s reason=%s last_order_error=%s",
                user_id,
                last_order_reason,
                final_error,
            )
            logger.info(
                "[NEXT_CYCLE_SCHEDULED] user_id=%s next_cycle_at=%s",
                user_id,
                state.next_cycle_at,
            )
            return last_order_status, build_robot_payload(state)
        except Exception as exc:
            error = str(exc).strip() or type(exc).__name__
            logger.exception("[ROBOT_CYCLE_RECOVERED] user_id=%s error=%s", user_id, exc)
            state = recover_analysis_error_to_window(
                user_id,
                error,
                locals().get("entry_window") if isinstance(locals().get("entry_window"), dict) else None,
            )
            return 200, build_robot_payload(state)
        finally:
            persist_robot(user_id)


async def run_analysis_now(user_id: str) -> tuple[int, dict[str, Any]]:
    state = auto_trader.get(user_id)
    if (
        not state.enabled
        or not state.connected
        or state.active_mode is None
        or state.operation_in_progress
        or state.pending_signal is not None
    ):
        return 200, build_robot_payload(state)
    logger.info(
        "[ANALYSIS_FORCED_START] user_id=%s current_candle_seconds=%s",
        user_id,
        state.current_candle_seconds,
    )
    state.next_cycle_at = utc_now()
    return await execute_robot_cycle(user_id)


async def execute_robot_worker_cycle(user_id: str) -> None:
    """Executa um ciclo do robô com limite total de duração.

    Args:
        user_id: Identificador autenticado do usuário dono do worker.

    Returns:
        None. O estado do robô é atualizado pelo ciclo ou pelo tratamento de timeout.

    Raises:
        asyncio.CancelledError: Propagado quando o worker é interrompido externamente.
    """
    try:
        await asyncio.wait_for(
            execute_robot_cycle(user_id),
            timeout=ROBOT_CYCLE_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        state = auto_trader.complete_cycle_without_trade(user_id, "ANALYSIS_TIMEOUT")
        state.blocked_filters = ["ANALYSIS_TIMEOUT"]
        state.block_reasons = ["Tempo máximo da análise excedido; novo ciclo agendado."]
        robot_worker_last_tick_at[user_id] = utc_now()
        persist_robot(user_id)
        logger.warning(
            "[ROBOT_CYCLE_TIMEOUT] user_id=%s timeout_seconds=%.2f action=reschedule",
            user_id,
            ROBOT_CYCLE_TIMEOUT_SECONDS,
        )


async def robot_worker(user_id: str) -> None:
    try:
        logger.info("[WORKER_RUNNING_TRUE] user_id=%s", user_id)
        while auto_trader.get(user_id).enabled:
            # Mantém o usuário "ativo" enquanto o robô roda, para que os
            # caminhos de sessão/conta não sejam curto-circuitados quando
            # o usuário fecha a aba do navegador.
            mark_user_active(user_id)
            recover_sync_timeout_if_needed(user_id)
            robot_worker_last_tick_at[user_id] = utc_now()
            logger.info("[WORKER_HEARTBEAT] user_id=%s", user_id)
            logger.info("[ROBOT_RUNNING] user_id=%s", user_id)
            state = auto_trader.get(user_id)
            if result_display_expired(state) or waiting_result_stale(state):
                state = reset_cycle_after_result(user_id)
            result_waiting = bool(
                state.operation_in_progress
                and str((state.last_trade or {}).get("result") or "").strip().upper() not in {"WIN", "LOSS", "TIMEOUT"}
            )
            guard = connection_guard_reason(user_id)
            if not state.connected or state.active_mode is None:
                resumed = resume_robot_connection_from_real_cache(user_id, state)
                if resumed is not None:
                    state = resumed
                else:
                    logger.warning("[ROBOT_WORKER_BLOCKED_DISCONNECTED] user_id=%s", user_id)
                    reconnected = await try_auto_reconnect_with_saved_credentials(user_id)
                    if reconnected:
                        logger.info("[ROBOT_WORKER_AUTO_RECONNECTED] user_id=%s", user_id)
                        continue
                    logger.info("[CPU_GUARD_SLEEP] user_id=%s seconds=3.00", user_id)
                    await asyncio.sleep(3)
                    continue
            if guard is not None and not robot_has_recent_real_cache(user_id, state):
                reason, remaining = guard
                sleep_seconds = max(1.0, min(float(remaining), float(SESSION_OFFLINE_TTL_SECONDS)))
                if reason == "offline":
                    logger.warning("[USER_OFFLINE_SKIPPED] user_id=%s retry_in=%.2f", user_id, remaining)
                else:
                    logger.warning("[BACKOFF_ACTIVE] user_id=%s retry_in=%.2f", user_id, remaining)
                logger.warning("[CPU_LOOP_PROTECTION] user_id=%s reason=%s", user_id, reason)
                # Sob carga o offline costuma ser fila no bullex, não queda real.
                # Tenta retomar pelo cache REAL antes de dormir o ciclo inteiro.
                resumed = resume_robot_connection_from_real_cache(user_id, state)
                if resumed is not None:
                    state = resumed
                    clear_session_backoff(user_id)
                    logger.warning(
                        "[ROBOT_WORKER_OFFLINE_BYPASSED_CACHE] user_id=%s previous_guard=%s",
                        user_id,
                        reason,
                    )
                else:
                    logger.info(
                        "[ROBOT_WORKER_BACKOFF_SLEEP] user_id=%s reason=%s seconds=%.2f",
                        user_id,
                        reason,
                        sleep_seconds,
                    )
                    await asyncio.sleep(sleep_seconds)
                    continue
            if guard is not None:
                reason, remaining = guard
                logger.warning(
                    "[ROBOT_WORKER_USING_RECENT_REAL_CACHE] user_id=%s guard=%s retry_in=%.2f",
                    user_id,
                    reason,
                    remaining,
                )
            if state.operation_in_progress or result_waiting:
                logger.info(
                    "[ANALYSIS_SKIPPED_WAITING_RESULT] user_id=%s order_id=%s",
                    user_id,
                    (state.last_trade or {}).get("order_id"),
                )
                logger.info(
                    "[SKIP_ANALYSIS_WAITING_RESULT] user_id=%s order_id=%s",
                    user_id,
                    (state.last_trade or {}).get("order_id"),
                )
                logger.info("[CPU_GUARD_SLEEP] user_id=%s seconds=0.50", user_id)
                await asyncio.sleep(0.5)
                continue
            post_trade_wait = seconds_until_next_candle_after_trade(state)
            if post_trade_wait is not None:
                sleep_seconds = max(0.5, min(post_trade_wait, 5.0))
                logger.info(
                    "[SKIP_HEAVY_ANALYSIS_UNTIL_NEXT_CANDLE] user_id=%s seconds=%.2f",
                    user_id,
                    post_trade_wait,
                )
                logger.info("[CPU_GUARD_SLEEP] user_id=%s seconds=%.2f", user_id, sleep_seconds)
                await asyncio.sleep(sleep_seconds)
                continue
            logger.info("[ROBOT_WORKER_TICK] user_id=%s", user_id)
            cycle_started_at = monotonic()
            await execute_robot_worker_cycle(user_id)
            logger.info(
                "[CYCLE_DURATION_MS] user_id=%s source=worker ms=%s",
                user_id,
                int((monotonic() - cycle_started_at) * 1000),
            )
            state = auto_trader.get(user_id)
            if not state.enabled:
                break
            if state.status in {
                STATUS_WAITING_ENTRY_WINDOW,
                STATUS_WAITING_ENTRY,
                STATUS_WAITING_GALE_ENTRY,
            }:
                delay = robot_worker_entry_wait_seconds(state.seconds_until_entry_window)
            elif state.status in TEMPORARY_WAIT_STATUSES:
                delay = max(0.5, min(5.0, float(state.to_dict()["seconds_until_next_cycle"] or 1)))
            elif state.status == STATUS_WAITING_ANALYSIS_WINDOW:
                delay = 1.0
            elif state.status == STATUS_ORDER_REJECTED and state.rejected_at is not None:
                delay = max(1, 5 - int((utc_now() - state.rejected_at).total_seconds()))
            elif state.status == STATUS_WAITING_NEXT_CYCLE and state.enabled:
                delay = max(0.5, min(5.0, float(state.to_dict()["seconds_until_next_cycle"])))
            else:
                delay = max(0.5, float(state.to_dict()["seconds_until_next_cycle"] or 1))
            delay = max(0.25, float(delay))
            logger.info("[CPU_GUARD_SLEEP] user_id=%s seconds=%.2f", user_id, delay)
            await asyncio.sleep(delay)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("[WORKER_CRASHED] user_id=%s error=%s", user_id, str(exc).strip() or type(exc).__name__)
        persist_robot(user_id)
    finally:
        current = asyncio.current_task()
        if robot_tasks.get(user_id) is current:
            robot_tasks.pop(user_id, None)
        logger.info("[WORKER_DESTROYED] user_id=%s", user_id)
        logger.info("[WORKER_STOPPED] user_id=%s", user_id)
        state = auto_trader.get(user_id)
        if (
            getattr(state, "enabled", False)
            and not is_stop_status(getattr(state, "status", None))
            and user_id not in robot_worker_restart_attempted
        ):
            robot_worker_restart_attempted.add(user_id)
            robot_tasks[user_id] = asyncio.create_task(robot_worker(user_id))
            logger.info("[WORKER_RESTARTED] user_id=%s", user_id)


def ensure_robot_worker(user_id: str) -> None:
    state = auto_trader.get(user_id)
    if not state.enabled:
        logger.info("[SESSION_RESTORE_SKIPPED] user_id=%s reason=robot_disabled", user_id)
        return
    # Fase 4: gateway em modo external só encaminha comando ao robot-runtime.
    if robot_runtime_mode() == "external":
        if is_panel_online(user_id) or is_user_active(user_id):
            mark_user_active(user_id)
        robot_bus.publish_command(user_id, "ensure")
        logger.warning("[ROBOT_WORKER_DELEGATED] user_id=%s mode=external", user_id)
        return
    # Após restart do gateway o painel polla /robot/state (panel_heartbeat) mas
    # ainda não há mark_user_active — sem isso o worker nunca sobe e a UI fica
    # em "analisando" (payouts do front) sem comprar.
    if not is_user_active(user_id):
        if is_panel_online(user_id) and state.enabled:
            mark_user_active(user_id)
            logger.warning(
                "[PANEL_ONLINE_RESUME_WORKER] user_id=%s action=mark_active_and_start",
                user_id,
            )
        else:
            logger.info("[OFFLINE_USER_SKIPPED] user_id=%s operation=worker_start", user_id)
            return
    account_snapshot = get_cached_account_snapshot(user_id)
    if (
        state.account_mode == "REAL"
        and (state.active_mode or account_snapshot.get("mode")) == "REAL"
        and account_snapshot.get("balance") is not None
        and float(account_snapshot["balance"]) <= 0
    ):
        auto_trader.insufficient_balance(user_id)
        persist_robot(user_id)
        logger.warning(
            "[INSUFFICIENT_BALANCE_REAL] user_id=%s balance=%s",
            user_id,
            account_snapshot.get("balance"),
        )
        logger.warning(
            "[ROBOT_STOPPED_BALANCE_ZERO] user_id=%s balance=%s",
            user_id,
            account_snapshot.get("balance"),
        )
        return
    snapshot_connected = account_snapshot.get("connected") is True
    snapshot_mode = account_snapshot.get("mode")
    snapshot_mode = str(snapshot_mode).strip().upper() if snapshot_mode else None
    effective_connected = bool(state.connected or snapshot_connected)
    effective_mode = state.active_mode or snapshot_mode
    if effective_connected and effective_mode and (
        not state.connected or state.active_mode is None
    ):
        state = auto_trader.sync_connection(
            user_id,
            connected=True,
            active_mode=effective_mode,
            source="worker_start_account_cache",
            align_status=True,
        )
    if robot_connection_unavailable(effective_connected, effective_mode):
        resumed = resume_robot_connection_from_real_cache(user_id, state)
        if resumed is not None:
            state = resumed
            effective_connected = True
            effective_mode = "REAL"
            logger.warning(
                "[ROBOT_WORKER_START_RESUMED_CACHE] user_id=%s",
                user_id,
            )
        else:
            logger.warning("[ROBOT_WORKER_BLOCKED_DISCONNECTED] user_id=%s", user_id)
            # Agenda tentativa de auto-reconexão em background (credenciais salvas).
            asyncio.create_task(_auto_reconnect_then_ensure_worker(user_id))
            return
    guard = connection_guard_reason(user_id)
    if guard is not None and not robot_has_recent_real_cache(user_id, state):
        reason, remaining = guard
        resumed = resume_robot_connection_from_real_cache(user_id, state)
        if resumed is None:
            logger.warning(
                "[SESSION_CHECK_SKIPPED] user_id=%s worker_start=true reason=%s retry_in=%.2f",
                user_id,
                reason,
                remaining,
            )
            return
        state = resumed
        clear_session_backoff(user_id)
        logger.warning(
            "[ROBOT_WORKER_START_OFFLINE_BYPASSED] user_id=%s reason=%s",
            user_id,
            reason,
        )
    task = robot_tasks.get(user_id)
    last_tick_at = robot_worker_last_tick_at.get(user_id)
    stale_worker = (
        task is not None
        and not task.done()
        and last_tick_at is not None
        and (utc_now() - last_tick_at).total_seconds() > ROBOT_WORKER_STALE_SECONDS
        and str(getattr(state, "analysis_result", "") or "").strip().upper() != "RUNNING"
    )
    if stale_worker:
        logger.warning("[WORKER_STALE_RESTART] user_id=%s last_tick_at=%s", user_id, last_tick_at)
        task.cancel()
        robot_tasks.pop(user_id, None)
        task = None
    if task is None or task.done():
        robot_tasks[user_id] = asyncio.create_task(robot_worker(user_id))
        logger.warning("[WORKER_CREATED] user_id=%s", user_id)
        logger.info("[WORKER_RUNNING_TRUE] user_id=%s", user_id)
        logger.info("[ROBOT_WORKER_STARTED] user_id=%s", user_id)
    else:
        logger.info("[WORKER_ALREADY_RUNNING] user_id=%s", user_id)
    logger.info("[WORKER_RUNNING_TRUE] user_id=%s", user_id)


async def _auto_reconnect_then_ensure_worker(user_id: str) -> None:
    """Reconecta com credenciais salvas e retoma o worker se o robô estiver ligado."""
    try:
        ok = await try_auto_reconnect_with_saved_credentials(user_id)
        if not ok:
            return
        state = auto_trader.get(user_id)
        if state.enabled and state.connected:
            # Evita recursão infinita: só cria o worker se já conectado.
            task = robot_tasks.get(user_id)
            if task is None or task.done():
                robot_tasks[user_id] = asyncio.create_task(robot_worker(user_id))
                logger.info("[ROBOT_WORKER_STARTED_AFTER_AUTO_RECONNECT] user_id=%s", user_id)
    except Exception:
        logger.warning(
            "[BULLEX_AUTO_RECONNECT_WORKER_FAILED] user_id=%s",
            user_id,
            exc_info=True,
        )


def schedule_robot_tick(user_id: str) -> None:
    if not auto_trader.get(user_id).enabled:
        return

    async def run_tick() -> None:
        try:
            await execute_robot_cycle(user_id)
        except Exception:
            logger.exception("[ROBOT_INITIAL_TICK_RECOVERED] user_id=%s", user_id)

    asyncio.create_task(run_tick())


async def stop_robot_worker(user_id: str) -> None:
    if robot_runtime_mode() == "external":
        robot_bus.publish_command(user_id, "stop")
        logger.info("[ROBOT_WORKER_STOP_DELEGATED] user_id=%s mode=external", user_id)
        return
    task = robot_tasks.pop(user_id, None)
    if task is None or task.done():
        logger.info("[WORKER_ALREADY_STOPPED] user_id=%s", user_id)
        return
    if task is asyncio.current_task():
        logger.info("[WORKER_STOPPED] user_id=%s", user_id)
        return
    logger.info("[WORKER_STOPPING] user_id=%s", user_id)
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


async def read_restored_session_status(user_id: str) -> bool:
    for attempt in range(5):
        _, payload = await call_bullex_service(
            "GET",
            "/sessions/status",
            user_id,
            allow_failure_backoff=False,
        )
        connected, _ = extract_account_status(payload)
        if connected:
            sync_user_store_from_payload(user_id, payload)
            return True
        if attempt < 4:
            await asyncio.sleep(2)
    return False


@app.on_event("startup")
async def restore_robot_states() -> None:
    print("[STARTUP_RESTORE_BEGIN]", flush=True)
    logger.warning("[STARTUP_RESTORE_BEGIN] mode=%s", robot_runtime_mode())
    logger.info("[STARTUP_RESTORE_DISABLED] no session restore on startup")
    restored_count = 0
    try:
        for user_id, payload in robot_persistence.load_states():
            session_restored = False
            payload = {
                **payload,
                "account_mode": "REAL",
                "allow_real": True,
                "confirm_real": True,
                "connected": session_restored,
                "active_mode": None,
                "connection_checked_at": None,
                "connection_status_source": "startup_no_session_restore",
            }
            restorable_robot_states[user_id] = deepcopy(payload)
            trades = robot_persistence.load_trades(user_id)
            if not trades:
                # robot_trades pode estar vazio; o histórico canônico fica em
                # robot_trade_history (fonte do /history e do placar).
                try:
                    trades = [
                        item
                        for item in robot_persistence.load_trade_history(user_id, 30)
                        if str(item.get("result") or "").upper() in {"WIN", "LOSS", "TIMEOUT", "DRAW"}
                    ]
                except Exception:
                    logger.exception(
                        "[STARTUP_HISTORY_HYDRATE_FAILED] user_id=%s",
                        user_id,
                    )
                    trades = []
            auto_trader.restore(
                user_id,
                payload,
                trades,
                source=robot_persistence_source(),
            )
            robot_state_hydrated_users.add(user_id)
            restored_count += 1
            logger.info(
                "[USER_STATE_LOADED_NO_WORKER] user_id=%s source=%s",
                user_id,
                robot_persistence_source(),
            )
    except Exception:
        logger.exception("[ROBOT STARTUP RESTORE ERROR]")
    logger.info("[ON_DEMAND_RESTORE_ONLY] robot restore requires user action")
    logger.info("[STARTUP_READY] restored_users=%s worker_start=false", restored_count)
    mode = robot_runtime_mode()
    logger.warning("[ROBOT_RUNTIME_MODE] mode=%s", mode)
    print(f"[ROBOT_RUNTIME_MODE] mode={mode}", flush=True)
    _boot_gateway_background_services()


_robot_state_relay_task: asyncio.Task[None] | None = None
_gateway_bg_services_started = False


async def _relay_robot_state_from_redis() -> None:
    """
    Gateway em modo external: escuta ``robot:state`` e agenda push WS.

    O robot-runtime publica snapshots no Redis; o hub WS vive só no gateway.
    """
    stop = {"flag": False}
    queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def _listen() -> None:
        try:
            for user_id, _payload in robot_bus.iter_state_messages(
                should_stop=lambda: stop["flag"]
            ):
                loop.call_soon_threadsafe(queue.put_nowait, (user_id, _payload))
        except Exception:
            logger.exception("[ROBOT_STATE_RELAY_LISTEN_FAILED]")

    listener = asyncio.create_task(asyncio.to_thread(_listen), name="robot-state-redis")
    logger.warning("[ROBOT_STATE_RELAY_STARTED] channel=robot:state")
    print("[ROBOT_STATE_RELAY_STARTED]", flush=True)
    try:
        while True:
            user_id, _payload = await queue.get()
            robot_state_ws_hub.schedule_publish(user_id)
    except asyncio.CancelledError:
        stop["flag"] = True
        listener.cancel()
        with suppress(asyncio.CancelledError):
            await listener
        raise


def _boot_gateway_background_services() -> None:
    """Liga hub WS + warmer + relay Redis no gateway (não no robot-runtime)."""
    global _robot_state_relay_task, _gateway_bg_services_started
    if robot_runtime_mode() == "worker":
        return
    # Hooks sempre atualizados (útil se o warmer for ligado depois do 1º boot).
    robot_state_ws_hub.snapshot_builder = build_robot_state_snapshot_payload
    robot_state_ws_hub.maintenance_hook = robot_panel_maintenance
    if _gateway_bg_services_started:
        return
    if robot_state_ws_hub._push_task is None or robot_state_ws_hub._push_task.done():
        robot_state_ws_hub.start_background_loops()
    logger.warning("[ROBOT_WS_HUB_STARTED] push+maintenance loops active")
    print("[ROBOT_WS_HUB_STARTED]", flush=True)
    if admin_dashboard_warmer is not None:
        admin_dashboard_warmer.start()
        logger.warning("[ADMIN_DASHBOARD_WARMER_STARTED] interval=30s")
    if robot_runtime_mode() == "external" and robot_bus.enabled:
        if _robot_state_relay_task is None or _robot_state_relay_task.done():
            _robot_state_relay_task = asyncio.create_task(
                _relay_robot_state_from_redis(),
                name="robot-state-relay",
            )
    _gateway_bg_services_started = True


@app.on_event("shutdown")
async def shutdown_robot_workers() -> None:
    global _robot_state_relay_task
    mode = robot_runtime_mode()
    if mode != "worker":
        if _robot_state_relay_task is not None and not _robot_state_relay_task.done():
            _robot_state_relay_task.cancel()
            with suppress(asyncio.CancelledError):
                await _robot_state_relay_task
            _robot_state_relay_task = None
        if admin_dashboard_warmer is not None:
            await admin_dashboard_warmer.aclose()
        await robot_state_ws_hub.aclose()
    if mode != "external":
        for user_id in list(robot_tasks):
            persist_robot(user_id)
            task = robot_tasks.pop(user_id, None)
            if task is not None and not task.done() and task is not asyncio.current_task():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
    await trade_result_monitor.shutdown()
    if supabase_auth_service is not None:
        await supabase_auth_service.aclose()
    if auth_session_service is not None:
        await auth_session_service.aclose()
    await aclose_bullex_http_client()
    robot_bus.close()
    # Não bloqueia o shutdown esperando `_ROBOT_PERSIST_EXECUTOR` — o
    # container recebe SIGKILL depois do grace period do Docker se demorar
    # demais, e a escrita é best-effort (mesma tolerância que já existia
    # quando essas chamadas eram síncronas e podiam ser interrompidas no meio
    # de um restart).


@app.get("/health")
async def health() -> dict[str, Any]:
    return build_success({"status": "healthy", "service": "backend-gateway"})


@app.get("/cors-test")
async def cors_test() -> dict[str, bool]:
    return {"ok": True}


@app.get("/signals/analyze")
async def signals_analyze(
    active: str,
    strategy_mode: str = Query(default="conservative"),
    auth: dict[str, str] = Depends(require_headers),
) -> JSONResponse:
    if not is_binary_asset_allowed(active):
        return json_response(400, build_error(ASSET_NOT_ALLOWED))

    symbol = normalize_binary_active(active)
    status_code, payload = await analyze_active_signal(
        auth["user_id"],
        symbol,
        strategy_mode=strategy_mode,
    )
    return json_response(status_code, payload)


@app.get("/signals/review")
async def signals_review(
    active: str,
    auth: dict[str, str] = Depends(require_headers),
) -> JSONResponse:
    if not is_binary_asset_allowed(active):
        return json_response(400, build_error(ASSET_NOT_ALLOWED))

    symbol = normalize_binary_active(active)
    status_code, payload = await analyze_active_signal(auth["user_id"], symbol)
    if not payload.get("ok"):
        return json_response(status_code, payload)

    signal = payload["data"]
    review = build_local_signal_review(signal)
    return json_response(200, build_success({"signal": signal, "review": review}))


@app.get("/signals/scan")
async def signals_scan(
    limit: int = Query(default=5, ge=1, le=len(BINARY_ALLOWED_ASSETS)),
    include_wait: bool = False,
    strategy_mode: str = Query(default="conservative"),
    auth: dict[str, str] = Depends(require_headers),
) -> JSONResponse:
    state = get_user_robot_state(auth["user_id"])
    status_code, payload = await scan_local_signals(
        auth["user_id"],
        limit=limit,
        include_wait=include_wait,
        strategy_mode=strategy_mode,
        market_mode=state.market_mode,
    )
    return json_response(status_code, payload)


@app.get("/signals/top-reviewed")
async def signals_top_reviewed(auth: dict[str, str] = Depends(require_headers)) -> JSONResponse:
    state = get_user_robot_state(auth["user_id"])
    status_code, payload = await scan_local_signals(
        auth["user_id"],
        limit=5,
        include_wait=False,
        market_mode=state.market_mode,
    )
    if not payload.get("ok"):
        return json_response(status_code, payload)

    reviewed = []
    for signal in payload["data"][:5]:
        review = build_local_signal_review(signal)
        reviewed.append({"signal": signal, "review": review})

    reviewed.sort(
        key=lambda item: (
            int(item["review"].get("quality") or 0),
            int(item["signal"].get("confidence") or 0),
        ),
        reverse=True,
    )
    return json_response(200, build_success(reviewed))


async def _robot_state_impl(auth: dict[str, str]) -> JSONResponse:
    user_id = auth["user_id"]
    mark_panel_heartbeat(user_id)
    if not is_user_active(user_id):
        logger.info("[OFFLINE_USER_SKIPPED] user_id=%s path=/robot/state", user_id)
    # This polling endpoint must remain independent from BullEx, WebSocket and
    # persistence latency. Startup restoration populates auto_trader; a missing
    # entry is represented by its in-memory default state.
    auto_trader.get(user_id)
    # Corrige TIMEOUT falso da última ordem (Bullex já tem WIN/LOSS).
    try:
        await reconcile_timeout_last_trade(user_id)
    except Exception as exc:
        logger.warning(
            "[TIMEOUT_RECONCILE_SKIPPED] user_id=%s error=%s",
            user_id,
            exc.__class__.__name__,
        )
    # Se o resultado fechou com a tela fechada, mostra e libera após ~8s online.
    if auto_trader.get(user_id).unseen_result:
        auto_trader.acknowledge_unseen_result(user_id)
        persist_robot(user_id)
    state = recover_sync_timeout_if_needed(user_id)
    state = clear_stale_open_operation_if_stopped(user_id, state)
    repaired_legacy_real_flags = (
        state.account_mode != "REAL"
        or not bool(state.allow_real)
        or not bool(state.confirm_real)
        or str(state.active_mode or "").strip().upper() == "DEMO"
    )
    state.account_mode = "REAL"
    state.allow_real = True
    state.confirm_real = True
    if str(state.active_mode or "").strip().upper() == "DEMO":
        state.active_mode = "REAL"
    if repaired_legacy_real_flags:
        persist_robot(user_id)
    account_snapshot = get_cached_account_snapshot(user_id)
    connected = bool(state.connected or account_snapshot.get("connected") is True)
    active_mode = state.active_mode or account_snapshot.get("mode")
    active_mode = str(active_mode).strip().upper() if active_mode else None
    source = state.connection_status_source or (
        "cached" if account_snapshot.get("connected") is not None else "memory"
    )
    if connected and active_mode == "REAL":
        if source in {"disconnected", "offline_cache", "backoff_active"}:
            source = "cached"
        clear_session_backoff(user_id)
        state = auto_trader.sync_connection(
            user_id,
            connected=True,
            active_mode="REAL",
            source=source,
            align_status=True,
        )
    window = get_entry_window(
        state.timeframe,
        utc_now().timestamp(),
        server_time_source="vps_fallback",
    )
    auto_trader.update_entry_window(
        user_id,
        window,
    )
    state = auto_trader.get(user_id)
    block_reason = real_block_reason(state, connected=connected, active_mode=active_mode, user_id=user_id)
    real_balance_warning = get_real_balance_warning(
        user_id,
        state,
        active_mode,
        snapshot=account_snapshot,
    )
    if (
        state.account_mode == "REAL"
        and active_mode == "REAL"
        and account_snapshot.get("balance") is not None
        and float(account_snapshot["balance"]) <= 0
    ):
        state = await stop_real_robot_for_insufficient_balance(
            user_id,
            balance=float(account_snapshot["balance"]),
        )
        real_balance_warning = "BALANCE_ZERO"
        block_reason = STATUS_INSUFFICIENT_BALANCE
    worker_task = robot_tasks.get(user_id)
    worker_running = bool(worker_task is not None and not worker_task.done())
    if (
        state.enabled
        and not worker_running
        and block_reason in {None, "BULLEX_NOT_CONNECTED", "BULLEX_ACTIVE_MODE_NOT_REAL"}
    ):
        if user_id in robot_worker_restart_attempted and block_reason is None:
            block_reason = "WORKER_NOT_RUNNING"
            logger.warning("[WORKER_NOT_RUNNING] user_id=%s action=restart_limit_reached", user_id)
        elif user_id not in robot_worker_restart_attempted or block_reason == "BULLEX_NOT_CONNECTED":
            # Poll do painel = intenção de operar; libera ensure_robot_worker
            # mesmo antes do TTL de active_users (pós-restart do gateway).
            # Se a sessão ainda não sincronizou, ensure agenda auto-reconnect.
            mark_user_active(user_id)
            logger.warning(
                "[WORKER_NOT_RUNNING] user_id=%s action=auto_create block_reason=%s",
                user_id,
                block_reason,
            )
            ensure_robot_worker(user_id)
            worker_task = robot_tasks.get(user_id)
            worker_running = bool(worker_task is not None and not worker_task.done())
            if worker_running:
                block_reason = real_block_reason(
                    auto_trader.get(user_id),
                    connected=True,
                    active_mode=auto_trader.get(user_id).active_mode or active_mode,
                    user_id=user_id,
                )
            elif block_reason is None:
                block_reason = "WORKER_NOT_RUNNING"
    if state.enabled and worker_running and state.status == STATUS_STOPPED:
        state.status = STATUS_ANALYZING
        persist_robot(user_id)
    balance_value = number_or_none(account_snapshot.get("balance"))
    real_ready = bool(
        connected
        and active_mode == "REAL"
        and block_reason is None
        and (balance_value is None or float(balance_value) > 0)
    )
    logger.info(
        "[ROBOT_STATE_FAST_RETURN] user_id=%s connected=%s source=%s",
        user_id,
        connected,
        source,
    )
    return json_response(
        200,
        build_robot_payload(
            state,
            user_id=user_id,
            connected=connected,
            active_mode=active_mode,
            balance=account_snapshot.get("balance"),
            currency=account_snapshot.get("currency"),
            email=account_snapshot.get("email"),
            connection_checked_at=state.connection_checked_at.isoformat()
            if state.connection_checked_at is not None
            else None,
            connection_status_source=source,
            real_ready=real_ready,
            real_block_reason=block_reason,
            real_balance_warning=real_balance_warning,
        ),
        headers=polling_headers(ROBOT_STATE_MIN_POLL_SECONDS),
    )


@app.get("/robot/state")
async def robot_state(auth: dict[str, str] = Depends(require_headers)) -> JSONResponse:
    try:
        response = await _robot_state_impl(auth)
        response.headers.update(polling_headers(ROBOT_STATE_MIN_POLL_SECONDS))
        return response
    except Exception as exc:
        logger.info(
            "[ROBOT_STATE_RECOVERED] user_id=%s reason=%s",
            auth.get("user_id"),
            exc.__class__.__name__,
            exc_info=True,
        )
        return json_response(
            200,
            build_robot_state_fallback_payload(auth.get("user_id"), exc),
            headers=polling_headers(ROBOT_STATE_MIN_POLL_SECONDS),
        )


def build_robot_state_snapshot_payload(user_id: str) -> dict[str, Any]:
    """
    Snapshot read-only do robô para push WS (sem reconcile/ensure).

    Args:
        user_id: Usuário autenticado.

    Returns:
        Envelope ``build_robot_payload`` (ok + data).
    """
    if robot_runtime_mode() == "external":
        remote = robot_bus.get_snapshot(user_id)
        if remote is not None:
            return remote
    state = auto_trader.get(user_id)
    account_snapshot = get_cached_account_snapshot(user_id)
    connected = bool(state.connected or account_snapshot.get("connected") is True)
    active_mode = state.active_mode or account_snapshot.get("mode")
    active_mode = str(active_mode).strip().upper() if active_mode else None
    source = state.connection_status_source or (
        "cached" if account_snapshot.get("connected") is not None else "memory"
    )
    block_reason = real_block_reason(
        state, connected=connected, active_mode=active_mode, user_id=user_id
    )
    balance_value = number_or_none(account_snapshot.get("balance"))
    real_ready = bool(
        connected
        and active_mode == "REAL"
        and block_reason is None
        and (balance_value is None or float(balance_value) > 0)
    )
    return build_robot_payload(
        state,
        user_id=user_id,
        connected=connected,
        active_mode=active_mode,
        balance=account_snapshot.get("balance"),
        currency=account_snapshot.get("currency"),
        email=account_snapshot.get("email"),
        connection_checked_at=state.connection_checked_at.isoformat()
        if state.connection_checked_at is not None
        else None,
        connection_status_source=source,
        real_ready=real_ready,
        real_block_reason=block_reason,
        real_balance_warning=get_real_balance_warning(
            user_id, state, active_mode, snapshot=account_snapshot
        ),
    )


async def robot_panel_maintenance(user_id: str) -> None:
    """Side-effects que antes rodavam em todo GET /robot/state (WS path)."""
    mark_panel_heartbeat(user_id)
    mark_user_active(user_id)
    try:
        await reconcile_timeout_last_trade(user_id)
    except Exception as exc:
        logger.warning(
            "[TIMEOUT_RECONCILE_SKIPPED] user_id=%s error=%s",
            user_id,
            exc.__class__.__name__,
        )
    state = auto_trader.get(user_id)
    if state.enabled:
        ensure_robot_worker(user_id)
    robot_state_ws_hub.schedule_publish(user_id)


@app.get("/robot/ws-ticket")
async def robot_ws_ticket(auth: dict[str, str] = Depends(require_headers)) -> dict[str, Any]:
    """
    Emite ticket one-shot (60s) para ``/ws/robot-state``.

    Necessário quando o cookie ``__Host-`` da API não acompanha o upgrade WS
    a partir do subdomínio do painel.
    """
    token = robot_state_ws_hub.issue_ticket(auth["user_id"], auth["company_id"])
    return {
        "ok": True,
        "data": {"ticket": token, "expires_in": 60, "path": "/ws/robot-state"},
    }


@app.websocket("/ws/robot-state")
async def ws_robot_state(websocket: WebSocket) -> None:
    """Push do estado do robô; auth por ticket query ou cookie de sessão."""
    ticket = str(websocket.query_params.get("ticket") or "").strip()
    user_id: str | None = None
    if ticket:
        consumed = robot_state_ws_hub.consume_ticket(ticket)
        if consumed is None:
            await websocket.accept()
            await close_robot_websocket(
                websocket, {"type": "error", "error": "INVALID_WS_TICKET"}
            )
            return
        user_id = consumed[0]
    else:
        access = extract_access_token_from_request(
            websocket.headers.get("authorization"),
            dict(websocket.cookies),
        )
        if not access or supabase_auth_service is None:
            await websocket.accept()
            await close_robot_websocket(
                websocket, {"type": "error", "error": "NO_AUTH"}
            )
            return
        try:
            user = await supabase_auth_service.authenticate(access)
        except AuthenticationError:
            await websocket.accept()
            await close_robot_websocket(
                websocket, {"type": "error", "error": "INVALID_SESSION"}
            )
            return
        if not user.grant_access and not user.is_admin:
            await websocket.accept()
            await close_robot_websocket(
                websocket, {"type": "error", "error": "ACCESS_DENIED"}
            )
            return
        user_id = user.user_id

    await websocket.accept()
    assert user_id is not None
    mark_panel_heartbeat(user_id)
    mark_user_active(user_id)
    await robot_state_ws_hub.register(user_id, websocket)
    try:
        await robot_state_ws_hub.force_snapshot(user_id)
        await robot_ws_receive_loop(robot_state_ws_hub, user_id, websocket)
    finally:
        await robot_state_ws_hub.unregister(user_id, websocket)


def build_segmented_robot_stats(items: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    """
    Agrupa desempenho por ativo, estratégia, setup e hora UTC.

    Args:
        items: Operações finalizadas do histórico do usuário autenticado.

    Returns:
        Mapas segmentados com trades, wins, losses, win rate e lucro.

    Raises:
        Não lança exceções para campos opcionais ausentes.
    """
    dimensions: dict[str, dict[str, dict[str, Any]]] = {
        "by_asset": {},
        "by_strategy": {},
        "by_setup": {},
        "by_hour_utc": {},
    }
    for item in items:
        result = str(item.get("final_result") or "").strip().upper()
        if result not in {"WIN", "LOSS"}:
            continue
        opened_at = parse_datetime(item.get("opened_at"))
        dimension_values = {
            "by_asset": str(item.get("active") or "UNKNOWN"),
            "by_strategy": str(item.get("strategy_name") or "UNKNOWN"),
            "by_setup": str(item.get("strategy_setup") or "UNKNOWN"),
            "by_hour_utc": f"{opened_at.hour:02d}" if opened_at is not None else "UNKNOWN",
        }
        for dimension, value in dimension_values.items():
            bucket = dimensions[dimension].setdefault(
                value,
                {"trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0, "profit": 0.0},
            )
            bucket["trades"] += 1
            bucket["wins"] += int(result == "WIN")
            bucket["losses"] += int(result == "LOSS")
            bucket["profit"] = round(bucket["profit"] + float(item.get("profit") or 0), 2)

    for buckets in dimensions.values():
        for bucket in buckets.values():
            bucket["win_rate"] = round((bucket["wins"] / bucket["trades"]) * 100, 2)
    return dimensions


def build_robot_stats(items: list[dict[str, Any]]) -> dict[str, Any]:
    final_results = [
        str(item.get("final_result") or "").strip().upper()
        for item in items
        if str(item.get("final_result") or "").strip().upper() in {"WIN", "LOSS"}
    ]
    wins = sum(1 for result in final_results if result == "WIN")
    losses = sum(1 for result in final_results if result == "LOSS")
    total_trades = wins + losses
    profit = round(sum(float(item.get("profit") or 0) for item in items), 2)
    gross_profit = sum(max(0.0, float(item.get("profit") or 0)) for item in items)
    gross_loss = abs(sum(min(0.0, float(item.get("profit") or 0)) for item in items))
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss else round(gross_profit, 2)

    current_win_streak = 0
    current_loss_streak = 0
    if final_results:
        current_result = final_results[0]
        for result in final_results:
            if result != current_result:
                break
            if current_result == "WIN":
                current_win_streak += 1
            elif current_result == "LOSS":
                current_loss_streak += 1

    best_win_streak = 0
    best_loss_streak = 0
    win_streak = 0
    loss_streak = 0
    for result in reversed(final_results):
        if result == "WIN":
            win_streak += 1
            loss_streak = 0
            best_win_streak = max(best_win_streak, win_streak)
        elif result == "LOSS":
            loss_streak += 1
            win_streak = 0
            best_loss_streak = max(best_loss_streak, loss_streak)

    return {
        "wins": wins,
        "losses": losses,
        "total_trades": total_trades,
        "win_rate": round((wins / total_trades) * 100, 2) if total_trades else 0.0,
        "profit": profit,
        "profit_factor": profit_factor,
        "current_win_streak": current_win_streak,
        "current_loss_streak": current_loss_streak,
        "best_win_streak": best_win_streak,
        "best_loss_streak": best_loss_streak,
        "segments": build_segmented_robot_stats(items),
    }


def load_robot_history_items(user_id: str, days: int) -> list[dict[str, Any]]:
    persisted_items = robot_persistence.load_trade_history(user_id, days)
    return _merge_robot_history_with_memory(user_id, days, persisted_items)


def load_robot_history_items_for_users(
    user_ids: list[str],
    days: int,
) -> dict[str, list[dict[str, Any]]]:
    """
    Carrega histórico de vários usuários em lote (dashboard admin).

    Args:
        user_ids: Clientes do tenant.
        days: Janela em dias.

    Returns:
        Mapa user_id → trades mesclados com memória do auto_trader.
    """
    persisted_by_user = robot_persistence.load_trade_history_for_users(user_ids, days)
    return {
        user_id: _merge_robot_history_with_memory(
            user_id,
            days,
            persisted_by_user.get(user_id, []),
        )
        for user_id in persisted_by_user
    }


def _merge_robot_history_with_memory(
    user_id: str,
    days: int,
    persisted_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Une histórico persistido com trades ainda só em memória."""
    items_by_order_id: dict[str, dict[str, Any]] = {}
    ordered_items: list[dict[str, Any]] = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))

    for item in persisted_items:
        order_id = str(item.get("order_id") or "").strip()
        if not order_id:
            continue
        normalized = strip_ai_fields(dict(item))
        items_by_order_id[order_id] = normalized
        ordered_items.append(normalized)

    for trade in auto_trader.history(user_id).get("trades", []):
        order_id = str(trade.get("order_id") or "").strip()
        result = str(trade.get("result") or "").strip().upper()
        finished_at = parse_datetime(trade.get("finished_at"))
        if not order_id or order_id in items_by_order_id:
            continue
        if result not in {"WIN", "LOSS"} or finished_at is None or finished_at < cutoff:
            continue
        normalized = strip_ai_fields(dict(trade))
        ordered_items.append(normalized)
        items_by_order_id[order_id] = normalized

    return sorted(
        ordered_items,
        key=lambda item: parse_datetime(item.get("finished_at"))
        or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )


class RobotResetCycleRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    reset_score: bool = False
    reset_daily_profit: bool = True


@app.get("/robot/history")
async def robot_history(
    days: int = Query(default=30),
    auth: dict[str, str] = Depends(require_headers),
) -> JSONResponse:
    if days < 1 or days > 90:
        raise HTTPException(status_code=422, detail="days must be between 1 and 90")
    items = await asyncio.to_thread(load_robot_history_items, auth["user_id"], days)
    return json_response(200, build_success({"items": items, "trades": items}))


@app.options("/robot/history")
async def robot_history_options() -> JSONResponse:
    return json_response(200, build_success({"preflight": True}))


@app.get("/robot/stats")
async def robot_stats(
    days: int = Query(default=30),
    auth: dict[str, str] = Depends(require_headers),
) -> JSONResponse:
    if days < 1 or days > 90:
        raise HTTPException(status_code=422, detail="days must be between 1 and 90")
    items = await asyncio.to_thread(load_robot_history_items, auth["user_id"], days)
    return json_response(200, build_success(build_robot_stats(items)))


@app.options("/robot/stats")
async def robot_stats_options() -> JSONResponse:
    return json_response(200, build_success({"preflight": True}))


@app.get("/robot/pattern-memory")
async def robot_pattern_memory(auth: dict[str, str] = Depends(require_headers)) -> JSONResponse:
    """
    Lista os padrões do caderno de memória (global por padrão).

    Útil para auditoria: quais contextos a memória considera fracos/fortes.
    """
    user_id = auth["user_id"]
    pattern_memory.ensure_hydrated(user_id, history_loader=pattern_memory_history_loader)
    patterns = pattern_memory.snapshot_for_user(user_id)
    weak = [
        row
        for row in patterns
        if int(row["samples"]) >= int(pattern_memory.min_samples_to_block)
        and float(row["win_rate"]) < float(pattern_memory.max_weak_win_rate)
    ]
    return json_response(
        200,
        build_success(
            {
                "enabled": pattern_memory.is_enabled(),
                "scope": "global" if pattern_memory.is_global() else "personal",
                "min_samples_to_block": pattern_memory.min_samples_to_block,
                "max_weak_win_rate": pattern_memory.max_weak_win_rate,
                "lookback_days": pattern_memory.lookback_days,
                "patterns": patterns,
                "weak_patterns": weak,
                "total_patterns": len(patterns),
                "weak_count": len(weak),
            }
        ),
    )


@app.options("/robot/pattern-memory")
async def robot_pattern_memory_options() -> JSONResponse:
    return json_response(200, build_success({"preflight": True}))


@app.get("/robot/persistence")
async def robot_persistence_status(auth: dict[str, str] = Depends(require_headers)) -> JSONResponse:
    try:
        payload = robot_persistence.get_restore_status(auth["user_id"])
    except Exception:
        logger.exception("[ROBOT PERSISTENCE ERROR] user_id=%s", auth["user_id"])
        payload = {"session_restored": False, "robot_restored": False, "last_restore_at": None}
    return JSONResponse(status_code=200, content=payload)


@app.get("/sessions/persistence-debug")
async def sessions_persistence_debug(_: None = Depends(require_api_key)) -> JSONResponse:
    status_code, payload = await call_bullex_service(
        "GET",
        "/sessions/persistence-debug",
        "persistence-debug",
    )
    if not payload.get("ok"):
        return json_response(status_code, payload)

    data = payload.get("data")
    if not isinstance(data, dict):
        return json_response(502, build_error("INVALID_BULLEX_RESPONSE"))
    return JSONResponse(status_code=status_code, content=data)


@app.get("/debug/bullex-connection-schema")
async def debug_bullex_connection_schema(
    auth: dict[str, str] = Depends(require_headers),
) -> JSONResponse:
    payload = build_connection_payload(
        {
            "email": "user@example.com",
            "connected": True,
            "requires_2fa": False,
            "active_mode": "REAL",
            "active_mode_from_bullex": "REAL",
            "currency": "BRL",
            "balance": 0,
        }
    )
    diagnostic = user_store.connection_upsert_diagnostic(auth["user_id"], payload)
    return json_response(200, build_success(diagnostic))


@app.get("/debug/user-isolation")
async def debug_user_isolation(
    auth: dict[str, str] = Depends(require_headers),
) -> JSONResponse:
    user_id = auth["user_id"]
    state = get_user_robot_state(user_id)
    return JSONResponse(
        status_code=200,
        content={
            "user_id": user_id,
            "has_state": auto_trader.has_state(user_id),
            "entry_value": state.entry_value,
            "stop_win": state.stop_win,
            "stop_loss": state.stop_loss,
            "source": auto_trader.source(user_id),
        },
    )


@app.get("/debug/robot-settings")
async def debug_robot_settings(
    auth: dict[str, str] = Depends(require_headers),
) -> JSONResponse:
    user_id = auth["user_id"]
    state = get_user_robot_state(user_id)
    return JSONResponse(
        status_code=200,
        content={
            "user_id": user_id,
            "source": auto_trader.source(user_id),
            "settings": extract_robot_settings(state.to_dict()),
        },
    )


@app.post("/robot/config")
async def robot_config(
    body: dict[str, Any] | None = Body(default=None),
    auth: dict[str, str] = Depends(require_headers),
) -> JSONResponse:
    user_id = auth["user_id"]
    get_user_robot_state(user_id)
    state = recover_sync_timeout_if_needed(user_id)
    state = clear_stale_open_operation_if_stopped(user_id, state)
    if robot_config_locked(user_id, state):
        logger.warning(
            "[ROBOT_CONFIG_LOCKED] user_id=%s enabled=%s worker_running=%s operation_in_progress=%s",
            user_id,
            state.enabled,
            bool(robot_tasks.get(user_id) is not None and not robot_tasks[user_id].done()),
            state.operation_in_progress,
        )
        return json_response(409, CONFIG_LOCK_ERROR)
    raw_body = dict(body or {})
    raw_body["account_mode"] = "REAL"
    raw_body["allow_real"] = True
    raw_body["confirm_real"] = True
    logger.warning("[ROBOT_CONFIG_PAYLOAD] user_id=%s payload=%s", user_id, raw_body)
    logger.info("[REAL_CONFIG_RECEIVED] user_id=%s payload=%s", user_id, raw_body)
    ignored_ai_fields = ignored_ai_config_fields(raw_body)
    if ignored_ai_fields:
        logger.info(
            "[ROBOT_AI_FIELDS_IGNORED] user_id=%s fields=%s",
            user_id,
            ignored_ai_fields,
        )
    filtered_body = filter_robot_config_payload(raw_body)
    if "market_mode" in filtered_body or "marketMode" in raw_body:
        requested_mode = filtered_body.get("market_mode", raw_body.get("marketMode"))
        selectable = coerce_selectable_market_mode(requested_mode)
        if normalize_market_mode(requested_mode) == "OPEN" and selectable != "OPEN":
            logger.info(
                "[OPEN_MARKET_UNAVAILABLE_COERCED] user_id=%s requested=%s coerced=%s",
                user_id,
                requested_mode,
                selectable,
            )
        filtered_body["market_mode"] = selectable
    partial_update = RobotConfigUpdate.model_validate(filtered_body)
    if partial_update.entry_value is not None:
        if partial_update.entry_value < MIN_REAL_ENTRY:
            return json_response(400, build_error("ENTRY_VALUE_TOO_LOW"))
        if partial_update.entry_value > MAX_REAL_ENTRY:
            return json_response(400, build_error("ENTRY_VALUE_TOO_HIGH"))
    effective_stop_win_mode = normalize_stop_mode(
        partial_update.stop_win_mode
        if partial_update.stop_win_mode is not None
        else getattr(auto_trader.get(user_id), "stop_win_mode", "money")
    )
    effective_stop_loss_mode = normalize_stop_mode(
        partial_update.stop_loss_mode
        if partial_update.stop_loss_mode is not None
        else getattr(auto_trader.get(user_id), "stop_loss_mode", "money")
    )
    if (
        effective_stop_win_mode == "money"
        and partial_update.stop_win is not None
        and partial_update.stop_win < MIN_STOP_MONEY
    ):
        return json_response(400, build_error("STOP_WIN_TOO_LOW"))
    if (
        effective_stop_loss_mode == "money"
        and partial_update.stop_loss is not None
        and partial_update.stop_loss < MIN_STOP_MONEY
    ):
        return json_response(400, build_error("STOP_LOSS_TOO_LOW"))
    if (
        partial_update.stop_win_operations is not None
        and partial_update.stop_win_operations < MIN_STOP_OPERATIONS
    ):
        return json_response(400, build_error("STOP_WIN_OPERATIONS_TOO_LOW"))
    if (
        partial_update.stop_loss_operations is not None
        and partial_update.stop_loss_operations < MIN_STOP_OPERATIONS
    ):
        return json_response(400, build_error("STOP_LOSS_OPERATIONS_TOO_LOW"))
    logger.warning(
        "[ROBOT_CONFIG_FILTERED_PAYLOAD] user_id=%s payload=%s",
        user_id,
        partial_update.model_dump(exclude_none=True),
    )
    state = auto_trader.update_config(
        user_id,
        partial_update,
    )
    saved_fields = partial_update.model_dump(exclude_none=True)
    persist_robot(user_id)
    logger.info(
        "[ROBOT_CONFIG_SAVED] user_id=%s saved_fields=%s",
        user_id,
        sorted(saved_fields.keys()),
    )
    logger.info(
        "[REAL_CONFIG_SAVED] user_id=%s account_mode=%s allow_real=%s confirm_real=%s",
        user_id,
        state.account_mode,
        state.allow_real,
        state.confirm_real,
    )
    if {"account_mode", "allow_real", "confirm_real"}.intersection(saved_fields):
        logger.info(
            "[REAL_CONFIRMATION_UPDATED] user_id=%s account_mode=%s allow_real=%s confirm_real=%s",
            user_id,
            state.account_mode,
            state.allow_real,
            state.confirm_real,
        )
    return json_response(200, build_robot_payload(state, user_id=user_id))


@app.post("/robot/settings")
async def robot_settings(
    body: dict[str, Any] | None = Body(default=None),
    auth: dict[str, str] = Depends(require_headers),
) -> JSONResponse:
    return await robot_config(body, auth)


def is_marketing_simulation_session(auth: dict[str, str]) -> bool:
    """True quando a sessão autenticada é conta marketing em modo simulação."""
    return (
        str(auth.get("account_type") or "").strip().lower() == "marketing"
        and str(auth.get("marketing_mode") or "").strip().lower() == "simulation"
    )


async def _robot_start_impl(auth: dict[str, str]) -> JSONResponse:
    user_id = auth["user_id"]
    mark_user_active(user_id)
    # Libera backoff/cache stale ANTES do status/account — senão o start lia
    # ``offline_user`` / ``BULLEX_NOT_CONNECTED`` com a Bullex ainda viva.
    clear_session_backoff(user_id)
    logger.info("[ROBOT_START_REQUEST] user_id=%s", user_id)
    try:
        await reconcile_timeout_last_trade(user_id)
    except Exception as exc:
        logger.warning(
            "[TIMEOUT_RECONCILE_SKIPPED] user_id=%s error=%s phase=start",
            user_id,
            exc.__class__.__name__,
        )
    state = get_user_robot_state(user_id)
    state.account_mode = "REAL"
    state.allow_real = True
    state.confirm_real = True
    # Stop pode vir do status, do placar da sessão ou do histórico do dia
    # (Shift+O na conta marketing grava em robot_trade_history e o
    # daily_stop_reason bloqueava o start com 403 STOP_*_HIT).
    session_stop = resolve_robot_stop_reason(
        state,
        profit=float(getattr(state, "profit", 0) or 0),
    )
    history_stop = daily_stop_reason(user_id, state)
    stop_blocks = is_stop_status(state.status) or session_stop is not None or history_stop is not None
    if stop_blocks and is_marketing_simulation_session(auth):
        previous_status = state.status
        state = auto_trader.reset_score(user_id)
        persist_robot(user_id)
        logger.info(
            "[MARKETING_AUTO_RESET_SCORE_ON_START] user_id=%s previous_status=%s "
            "session_stop=%s history_stop=%s",
            user_id,
            previous_status,
            session_stop,
            history_stop,
        )
    elif is_stop_status(state.status):
        # Se o placar já não viola o stop (ex.: após "Reiniciar placar"), libera.
        # Caso contrário, pede o reset explícito do placar/ciclo.
        still_blocked = resolve_robot_stop_reason(
            state,
            profit=float(getattr(state, "profit", 0) or 0),
        ) or daily_stop_reason(user_id, state)
        if still_blocked is None:
            logger.info(
                "[STOP_STATUS_CLEARED_ON_START] user_id=%s previous_status=%s",
                user_id,
                state.status,
            )
            state.status = STATUS_STOPPED
            state.rejection_reason = None
            state.last_rejection_reason = None
            state.last_order_error = None
            persist_robot(user_id)
        else:
            return json_response(409, build_error("RESET_CYCLE_REQUIRED"))
    elif session_stop is not None or history_stop is not None:
        return json_response(409, build_error("RESET_CYCLE_REQUIRED"))
    if fresh_robot_connection(state) and state.connected and state.active_mode is not None:
        connected = True
        active_mode = state.active_mode
    else:
        _, _, state, connected, active_mode, _ = await fetch_and_sync_robot_connection(
            user_id,
            allow_session_restore=True,
        )
    if not connected:
        # Tenta soft (SSID) + senha antes de abortar — sem marcar ACCOUNT_DISCONNECTED.
        reconnected = await try_auto_reconnect_with_saved_credentials(user_id)
        if reconnected:
            _, _, state, connected, active_mode, _ = await fetch_and_sync_robot_connection(
                user_id,
                allow_session_restore=True,
            )
    if not connected:
        mark_robot_start_blocked_without_disconnect(user_id, reason="NOT_CONNECTED")
        await stop_robot_worker(user_id)
        return json_response(409, build_error("BULLEX_NOT_CONNECTED"))
    # active_mode=None no status é comum; não aborta aqui — /account confirma REAL.
    if active_mode is not None and active_mode != "REAL":
        logger.warning(
            "[REAL_MODE_NOT_CONFIRMED] user_id=%s active_mode=%s",
            user_id,
            active_mode,
        )
        state = auto_trader.require_real_mode(user_id)
        persist_robot(user_id)
        await stop_robot_worker(user_id)
        return json_response(
            409,
            {
                "ok": False,
                "error": "REAL_MODE_NOT_CONFIRMED",
                "data": build_robot_payload(state, user_id=user_id)["data"],
            },
        )

    try:
        _, account_payload = await call_bullex_service("GET", "/account", user_id)
    except Exception as exc:
        logger.warning(
            "[REAL_MODE_NOT_CONFIRMED] user_id=%s reason=%s",
            user_id,
            exc.__class__.__name__,
        )
        # Timeout/fila no /account ≠ conta desconectada. Mantém sessão e pede retry.
        mark_robot_start_blocked_without_disconnect(
            user_id,
            reason=f"ACCOUNT_FETCH_{exc.__class__.__name__}",
        )
        await stop_robot_worker(user_id)
        return json_response(409, build_error("BULLEX_NOT_CONNECTED"))
    account_contract = build_real_account_contract(account_payload)
    account_data = account_contract["data"] if isinstance(account_contract.get("data"), dict) else {}
    real_balance = (
        number_or_none(account_data.get("balance_real"))
        if account_contract.get("ok")
        else None
    )

    # Sessão morta (SESSION_NOT_FOUND) ou saldo omitido: reconecta ANTES do
    # fallback de memória. Soft (SSID) primeiro; senha só se necessário.
    if not account_contract.get("ok") or real_balance is None:
        reconnected = await try_auto_reconnect_with_saved_credentials(user_id)
        if reconnected:
            try:
                _, account_payload = await call_bullex_service(
                    "GET", "/account", user_id, force_refresh=True
                )
            except Exception as exc:
                logger.warning(
                    "[REAL_BALANCE_RECONNECT_RETRY_FAILED] user_id=%s reason=%s",
                    user_id,
                    exc.__class__.__name__,
                )
            else:
                account_contract = build_real_account_contract(account_payload)
                account_data = (
                    account_contract["data"]
                    if isinstance(account_contract.get("data"), dict)
                    else {}
                )
                if account_contract.get("ok"):
                    real_balance = number_or_none(account_data.get("balance_real"))
                    logger.info(
                        "[REAL_BALANCE_AFTER_RECONNECT] user_id=%s balance=%s",
                        user_id,
                        real_balance,
                    )

    if not account_contract.get("ok"):
        recovered = recover_real_account_contract_for_start(user_id, account_contract)
        if recovered is not None:
            account_contract = recovered
            account_data = (
                account_contract["data"]
                if isinstance(account_contract.get("data"), dict)
                else {}
            )
            real_balance = number_or_none(account_data.get("balance_real"))
        else:
            mark_robot_start_blocked_without_disconnect(
                user_id,
                reason=f"ACCOUNT_CONTRACT:{account_contract.get('error')}",
            )
            await stop_robot_worker(user_id)
            logger.warning(
                "[ROBOT_START_BLOCKED_ACCOUNT_CONTRACT] user_id=%s error=%s",
                user_id,
                account_contract.get("error"),
            )
            return json_response(409, build_error("BULLEX_NOT_CONNECTED"))

    if real_balance is None:
        cached = get_cached_account_snapshot(user_id)
        cached_balance = number_or_none(cached.get("balance"))
        if (
            cached.get("mode") == "REAL"
            and cached_balance is not None
            and float(cached_balance) > 0
        ):
            real_balance = cached_balance
            logger.warning(
                "[REAL_BALANCE_FROM_CACHE] user_id=%s balance=%s",
                user_id,
                real_balance,
            )

    if real_balance is None:
        # Saldo desconhecido ≠ saldo zero e ≠ logout. Mantém a sessão no painel.
        state = mark_robot_start_blocked_without_disconnect(user_id, reason="BALANCE_UNKNOWN")
        await stop_robot_worker(user_id)
        logger.warning(
            "[ROBOT_START_BLOCKED_BALANCE_UNKNOWN] user_id=%s entry_value=%s",
            user_id,
            state.entry_value,
        )
        return json_response(409, build_error("BULLEX_NOT_CONNECTED"))

    if float(real_balance) <= 0:
        state = auto_trader.insufficient_balance(user_id)
        persist_robot(user_id)
        await stop_robot_worker(user_id)
        logger.warning(
            "[ROBOT_START_BLOCKED_INSUFFICIENT_BALANCE] user_id=%s balance=%s entry_value=%s",
            user_id,
            real_balance,
            state.entry_value,
        )
        logger.warning(
            "[INSUFFICIENT_BALANCE_REAL] user_id=%s balance=%s entry_value=%s",
            user_id,
            real_balance,
            state.entry_value,
        )
        return json_response(
            200,
            build_insufficient_balance_start_response(
                state,
                message=INSUFFICIENT_BALANCE_START_MESSAGE,
            ),
        )
    if float(real_balance) < float(state.entry_value):
        state = auto_trader.insufficient_balance(user_id)
        persist_robot(user_id)
        await stop_robot_worker(user_id)
        logger.warning(
            "[ROBOT_START_BLOCKED_INSUFFICIENT_BALANCE] user_id=%s balance=%s entry_value=%s",
            user_id,
            real_balance,
            state.entry_value,
        )
        logger.warning(
            "[INSUFFICIENT_BALANCE_REAL] user_id=%s balance=%s entry_value=%s",
            user_id,
            real_balance,
            state.entry_value,
        )
        return json_response(
            200,
            build_insufficient_balance_start_response(
                state,
                message=ENTRY_VALUE_EXCEEDS_BALANCE_MESSAGE,
            ),
        )
    state = auto_trader.sync_connection(
        user_id,
        connected=True,
        active_mode="REAL",
        source=connection_source_from_payload(account_payload),
        align_status=True,
    )
    if state.account_mode == "REAL":
        logger.info(
            "[REAL MODE DETECTED] user_id=%s active_mode=%s confirm_real=%s",
            user_id,
            state.active_mode,
            state.confirm_real,
        )
        block_reason = real_block_reason(state, connected=connected, active_mode=active_mode, user_id=user_id)
        if block_reason is not None:
            auto_trader.lock_real(user_id, block_reason)
            persist_robot(user_id)
            logger.warning(
                "[REAL BUY BLOCKED reason=%s] user_id=%s",
                block_reason,
                user_id,
            )
            return json_response(403, build_error(block_reason))

    logger.info(
        "[ROBOT_START_VALIDATED] user_id=%s connected=%s active_mode=%s balance=%s entry_value=%s",
        user_id,
        connected,
        active_mode,
        real_balance,
        state.entry_value,
    )
    state = auto_trader.start(user_id)
    robot_worker_restart_attempted.discard(user_id)
    logger.info(
        "[CYCLE_START] user_id=%s cycle_id=%s next_cycle_at=%s cycle_minutes=%s",
        user_id,
        state.cycle_id,
        state.next_cycle_at,
        state.cycle_minutes,
    )
    logger.info(
        "[CYCLE_CONFIG] user_id=%s cycle_minutes=%s source=robot_start",
        user_id,
        state.cycle_minutes,
    )
    persist_robot(user_id)
    ensure_robot_worker(user_id)
    logger.info(
        "[ROBOT_START_NEW_CYCLE] user_id=%s cycle_id=%s current_cycle_started_at=%s next_cycle_at=%s",
        user_id,
        state.cycle_id,
        state.current_cycle_started_at,
        state.next_cycle_at,
    )
    logger.info(
        "[ROBOT_START_DELAYED] user_id=%s next_cycle_at=%s cycle_minutes=%s",
        user_id,
        state.next_cycle_at,
        state.cycle_minutes,
    )
    logger.info("[ROBOT START] user_id=%s", user_id)
    return json_response(200, build_robot_payload(auto_trader.get(user_id), user_id=user_id))


@app.post("/robot/start")
async def robot_start(auth: dict[str, str] = Depends(require_headers)) -> JSONResponse:
    try:
        return await _robot_start_impl(auth)
    except Exception as exc:
        logger.warning(
            "[ROBOT_START_RECOVERED] user_id=%s reason=%s",
            auth.get("user_id"),
            exc.__class__.__name__,
            exc_info=True,
        )
        return json_response(200, build_controlled_upstream_error(exc))


async def _robot_stop_impl(auth: dict[str, str]) -> JSONResponse:
    user_id = auth["user_id"]
    mark_user_active(user_id)
    logger.info("[ROBOT_STOP_REQUEST] user_id=%s", user_id)
    state = auto_trader.stop(user_id)
    persist_robot(user_id)
    await stop_robot_worker(user_id)
    logger.info("[ROBOT STOP] user_id=%s", user_id)
    return json_response(200, build_robot_payload(state, user_id=user_id))


@app.post("/robot/stop")
async def robot_stop(auth: dict[str, str] = Depends(require_headers)) -> JSONResponse:
    try:
        return await _robot_stop_impl(auth)
    except Exception as exc:
        logger.warning(
            "[ROBOT_STOP_RECOVERED] user_id=%s reason=%s",
            auth.get("user_id"),
            exc.__class__.__name__,
            exc_info=True,
        )
        return json_response(200, build_controlled_upstream_error(exc))


@app.post("/robot/reset-cycle")
async def robot_reset_cycle(
    body: dict[str, Any] | None = Body(default=None),
    auth: dict[str, str] = Depends(require_headers),
) -> JSONResponse:
    RobotResetCycleRequest.model_validate(body or {})
    user_id = auth["user_id"]
    async with auto_trader.lock(user_id):
        state = auto_trader.reset_cycle(user_id, reset_score=True, reset_daily_profit=True)
        robot_persistence.clear_finished_trades(user_id)
        robot_persistence.clear_trade_history(user_id)
        persist_robot(user_id)
    await stop_robot_worker(user_id)
    await clear_marketing_simulated_history(user_id)
    logger.info("[RESET_CYCLE] user_id=%s cycle_id=%s", user_id, state.cycle_id)
    logger.info(
        "[ROBOT_RESET_SCORE] user_id=%s wins=%s losses=%s profit=%s",
        user_id,
        state.wins,
        state.losses,
        state.profit,
    )
    logger.info(
        "[ROBOT_CYCLE_RESET] user_id=%s cycle_id=%s status=%s",
        user_id,
        state.cycle_id,
        state.status,
    )
    return json_response(200, build_robot_payload(state, user_id=user_id))


@app.options("/robot/reset-cycle")
async def robot_reset_cycle_options() -> JSONResponse:
    return json_response(200, build_success({"preflight": True}))


async def clear_marketing_simulated_history(user_id: str) -> None:
    """Limpa o histórico sintético da conta marketing (ex.: ao reiniciar o ciclo)."""
    context = _marketing_override_by_user.get(user_id)
    if not context:
        return
    company_id = str(context["company_id"])
    try:
        history = await admin_repository.list_simulated_trades(company_id, user_id, limit=10_000)
        for item in history:
            trade_id = str(item.get("id") or "")
            if trade_id:
                await admin_repository.delete_simulated_trade(company_id, user_id, trade_id)
    except Exception:
        logger.exception("[MARKETING_HISTORY_CLEAR_FAILED] user_id=%s", user_id)


@app.post("/robot/reset-score")
async def robot_reset_score(auth: dict[str, str] = Depends(require_headers)) -> JSONResponse:
    """Zera apenas o placar visual (wins/losses/profit) da sessão.

    Não apaga ``robot_trade_history``, ``robot_trades`` nem o histórico
    sintético de marketing — isso fica para ``POST /robot/reset-cycle``.
    """
    user_id = auth["user_id"]
    async with auto_trader.lock(user_id):
        state = auto_trader.reset_score(user_id)
        persist_robot(user_id)
    logger.info(
        "[ROBOT_SCORE_RESET] user_id=%s wins=%s losses=%s profit=%s",
        user_id,
        state.wins,
        state.losses,
        state.profit,
    )
    return json_response(200, build_robot_payload(state, user_id=user_id))


@app.options("/robot/reset-score")
async def robot_reset_score_options() -> JSONResponse:
    return json_response(200, build_success({"preflight": True}))


@app.post("/robot/tick")
async def robot_tick(
    body: dict[str, Any] | None = Body(default=None),
    auth: dict[str, str] = Depends(require_headers),
) -> JSONResponse:
    """Avança o ciclo do robô.

    Com ``{"force": true}`` ignora o ``next_cycle_at`` e dispara análise imediata
    (útil para diagnóstico após reconectar a Bullex).
    """
    force = bool((body or {}).get("force"))
    if force:
        status_code, payload = await run_analysis_now(auth["user_id"])
    else:
        status_code, payload = await execute_robot_cycle(auth["user_id"])
    return json_response(status_code, payload)


@app.post("/robot/sync-connection")
async def robot_sync_connection(auth: dict[str, str] = Depends(require_headers)) -> JSONResponse:
    user_id = auth["user_id"]
    get_user_robot_state(user_id)
    status_code, _, state, connected, active_mode, source = await fetch_and_sync_robot_connection(user_id)
    persist_robot(user_id)
    account_snapshot = await refresh_account_snapshot_if_needed(
        user_id,
        connected=connected,
        active_mode=active_mode,
    )
    block_reason = real_block_reason(state, connected=connected, active_mode=active_mode, user_id=user_id)
    real_balance_warning = get_real_balance_warning(user_id, state, active_mode)
    return json_response(
        200 if status_code < 500 else status_code,
        build_robot_payload(
            state,
            user_id=user_id,
            connected=connected,
            active_mode=active_mode,
            balance=account_snapshot.get("balance"),
            currency=account_snapshot.get("currency"),
            email=account_snapshot.get("email"),
            connection_checked_at=state.connection_checked_at.isoformat()
            if state.connection_checked_at is not None
            else None,
            connection_status_source=source,
            real_ready=block_reason is None,
            real_block_reason=block_reason,
            real_balance_warning=real_balance_warning,
        ),
    )


@app.post("/robot/execute-demo")
async def robot_execute_demo(auth: dict[str, str] = Depends(require_headers)) -> JSONResponse:
    return json_response(409, build_error("REAL_MODE_ONLY"))


@app.post("/robot/execute-real")
async def robot_execute_real(
    auth: dict[str, str] = Depends(require_headers),
) -> JSONResponse:
    status_code, payload = await execute_robot_cycle(
        auth["user_id"],
        required_mode="REAL",
    )
    return json_response(status_code, payload)


@app.websocket("/ws/market")
async def ws_market(websocket: WebSocket) -> None:
    if (
        websocket.cookies.get("elcapo-support")
        or websocket.cookies.get("__Host-elcapo-support")
        or websocket.cookies.get("elcapo-impersonation")
        or websocket.cookies.get("__Host-elcapo-impersonation")
    ):
        await websocket.accept()
        await close_market_websocket(
            websocket,
            {"type": "error", "error": "SUPPORT_CONTEXT_FORBIDDEN"},
        )
        return
    api_key = normalize_ws_value(websocket.query_params.get("api_key"))
    user_id = normalize_ws_value(websocket.query_params.get("user_id"))
    active = normalize_ws_value(websocket.query_params.get("active"))

    logger.info("[MARKET WS CONNECTING] user_id=%s active=%s", user_id or "<missing>", active or "<missing>")

    if not config.panel_api_key:
        await websocket.accept()
        await close_market_websocket(websocket, {"type": "error", "error": "PANEL_API_KEY_NOT_CONFIGURED"})
        return
    if api_key != config.panel_api_key:
        await websocket.accept()
        await close_market_websocket(websocket, {"type": "error", "error": "INVALID_API_KEY"})
        return
    if not user_id:
        await websocket.accept()
        await close_market_websocket(websocket, {"type": "error", "error": "MISSING_USER_ID"})
        return
    if not active:
        await websocket.accept()
        await close_market_websocket(websocket, {"type": "error", "error": "MISSING_ACTIVE"})
        return
    if not is_binary_asset_allowed(active):
        await websocket.accept()
        await close_market_websocket(websocket, build_error(ASSET_NOT_ALLOWED))
        return
    active = normalize_binary_active(active)

    await manager.connect(user_id, active, websocket)
    logger.info("[MARKET WS CONNECTED] user_id=%s active=%s", user_id, active)

    try:
        await stream_market_updates(websocket, user_id, active)
    except WebSocketDisconnect:
        logger.info("[MARKET WS DISCONNECTED] user_id=%s active=%s", user_id, active)
    except Exception:
        logger.exception("[MARKET WS ERROR] user_id=%s active=%s error=UNHANDLED_WEBSOCKET_EXCEPTION", user_id, active)
        try:
            await websocket.send_json({"type": "warning", "error": "MARKET_STREAM_TEMPORARY_ERROR"})
        except Exception:
            logger.exception("falha ao enviar warning final do websocket de mercado")
    finally:
        await manager.disconnect(user_id, active, websocket)
        logger.info("[MARKET WS DISCONNECTED] user_id=%s active=%s", user_id, active)


async def _bullex_connect_impl(
    body: dict[str, Any],
    auth: dict[str, str],
) -> JSONResponse:
    user_id = auth["user_id"]
    # Conexão pedida pelo cliente: encerra o bloqueio da desconexão manual.
    bullex_manual_disconnect.discard(user_id)
    logger.info("[CONNECT_REQUEST] user_id=%s", user_id)
    remaining = bullex_login_rate_limit_remaining()
    if remaining > 0:
        logger.warning(
            "[BULLEX_LOGIN_RATE_LIMIT] user_id=%s retry_after=%s source=gateway_gate",
            user_id,
            remaining,
        )
        return json_response(
            200,
            build_controlled_upstream_error(
                BULLEX_REQUESTS_LIMIT_EXCEEDED,
                data={"retry_after_seconds": remaining},
            ),
        )
    mark_user_active(user_id)
    # Limpa cache desconectado ANTES do login para o status pós-connect
    # não voltar a servir connected:false antigo.
    reset_session_connection_cache(user_id)
    logger.info("[CONNECT_BACKOFF_CLEARED] user_id=%s", user_id)
    logger.info("[CONNECT_ATTEMPT] user_id=%s", user_id)
    logger.info("[REAL_MODE_REQUESTED] user_id=%s", user_id)
    connect_body = {
        **body,
        "account_mode": "REAL",
        "mode": "REAL",
    }
    status_code, payload = await call_bullex_service(
        "POST",
        "/sessions/connect",
        user_id,
        json_body=connect_body,
    )
    payload = normalize_service_payload(payload)
    if status_code >= 500:
        error = str(payload.get("error") or "").strip().upper()
        logger.warning(
            "[CONNECT_UPSTREAM_HANDLED] user_id=%s upstream_status=%s",
            user_id,
            status_code,
        )
        if error == "LOGIN_TIMEOUT":
            logger.warning("[CONNECT_TIMEOUT_HANDLED] user_id=%s source=gateway_endpoint", user_id)
        code, retry_after, _ = classify_bullex_connect_error(
            error or BULLEX_TEMPORARY_UNAVAILABLE,
            payload.get("data") if isinstance(payload.get("data"), dict) else None,
        )
        if code == BULLEX_REQUESTS_LIMIT_EXCEEDED:
            note_bullex_login_rate_limit(user_id, retry_after)
        logger.warning(
            "[CONNECT_FAILED_HANDLED] user_id=%s detail=%s",
            user_id,
            code,
        )
        return json_response(
            200,
            build_controlled_upstream_error(
                code,
                data=payload.get("data") if isinstance(payload.get("data"), dict) else None,
            ),
        )
    if not payload.get("ok"):
        detail = payload.get("error") or "LOGIN_FAILED"
        data = payload.get("data") if isinstance(payload.get("data"), dict) else None
        code, retry_after, _ = classify_bullex_connect_error(detail, data)
        logger.warning("[CONNECT_FAILED_HANDLED] user_id=%s detail=%s", user_id, code)
        if code == BULLEX_REQUESTS_LIMIT_EXCEEDED:
            note_bullex_login_rate_limit(user_id, retry_after)
        if detail in {
            "BULLEX_ACCOUNT_STILL_PRACTICE",
            "BULLEX_ACTIVE_MODE_NOT_REAL",
            "REAL_BALANCE_NOT_DETECTED",
        }:
            state = auto_trader.require_real_mode(user_id)
            persist_robot(user_id)
            await stop_robot_worker(user_id)
            return json_response(
                200,
                {
                    **payload,
                    "data": {
                        **(payload.get("data") if isinstance(payload.get("data"), dict) else {}),
                        "robot": build_robot_payload(state, user_id=user_id)["data"],
                    },
                },
            )
        return json_response(200, build_controlled_upstream_error(detail, data=data))
    sync_user_store_from_payload(
        user_id,
        payload,
        connect_body.get("email"),
        is_new_connection=True,
    )
    if payload.get("ok"):
        persist_bullex_credentials(
            user_id,
            str(connect_body.get("email") or ""),
            str(connect_body.get("password") or ""),
        )
    connected, active_mode = extract_account_status(payload)
    # Só bloqueia PRACTICE/DEMO explícito. Sem modo no payload, segue e deixa
    # o sync/account resolver (mesmo padrão do start e do status).
    if connected and active_mode is not None and active_mode != "REAL":
        logger.warning(
            "[REAL_MODE_NOT_CONFIRMED] user_id=%s active_mode=%s",
            user_id,
            active_mode,
        )
        state = auto_trader.require_real_mode(user_id)
        persist_robot(user_id)
        await stop_robot_worker(user_id)
        return json_response(
            200,
            {
                "ok": False,
                "data": {
                    "connected": connected,
                    "active_mode_from_bullex": active_mode,
                    "robot": build_robot_payload(state, user_id=user_id)["data"],
                },
                "error": "BULLEX_ACCOUNT_STILL_PRACTICE",
            },
        )
    if payload.get("ok") and connected:
        logger.info("[REAL_MODE_CONFIRMED] user_id=%s active_mode=%s", user_id, active_mode)
        state = auto_trader.get(user_id)
        state.account_mode = "REAL"
        state.allow_real = True
        state.confirm_real = True
        state = auto_trader.sync_connection(
            user_id,
            connected=True,
            active_mode=active_mode,
            source="bullex_service",
            align_status=True,
        )
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        seed_connected_session_cache(
            user_id,
            active_mode=active_mode,
            email=str(data.get("email") or connect_body.get("email") or "") or None,
            balance=(
                float(data["balance"])
                if data.get("balance") is not None
                else None
            ),
            currency=str(data.get("currency") or "") or None,
        )
        if user_id in robot_state_hydrated_users or state.enabled:
            persist_robot(user_id)
        logger.info(
            "[BULLEX_CONNECTED] user_id=%s active_mode=%s",
            user_id,
            active_mode,
        )
        logger.info(
            "[ROBOT_CONNECTION_SYNCED] user_id=%s connected=true active_mode=%s source=bullex_service",
            user_id,
            active_mode,
        )
        if state.enabled:
            # Após reconectar a corretora, retoma o worker se o robô já estava ligado.
            ensure_robot_worker(user_id)
            logger.info("[ROBOT_WORKER_RESUMED_AFTER_CONNECT] user_id=%s", user_id)
        if isinstance(payload.get("data"), dict):
            payload["data"]["robot"] = build_robot_payload(
                state,
                connected=True,
                active_mode=active_mode,
                connection_checked_at=state.connection_checked_at.isoformat()
                if state.connection_checked_at is not None
                else None,
                connection_status_source=state.connection_status_source,
            )["data"]
        logger.info("[CONNECT_SUCCESS] user_id=%s active_mode=%s", user_id, active_mode)
    return json_response(status_code, attach_credentials_meta(payload, user_id))


@app.post("/bullex/connect")
async def bullex_connect(
    body: dict[str, Any],
    auth: dict[str, str] = Depends(require_headers),
) -> JSONResponse:
    try:
        return await _bullex_connect_impl(body, auth)
    except Exception as exc:
        logger.warning(
            "[CONNECT_FAILED_HANDLED] user_id=%s detail=%s",
            auth.get("user_id"),
            exc.__class__.__name__,
        )
        logger.warning(
            "[CONNECT_RECOVERED] user_id=%s reason=%s",
            auth.get("user_id"),
            exc.__class__.__name__,
            exc_info=True,
        )
        return json_response(200, build_controlled_upstream_error(exc))


async def _bullex_status_impl(auth: dict[str, str]) -> JSONResponse:
    user_id = auth["user_id"]
    mark_user_active(user_id)
    try:
        status_code, payload = await call_bullex_service("GET", "/sessions/status", user_id)
        payload = normalize_service_payload(payload)
    except Exception as exc:
        logger.warning(
            "[UPSTREAM_ERROR_HANDLED] user_id=%s path=/sessions/status reason=%s",
            user_id,
            exc.__class__.__name__,
            exc_info=True,
        )
        # Upstream fora: tenta login salvo antes do cache em memória.
        if await try_auto_reconnect_with_saved_credentials(user_id):
            restored = await refresh_connected_status_after_auto_reconnect(user_id)
            if restored is not None:
                return json_response(200, restored)
        fallback = memory_status_fallback(user_id)
        fallback_data = fallback.get("data") if isinstance(fallback, dict) else None
        if isinstance(fallback_data, dict) and fallback_data.get("connected") is True:
            return json_response(200, fallback)
        return json_response(200, build_controlled_upstream_error(exc))
    if payload.get("ok") and isinstance(payload.get("data"), dict) and payload["data"].get("status") == "backoff":
        recovered = resolve_backoff_panel_payload(user_id, path="/sessions/status")
        if recovered is not None:
            logger.warning(
                "[STATUS_BACKOFF_PANEL_RECOVERED] user_id=%s source=memory_or_grace",
                user_id,
            )
            return json_response(200, attach_credentials_meta(recovered, user_id))
        return json_response(200, attach_credentials_meta(payload, user_id))
    connected, active_mode = extract_account_status(payload)
    if payload.get("ok") and connected:
        # Só derruba o worker com PRACTICE/DEMO explícito. active_mode=None
        # (status sem modo) NÃO pode desligar operação recém-iniciada.
        if active_mode is not None and active_mode != "REAL":
            await stop_robot_worker(user_id)
        return json_response(200, finalize_connected_status_payload(user_id, payload, active_mode))

    session_dead = (
        status_code == 404
        or is_session_disconnected(payload)
        or payload_connected_state(payload) is False
    )
    if session_dead:
        # Entrada no painel / SSID morto: reconecta com credenciais salvas
        # ANTES de servir cache em memória (que pode mentir "conectado").
        if await try_auto_reconnect_with_saved_credentials(user_id):
            restored = await refresh_connected_status_after_auto_reconnect(user_id)
            if restored is not None:
                return json_response(200, restored)
        fallback = memory_status_fallback(user_id)
        if fallback is not None:
            logger.warning(
                "[UPSTREAM_ERROR_HANDLED] user_id=%s path=/sessions/status reason=%s",
                user_id,
                payload.get("error") or "connected_false",
            )
            return json_response(200, fallback)
        return json_response(
            200,
            attach_credentials_meta(build_success({"connected": False}), user_id),
        )

    fallback = memory_status_fallback(user_id)
    if fallback is not None:
        logger.warning(
            "[UPSTREAM_ERROR_HANDLED] user_id=%s path=/sessions/status reason=%s",
            user_id,
            payload.get("error") or "connected_false",
        )
        return json_response(200, fallback)
    return json_response(
        200,
        build_controlled_upstream_error(payload.get("error") or BULLEX_TEMPORARY_UNAVAILABLE),
    )


@app.get("/bullex/status")
async def bullex_status(auth: dict[str, str] = Depends(require_headers)) -> JSONResponse:
    try:
        response = await _bullex_status_impl(auth)
        response.headers.update(polling_headers(SESSION_STATUS_MIN_POLL_SECONDS))
        return response
    except Exception as exc:
        logger.warning(
            "[STATUS_RECOVERED] user_id=%s reason=%s",
            auth.get("user_id"),
            exc.__class__.__name__,
            exc_info=True,
        )
        return json_response(
            200,
            build_controlled_upstream_error(exc),
            headers=polling_headers(SESSION_STATUS_MIN_POLL_SECONDS),
        )


@app.get("/bullex/balance")
async def bullex_balance(auth: dict[str, str] = Depends(require_headers)) -> JSONResponse:
    response = await _bullex_account_impl(auth)
    response.headers.update(polling_headers(ACCOUNT_MIN_POLL_SECONDS))
    return response


@app.post("/bullex/change-mode")
async def bullex_change_mode(
    body: dict[str, Any],
    auth: dict[str, str] = Depends(require_headers),
) -> JSONResponse:
    status_code, payload = await call_bullex_service(
        "POST",
        "/account/change-mode",
        auth["user_id"],
        json_body={"mode": "REAL", "confirm_real": True},
    )
    sync_user_store_from_payload(auth["user_id"], payload)
    return json_response(status_code, payload)


@app.get("/bullex/assets")
async def bullex_assets(auth: dict[str, str] = Depends(require_headers)) -> JSONResponse:
    user_id = auth["user_id"]
    retry_remaining = assets_retry_remaining(user_id)
    if retry_remaining is not None:
        cached_payload = get_cached_assets_payload(user_id) or get_snapshot_assets_payload(user_id)
        if cached_payload is not None:
            logger.info(
                "[ASSETS_CACHE_HIT] user_id=%s source=%s retry_in=%.2f",
                user_id,
                ((cached_payload.get("meta") or {}).get("source") or "cache"),
                retry_remaining,
            )
            return json_response(200, cached_payload)

    status_code, payload = await call_bullex_service("GET", "/assets", user_id)
    payload = normalize_service_payload(payload)
    log_ignored_disconnect(user_id, "/assets", payload)
    if payload.get("ok") and isinstance(payload.get("data"), list):
        allowed_assets = normalize_allowed_assets_list(payload.get("data"))
        clear_assets_backoff(user_id)
        payload = build_assets_payload(allowed_assets, source="bullex_service", stale=False)
        get_session_cache(user_id).responses["/assets"] = BullexResponseCacheEntry(
            status_code=200,
            payload=payload,
            expires_at=utc_now() + timedelta(seconds=ASSETS_CACHE_TTL_SECONDS),
        )
        try:
            user_store.save_market_assets_snapshot(user_id, allowed_assets)
        except Exception:
            logger.exception("falha ao salvar snapshot de market_assets para %s", user_id)
        return json_response(200, payload)

    retry_seconds = schedule_assets_retry(user_id)
    cached_payload = get_cached_assets_payload(user_id) or get_snapshot_assets_payload(user_id)
    if cached_payload is not None:
        if account_still_connected(user_id):
            logger.info("[ACCOUNT_STILL_CONNECTED] user_id=%s source=assets_failure", user_id)
        logger.warning(
            "[ASSETS_FETCH_FAILED_USING_CACHE] user_id=%s retry_in=%ss error=%s",
            user_id,
            retry_seconds,
            payload.get("error"),
        )
        return json_response(200, cached_payload)

    if account_still_connected(user_id):
        logger.info("[ACCOUNT_STILL_CONNECTED] user_id=%s source=assets_failure_no_cache", user_id)
    logger.warning(
        "[ASSETS_FETCH_FAILED_USING_CACHE] user_id=%s retry_in=%ss error=%s source=empty_fallback",
        user_id,
        retry_seconds,
        payload.get("error"),
    )
    return json_response(
        200,
        build_assets_payload([], source="empty_fallback", stale=True),
    )


@app.get("/bullex/candles")
async def bullex_candles(
    active: str | None = None,
    interval: int | None = None,
    count: int | None = None,
    endtime: int | None = None,
    symbol: str | None = None,
    timeframe: str | None = None,
    limit: int | None = None,
    auth: dict[str, str] = Depends(require_headers),
) -> JSONResponse:
    resolved_symbol = normalize_binary_active(symbol or active or "")
    if resolved_symbol not in CHART_ALLOWED_ASSET_SET:
        logger.warning(
            "[CANDLES_ERROR_HANDLED] user_id=%s symbol=%s error=%s",
            auth["user_id"],
            resolved_symbol,
            ASSET_NOT_ALLOWED,
        )
        return json_response(200, build_chart_candles_unavailable())
    user_id = auth["user_id"]
    try:
        resolved_timeframe, resolved_interval = normalize_timeframe_seconds(timeframe, interval)
        resolved_limit = max(1, min(int(limit or count or 60), 500))
    except (TypeError, ValueError) as exc:
        logger.warning(
            "[CANDLES_ERROR_HANDLED] user_id=%s symbol=%s reason=%s",
            user_id,
            resolved_symbol,
            type(exc).__name__,
        )
        return json_response(200, build_chart_candles_unavailable())
    cache_key = (user_id, resolved_symbol, resolved_timeframe)
    params = {
        "active": resolved_symbol,
        "interval": resolved_interval,
        "count": resolved_limit,
    }
    if endtime is not None:
        params["endtime"] = endtime
    logger.info(
        "[CANDLES_FETCH] user_id=%s symbol=%s timeframe=%s count=%s",
        user_id,
        resolved_symbol,
        resolved_timeframe,
        resolved_limit,
    )
    timed_out = False
    try:
        status_code, payload = await asyncio.wait_for(
            call_bullex_service(
                "GET",
                "/candles",
                user_id,
                params=params,
                force_refresh=True,
            ),
            timeout=CANDLES_REQUEST_TIMEOUT_SECONDS,
        )
        payload = normalize_service_payload(
            payload,
            error="CANDLES_TEMPORARY_UNAVAILABLE",
        )
        if payload.get("ok"):
            server_timestamp = extract_server_timestamp(payload) or utc_now().timestamp()
            live_payload = build_live_candles_payload(
                resolved_symbol,
                resolved_timeframe,
                resolved_interval,
                resolved_limit,
                float(server_timestamp),
                payload,
            )
            chart_candles_cache[cache_key] = deepcopy(live_payload)
            logger.info(
                "[CANDLES_OK] user_id=%s symbol=%s candles=%s",
                user_id,
                resolved_symbol,
                len(live_payload["candles"]),
            )
            return json_response(
                200,
                build_chart_candles_success(
                    live_payload,
                    from_cache=False,
                    limit=resolved_limit,
                ),
            )
        logger.warning(
            "[CANDLES_ERROR_HANDLED] user_id=%s symbol=%s status=%s error=%s",
            user_id,
            resolved_symbol,
            status_code,
            payload.get("error"),
        )
    except asyncio.TimeoutError:
        timed_out = True
        logger.warning(
            "[CANDLES_TIMEOUT_HANDLED] user_id=%s symbol=%s timeout_seconds=%s",
            user_id,
            resolved_symbol,
            CANDLES_REQUEST_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        logger.warning(
            "[CANDLES_ERROR_HANDLED] user_id=%s symbol=%s reason=%s",
            user_id,
            resolved_symbol,
            type(exc).__name__,
            exc_info=True,
        )

    cached = chart_candles_cache.get(cache_key)
    if cached is not None:
        logger.info(
            "[CANDLES_CACHE_RETURNED] user_id=%s symbol=%s timeframe=%s candles=%s",
            user_id,
            resolved_symbol,
            resolved_timeframe,
            len(cached.get("candles") or []),
        )
        return json_response(
            200,
            build_chart_candles_success(
                cached,
                from_cache=True,
                limit=resolved_limit,
            ),
        )
    if not timed_out:
        logger.warning(
            "[CANDLES_ERROR_HANDLED] user_id=%s symbol=%s error=CANDLES_TEMPORARY_UNAVAILABLE",
            user_id,
            resolved_symbol,
        )
    return json_response(200, build_chart_candles_unavailable())


@app.get("/debug/candles-live")
async def debug_candles_live(
    symbol: str,
    timeframe: str = "M1",
    auth: dict[str, str] = Depends(require_headers),
) -> JSONResponse:
    response = await bullex_candles(
        symbol=symbol,
        timeframe=timeframe,
        limit=60,
        auth=auth,
    )
    payload = json.loads(response.body)
    if not payload.get("ok"):
        return response
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    candles = data.get("candles") if isinstance(data.get("candles"), list) else []
    last_candle = candles[-1] if candles else {}
    server_time = float(data.get("server_time") or utc_now().timestamp())
    last_candle_time = numeric_candle_time(last_candle) if isinstance(last_candle, dict) else None
    age_seconds = None if last_candle_time is None else max(0, round(server_time - last_candle_time, 3))
    _, interval = normalize_timeframe_seconds(timeframe)
    return json_response(
        200,
        build_success(
            {
                "symbol": normalize_binary_active(symbol),
                "timeframe": timeframe,
                "last_candle_time": int(last_candle_time) if last_candle_time is not None else None,
                "last_close": last_candle.get("close") if isinstance(last_candle, dict) else None,
                "server_time": server_time,
                "age_seconds": age_seconds,
                "is_realtime": bool(age_seconds is not None and age_seconds <= interval),
            }
        ),
    )


@app.get("/bullex/payouts")
async def bullex_payouts(
    active: str | None = None,
    auth: dict[str, str] = Depends(require_headers),
) -> JSONResponse:
    if active is not None and not is_binary_asset_allowed(active):
        return json_response(400, build_error(ASSET_NOT_ALLOWED))
    active = normalize_binary_active(active) if active is not None else None
    params = {"active": active} if active is not None else None
    user_id = auth["user_id"]
    status_code, payload = await call_bullex_service("GET", "/payouts", user_id, params=params)
    payload = normalize_service_payload(
        payload,
        error="PAYOUTS_TEMPORARY_UNAVAILABLE",
    )
    log_ignored_disconnect(user_id, "/payouts", payload)
    if payload.get("ok") and active and isinstance(payload.get("data"), list):
        payout_item = next(
            (
                item
                for item in payload["data"]
                if isinstance(item, dict) and item.get("symbol") == active and item.get("payout") is not None
            ),
            None,
        )
        if payout_item is not None:
            try:
                user_store.save_market_asset_payout(user_id, active, payout_item.get("payout"))
            except Exception:
                logger.exception("falha ao salvar payout de market_assets para %s %s", user_id, active)
    if payload.get("ok"):
        return json_response(200, payload)
    cached = cached_successful_response(user_id, build_cache_key("/payouts", params))
    if cached is not None:
        logger.info("[PAYOUT_CACHE_RETURNED] user_id=%s active=%s", user_id, active)
        return json_response(200, add_stale_warning(cached.payload))
    return json_response(
        200,
        {
            "ok": False,
            "data": [],
            "error": "PAYOUTS_TEMPORARY_UNAVAILABLE",
        },
    )


@app.post("/bullex/buy-demo")
async def bullex_buy_demo(
    body: dict[str, Any],
    auth: dict[str, str] = Depends(require_headers),
) -> JSONResponse:
    return json_response(409, build_error("REAL_MODE_ONLY"))


@app.post("/bullex/buy-real")
async def bullex_buy_real(
    body: dict[str, Any],
    auth: dict[str, str] = Depends(require_headers),
) -> JSONResponse:
    user_id = auth["user_id"]
    state = get_user_robot_state(user_id)
    logger.info(
        "[REAL MODE DETECTED] user_id=%s account_mode=%s confirm_real=%s",
        user_id,
        state.account_mode,
        body.get("confirm_real"),
    )
    logger.info(
        "[REAL BUY ATTEMPT] user_id=%s active=%s amount=%s",
        user_id,
        body.get("active"),
        body.get("amount"),
    )
    block_reason = real_buy_gateway_block_reason(user_id, state, body)
    if block_reason is not None:
        logger.warning("[REAL BUY BLOCKED reason=%s] user_id=%s", block_reason, user_id)
        return json_response(403, build_error(block_reason))

    status_code, payload = await call_bullex_service(
        "POST",
        "/orders/buy-real",
        user_id,
        json_body=body,
    )
    if payload.get("ok"):
        order_data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        logger.info("[REAL BUY SUCCESS order_id=%s] user_id=%s", order_data.get("order_id"), user_id)
    else:
        logger.warning(
            "[REAL BUY BLOCKED reason=%s] user_id=%s",
            payload.get("error") or "ORDER_FAILED",
            user_id,
        )
    return json_response(status_code, payload)


@app.post("/bullex/disconnect")
async def bullex_disconnect(auth: dict[str, str] = Depends(require_headers)) -> JSONResponse:
    user_id = auth["user_id"]
    # ANTES da chamada: o poll do painel corre em paralelo e poderia disparar o
    # auto-reconnect na janela entre desconectar e marcar.
    bullex_manual_disconnect.add(user_id)
    status_code, payload = await call_bullex_service("POST", "/sessions/disconnect", user_id)
    payload = normalize_service_payload(payload)
    auto_trader.disconnect_account(user_id)
    persist_robot(user_id)
    await stop_robot_worker(user_id)
    await manager.disconnect_user(user_id)
    # Sempre limpa o snapshot local — evita GET /account restaurar "conectado" após o clique.
    # Credenciais criptografadas PERMANECEM para auto-reconexão (usar DELETE /bullex/credentials).
    try:
        user_store.disconnect(user_id)
    except Exception:
        logger.exception("[BULLEX_DISCONNECT_STORE_FAILED] user_id=%s", user_id)
    # NÃO usar mark_session_failure(force_offline): ele preservava /account REAL
    # e o painel voltava para Conectado com email/saldo "—".
    apply_manual_disconnect_session_state(user_id)
    logger.info(
        "[BULLEX_DISCONNECT] user_id=%s upstream_ok=%s status_code=%s credentials_kept=%s",
        user_id,
        bool(payload.get("ok")),
        status_code,
        credentials_saved_flag(user_id),
    )
    return json_response(
        200,
        build_success(
            {
                "connected": False,
                "status": "disconnected",
                "credentials_saved": credentials_saved_flag(user_id),
            }
        ),
    )


@app.get("/bullex/credentials")
async def bullex_credentials_status(
    auth: dict[str, str] = Depends(require_headers),
) -> JSONResponse:
    """Retorna se há login Bullex salvo (nunca devolve a senha)."""
    user_id = auth["user_id"]
    email = None
    try:
        record = user_store.get_user(user_id)
        if record is not None:
            email = record.bullex_email
    except Exception:
        logger.warning("[BULLEX_CREDENTIALS_STATUS_FAILED] user_id=%s", user_id, exc_info=True)
    return json_response(
        200,
        build_success(
            {
                "credentials_saved": credentials_saved_flag(user_id),
                "email": email,
            }
        ),
    )


@app.delete("/bullex/credentials")
async def bullex_credentials_forget(
    auth: dict[str, str] = Depends(require_headers),
) -> JSONResponse:
    """Esquece email/senha Bullex salvos (esquecimento explícito do cliente)."""
    user_id = auth["user_id"]
    if bullex_credentials_service is not None:
        try:
            bullex_credentials_service.clear(user_id)
        except Exception:
            logger.warning("[BULLEX_CREDENTIALS_CLEAR_FAILED] user_id=%s", user_id, exc_info=True)
            return json_response(200, build_controlled_upstream_error("CREDENTIALS_CLEAR_FAILED"))
    return json_response(200, build_success({"credentials_saved": False}))


@app.post("/bullex/reconnect")
async def bullex_reconnect(auth: dict[str, str] = Depends(require_headers)) -> JSONResponse:
    user_id = auth["user_id"]
    # Reconexão pedida pelo cliente: encerra o bloqueio da desconexão manual.
    bullex_manual_disconnect.discard(user_id)
    clear_session_backoff(user_id)
    # Preferência: reconectar sessão viva; se falhar e houver credenciais, faz login novo.
    status_code, payload = await call_bullex_service("POST", "/sessions/reconnect", user_id)
    payload = normalize_service_payload(payload)
    connected, _ = extract_account_status(payload)
    if not connected:
        if await try_auto_reconnect_with_saved_credentials(user_id):
            _, payload = await call_bullex_service("GET", "/sessions/status", user_id)
            payload = normalize_service_payload(payload)
            status_code = 200
        else:
            # Nunca vazar o status cru do upstream (404/409) pro painel: sem
            # isso, o frontend recebia "Erro 404" em vez do contrato
            # {ok:false,error:...} e desistia de tentar de novo sozinho.
            # Ver BULLEX_CREDENCIAIS.md "Reconexão sem vazar status cru".
            logger.warning(
                "[BULLEX_RECONNECT_FALLBACK_FAILED] user_id=%s upstream_status=%s upstream_error=%s",
                user_id,
                status_code,
                payload.get("error"),
            )
            retry_after = bullex_login_rate_limit_remaining() or BULLEX_AUTO_RECONNECT_COOLDOWN_SECONDS
            payload = build_controlled_upstream_error(
                payload.get("error") or "BULLEX_RECONNECT_PENDING",
                data={"retry_after_seconds": retry_after},
            )
            status_code = 200
    sync_user_store_from_payload(user_id, payload)
    return json_response(status_code, attach_credentials_meta(payload, user_id))


async def _bullex_account_impl(auth: dict[str, str]) -> JSONResponse:
    user_id = auth["user_id"]
    mark_user_active(user_id)
    try:
        status_code, payload = await call_bullex_service("GET", "/account", user_id)
        payload = normalize_service_payload(
            payload,
            error="ACCOUNT_TEMPORARY_UNAVAILABLE",
        )
    except Exception as exc:
        logger.warning(
            "[UPSTREAM_ERROR_HANDLED] user_id=%s path=/account reason=%s",
            user_id,
            exc.__class__.__name__,
            exc_info=True,
        )
        if await try_auto_reconnect_with_saved_credentials(user_id):
            restored = await refresh_connected_account_after_auto_reconnect(user_id)
            if restored is not None:
                return json_response(200, restored)
        fallback = memory_account_fallback(user_id)
        if fallback is not None:
            logger.warning(
                "[ACCOUNT_FETCH_FALLBACK] user_id=%s source=memory reason=%s",
                user_id,
                exc.__class__.__name__,
            )
            contract = build_real_account_contract(fallback)
        else:
            contract = build_real_account_contract(build_success({"connected": False}))
        contract = finalize_account_contract_with_poll_recovery(user_id, contract)
        if contract.get("ok"):
            return json_response(200, attach_credentials_meta(contract, user_id))
        if should_stop_robot_for_account_contract(user_id, contract):
            state = auto_trader.require_real_mode(user_id)
            persist_robot(user_id)
            await stop_robot_worker(user_id)
            contract["data"]["robot"] = build_robot_payload(state, user_id=user_id)["data"]
        else:
            state = auto_trader.get(user_id)
            contract["data"]["robot"] = build_robot_payload(state, user_id=user_id)["data"]
        return json_response(200, attach_credentials_meta(contract, user_id))
    if payload.get("ok") and isinstance(payload.get("data"), dict) and payload["data"].get("status") == "backoff":
        recovered = resolve_backoff_panel_payload(user_id, path="/account")
        if recovered is not None:
            logger.warning(
                "[ACCOUNT_BACKOFF_PANEL_RECOVERED] user_id=%s source=memory_or_grace",
                user_id,
            )
            contract = finalize_account_contract_with_poll_recovery(
                user_id, build_real_account_contract(recovered)
            )
            if contract.get("ok"):
                return json_response(200, attach_credentials_meta(contract, user_id))
            return json_response(200, attach_credentials_meta(recovered, user_id))
        return json_response(200, attach_credentials_meta(payload, user_id))
    if isinstance(payload.get("data"), dict):
        contract = build_real_account_contract(payload)
        if contract.get("ok"):
            sync_user_store_from_payload(user_id, contract)
            auto_trader.sync_connection(
                user_id,
                connected=True,
                active_mode="REAL",
                source=connection_source_from_payload(payload),
                align_status=True,
            )
            return json_response(200, attach_credentials_meta(contract, user_id))
        # Conta não REAL / sem saldo: tenta login salvo se a sessão estiver morta.
        if status_code == 404 or is_session_disconnected(payload) or payload_connected_state(payload) is False:
            if await try_auto_reconnect_with_saved_credentials(user_id):
                restored = await refresh_connected_account_after_auto_reconnect(user_id)
                if restored is not None:
                    return json_response(200, restored)
        contract = finalize_account_contract_with_poll_recovery(
            user_id, contract, upstream_payload=payload
        )
        if contract.get("ok"):
            return json_response(200, attach_credentials_meta(contract, user_id))
        data = contract["data"]
        if should_stop_robot_for_account_contract(user_id, contract):
            state = auto_trader.require_real_mode(user_id)
            persist_robot(user_id)
            await stop_robot_worker(user_id)
            data["robot"] = build_robot_payload(state, user_id=user_id)["data"]
        else:
            data["robot"] = build_robot_payload(auto_trader.get(user_id), user_id=user_id)["data"]
        return json_response(200, attach_credentials_meta(contract, user_id))
    fallback = memory_account_fallback(user_id)
    if fallback is not None:
        logger.warning(
            "[ACCOUNT_FETCH_FALLBACK] user_id=%s source=memory reason=%s",
            user_id,
            payload.get("error") or "connected_false",
        )
        contract = finalize_account_contract_with_poll_recovery(
            user_id, build_real_account_contract(fallback)
        )
        if contract.get("ok"):
            return json_response(200, attach_credentials_meta(contract, user_id))
        if should_stop_robot_for_account_contract(user_id, contract):
            state = auto_trader.require_real_mode(user_id)
            persist_robot(user_id)
            await stop_robot_worker(user_id)
            contract["data"]["robot"] = build_robot_payload(state, user_id=user_id)["data"]
        else:
            contract["data"]["robot"] = build_robot_payload(
                auto_trader.get(user_id), user_id=user_id
            )["data"]
        return json_response(200, attach_credentials_meta(contract, user_id))
    if status_code == 404 or is_session_disconnected(payload) or payload_connected_state(payload) is False:
        if await try_auto_reconnect_with_saved_credentials(user_id):
            restored = await refresh_connected_account_after_auto_reconnect(user_id)
            if restored is not None:
                return json_response(200, restored)
        contract = build_real_account_contract(build_success({"connected": False}))
    else:
        contract = build_real_account_contract(payload)
    contract = finalize_account_contract_with_poll_recovery(
        user_id, contract, upstream_payload=payload
    )
    if contract.get("ok"):
        return json_response(200, attach_credentials_meta(contract, user_id))
    if should_stop_robot_for_account_contract(user_id, contract):
        state = auto_trader.require_real_mode(user_id)
        persist_robot(user_id)
        await stop_robot_worker(user_id)
        contract["data"]["robot"] = build_robot_payload(state, user_id=user_id)["data"]
    else:
        contract["data"]["robot"] = build_robot_payload(
            auto_trader.get(user_id), user_id=user_id
        )["data"]
    return json_response(200, attach_credentials_meta(contract, user_id))


@app.get("/bullex/account")
async def bullex_account(auth: dict[str, str] = Depends(require_headers)) -> JSONResponse:
    try:
        response = await _bullex_account_impl(auth)
        response.headers.update(polling_headers(ACCOUNT_MIN_POLL_SECONDS))
        return response
    except Exception as exc:
        logger.warning(
            "[ACCOUNT_RECOVERED] user_id=%s reason=%s",
            auth.get("user_id"),
            exc.__class__.__name__,
            exc_info=True,
        )
        return json_response(
            200,
            build_controlled_upstream_error(exc),
            headers=polling_headers(ACCOUNT_MIN_POLL_SECONDS),
        )


@app.get("/bullex/order-result/{order_id}")
async def bullex_order_result(order_id: str, auth: dict[str, str] = Depends(require_headers)) -> JSONResponse:
    status_code, payload = await call_bullex_service("GET", f"/orders/{order_id}/result", auth["user_id"])
    return json_response(status_code, payload, headers=polling_headers(ORDER_RESULT_CACHE_TTL_SECONDS))


@app.get("/feedbacks")
async def list_public_feedbacks(
    limit: int = Query(default=10, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    auth: dict[str, str] = Depends(require_headers),
) -> JSONResponse:
    """Lista feedbacks aprovados (visíveis a todos os usuários autenticados)."""
    _ = auth
    items = await asyncio.to_thread(
        feedback_store.list_approved,
        limit=limit + 1,
        offset=offset,
    )
    page = build_feedback_page(items, limit, offset, public_feedback_view)
    return json_response(200, build_success(page))


@app.get("/feedbacks/mine")
async def list_my_feedbacks(
    limit: int = Query(default=10, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    auth: dict[str, str] = Depends(require_headers),
) -> JSONResponse:
    """Lista feedbacks do usuário autenticado (incluindo pendentes/rejeitados)."""
    items = await asyncio.to_thread(
        feedback_store.list_by_user,
        auth["user_id"],
        limit=limit + 1,
        offset=offset,
    )
    page = build_feedback_page(items, limit, offset, owner_feedback_view)
    return json_response(200, build_success(page))


@app.post("/feedbacks")
async def create_feedback(
    payload: FeedbackCreatePayload,
    auth: dict[str, str] = Depends(require_headers),
) -> JSONResponse:
    """Envia um novo feedback; fica pendente até aprovação do admin."""
    try:
        item = await asyncio.to_thread(
            feedback_store.create,
            user_id=auth["user_id"],
            author_name=payload.author_name,
            content=payload.resolved_content(),
            rating=payload.rating,
            result=payload.result,
            video_url=payload.video_url,
        )
    except FeedbackValidationError as exc:
        return json_response(400, build_error(str(exc)))
    return json_response(201, build_success(owner_feedback_view(item)))


@app.get("/admin/feedbacks")
async def admin_list_feedbacks(
    status: str | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    auth: dict[str, str] = Depends(require_admin),
) -> JSONResponse:
    """Lista feedbacks para moderação (somente admin)."""
    _ = auth
    try:
        items = await asyncio.to_thread(
            feedback_store.list_for_admin,
            status,
            limit=limit + 1,
            offset=offset,
        )
    except FeedbackValidationError as exc:
        return json_response(400, build_error(str(exc)))
    page = build_feedback_page(items, limit, offset, admin_feedback_view)
    return json_response(200, build_success(page))


@app.post("/admin/feedbacks/{feedback_id}/approve")
async def admin_approve_feedback(
    feedback_id: str,
    auth: dict[str, str] = Depends(require_admin),
) -> JSONResponse:
    """Aprova um feedback para exibição pública."""
    item = await asyncio.to_thread(
        feedback_store.review,
        feedback_id,
        "approved",
        auth["user_id"],
    )
    if item is None:
        raise HTTPException(status_code=404, detail="FEEDBACK_NOT_FOUND")
    return json_response(200, build_success(admin_feedback_view(item)))


@app.post("/admin/feedbacks/{feedback_id}/reject")
async def admin_reject_feedback(
    feedback_id: str,
    auth: dict[str, str] = Depends(require_admin),
) -> JSONResponse:
    """Rejeita um feedback (não aparece na lista pública)."""
    item = await asyncio.to_thread(
        feedback_store.review,
        feedback_id,
        "rejected",
        auth["user_id"],
    )
    if item is None:
        raise HTTPException(status_code=404, detail="FEEDBACK_NOT_FOUND")
    return json_response(200, build_success(admin_feedback_view(item)))


@app.patch("/admin/feedbacks/{feedback_id}")
async def admin_update_feedback(
    feedback_id: str,
    payload: FeedbackAdminUpdatePayload,
    auth: dict[str, str] = Depends(require_admin),
) -> JSONResponse:
    """Arquiva um feedback aprovado para removê-lo da lista pública."""
    if payload.status != "archived":
        return json_response(400, build_error("Status administrativo inválido"))
    item = await asyncio.to_thread(
        feedback_store.archive,
        feedback_id,
        auth["user_id"],
    )
    if item is None:
        raise HTTPException(status_code=404, detail="FEEDBACK_NOT_FOUND")
    return json_response(200, build_success(admin_feedback_view(item)))


@app.delete("/admin/feedbacks/{feedback_id}", status_code=204)
async def admin_delete_feedback(
    feedback_id: str,
    auth: dict[str, str] = Depends(require_admin),
) -> Response:
    """Exclui definitivamente um feedback."""
    _ = auth
    deleted = await asyncio.to_thread(feedback_store.delete, feedback_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="FEEDBACK_NOT_FOUND")
    return Response(status_code=204)


app.include_router(
    create_webhook_router(
        webhook_service,
        require_secure_admin,
        account_links=account_link_service,
    )
)
app.include_router(
    create_email_router(
        email_service,
        require_secure_admin,
    )
)
app.include_router(
    create_finance_router(
        finance_service,
        cakto_service,
        require_headers,
        require_secure_admin,
        public_webhook_url=f"{config.public_api_url}/webhooks/cakto",
    )
)
async def _compute_admin_dashboard_for_warm(
    company_id: str,
    days: int,
) -> dict[str, Any]:
    """Pipeline async injetado no warmer do cache admin dashboard."""
    return await compute_admin_dashboard_payload(
        service=admin_management_service,
        company_id=company_id,
        days=days,
        history_loader=load_robot_history_items,
        history_batch_loader=load_robot_history_items_for_users,
    )


admin_dashboard_warmer = AdminDashboardWarmer(
    _compute_admin_dashboard_for_warm,
)

app.include_router(
    create_admin_router(
        admin_management_service,
        require_headers,
        require_admin,
        require_secure_headers,
        require_secure_admin,
        load_robot_history_items,
        app_env=config.app_env,
        marketing_display_sync=sync_marketing_display_to_robot,
        asset_payout_resolver=resolve_marketing_asset_payout,
        robot_history_deleter=delete_marketing_robot_history_item,
        history_batch_loader=load_robot_history_items_for_users,
        access_profile_invalidator=(
            (lambda user_id: supabase_auth_service.invalidate_user(user_id))
            if supabase_auth_service is not None
            else None
        ),
        dashboard_warmer=admin_dashboard_warmer,
    )
)
