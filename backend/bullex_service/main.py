import asyncio
import contextvars
import logging
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from threading import BoundedSemaphore, Lock, RLock
from typing import Any

from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import bullexapi.global_value as global_value
from bullexapi import constants as OP_code
from bullexapi.stable_api import Bullex
from bullex_service.session_store import SessionStore, create_session_store
from bullex_service.proxy_config import proxies_configured, requests_proxies_for_client
from bullex_service.rate_limit import (
    BULLEX_REQUESTS_LIMIT_EXCEEDED,
    is_requests_limit_error,
    login_block_remaining_seconds,
    note_requests_limit,
    rate_limit_error_payload,
)
from websocket._exceptions import WebSocketConnectionClosedException


logging.basicConfig(level=logging.INFO)
from bullex_service.digital_orders import (
    DigitalOrderError,
    place_digital_order,
    read_digital_result,
)

logger = logging.getLogger("bullex-service")

CORS_ALLOWED_ORIGINS_DEFAULT = (
    "https://elcapobot.online,"
    "https://www.elcapobot.online,"
    "http://localhost:5173,"
    "http://localhost:3000"
)
CORS_ALLOWED_METHODS = ["GET", "POST", "OPTIONS"]
CORS_ALLOWED_HEADERS = [
    "x-api-key",
    "x-user-id",
    "content-type",
    "authorization",
]

ALLOWED_BALANCE_MODES = {"PRACTICE", "REAL", "TOURNAMENT"}
ALLOWED_ACTIONS = {"call", "put"}
SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
SESSION_DISCONNECTED = "SESSION_DISCONNECTED"
BULLEX_ACTIVE_MODE_NOT_REAL = "BULLEX_ACTIVE_MODE_NOT_REAL"
ASSET_NOT_ALLOWED = "ASSET_NOT_ALLOWED"
# Espelho EXATO de `backend.main.BINARY_ALLOWED_ASSETS`. As duas listas são
# a mesma allowlist em processos diferentes: o gateway decide o que varrer e
# este serviço decide o que a corretora pode responder. Quando divergem, o
# gateway pede um ativo que ele considera válido e leva HTTP 400
# `ASSET_NOT_ALLOWED` da nossa própria porta — foi o que aconteceu com os 11
# pares de mercado aberto adicionados em 04/09 só no gateway: em 07/09 a
# conta em `market_mode=OPEN` recebia 400 em EURAUD/EURNZD/AUDCHF/... e caía
# para OTC todo ciclo. `tests/test_binary_allowlist_parity.py` trava isso.
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
    "NZDUSD",
    "AUDCAD",
    "GBPCAD",
    "GBPCHF",
    "GBPAUD",
    "EURCAD",
    "CHFJPY",
    "CADCHF",
    "EURAUD",
    "EURNZD",
    "AUDCHF",
]
BINARY_ALLOWED_ASSET_SET = set(BINARY_ALLOWED_ASSETS)
SESSION_EXCEPTION_TYPES = (WebSocketConnectionClosedException, ConnectionError, TimeoutError)
SESSION_STATUS_TTL_SECONDS = 20
ACCOUNT_TTL_SECONDS = 45
# Tempo máximo na fila do lock global para probes/market data sob carga.
CALL_GATE_TIMEOUT_SECONDS = 2.5
ASSETS_TTL_SECONDS = 300
PAYOUT_TTL_SECONDS = 60
CANDLES_TTL_SECONDS = 60
STALE_MARKET_DATA_SECONDS = 120
INSTRUMENTS_CACHE_TTL_SECONDS = 300
# O `/assets` devolvia INSTRUMENTS_TIMEOUT em 100% das chamadas, mas **não era
# falta de tempo**: subir para 25s falhou igual. A causa era
# `update_ACTIVES_OPCODE` pedir crypto/forex/cfd, que a BullEx não serve
# (TimeoutError nos três em `GET /instruments/probe`), consumindo o orçamento
# inteiro. A correção está em `read_assets_uncached`, que agora pede só
# binary/turbo. O valor volta aos 8s originais: este timeout segura uma thread
# de requisição em `future.result(...)`, e alargá-lo triplicaria a ocupação do
# pool httpx numa falha de WS — a condição do PoolTimeout de 08/08.
INSTRUMENTS_TIMEOUT_SECONDS = int(os.getenv("INSTRUMENTS_TIMEOUT_SECONDS", "8"))
# Status turbo/binary (canal onde a ordem é enviada). TTL de sucesso mais
# longo (o gateway já tem cooldown por ativo) e backoff em falha para o
# get_all_init_v2 não penalizar os /payouts repetidamente.
BINARY_OPEN_TTL_SECONDS = 60
BINARY_OPEN_FAILURE_TTL_SECONDS = 30
# init_v2 com 8s (INSTRUMENTS_TIMEOUT) estourava o fetch de payout p/ 8s+;
# 3s é suficiente quando o websocket está saudável.
BINARY_OPEN_FETCH_TIMEOUT_SECONDS = 3
INSTRUMENTS_BACKOFF_SECONDS = (10, 30, 60)
# Hang da WS morta: sem timeout a fila da Bullex trava o robô em CANDLES_TIMEOUT eterno.
MARKET_DATA_OP_TIMEOUT_SECONDS = 12
MIN_API_CALL_SPACING_SECONDS = 0.05
SESSION_STATUS_THROTTLE_SECONDS = 10
SESSION_OFFLINE_TTL_SECONDS = 60
# Restore passivo (poll de status) que falha não pode virar loop de login na
# corretora — cada tentativa abre um websocket novo e conta no rate limit.
SESSION_RESTORE_COOLDOWN_SECONDS = 30.0
SESSION_FAILURE_BACKOFF_SECONDS = (10, 30, 60, 300)
LOGIN_TIMEOUT_SECONDS = 60
LOGIN_RETRY_DELAY_SECONDS = 5
LOGIN_MAX_ATTEMPTS = 3
LOGIN_PROGRESS_STATES = (
    "CONNECTING",
    "AUTHENTICATING",
    "OPENING_WEBSOCKET",
    "LOADING_PROFILE",
    "LOADING_BALANCE",
    "READY",
)


class ServiceError(Exception):
    def __init__(
        self,
        message: str,
        status_code: int = 400,
        data: dict[str, Any] | None = None,
    ):
        self.message = message
        self.status_code = status_code
        self.data = data
        super().__init__(message)


def create_bullex_client(email: str, password: str) -> Bullex:
    """
    Instancia o cliente Bullex aplicando proxy de egress se configurado.

    Args:
        email: E-mail da conta Bullex.
        password: Senha (pode ser vazia em restore só com SSID).

    Returns:
        Instância ``Bullex`` pronta para ``connect`` / ``restore_with_ssid``.
    """
    return Bullex(email, password, proxies=requests_proxies_for_client())


def raise_if_login_rate_limited(*, allow_proxy_bypass: bool = True) -> None:
    """
    Bloqueia novo login enquanto o IP do VPS estiver em rate limit.

    Com proxy configurado o gate é ignorado (egress diferente). Sem proxy,
    devolve ``BULLEX_REQUESTS_LIMIT_EXCEEDED`` para o gateway/UI.

    Args:
        allow_proxy_bypass: Se True e há proxy, não bloqueia a tentativa.

    Raises:
        ServiceError: Quando o gate global está ativo e não há bypass.
    """
    remaining = login_block_remaining_seconds()
    if remaining <= 0:
        return
    if allow_proxy_bypass and proxies_configured():
        logger.info(
            "[BULLEX_LOGIN_RATE_LIMIT_BYPASS_PROXY] remaining=%s",
            remaining,
        )
        return
    raise ServiceError(
        BULLEX_REQUESTS_LIMIT_EXCEEDED,
        429,
        rate_limit_error_payload(remaining),
    )


def _extract_broker_error_code(reason: Any) -> str | None:
    """
    Extrai o ``code`` JSON da corretora (ex.: invalid_credentials).

    Args:
        reason: Motivo bruto retornado pelo cliente Bullex.

    Returns:
        Código em minúsculas ou ``None``.
    """
    if isinstance(reason, dict):
        code = str(reason.get("code") or "").strip().lower()
        return code or None
    text = str(reason or "").strip()
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        if "invalid_credentials" in text.lower():
            return "invalid_credentials"
        return None
    try:
        import json as _json

        payload = _json.loads(text[start : end + 1])
    except Exception:
        if "invalid_credentials" in text.lower():
            return "invalid_credentials"
        return None
    if not isinstance(payload, dict):
        return None
    code = str(payload.get("code") or "").strip().lower()
    return code or None


class ConnectRequest(BaseModel):
    email: str | None = None
    password: str | None = None
    sms_code: str | None = None
    account_mode: str = Field(default="REAL")


class ChangeModeRequest(BaseModel):
    mode: str
    confirm_real: bool = True


class BuyDigitalRequest(BaseModel):
    amount: float
    active: str
    action: str
    duration: int = 1


class BuyOrderRequest(BaseModel):
    amount: float
    active: str
    action: str
    expiration: int
    confirm_real: bool = True


@dataclass
class SessionState:
    check_websocket_if_connect: Any = None
    ssl_Mutual_exclusion: bool = False
    ssl_Mutual_exclusion_write: bool = False
    SSID: Any = None
    check_websocket_if_error: bool = False
    websocket_error_reason: Any = None
    balance_id: Any = None


@dataclass
class ManagedSession:
    user_id: str
    client: Bullex
    email: str | None = None
    password: str | None = None
    sms_code: str | None = None
    desired_mode: str = "REAL"
    requires_2fa: bool = False
    active_mode: str | None = None
    real_mode_confirmed: bool = False
    state: SessionState = field(default_factory=SessionState)


@dataclass
class CachedProbe:
    status_code: int
    payload: dict[str, Any]
    expires_at: float


@dataclass
class SessionProbeState:
    responses: dict[str, CachedProbe] = field(default_factory=dict)
    failure_count: int = 0
    next_retry_at: float = 0.0
    offline_until: float = 0.0
    last_request_at: dict[str, float] = field(default_factory=dict)


@dataclass
class LoginProgress:
    state: str = "IDLE"
    attempt: int = 0
    max_attempts: int = LOGIN_MAX_ATTEMPTS
    updated_at: float = 0.0
    error: str | None = None
    active: bool = False


@dataclass
class InstrumentsCacheState:
    assets: list[dict[str, Any]] | None = None
    expires_at: float = 0.0
    updated_at: float = 0.0
    failure_count: int = 0
    next_retry_at: float = 0.0
    lock: Lock = field(default_factory=Lock)


class SessionManager:
    def __init__(self, store: SessionStore | None = None) -> None:
        self.sessions: dict[str, ManagedSession] = {}
        self.websockets: dict[str, Any] = {}
        self.workers: dict[str, Any] = {}
        self.locks: dict[str, RLock] = {}
        self.async_locks: dict[str, asyncio.Lock] = {}
        self.last_account_cache: dict[str, dict[str, Any]] = {}
        self.last_status_cache: dict[str, dict[str, Any]] = {}
        self._probe_cache: dict[str, SessionProbeState] = {}
        self._restore_retry_at: dict[str, float] = {}
        # Dados de mercado (candles/payouts) são iguais para qualquer usuário
        # observando o mesmo ativo/intervalo — cache compartilhado evita que
        # N usuários gerem N chamadas upstream redundantes disputando o
        # _call_gate (ver [CALL_GATE_TIMEOUT] em PERFORMANCE_SISTEMA.md).
        self._market_data_cache: dict[str, CachedProbe] = {}
        self._market_data_cache_lock = Lock()
        self._login_progress: dict[str, LoginProgress] = {}
        self._instruments_cache: dict[str, InstrumentsCacheState] = {}
        self._instruments_cache_guard = RLock()
        self._market_data_executor = ThreadPoolExecutor(
            max_workers=int(os.getenv("BULLEX_MARKET_DATA_WORKERS", "4"))
        )
        self.store = store
        # Lock só para mutações estruturais do manager (não serializa mais
        # chamadas à corretora — isolamento via ContextVar SessionGlobals).
        self._runtime_lock = RLock()
        self._max_concurrent_api_calls = read_max_concurrent_api_calls()
        self._call_gate = BoundedSemaphore(self._max_concurrent_api_calls)
        self._last_api_call_at = 0.0

        logger.info(
            "bullex-service session isolation: ContextVar SessionGlobals; "
            "BULLEX_MAX_CONCURRENT_API_CALLS=%s",
            self._max_concurrent_api_calls,
        )

    def get(self, user_id: str) -> ManagedSession | None:
        return self.sessions.get(user_id)

    def user_lock(self, user_id: str) -> RLock:
        return self.locks.setdefault(user_id, RLock())

    def async_user_lock(self, user_id: str) -> asyncio.Lock:
        return self.async_locks.setdefault(user_id, asyncio.Lock())

    def get_probe_state(self, user_id: str) -> SessionProbeState:
        return self._probe_cache.setdefault(user_id, SessionProbeState())

    def get_login_progress(self, user_id: str) -> LoginProgress:
        return self._login_progress.setdefault(user_id, LoginProgress())

    def upsert(self, session: ManagedSession) -> ManagedSession:
        self.sessions[session.user_id] = session
        self.websockets[session.user_id] = getattr(session.client, "api", session.client)
        return session

    def remove(self, user_id: str) -> None:
        self.sessions.pop(user_id, None)
        self.websockets.pop(user_id, None)
        self.workers.pop(user_id, None)

    def clear_probe_cache(self, user_id: str) -> None:
        probe = self.get_probe_state(user_id)
        probe.responses.clear()
        probe.last_request_at.clear()

    def clear_user_runtime_cache(self, user_id: str) -> None:
        probe = self.get_probe_state(user_id)
        probe.responses.clear()
        probe.last_request_at.clear()
        probe.failure_count = 0
        probe.next_retry_at = 0.0
        probe.offline_until = 0.0
        with self._instruments_cache_guard:
            self._instruments_cache.pop(user_id, None)
        clear_binary_open_cache(user_id)
        self.last_account_cache.pop(user_id, None)
        self.last_status_cache.pop(user_id, None)

    def get_instruments_cache_state(self, user_id: str) -> InstrumentsCacheState:
        with self._instruments_cache_guard:
            return self._instruments_cache.setdefault(user_id, InstrumentsCacheState())

    def login_progress_payload(self, user_id: str) -> dict[str, Any]:
        progress = self.get_login_progress(user_id)
        return {
            "state": progress.state,
            "attempt": progress.attempt,
            "max_attempts": progress.max_attempts,
            "updated_at": progress.updated_at,
            "error": progress.error,
            "active": progress.active,
        }

    def _set_login_progress(
        self,
        user_id: str,
        state: str,
        *,
        attempt: int,
        active: bool,
        error: str | None = None,
    ) -> None:
        progress = self.get_login_progress(user_id)
        progress.state = state
        progress.attempt = attempt
        progress.max_attempts = LOGIN_MAX_ATTEMPTS
        progress.updated_at = time.time()
        progress.error = error
        progress.active = active

    def _clear_login_progress(self, user_id: str) -> None:
        progress = self.get_login_progress(user_id)
        progress.state = "READY"
        progress.attempt = 0
        progress.max_attempts = LOGIN_MAX_ATTEMPTS
        progress.updated_at = time.time()
        progress.error = None
        progress.active = False

    def _cache_probe(
        self,
        user_id: str,
        cache_key: str,
        status_code: int,
        payload: dict[str, Any],
        *,
        ttl_seconds: int,
    ) -> None:
        self.get_probe_state(user_id).responses[cache_key] = CachedProbe(
            status_code=status_code,
            payload=payload,
            expires_at=time.time() + ttl_seconds,
        )

    def _throttled_probe(self, user_id: str, cache_key: str, *, path: str) -> tuple[int, dict[str, Any]] | None:
        if path != "/sessions/status":
            return None
        probe = self.get_probe_state(user_id)
        now = time.time()
        last_request_at = probe.last_request_at.get(cache_key, 0.0)
        probe.last_request_at[cache_key] = now
        if last_request_at <= 0 or (now - last_request_at) >= SESSION_STATUS_THROTTLE_SECONDS:
            return None
        cached = probe.responses.get(cache_key)
        if cached is None:
            return None
        logger.debug("[SESSION_STATUS_THROTTLED] %s %s", user_id, cache_key)
        return cached.status_code, cached.payload

    def _probe_backoff_seconds(self, failure_count: int) -> int:
        index = min(max(failure_count, 1), len(SESSION_FAILURE_BACKOFF_SECONDS)) - 1
        return SESSION_FAILURE_BACKOFF_SECONDS[index]

    def _mark_probe_success(
        self,
        user_id: str,
        cache_key: str,
        status_code: int,
        payload: dict[str, Any],
        *,
        ttl_seconds: int,
    ) -> None:
        probe = self.get_probe_state(user_id)
        probe.failure_count = 0
        probe.next_retry_at = 0.0
        probe.offline_until = 0.0
        self._cache_probe(user_id, cache_key, status_code, payload, ttl_seconds=ttl_seconds)
        if cache_key == "/account":
            self.last_account_cache[user_id] = payload
        elif cache_key == "/sessions/status":
            self.last_status_cache[user_id] = payload

    def _last_good_probe_response(
        self, user_id: str, path: str
    ) -> tuple[int, dict[str, Any]] | None:
        """Última resposta boa de /account ou /sessions/status (anti-flap visual).

        Args:
            user_id: Identificador do usuário Bullex.
            path: Caminho da sonda (``/account`` ou ``/sessions/status``).

        Returns:
            Tupla ``(status_code, payload)`` ou ``None`` se não houver cache bom.
        """
        if path == "/account":
            payload = self.last_account_cache.get(user_id)
            if isinstance(payload, dict):
                return 200, payload
        if path == "/sessions/status":
            payload = self.last_status_cache.get(user_id)
            if isinstance(payload, dict):
                return 200, payload
        return None

    def _mark_probe_failure(self, user_id: str, *, offline: bool = False) -> None:
        """Registra falha de sonda sem apagar snapshot bom em blips transitórios.

        Falha soft (backoff): só agenda retry — **não** sobrescreve o cache de
        conta/status com ``connected:false`` (isso fazia o painel piscar
        Desconectado sem email/saldo ao trocar de aba).

        Offline confirmado: marca janela offline, mas mantém ``last_*_cache``
        para o gateway/painel servirem stale até a reconexão.
        """
        probe = self.get_probe_state(user_id)
        probe.failure_count += 1
        now = time.time()
        if offline:
            probe.offline_until = now + SESSION_OFFLINE_TTL_SECONDS
            probe.next_retry_at = probe.offline_until
            # Não apaga last_account_cache / last_status_cache: o painel usa
            # esses snapshots para não mostrar "Desconectado" vazio.
            return
        probe.next_retry_at = now + self._probe_backoff_seconds(probe.failure_count)

    def get_cached_probe(self, user_id: str, cache_key: str, *, path: str) -> tuple[int, dict[str, Any]] | None:
        throttled = self._throttled_probe(user_id, cache_key, path=path)
        if throttled is not None:
            return throttled
        probe = self.get_probe_state(user_id)
        now = time.time()
        cached = probe.responses.get(cache_key)
        if cached is not None and now < cached.expires_at:
            logger.debug("[CACHE_HIT] user_id=%s path=%s", user_id, path)
            if path == "/sessions/status":
                logger.debug("[SESSION_STATUS_CACHE_HIT] %s %s", user_id, path)
            return cached.status_code, cached.payload
        logger.debug("[CACHE_MISS] user_id=%s path=%s", user_id, path)
        if path == "/sessions/status":
            logger.debug("[SESSION_STATUS_CACHE_MISS] %s %s", user_id, path)
        if probe.offline_until > now:
            logger.warning("[SESSION_CHECK_SKIPPED] %s %s reason=offline", user_id, path)
            logger.warning("[USER_OFFLINE_SKIPPED] %s %s", user_id, path)
            logger.warning("[CPU_LOOP_PROTECTION] %s %s reason=offline", user_id, path)
            if cached is not None:
                return cached.status_code, cached.payload
            last_good = self._last_good_probe_response(user_id, path)
            if last_good is not None:
                return last_good
            if path == "/account":
                return 200, build_success({"connected": False, "status": "backoff"})
            return 404, {"ok": False, "data": {"connected": False}, "error": SESSION_NOT_FOUND}
        if probe.next_retry_at > now:
            logger.warning("[SESSION_CHECK_SKIPPED] %s %s reason=backoff", user_id, path)
            logger.warning("[BACKOFF_ACTIVE] %s %s", user_id, path)
            logger.warning("[CPU_LOOP_PROTECTION] %s %s reason=backoff", user_id, path)
            if cached is not None:
                return cached.status_code, cached.payload
            last_good = self._last_good_probe_response(user_id, path)
            if last_good is not None:
                return last_good
            return 200, build_success({"connected": False, "status": "backoff"})
        return None

    def get_shared_market_cache(self, cache_key: str) -> tuple[int, dict[str, Any]] | None:
        """Retorna resposta de mercado (candles/payouts) reaproveitável entre usuários.

        Args:
            cache_key: chave gerada por `build_cache_key` (path + params
                normalizados, sem `user_id`).

        Returns:
            Tupla `(status_code, payload)` se houver entrada válida (TTL não
            expirado), ou `None` em caso de cache miss.
        """
        with self._market_data_cache_lock:
            cached = self._market_data_cache.get(cache_key)
        if cached is not None and time.time() < cached.expires_at:
            return cached.status_code, cached.payload
        return None

    def set_shared_market_cache(
        self,
        cache_key: str,
        status_code: int,
        payload: dict[str, Any],
        *,
        ttl_seconds: int,
    ) -> None:
        """Grava resposta de mercado no cache compartilhado entre usuários.

        Args:
            cache_key: chave gerada por `build_cache_key`.
            status_code: código HTTP lógico da resposta cacheada.
            payload: corpo já serializado (mesmo formato devolvido ao painel).
            ttl_seconds: tempo de vida da entrada, em segundos.
        """
        with self._market_data_cache_lock:
            self._market_data_cache[cache_key] = CachedProbe(
                status_code=status_code,
                payload=payload,
                expires_at=time.time() + ttl_seconds,
            )

    def get_last_probe(self, user_id: str, cache_key: str) -> tuple[int, dict[str, Any]] | None:
        cached = self.get_probe_state(user_id).responses.get(cache_key)
        if cached is None:
            return None
        return cached.status_code, cached.payload

    def require(self, user_id: str) -> ManagedSession:
        session = self.get(user_id)
        if session is None:
            raise ServiceError(SESSION_NOT_FOUND, 404)
        return session

    def ensure_session_alive(self, user_id: str) -> ManagedSession:
        cached = self.get_cached_probe(user_id, "/sessions/status", path="/sessions/status")
        if cached is not None:
            session = self.get(user_id)
            if session is not None:
                return session
        logger.info("[SESSION-CHECK] %s", user_id)
        session = self.require(user_id)
        if session.requires_2fa:
            logger.info("[SESSION-ALIVE] %s", user_id)
            return session

        alive = False
        dead_reason = "UNKNOWN"
        try:
            with self._session_context(session):
                alive = self._is_session_alive(session)
        except SESSION_EXCEPTION_TYPES as exc:
            dead_reason = type(exc).__name__
        except Exception as exc:
            dead_reason = type(exc).__name__

        if alive:
            logger.info("[SESSION-ALIVE] %s", user_id)
            self._mark_probe_success(
                user_id,
                "/sessions/status",
                200,
                build_success(
                    {
                        "user_id": user_id,
                        "connected": True,
                        "requires_2fa": session.requires_2fa,
                        "active_mode": session.client.get_balance_mode(),
                    }
                ),
                ttl_seconds=SESSION_STATUS_TTL_SECONDS,
            )
            return session

        logger.warning("[SESSION-DEAD] %s %s", user_id, dead_reason)
        self._mark_probe_failure(user_id)
        return self._attempt_reconnect(session, dead_reason)

    def _run_with_timeout(self, operation, *, timeout_seconds: int = LOGIN_TIMEOUT_SECONDS):
        # Propaga ContextVar (SessionGlobals) para a thread do executor —
        # ThreadPoolExecutor.submit sozinho não copia o contexto.
        executor = ThreadPoolExecutor(max_workers=1)
        ctx = contextvars.copy_context()
        future = executor.submit(ctx.run, operation)
        try:
            return future.result(timeout=timeout_seconds)
        except FutureTimeoutError as exc:
            future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)
            raise TimeoutError(f"operation exceeded {timeout_seconds}s") from exc
        finally:
            if future.done():
                executor.shutdown(wait=False, cancel_futures=True)

    def _wait_until_session_ready(self, session: ManagedSession, *, timeout_seconds: int = LOGIN_TIMEOUT_SECONDS) -> None:
        deadline = time.time() + timeout_seconds
        last_error: Exception | None = None
        while time.time() < deadline:
            try:
                if self._is_session_alive(session):
                    return
            except Exception as exc:
                last_error = exc
            time.sleep(0.5)
        if last_error is not None:
            raise TimeoutError(type(last_error).__name__)
        raise TimeoutError("websocket_not_ready")

    def _populate_ready_state(self, session: ManagedSession, *, user_id: str, attempt: int) -> None:
        self._set_login_progress(user_id, "OPENING_WEBSOCKET", attempt=attempt, active=True)
        logger.info("[LOGIN_WS] user_id=%s attempt=%s", user_id, attempt)
        with self._session_context(session):
            self._wait_until_session_ready(session)
            self._set_login_progress(user_id, "LOADING_PROFILE", attempt=attempt, active=True)
            session.desired_mode = "REAL"
            logger.info("[REAL_MODE_FORCED] user_id=%s attempt=%s", user_id, attempt)
            real_balance_id, practice_balance_id, balances_discovered = discover_balance_ids(
                session.client
            )
            if real_balance_id is not None:
                logger.info(
                    "[REAL_BALANCE_ID_FOUND] user_id=%s real_balance_id=%s practice_balance_id=%s",
                    user_id,
                    real_balance_id,
                    practice_balance_id,
                )
            current_mode = normalize_mode(session.client.get_balance_mode())
            logger.info("[LOGIN_AUTH] user_id=%s attempt=%s mode=%s", user_id, attempt, current_mode)
            if balances_discovered and real_balance_id is None:
                logger.warning(
                    "[CHANGE_BALANCE_REAL_FAILED] user_id=%s active_mode=%s reason=real_balance_id_missing",
                    user_id,
                    current_mode,
                )
                raise real_mode_service_error(current_mode)
            try:
                current_mode = force_real_mode(session, user_id=user_id)
                session.active_mode = current_mode
                session.real_mode_confirmed = current_mode == "REAL"
                logger.info("[REAL_MODE_CONFIRMED] user_id=%s active_mode=%s", user_id, current_mode)
            except ServiceError as exc:
                current_mode = str((exc.data or {}).get("active_mode") or current_mode).strip().upper()
                session.active_mode = current_mode
                session.real_mode_confirmed = False
                logger.warning(
                    "[REAL_MODE_FAILED] user_id=%s active_mode=%s",
                    user_id,
                    current_mode,
                )
                # Propaga em vez de devolver sessão "meio pronta": sem saldo e
                # sem SSID persistido, ela ficava viva com connected=true e o
                # painel mostrava "Sessão incompleta (sem email/saldo)". O saldo
                # PRACTICE continua não sendo carregado — o erro sobe.
                raise
            self._set_login_progress(user_id, "LOADING_BALANCE", attempt=attempt, active=True)
            session.client.get_balance()
            session.client.get_currency()

    def _connect_with_retries(self, user_id: str, action) -> ManagedSession:
        last_error: Exception | None = None
        for attempt in range(1, LOGIN_MAX_ATTEMPTS + 1):
            self._set_login_progress(user_id, "CONNECTING", attempt=attempt, active=True)
            if attempt == 1:
                logger.info("[LOGIN_STARTED] user_id=%s", user_id)
            else:
                logger.warning("[LOGIN_RETRY] user_id=%s attempt=%s", user_id, attempt)
            try:
                session = action(attempt)
                self._set_login_progress(user_id, "READY", attempt=attempt, active=False)
                logger.info("[LOGIN_READY] user_id=%s attempt=%s", user_id, attempt)
                logger.info("[LOGIN_SUCCESS] user_id=%s attempt=%s", user_id, attempt)
                return session
            except TimeoutError as exc:
                last_error = exc
                self._set_login_progress(user_id, "CONNECTING", attempt=attempt, active=True, error="LOGIN_TIMEOUT")
                logger.warning("[LOGIN_TIMEOUT] user_id=%s attempt=%s error=%s", user_id, attempt, exc)
                if attempt >= LOGIN_MAX_ATTEMPTS:
                    break
                time.sleep(LOGIN_RETRY_DELAY_SECONDS)
            except ServiceError as exc:
                last_error = exc
                if "timeout" in str(exc.message).lower() and attempt < LOGIN_MAX_ATTEMPTS:
                    logger.warning("[LOGIN_TIMEOUT] user_id=%s attempt=%s error=%s", user_id, attempt, exc.message)
                    logger.warning("[LOGIN_RETRY] user_id=%s attempt=%s", user_id, attempt + 1)
                    time.sleep(LOGIN_RETRY_DELAY_SECONDS)
                    continue
                self._set_login_progress(user_id, "CONNECTING", attempt=attempt, active=False, error=exc.message)
                logger.warning("[LOGIN_FAILED] user_id=%s attempt=%s error=%s", user_id, attempt, exc.message)
                raise
            except Exception as exc:
                last_error = exc
                self._set_login_progress(user_id, "CONNECTING", attempt=attempt, active=False, error=type(exc).__name__)
                logger.warning("[LOGIN_FAILED] user_id=%s attempt=%s error=%s", user_id, attempt, type(exc).__name__)
                raise ServiceError(type(exc).__name__, 401) from exc

        error_message = "LOGIN_TIMEOUT" if isinstance(last_error, TimeoutError) else type(last_error).__name__ if last_error else "LOGIN_FAILED"
        self._set_login_progress(user_id, "CONNECTING", attempt=LOGIN_MAX_ATTEMPTS, active=False, error=error_message)
        logger.warning("[LOGIN_FAILED] user_id=%s attempt=%s error=%s", user_id, LOGIN_MAX_ATTEMPTS, error_message)
        raise ServiceError(error_message, 504 if error_message == "LOGIN_TIMEOUT" else 401)

    def run(
        self,
        user_id: str,
        operation,
        *,
        disconnect_on_error: bool = True,
        timeout_seconds: float | None = None,
        gate_timeout: float | None = None,
    ):
        with self.user_lock(user_id):
            session = self.ensure_session_alive(user_id)
            try:
                with self._session_context(session, gate_timeout=gate_timeout):
                    now = time.monotonic()
                    elapsed = now - self._last_api_call_at
                    if elapsed < MIN_API_CALL_SPACING_SECONDS:
                        time.sleep(MIN_API_CALL_SPACING_SECONDS - elapsed)
                    self._last_api_call_at = time.monotonic()
                    if timeout_seconds is not None and timeout_seconds > 0:
                        try:
                            return self._run_with_timeout(
                                lambda: operation(session),
                                timeout_seconds=int(timeout_seconds),
                            )
                        except TimeoutError as exc:
                            logger.warning(
                                "[MARKET_DATA_OP_TIMEOUT] user_id=%s timeout_seconds=%s",
                                user_id,
                                int(timeout_seconds),
                            )
                            try:
                                close = getattr(getattr(session.client, "api", None), "close", None)
                                if callable(close):
                                    close()
                            except Exception:
                                logger.exception(
                                    "falha ao fechar websocket apos timeout de market data user_id=%s",
                                    user_id,
                                )
                            self._mark_probe_failure(user_id)
                            raise ServiceError(SESSION_DISCONNECTED, 409) from exc
                    return operation(session)
            except ServiceError as exc:
                if exc.message == SESSION_DISCONNECTED and disconnect_on_error:
                    logger.warning("[SESSION-DISCONNECTED] %s", user_id)
                    self.remove(user_id)
                raise
            except SESSION_EXCEPTION_TYPES as exc:
                if disconnect_on_error:
                    self._mark_disconnected(user_id, type(exc).__name__)
                raise ServiceError(SESSION_DISCONNECTED, 409) from exc
            except Exception as exc:
                if disconnect_on_error:
                    self._mark_disconnected(user_id, type(exc).__name__)
                raise ServiceError(type(exc).__name__, 503) from exc

    def connect(self, user_id: str, payload: ConnectRequest) -> ManagedSession:
        with self.user_lock(user_id):
            payload.account_mode = "REAL"
            logger.info("[REAL_MODE_FORCED] user_id=%s requested_mode=REAL", user_id)
            logger.info("[REAL_MODE_REQUESTED] user_id=%s", user_id)
            logger.info("[CONNECT_REQUEST] user_id=%s", user_id)
            raise_if_login_rate_limited()
            self.clear_user_runtime_cache(user_id)
            logger.info("[CONNECT_BACKOFF_CLEARED] user_id=%s", user_id)

            is_2fa_continuation = bool(
                payload.sms_code
                and self.get(user_id) is not None
                and not payload.email
                and not payload.password
            )
            if not is_2fa_continuation:
                existing = self.get(user_id)
                # Reusa sessão viva com o mesmo email — evita fechar o WS e
                # derrubar o login do usuário na corretora (app/site) a cada
                # auto-reconnect do start/worker. Só limpa se estiver morta
                # ou se o email mudou (troca de conta).
                same_email = bool(
                    existing is not None
                    and payload.email
                    and existing.email
                    and payload.email.strip().lower() == existing.email.strip().lower()
                    and not existing.requires_2fa
                )
                if same_email and existing is not None:
                    try:
                        with self._session_context(existing):
                            if self._is_session_alive(existing):
                                target_mode = normalize_mode(payload.account_mode)
                                if (
                                    existing.desired_mode != target_mode
                                    or not existing.real_mode_confirmed
                                ):
                                    existing.desired_mode = target_mode
                                    self._populate_ready_state(
                                        existing, user_id=user_id, attempt=1
                                    )
                                    self._persist_connected(existing)
                                connected = (
                                    (not existing.requires_2fa)
                                    and existing.real_mode_confirmed
                                )
                                self._mark_probe_success(
                                    user_id,
                                    "/sessions/status",
                                    200,
                                    build_success(
                                        {
                                            "user_id": user_id,
                                            "connected": connected,
                                            "requires_2fa": existing.requires_2fa,
                                            "active_mode": (
                                                existing.active_mode
                                                or existing.client.get_balance_mode()
                                                if not existing.requires_2fa
                                                else None
                                            ),
                                        }
                                    ),
                                    ttl_seconds=SESSION_STATUS_TTL_SECONDS,
                                )
                                self._set_login_progress(
                                    user_id, "READY", attempt=1, active=False
                                )
                                logger.info(
                                    "[CONNECT_REUSE_ALIVE_SESSION] user_id=%s email=%s",
                                    user_id,
                                    existing.email,
                                )
                                logger.info(
                                    "[LOGIN_SUCCESS] user_id=%s attempt=1 reused_session=true",
                                    user_id,
                                )
                                logger.info("[CONNECT_SUCCESS] user_id=%s", user_id)
                                return existing
                    except Exception:
                        logger.warning(
                            "[CONNECT_REUSE_FAILED] user_id=%s",
                            user_id,
                            exc_info=True,
                        )
                logger.info(
                    "[CONNECT_CLEAR_OLD_SESSION] user_id=%s had_active_session=%s same_email=%s",
                    user_id,
                    existing is not None,
                    same_email,
                )
                if existing is not None:
                    self._close_session(existing)
                self.remove(user_id)
                self.clear_user_runtime_cache(user_id)
                if self.store is not None:
                    self.store.mark_disconnected(user_id, revoke_token=True)
                logger.info(
                    "[CONNECT_OLD_SESSION_CLOSED] user_id=%s had_active_session=%s",
                    user_id,
                    existing is not None,
                )

            try:
                logger.info("[CONNECT_ATTEMPT] user_id=%s", user_id)
                session = self._connect_unlocked(user_id, payload)
                connected = (not session.requires_2fa) and session.real_mode_confirmed
                self._mark_probe_success(
                    user_id,
                    "/sessions/status",
                    200,
                    build_success(
                        {
                            "user_id": user_id,
                            "connected": connected,
                            "requires_2fa": session.requires_2fa,
                            "active_mode": (
                                session.active_mode or session.client.get_balance_mode()
                                if not session.requires_2fa
                                else None
                            ),
                        }
                    ),
                    ttl_seconds=SESSION_STATUS_TTL_SECONDS,
                )
                logger.info("[CONNECT_SUCCESS] user_id=%s", user_id)
                return session
            except Exception as exc:
                logger.warning(
                    "[CONNECT_FAILED_HANDLED] user_id=%s detail=%s",
                    user_id,
                    getattr(exc, "message", None) or type(exc).__name__,
                )
                raise

    def _connect_unlocked(self, user_id: str, payload: ConnectRequest) -> ManagedSession:
        probe = self.get_probe_state(user_id)
        probe.failure_count = 0
        probe.next_retry_at = 0.0
        probe.offline_until = 0.0
        existing = self.get(user_id)

        if existing is not None and not existing.requires_2fa:
            try:
                with self._session_context(existing):
                    if self._is_session_alive(existing):
                        target_mode = normalize_mode(payload.account_mode)
                        if existing.desired_mode != target_mode:
                            existing.desired_mode = target_mode
                            self._populate_ready_state(existing, user_id=user_id, attempt=1)
                            self._persist_connected(existing)
                        self._set_login_progress(user_id, "READY", attempt=1, active=False)
                        logger.info("[LOGIN_SUCCESS] user_id=%s attempt=1 reused_session=true", user_id)
                        return existing
            except Exception:
                logger.warning("[SESSION-REUSE-FAILED] %s", user_id, exc_info=True)

        if payload.sms_code and existing and not payload.email and not payload.password:
            def connect_existing_2fa(attempt: int) -> ManagedSession:
                session = existing
                session.sms_code = payload.sms_code
                session.desired_mode = normalize_mode(payload.account_mode)
                self._set_login_progress(user_id, "AUTHENTICATING", attempt=attempt, active=True)
                logger.info("[LOGIN_AUTH] user_id=%s attempt=%s mode=2FA", user_id, attempt)
                with self._session_context(session):
                    ok, reason = self._run_with_timeout(
                        lambda: session.client.connect_2fa(payload.sms_code),
                        timeout_seconds=LOGIN_TIMEOUT_SECONDS,
                    )
                self._finalize_connect(session, ok, reason, user_id=user_id, attempt=attempt)
                self._persist_connected(session)
                return session

            return self._connect_with_retries(user_id, connect_existing_2fa)

        if not payload.email or not payload.password:
            raise ServiceError("email e password sao obrigatorios para conectar")

        desired_mode = normalize_mode(payload.account_mode)

        def connect_new_session(attempt: int) -> ManagedSession:
            logger.info("[CONNECT_CREATE_SESSION] user_id=%s attempt=%s", user_id, attempt)
            new_session = ManagedSession(
                user_id=user_id,
                client=create_bullex_client(payload.email, payload.password),
                email=payload.email,
                password=payload.password,
                sms_code=payload.sms_code,
                desired_mode=desired_mode,
            )
            if existing is not None and attempt == 1:
                self._close_session(existing)
            self.upsert(new_session)
            self._set_login_progress(user_id, "AUTHENTICATING", attempt=attempt, active=True)
            logger.info("[LOGIN_AUTH] user_id=%s attempt=%s mode=password", user_id, attempt)
            logger.info("[CONNECT_WS_START] user_id=%s attempt=%s", user_id, attempt)
            try:
                with self._session_context(new_session):
                    ok, reason = self._run_with_timeout(
                        lambda: new_session.client.connect(payload.sms_code),
                        timeout_seconds=LOGIN_TIMEOUT_SECONDS,
                    )
                self._finalize_connect(new_session, ok, reason, user_id=user_id, attempt=attempt)
                self._persist_connected(new_session)
                return new_session
            except Exception:
                # Fecha o websocket órfão: a thread dele continua viva e segue
                # escrevendo em bullexapi.global_value (on_close/on_error),
                # contaminando o check_connect das outras sessões.
                self._close_session(new_session)
                self.remove(user_id)
                raise

        return self._connect_with_retries(user_id, connect_new_session)

    def disconnect(self, user_id: str) -> str:
        with self.user_lock(user_id):
            session = self.get(user_id)
            if session is None:
                self.remove(user_id)
                if self.store is not None:
                    self.store.mark_disconnected(user_id, revoke_token=True)
                self._mark_probe_failure(user_id, offline=True)
                logger.info("[WORKER_DESTROYED] user_id=%s", user_id)
                logger.info("[SESSION-DISCONNECTED] %s", user_id)
                return user_id
            with self._session_context(session):
                try:
                    session.client.logout()
                except Exception:
                    logger.exception("falha ao executar logout da sessao %s", session.user_id)
                try:
                    session.client.api.close()
                except Exception:
                    logger.exception("falha ao fechar websocket da sessao %s", session.user_id)
            self.remove(user_id)
            if self.store is not None:
                self.store.mark_disconnected(user_id, revoke_token=True)
            self._mark_probe_failure(user_id, offline=True)
            logger.info("[WORKER_DESTROYED] user_id=%s", user_id)
            logger.info("[SESSION-DISCONNECTED] %s", user_id)
            return user_id

    def reconnect(self, user_id: str) -> ManagedSession:
        """Reconecta a sessão do usuário.

        Depois de um restart do processo (deploy), ``self.sessions`` fica
        vazio para todo mundo — sem esse fallback, ``/sessions/reconnect``
        devolvia ``SESSION_NOT_FOUND`` (404) sempre no primeiro poll pós-deploy,
        mesmo havendo SSID persistido no SQLite. Ver ``BULLEX_CREDENCIAIS.md``
        "Reconexão sem vazar status cru".
        """
        with self.user_lock(user_id):
            if self.get(user_id) is None:
                logger.info(
                    "[SESSION-RECONNECT-NO-MEMORY] user_id=%s action=restore_on_demand",
                    user_id,
                )
                # Reconexão explícita: ignora o cooldown do restore passivo.
                return self.restore_on_demand(user_id, force=True)
            session = self.require(user_id)
            return self._attempt_reconnect(session, "MANUAL")

    def _is_session_alive(self, session: ManagedSession) -> bool:
        check_connect = getattr(session.client, "check_connect", None)
        if callable(check_connect) and not bool(check_connect()):
            return False

        websocket_alive = getattr(session.client, "websocket_alive", None)
        if callable(websocket_alive):
            return bool(websocket_alive())
        if websocket_alive is not None:
            return bool(websocket_alive)

        api = getattr(session.client, "api", None)
        api_websocket_alive = getattr(api, "websocket_alive", None)
        if callable(api_websocket_alive):
            return bool(api_websocket_alive())
        if api_websocket_alive is not None:
            return bool(api_websocket_alive)

        return True

    def _attempt_reconnect(self, session: ManagedSession, reason: str) -> ManagedSession:
        user_id = session.user_id
        logger.info("[SESSION-RECONNECT-START] %s", user_id)
        old_client = session.client

        def reconnect_attempt(attempt: int) -> ManagedSession:
            new_session = ManagedSession(
                user_id=user_id,
                client=create_bullex_client(session.email or "", session.password or ""),
                email=session.email,
                password=session.password,
                sms_code=session.sms_code,
                desired_mode="REAL",
                requires_2fa=session.requires_2fa,
                state=SessionState(SSID=session.state.SSID),
            )
            try:
                old_client.api.close()
            except Exception:
                pass

            restore_with_ssid = getattr(new_session.client, "restore_with_ssid", None)
            if new_session.state.SSID and callable(restore_with_ssid):
                self._set_login_progress(user_id, "AUTHENTICATING", attempt=attempt, active=True)
                logger.info("[LOGIN_AUTH] user_id=%s attempt=%s mode=ssid_restore", user_id, attempt)
                with self._session_context(new_session):
                    ok, connect_reason = self._run_with_timeout(
                        lambda: restore_with_ssid(str(new_session.state.SSID)),
                        timeout_seconds=LOGIN_TIMEOUT_SECONDS,
                    )
                self._finalize_connect(new_session, ok, connect_reason, user_id=user_id, attempt=attempt)
                self.upsert(new_session)
                self._persist_connected(new_session)
                logger.info("[SESSION-RECONNECT-OK] %s restored_ssid=true", user_id)
                return new_session

            if not session.email or not session.password:
                logger.warning("[SESSION-RECONNECT-FAILED] %s missing_credentials", user_id)
                self._mark_disconnected(user_id, reason)

            self._set_login_progress(user_id, "AUTHENTICATING", attempt=attempt, active=True)
            logger.info("[LOGIN_AUTH] user_id=%s attempt=%s mode=password", user_id, attempt)
            with self._session_context(new_session):
                ok, connect_reason = self._run_with_timeout(
                    lambda: new_session.client.connect(new_session.sms_code),
                    timeout_seconds=LOGIN_TIMEOUT_SECONDS,
                )
            self._finalize_connect(new_session, ok, connect_reason, user_id=user_id, attempt=attempt)
            self.upsert(new_session)
            self._persist_connected(new_session)
            logger.info("[SESSION-RECONNECT-OK] %s restored_ssid=false", user_id)
            return new_session

        try:
            return self._connect_with_retries(user_id, reconnect_attempt)
        except ServiceError as exc:
            logger.warning("[SESSION-RECONNECT-FAILED] %s %s", user_id, exc.message)
            self._mark_disconnected(user_id, exc.message)

    def restore_on_demand(self, user_id: str, *, force: bool = False) -> ManagedSession:
        """Recria a sessão a partir do SSID persistido.

        Args:
            user_id: Usuário autenticado.
            force: Ignora o cooldown de retry. Use em reconexão explícita
                (botão do painel / ``/sessions/reconnect``); o poll de status
                deve respeitar o cooldown para não martelar a corretora.
        """
        existing = self.get(user_id)
        if existing is not None:
            logger.info(
                "[SESSION_RESTORE_SKIPPED] user_id=%s reason=already_active",
                user_id,
            )
            return existing

        with self.user_lock(user_id):
            existing = self.get(user_id)
            if existing is not None:
                logger.info(
                    "[SESSION_RESTORE_SKIPPED] user_id=%s reason=already_active",
                    user_id,
                )
                return existing

            retry_at = self._restore_retry_at.get(user_id, 0.0)
            now = time.monotonic()
            if not force and now < retry_at:
                logger.info(
                    "[SESSION_RESTORE_SKIPPED] user_id=%s reason=cooldown retry_in=%.1f",
                    user_id,
                    retry_at - now,
                )
                raise ServiceError(SESSION_DISCONNECTED, 409)

            logger.info("[SESSION_RESTORE_ON_DEMAND] user_id=%s", user_id)
            if self.store is None:
                raise ServiceError(SESSION_NOT_FOUND, 404)
            persisted = self.store.load_connected_user(user_id)
            if persisted is None:
                raise ServiceError(SESSION_NOT_FOUND, 404)
            session = ManagedSession(
                user_id=persisted.user_id,
                client=create_bullex_client(persisted.email, ""),
                email=persisted.email,
                desired_mode="REAL",
                state=SessionState(SSID=persisted.session_token),
            )
            restore_with_ssid = getattr(session.client, "restore_with_ssid", None)
            if not callable(restore_with_ssid):
                # SSID inútil sem método de restore: revoga para não ficar
                # retentando o mesmo token em todo poll de status.
                self.store.mark_disconnected(session.user_id, revoke_token=True)
                logger.warning(
                    "[SESSION_RESTORE] status=unsupported reason=no_ssid_restore_method user_id=%s",
                    session.user_id,
                )
                raise ServiceError(SESSION_DISCONNECTED, 409)

            restore_failure_reason = "unknown"
            try:
                with self._session_context(session):
                    ok, reason = restore_with_ssid(persisted.session_token)
                    # Só é "motivo de falha" quando o restore de fato falhou —
                    # senão um erro posterior (ex.: modo REAL) revogaria um SSID
                    # que a corretora acabou de aceitar.
                    if not ok:
                        restore_failure_reason = str(reason or "restore_rejected")
                self._finalize_connect(session, ok, reason, user_id=session.user_id, attempt=1)
                self.upsert(session)
                self._persist_connected(session)
                self._clear_login_progress(session.user_id)
                self._restore_retry_at.pop(session.user_id, None)
                logger.info("[SESSION_RESTORE] user_id=%s status=success", session.user_id)
                return session
            except Exception as exc:
                self._close_session(session)
                self.remove(session.user_id)
                self._restore_retry_at[session.user_id] = (
                    time.monotonic() + SESSION_RESTORE_COOLDOWN_SECONDS
                )
                # Só revoga o SSID quando a corretora respondeu que ele não vale
                # mais. Timeout/rate limit/erro de rede são transitórios: manter
                # o token deixa o próximo restore funcionar sem pedir senha.
                ssid_rejected = restore_failure_reason in {"invalid_ssid", "restore_rejected"}
                self.store.mark_disconnected(session.user_id, revoke_token=ssid_rejected)
                if restore_failure_reason == "invalid_ssid":
                    logger.warning(
                        "[SESSION_RESTORE] user_id=%s status=unsupported reason=broker_invalidates_ssid",
                        session.user_id,
                    )
                else:
                    logger.warning(
                        "[SESSION_RESTORE] user_id=%s status=failed reason=%s",
                        session.user_id,
                        restore_failure_reason if restore_failure_reason != "unknown" else type(exc).__name__,
                    )
                raise ServiceError(SESSION_DISCONNECTED, 409) from exc

    def persistence_debug(self) -> dict[str, Any]:
        if self.store is None:
            return {
                "stored_sessions": 0,
                "users": [],
            }
        return self.store.persistence_debug()

    def _mark_disconnected(self, user_id: str, reason: str) -> None:
        logger.warning("[SESSION-DISCONNECTED] %s", user_id)
        session = self.get(user_id)
        if session is not None:
            try:
                session.client.api.close()
            except Exception:
                logger.warning(
                    "[SESSION_CLOSE_FAILED] user_id=%s reason=%s",
                    user_id,
                    reason,
                )
        self.remove(user_id)
        logger.info("[WORKER_DESTROYED] user_id=%s", user_id)
        if self.store is not None:
            self.store.mark_disconnected(user_id)
        self._mark_probe_failure(user_id, offline=True)
        raise ServiceError(SESSION_DISCONNECTED, 409) from None

    def _persist_connected(self, session: ManagedSession) -> None:
        if self.store is None or session.requires_2fa:
            return
        if not session.real_mode_confirmed:
            logger.warning(
                "[REAL_MODE_FAILED] user_id=%s active_mode=%s reason=persist_blocked",
                session.user_id,
                session.active_mode or "UNKNOWN",
            )
            return
        token = session.state.SSID
        if not session.email or not token:
            return
        self.store.save_connected(
            session.user_id,
            session.email,
            session.desired_mode,
            str(token),
        )

    def _finalize_connect(
        self,
        session: ManagedSession,
        ok: bool,
        reason: Any,
        *,
        user_id: str,
        attempt: int,
    ) -> None:
        if ok:
            session.requires_2fa = False
            self._populate_ready_state(session, user_id=user_id, attempt=attempt)
            return

        session.requires_2fa = reason == "2FA"
        if session.requires_2fa:
            return
        if is_requests_limit_error(reason):
            remaining = note_requests_limit(reason)
            raise ServiceError(
                BULLEX_REQUESTS_LIMIT_EXCEEDED,
                429,
                rate_limit_error_payload(remaining),
            )
        broker_code = _extract_broker_error_code(reason)
        if broker_code == "invalid_credentials":
            # Guarda a resposta crua da corretora: só o `code` não distingue
            # senha errada de conta bloqueada / limite de tentativas, e o
            # painel mostra "email ou senha inválidos" nos três casos.
            # Identificador mascarado + tamanho da senha: revela autofill do
            # navegador (senha do painel no campo da corretora) sem registrar
            # credencial nenhuma no log.
            email_value = str(getattr(session, "email", "") or "")
            local, _, domain = email_value.partition("@")
            masked_email = f"{local[:3]}***@{domain}" if domain else "(vazio)"
            logger.warning(
                "[BROKER_LOGIN_REJECTED] user_id=%s code=%s identifier=%s password_length=%s detail=%s",
                user_id,
                broker_code,
                masked_email,
                len(str(getattr(session, "password", "") or "")),
                str(reason)[:300],
            )
            raise ServiceError("invalid_credentials", 401)
        raise ServiceError(f"falha ao conectar: {reason}", 401)

    def _close_session(self, session: ManagedSession) -> None:
        with self._session_context(session):
            try:
                session.client.api.close()
            except Exception:
                logger.exception("falha ao fechar sessao anterior de %s", session.user_id)

    @contextmanager
    def _session_context(
        self,
        session: ManagedSession,
        *,
        gate_timeout: float | None = None,
    ):
        """Ativa SessionGlobals da sessão no ContextVar (concorrência até o gate).

        Args:
            session: Sessão gerenciada.
            gate_timeout: Se informado, desiste da fila do semáforo após N
                segundos (evita /account e candles segurarem 50s+ sob carga).
        """
        acquired = False
        token = None
        try:
            if gate_timeout is None:
                self._call_gate.acquire()
                acquired = True
            else:
                acquired = self._call_gate.acquire(timeout=max(0.05, float(gate_timeout)))
                if not acquired:
                    logger.warning(
                        "[CALL_GATE_TIMEOUT] user_id=%s timeout_seconds=%s",
                        session.user_id,
                        gate_timeout,
                    )
                    raise ServiceError("BULLEX_TEMPORARY_UNAVAILABLE", 503)
            token = self._activate_session(session)
            try:
                yield
            finally:
                session.state = self._capture()
        finally:
            if token is not None:
                global_value.reset_session_globals(token)
            if acquired:
                self._call_gate.release()

    def _activate_session(self, session: ManagedSession):
        """Ativa SessionGlobals da sessão no ContextVar e no ``api``.

        Args:
            session: Sessão gerenciada com ``state`` e client Bullex.

        Returns:
            Token do ContextVar para reset no fim do ``_session_context``.
        """
        owner_api = getattr(session.client, "api", None)
        sg = None
        if owner_api is not None:
            sg = getattr(owner_api, "_session_globals", None)
        if sg is None:
            sg = global_value.SessionGlobals(
                check_websocket_if_connect=session.state.check_websocket_if_connect,
                ssl_Mutual_exclusion=session.state.ssl_Mutual_exclusion,
                ssl_Mutual_exclusion_write=session.state.ssl_Mutual_exclusion_write,
                SSID=session.state.SSID,
                check_websocket_if_error=session.state.check_websocket_if_error,
                websocket_error_reason=session.state.websocket_error_reason,
                balance_id=session.state.balance_id,
            )
        else:
            # Objeto vivo no api pode estar mais fresco (WS entre contexts).
            # SSID/balance_id do state ganham se o state tiver valor (reconnect).
            if session.state.SSID is not None:
                sg.SSID = session.state.SSID
            if session.state.balance_id is not None:
                sg.balance_id = session.state.balance_id
            if session.state.check_websocket_if_connect is not None:
                sg.check_websocket_if_connect = session.state.check_websocket_if_connect
            sg.check_websocket_if_error = session.state.check_websocket_if_error
            sg.websocket_error_reason = session.state.websocket_error_reason
        if owner_api is not None:
            owner_api._session_globals = sg
            sg.balance_id_owner = id(owner_api)
        else:
            sg.balance_id_owner = None
        return global_value.set_session_globals(sg)

    def _capture(self) -> SessionState:
        """Lê o SessionGlobals ativo (ContextVar) para persistir em SessionState."""
        sg = global_value.get_session_globals()
        return SessionState(
            check_websocket_if_connect=sg.check_websocket_if_connect,
            ssl_Mutual_exclusion=sg.ssl_Mutual_exclusion,
            ssl_Mutual_exclusion_write=sg.ssl_Mutual_exclusion_write,
            SSID=sg.SSID,
            check_websocket_if_error=sg.check_websocket_if_error,
            websocket_error_reason=sg.websocket_error_reason,
            balance_id=sg.balance_id,
        )


def normalize_mode(mode: str) -> str:
    normalized = (mode or "").strip().upper()
    if normalized not in ALLOWED_BALANCE_MODES:
        raise ServiceError("mode invalido. Use PRACTICE, REAL ou TOURNAMENT")
    return normalized


def read_max_concurrent_api_calls() -> int:
    """Lê o teto de chamadas concorrentes à corretora (default 3 pós-isolamento).

    Returns:
        Inteiro >= 1 (``BULLEX_MAX_CONCURRENT_API_CALLS``).

    Raises:
        RuntimeError: Se o valor da env não for inteiro.
    """
    raw_value = os.getenv("BULLEX_MAX_CONCURRENT_API_CALLS", "3").strip()
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise RuntimeError("BULLEX_MAX_CONCURRENT_API_CALLS must be an integer") from exc

    return max(1, value)


def normalize_action(action: str) -> str:
    normalized = (action or "").strip().lower()
    if normalized not in ALLOWED_ACTIONS:
        raise ServiceError("action invalida. Use call ou put")
    return normalized


# A BullEx nomeia o par de MERCADO ABERTO com sufixo `-op` (`EURUSD-op`), e o
# par sintetico com `-OTC`. O El Capo usa o nome nu (`EURUSD`) para o mercado
# aberto em todo o resto do sistema — inclusive nas velas, que funcionam assim.
# Ate 08/09/2026 essa diferenca fazia o catalogo de opcao ser invisivel: o mapa
# de canais era indexado por `EURUSD-OP` e a consulta pedia `EURUSD`, entao todo
# par aberto voltava `payout=None` e morria em PAYOUT_UNAVAILABLE. Medido no
# mesmo dia: 41 ordens OTC e zero abertas, com EURUSD-op ABERTO e payout 84/85.
BROKER_OPEN_SUFFIX = "-op"


def normalize_binary_active(active: str) -> str:
    return (active or "").strip().upper()


def to_internal_active(broker_name: str) -> str:
    """Nome da corretora -> nome do El Capo (`EURUSD-op` -> `EURUSD`).

    Args:
        broker_name: Nome como aparece no catalogo da corretora.

    Returns:
        Nome normalizado usado internamente. `-OTC` e qualquer outro passam
        intactos.
    """
    simbolo = normalize_binary_active(broker_name)
    sufixo = BROKER_OPEN_SUFFIX.upper()
    return simbolo[: -len(sufixo)] if simbolo.endswith(sufixo) else simbolo


def to_broker_active(active: str) -> str:
    """Nome do El Capo -> nome da corretora (`EURUSD` -> `EURUSD-op`).

    So o par de mercado aberto ganha sufixo. `-OTC` vai como esta, e e por isso
    que o caminho OTC nao muda em nada.

    Args:
        active: Nome interno do ativo.

    Returns:
        Nome a enviar para a corretora (catalogo de opcao e compra).
    """
    simbolo = normalize_binary_active(active)
    if not simbolo or simbolo.endswith("-OTC") or simbolo.endswith(BROKER_OPEN_SUFFIX.upper()):
        return active
    return f"{simbolo}{BROKER_OPEN_SUFFIX}"


def ensure_binary_asset_allowed(active: str) -> str:
    normalized = normalize_binary_active(active)
    if normalized not in BINARY_ALLOWED_ASSET_SET:
        raise ServiceError(ASSET_NOT_ALLOWED, 400)
    return normalized


def build_success(data: Any) -> dict[str, Any]:
    return {"ok": True, "data": data, "error": None}


def build_error(
    message: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {"ok": False, "data": data, "error": message}


def with_login_progress(payload: dict[str, Any], user_id: str) -> dict[str, Any]:
    data = payload.get("data")
    if isinstance(data, dict):
        data["login_progress"] = session_manager.login_progress_payload(user_id)
    return payload


def require_user_id(x_user_id: str | None) -> str:
    user_id = (x_user_id or "").strip()
    if not user_id:
        raise ServiceError("header x-user-id e obrigatorio", 400)
    return user_id


def ensure_mode_matches(client: Bullex, expected_mode: str) -> str:
    current_mode = normalize_mode(client.get_balance_mode())
    if current_mode != expected_mode:
        raise ServiceError(
            f"conta ativa em {current_mode}; esperado {expected_mode} para esta operacao",
            409,
        )
    return current_mode


def ensure_session_ready(session: ManagedSession) -> None:
    if session.requires_2fa:
        raise ServiceError("sessao aguardando 2FA", 409)
    if not session.client.check_connect():
        raise ServiceError(SESSION_DISCONNECTED, 409)


def parse_order_id(order_id: str) -> int | str:
    return int(order_id) if order_id.isdigit() else order_id


def build_cache_key(path: str, params: dict[str, Any] | None = None) -> str:
    if not params:
        return path
    normalized_params = dict(params)
    if path == "/candles":
        try:
            interval = int(normalized_params.get("interval") or 60)
        except (TypeError, ValueError):
            interval = 60
        try:
            endtime = int(normalized_params.get("endtime") or time.time())
        except (TypeError, ValueError):
            endtime = int(time.time())
        normalized_params["endtime"] = int(endtime // interval) * interval
    parts = [f"{key}={normalized_params[key]}" for key in sorted(normalized_params)]
    return f"{path}?{'&'.join(parts)}"


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
    logger.info(
        "[%s] user_id=%s path=%s source=%s status_code=%s ms=%s",
        label,
        user_id,
        path,
        source,
        status_code,
        int((time.monotonic() - started_at) * 1000),
    )


def add_stale_warning(payload: dict[str, Any]) -> dict[str, Any]:
    cloned = dict(payload)
    cloned["warning"] = "BULLEX_TEMPORARY_UNAVAILABLE"
    data = cloned.get("data")
    if isinstance(data, dict):
        data = dict(data)
        data["from_cache"] = True
        cloned["data"] = data
    cloned["meta"] = {
        **(cloned.get("meta") if isinstance(cloned.get("meta"), dict) else {}),
        "source": "cache",
        "stale": True,
    }
    return cloned


def stale_cached_probe(user_id: str, cache_key: str, *, max_age_seconds: int = STALE_MARKET_DATA_SECONDS) -> tuple[int, dict[str, Any]] | None:
    cached = session_manager.get_last_probe(user_id, cache_key)
    if cached is None:
        return None
    probe = session_manager.get_probe_state(user_id).responses.get(cache_key)
    if probe is None or time.time() > probe.expires_at + max_age_seconds:
        return None
    return cached[0], add_stale_warning(cached[1])


def normalize_balance_value(balance: Any) -> float | None:
    if balance is None:
        return None
    return float(balance)


def real_mode_service_error(active_mode: Any) -> ServiceError:
    try:
        mode = normalize_mode(active_mode)
    except ServiceError:
        mode = str(active_mode or "UNKNOWN").strip().upper() or "UNKNOWN"
    return ServiceError(
        BULLEX_ACTIVE_MODE_NOT_REAL,
        409,
        {
            "connected": True,
            "active_mode": mode,
            "mode": mode,
            "balance": None,
        },
    )


def force_real_mode(session: ManagedSession, *, user_id: str) -> str:
    session.desired_mode = "REAL"
    session.real_mode_confirmed = False
    logger.info("[REAL_MODE_FORCED] user_id=%s", user_id)
    active_mode = "UNKNOWN"
    for attempt in range(1, 4):
        try:
            before_mode = normalize_mode(session.client.get_balance_mode())
        except ServiceError:
            before_mode = "UNKNOWN"
        logger.info(
            "[CHANGE_BALANCE_REAL_ATTEMPT] user_id=%s attempt=%s active_mode_before=%s",
            user_id,
            attempt,
            before_mode,
        )
        logger.info("[CHANGE_BALANCE_REAL_CALL] user_id=%s active_mode=%s", user_id, before_mode)
        try:
            session.client.change_balance("REAL")
            logger.info(
                "[CHANGE_BALANCE_REAL_RESULT] user_id=%s attempt=%s result=called",
                user_id,
                attempt,
            )
        except Exception:
            logger.warning(
                "[CHANGE_BALANCE_REAL_RESULT] user_id=%s attempt=%s result=exception",
                user_id,
                attempt,
                exc_info=True,
            )
            logger.warning(
                "[CHANGE_BALANCE_REAL_FAILED] user_id=%s active_mode=%s reason=exception",
                user_id,
                before_mode,
                exc_info=True,
            )
        time.sleep(1)
        try:
            active_mode = normalize_mode(session.client.get_balance_mode())
        except ServiceError:
            active_mode = "UNKNOWN"
        session.active_mode = active_mode
        logger.info(
            "[ACTIVE_MODE_AFTER_CHANGE] user_id=%s attempt=%s active_mode=%s",
            user_id,
            attempt,
            active_mode,
        )
        if active_mode == "REAL":
            session.real_mode_confirmed = True
            # change_balance pode ter setado id do profile cacheado — pina o fresco.
            fresh_real_id, _, _ = discover_balance_ids(
                session.client,
                prefer_fresh=True,
            )
            if fresh_real_id is not None:
                pin_global_balance_id(
                    session.client,
                    fresh_real_id,
                    user_id=user_id,
                    owned_previous=session.state.balance_id,
                )
            session.state.balance_id = global_value.balance_id
            logger.info("[CHANGE_BALANCE_REAL_RESULT] user_id=%s attempt=%s result=confirmed", user_id, attempt)
            logger.info("[CHANGE_BALANCE_REAL_OK] user_id=%s active_mode=%s", user_id, active_mode)
            logger.info("[REAL_MODE_CONFIRMED] user_id=%s active_mode=%s", user_id, active_mode)
            return active_mode
        if attempt < 3:
            logger.warning(
                "[CHANGE_BALANCE_REAL_RESULT] user_id=%s attempt=%s result=still_%s",
                user_id,
                attempt,
                active_mode,
            )

    session.real_mode_confirmed = False
    logger.warning("[REAL_MODE_FAILED] user_id=%s active_mode=%s", user_id, active_mode)
    logger.warning("[CHANGE_BALANCE_REAL_FAILED] user_id=%s active_mode=%s", user_id, active_mode)
    raise real_mode_service_error(active_mode)


def is_user_balance_not_found_error(message: Any) -> bool:
    """Detecta rejeição da corretora por user_balance_id inválido/ausente."""
    text = str(message or "").strip().lower()
    return "user balance not found" in text or "balance not found" in text


def _sync_profile_balances(client: Bullex, balances: list[Any]) -> None:
    """
    Atualiza o profile em memória com a lista fresca de balances.

    ``change_balance()`` / ``get_balance_mode()`` do bullexapi leem
    ``get_profile_ansyc()["balances"]`` (cache do WS de login). Sem esse sync,
    o buy continua com um ``balance_id`` antigo mesmo com saldo REAL válido.
    """
    api = getattr(client, "api", None)
    profile = getattr(api, "profile", None) if api is not None else None
    if profile is None:
        return
    try:
        msg = getattr(profile, "msg", None)
        if isinstance(msg, dict):
            msg["balances"] = balances
        if hasattr(profile, "balances"):
            profile.balances = balances
    except Exception:
        logger.exception("[PROFILE_BALANCES_SYNC_FAILED]")


def fetch_balances_list(
    client: Bullex,
    *,
    prefer_fresh: bool = True,
) -> tuple[list[Any] | None, str]:
    """
    Obtém a lista de balances da corretora.

    Prefere ``get_balances()`` (pedido fresco no websocket). O profile cacheado
    do login pode trazer ``balance_id`` REAL obsoleto → ``User balance not found``
    mesmo com dinheiro na conta.
    """
    if prefer_fresh:
        get_balances = getattr(client, "get_balances", None)
        if callable(get_balances):
            try:
                raw = get_balances()
                items = raw.get("msg") if isinstance(raw, dict) else None
                if isinstance(items, list) and items:
                    _sync_profile_balances(client, items)
                    return items, "get_balances"
            except Exception:
                logger.exception("[BALANCES_FETCH_FAILED] source=get_balances")

    get_profile = getattr(client, "get_profile_ansyc", None)
    if callable(get_profile):
        try:
            profile = get_profile()
            items = profile.get("balances") if isinstance(profile, dict) else None
            if isinstance(items, list) and items:
                return items, "profile_cache"
        except Exception:
            logger.exception("[BALANCES_FETCH_FAILED] source=profile_cache")

    get_balances = getattr(client, "get_balances", None)
    if callable(get_balances) and not prefer_fresh:
        try:
            raw = get_balances()
            items = raw.get("msg") if isinstance(raw, dict) else None
            if isinstance(items, list) and items:
                _sync_profile_balances(client, items)
                return items, "get_balances"
        except Exception:
            logger.exception("[BALANCES_FETCH_FAILED] source=get_balances_fallback")
    return None, "none"


def pick_real_and_practice_balance_ids(
    balances: list[Any],
) -> tuple[Any, Any]:
    """Escolhe ids REAL (type=1) e PRACTICE (type=4); REAL com saldo > 0 tem prioridade."""
    real_candidates: list[dict[str, Any]] = []
    practice_balance_id = None
    for balance in balances:
        if not isinstance(balance, dict):
            continue
        balance_type = balance.get("type")
        if balance_type == 1:
            real_candidates.append(balance)
        elif balance_type == 4:
            practice_balance_id = balance.get("id")

    def _score(item: dict[str, Any]) -> tuple[bool, float, int]:
        try:
            amount = float(item.get("amount") or 0)
        except (TypeError, ValueError):
            amount = 0.0
        try:
            balance_id = int(item.get("id") or 0)
        except (TypeError, ValueError):
            balance_id = 0
        return (amount > 0, amount, balance_id)

    real_candidates.sort(key=_score, reverse=True)
    real_balance_id = real_candidates[0].get("id") if real_candidates else None
    return real_balance_id, practice_balance_id


def discover_balance_ids(
    client: Bullex,
    *,
    prefer_fresh: bool = True,
) -> tuple[Any, Any, bool]:
    balances, source = fetch_balances_list(client, prefer_fresh=prefer_fresh)
    if balances is None:
        return None, None, False
    real_balance_id, practice_balance_id = pick_real_and_practice_balance_ids(balances)
    logger.info(
        "[BALANCE_IDS_DISCOVERED] source=%s real=%s practice=%s count=%s",
        source,
        real_balance_id,
        practice_balance_id,
        len(balances),
    )
    return real_balance_id, practice_balance_id, True


def pin_global_balance_id(
    client: Bullex,
    balance_id: Any,
    *,
    user_id: str,
    owned_previous: Any = None,
) -> None:
    """Força ``global_value.balance_id`` usado pelo buyv3 e re-subscribe posições.

    Args:
        client: Cliente Bullex da sessão ativa.
        balance_id: Id REAL fresco a pinhar.
        user_id: Usuário dono da sessão (só para logs).
        owned_previous: Se informado, só faz unsubscribe desse id (nunca do
            balance_id de outro usuário que ainda esteja no global).
    """
    previous = global_value.balance_id
    if previous == balance_id and balance_id is not None:
        return
    logger.warning(
        "[REAL_BALANCE_ID_PIN] user_id=%s previous=%s pinned=%s",
        user_id,
        previous,
        balance_id,
    )
    position_change = getattr(client, "position_change_all", None)
    unsubscribe_id = owned_previous if owned_previous is not None else previous
    # Não desinscreve id de outro usuário no WS deste client.
    if (
        unsubscribe_id is not None
        and unsubscribe_id == previous
        and callable(position_change)
    ):
        try:
            position_change("unsubscribeMessage", unsubscribe_id)
        except Exception:
            logger.exception(
                "[REAL_BALANCE_ID_UNSUBSCRIBE_FAILED] user_id=%s balance_id=%s",
                user_id,
                unsubscribe_id,
            )
    global_value.balance_id = balance_id
    owner_api = getattr(client, "api", None)
    if owner_api is not None:
        global_value.balance_id_owner = id(owner_api)
    if balance_id is not None and callable(position_change):
        try:
            position_change("subscribeMessage", balance_id)
        except Exception:
            logger.exception(
                "[REAL_BALANCE_ID_SUBSCRIBE_FAILED] user_id=%s balance_id=%s",
                user_id,
                balance_id,
            )


def resolve_active_balance_mode(session: ManagedSession, *, user_id: str) -> str | None:
    """Resolve o modo ativo; se o balance_id estiver stale, rediscobre e pina o REAL.

    Returns:
        ``REAL`` / ``PRACTICE`` / ``TOURNAMENT`` / ``None`` se não for possível.
    """
    get_mode = getattr(session.client, "get_balance_mode", None)
    mode = None
    if callable(get_mode):
        try:
            raw = get_mode()
            mode = str(raw).strip().upper() if raw else None
        except Exception:
            logger.exception("[ACTIVE_MODE_READ_FAILED] user_id=%s", user_id)
            mode = None
    if mode in ALLOWED_BALANCE_MODES:
        return mode

    # balance_id global/errado → get_balance_mode devolve None. Recupera via
    # catálogo fresco antes de devolver "conta sem modo" ao gateway.
    try:
        real_balance_id, _, discovered = discover_balance_ids(
            session.client,
            prefer_fresh=True,
        )
    except Exception:
        logger.exception("[ACTIVE_MODE_DISCOVER_FAILED] user_id=%s", user_id)
        real_balance_id, discovered = None, False

    if real_balance_id is not None:
        pin_global_balance_id(
            session.client,
            real_balance_id,
            user_id=user_id,
            owned_previous=session.state.balance_id,
        )
        session.state.balance_id = real_balance_id
        if callable(get_mode):
            try:
                raw = get_mode()
                mode = str(raw).strip().upper() if raw else None
            except Exception:
                mode = None
        if mode in ALLOWED_BALANCE_MODES:
            logger.info(
                "[ACTIVE_MODE_RECOVERED] user_id=%s mode=%s balance_id=%s",
                user_id,
                mode,
                real_balance_id,
            )
            return mode
        logger.info(
            "[ACTIVE_MODE_RECOVERED] user_id=%s mode=REAL balance_id=%s source=discovered",
            user_id,
            real_balance_id,
        )
        return "REAL"

    if session.real_mode_confirmed or str(session.active_mode or "").upper() == "REAL":
        logger.warning(
            "[ACTIVE_MODE_KEPT_FROM_SESSION] user_id=%s discovered=%s",
            user_id,
            discovered,
        )
        return "REAL"
    return None


def ensure_real_balance_id_for_buy(session: ManagedSession, *, user_id: str) -> Any:
    """
    Garante que global_value.balance_id (usado pelo buyv3) aponta para a conta REAL.

    Após trades o balance_id pode ficar None/stale: get_balance_mode() até confirma REAL
    depois do force, mas o buy ainda falha com "User balance not found" porque o
    profile cacheado guarda um id REAL antigo. Sempre consulta get_balances fresco
    e **fixixa** o id descoberto (change_balance sozinho não basta).
    """
    real_balance_id, _, balances_discovered = discover_balance_ids(
        session.client,
        prefer_fresh=True,
    )
    current_id = global_value.balance_id
    if real_balance_id is None:
        if balances_discovered:
            logger.warning(
                "[REAL_BALANCE_ID_MISSING] user_id=%s current_balance_id=%s",
                user_id,
                current_id,
            )
            raise ServiceError("REAL_BALANCE_ID_MISSING", 409)
        if current_id is None:
            raise ServiceError("REAL_BALANCE_ID_MISSING", 409)
        session.state.balance_id = current_id
        logger.info(
            "[REAL_BUY_BALANCE_ID] user_id=%s balance_id=%s source=current",
            user_id,
            current_id,
        )
        return current_id

    if current_id != real_balance_id:
        logger.warning(
            "[REAL_BALANCE_ID_RESYNC] user_id=%s previous=%s real=%s",
            user_id,
            current_id,
            real_balance_id,
        )
        try:
            session.client.change_balance("REAL")
        except Exception:
            logger.exception(
                "[REAL_BALANCE_ID_RESYNC_FAILED] user_id=%s falling_back_to_direct_set",
                user_id,
            )

    # change_balance() lê o profile cacheado e pode reaplicar o id velho —
    # sempre pinamos o id fresco descoberto via get_balances.
    pin_global_balance_id(
        session.client,
        real_balance_id,
        user_id=user_id,
        owned_previous=session.state.balance_id,
    )
    if global_value.balance_id is None:
        global_value.balance_id = real_balance_id

    session.state.balance_id = global_value.balance_id
    logger.info(
        "[REAL_BUY_BALANCE_ID] user_id=%s balance_id=%s source=real",
        user_id,
        global_value.balance_id,
    )
    return global_value.balance_id


def read_separated_balances(client: Bullex) -> tuple[float | None, float | None]:
    real_balance = None
    practice_balance = None
    balances, _source = fetch_balances_list(client, prefer_fresh=True)
    if isinstance(balances, list):
        for item in balances:
            if not isinstance(item, dict):
                continue
            amount = normalize_balance_value(item.get("amount"))
            if item.get("type") == 1:
                real_balance = amount
            elif item.get("type") == 4:
                practice_balance = amount
    return real_balance, practice_balance


def build_real_only_account_response(account: dict[str, Any]) -> dict[str, Any]:
    active_mode = str(
        account.get("active_mode")
        or account.get("active_mode_from_bullex")
        or account.get("mode")
        or ""
    ).strip().upper() or None
    if bool(account.get("connected")) and active_mode != "REAL":
        blocked = {
            **account,
            "active_mode": active_mode,
            "active_mode_from_bullex": active_mode,
            "mode": active_mode,
            "balance": None,
        }
        logger.warning(
            "[PRACTICE_BALANCE_BLOCKED] user_id=%s active_mode=%s",
            account.get("user_id") or "unknown",
            active_mode,
        )
        return {
            "ok": False,
            "data": blocked,
            "error": BULLEX_ACTIVE_MODE_NOT_REAL,
        }
    return build_success(account)


def normalize_real_only_account_payload(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, dict):
        return build_real_only_account_response(data)
    return payload


def build_account_payload(session: ManagedSession) -> dict[str, Any]:
    connected = bool(session.client.check_connect())
    account = {
        "connected": connected,
        "balance": None,
        "currency": None,
        "mode": None,
        "user_id": session.user_id,
        "email": session.email,
        "requires_2fa": session.requires_2fa,
        "active_mode_real_detected": False,
        "active_mode": None,
        "active_mode_from_bullex": None,
        "balance_real": None,
        "balance_practice": None,
    }

    if connected and not session.requires_2fa:
        mode = resolve_active_balance_mode(session, user_id=session.user_id)
        if mode is None:
            logger.warning(
                "[ACCOUNT_MODE_UNRESOLVED] user_id=%s keeping_connected=%s",
                session.user_id,
                connected,
            )
            # Não derruba connected: o gateway trataria como REAL_BALANCE_NOT_DETECTED.
            if session.real_mode_confirmed or str(session.active_mode or "").upper() == "REAL":
                mode = "REAL"
            else:
                return account
        balance_real, balance_practice = read_separated_balances(session.client)
        if balance_real is None and balance_practice is None:
            current_balance = normalize_balance_value(session.client.get_balance())
            if mode == "REAL":
                balance_real = current_balance
            elif mode == "PRACTICE":
                balance_practice = current_balance
        balance = balance_real if mode == "REAL" else None
        currency = (session.client.get_currency() or "BRL") if mode == "REAL" else None
        account["balance"] = balance
        account["currency"] = currency
        account["mode"] = mode
        account["active_mode_real_detected"] = mode == "REAL"
        account["active_mode"] = mode
        account["active_mode_from_bullex"] = mode
        account["balance_real"] = balance_real
        account["balance_practice"] = balance_practice
        session.active_mode = mode
        if mode == "REAL":
            session.real_mode_confirmed = True
            logger.info("[ACCOUNT_REAL_CONNECTED] user_id=%s email=%s", session.user_id, session.email)
            logger.info("[REAL_BALANCE_DETECTED] user_id=%s balance=%s", session.user_id, balance_real)
            if balance == 0:
                account["real_balance_warning"] = "BALANCE_ZERO"
                logger.info("[BALANCE_ZERO_NOT_DISCONNECTED] user_id=%s mode=REAL", session.user_id)
        elif balance_practice is not None:
            logger.warning(
                "[PRACTICE_BALANCE_IGNORED] user_id=%s balance=%s",
                session.user_id,
                balance_practice,
            )

    return account


def normalize_active(symbol: Any, active_id: Any) -> dict[str, Any]:
    return {
        "active_id": active_id,
        "symbol": str(symbol),
        "name": str(symbol),
        "enabled": True,
    }


def normalize_assets(raw_assets: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_assets, dict):
        return []
    available_assets = {
        normalize_binary_active(symbol): normalize_active(normalize_binary_active(symbol), active_id)
        for symbol, active_id in raw_assets.items()
    }
    filtered_assets = []
    for symbol in BINARY_ALLOWED_ASSETS:
        asset = available_assets.get(symbol)
        if asset is None:
            logger.warning("[BINARY ASSET MISSING] %s", symbol)
            continue
        filtered_assets.append(asset)
    return filtered_assets


def normalize_candle(candle: dict[str, Any]) -> dict[str, Any]:
    return {
        "from": candle.get("from") or candle.get("at") or candle.get("id"),
        "open": candle.get("open"),
        "close": candle.get("close"),
        "min": candle.get("min") if "min" in candle else candle.get("low"),
        "max": candle.get("max") if "max" in candle else candle.get("high"),
        "volume": candle.get("volume", 0),
    }


def normalize_candles(raw_candles: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_candles, list):
        return []
    return [normalize_candle(candle) for candle in raw_candles if isinstance(candle, dict)]


def clone_assets(assets: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    if assets is None:
        return None
    return [dict(asset) for asset in assets]


def cached_assets_if_valid(state: InstrumentsCacheState) -> list[dict[str, Any]] | None:
    cached = clone_assets(state.assets)
    if cached is not None and time.monotonic() < state.expires_at:
        return cached
    return None


def stale_assets_if_available(state: InstrumentsCacheState) -> list[dict[str, Any]] | None:
    return clone_assets(state.assets)


def instruments_backoff_seconds(failure_count: int) -> int:
    index = min(max(failure_count, 1), len(INSTRUMENTS_BACKOFF_SECONDS)) - 1
    return INSTRUMENTS_BACKOFF_SECONDS[index]


def read_assets_uncached(client: Bullex) -> list[dict[str, Any]]:
    """Lê os instrumentos da corretora — só os canais que ela responde.

    `update_ACTIVES_OPCODE` da biblioteca pede binary/turbo e **depois**
    crypto, forex e cfd, com um deadline compartilhado. Sondados um a um em
    2026-09-04 (`GET /instruments/probe`), os três últimos deram
    **TimeoutError** — a BullEx não serve esses canais. Como eles consumiam
    todo o orçamento, o `/assets` estourava em 100% das chamadas (com 8s e
    também com 25s), o robô caía na lista fixa `BINARY_ALLOWED_ASSETS` e
    ninguém nunca conseguia enumerar o que a corretora de fato oferece.

    Aqui só o passo de binary/turbo é executado. Se um dia a corretora passar
    a servir forex/cfd, a sonda mostra e este trecho volta a incluí-los.
    """
    client.get_ALL_Binary_ACTIVES_OPCODE(timeout=INSTRUMENTS_TIMEOUT_SECONDS)
    return normalize_assets(client.get_all_ACTIVES_OPCODE())


def read_assets(client: Bullex, *, user_id: str) -> list[dict[str, Any]]:
    state = session_manager.get_instruments_cache_state(user_id)
    now = time.monotonic()
    cached = cached_assets_if_valid(state)
    if cached is not None:
        logger.info("[INSTRUMENTS_CACHE_HIT] user_id=%s ttl_remaining_ms=%s", user_id, int((state.expires_at - now) * 1000))
        return cached

    if state.next_retry_at > now:
        logger.warning("[INSTRUMENTS_BACKOFF_ACTIVE] user_id=%s retry_in_ms=%s", user_id, int((state.next_retry_at - now) * 1000))
        cached = stale_assets_if_available(state)
        if cached is not None:
            logger.warning("[INSTRUMENTS_CACHE_STALE_USED] user_id=%s reason=backoff", user_id)
            return cached
        raise ServiceError("INSTRUMENTS_BACKOFF_ACTIVE", 503)

    lock_acquired = state.lock.acquire(blocking=False)
    if not lock_acquired:
        logger.info("[READ_ASSETS_LOCK_WAIT] user_id=%s", user_id)
        cached = stale_assets_if_available(state)
        if cached is not None:
            logger.info("[READ_ASSETS_LOCK_REUSED] user_id=%s", user_id)
            logger.warning("[INSTRUMENTS_CACHE_STALE_USED] user_id=%s reason=lock_busy", user_id)
            return cached
        if not state.lock.acquire(timeout=INSTRUMENTS_TIMEOUT_SECONDS):
            logger.warning("[GET_INSTRUMENTS_TIMEOUT] user_id=%s timeout_seconds=%s phase=lock_wait", user_id, INSTRUMENTS_TIMEOUT_SECONDS)
            raise ServiceError("INSTRUMENTS_TIMEOUT", 504)
        logger.info("[READ_ASSETS_LOCK_REUSED] user_id=%s", user_id)
        state.lock.release()
        cached = stale_assets_if_available(state)
        if cached is not None:
            return cached
        raise ServiceError("INSTRUMENTS_TEMPORARY_UNAVAILABLE", 503)

    now = time.monotonic()
    cached = cached_assets_if_valid(state)
    if cached is not None:
        state.lock.release()
        logger.info("[INSTRUMENTS_CACHE_HIT] user_id=%s ttl_remaining_ms=%s", user_id, int((state.expires_at - now) * 1000))
        return cached
    if state.next_retry_at > now:
        state.lock.release()
        logger.warning("[INSTRUMENTS_BACKOFF_ACTIVE] user_id=%s retry_in_ms=%s", user_id, int((state.next_retry_at - now) * 1000))
        cached = stale_assets_if_available(state)
        if cached is not None:
            logger.warning("[INSTRUMENTS_CACHE_STALE_USED] user_id=%s reason=backoff", user_id)
            return cached
        raise ServiceError("INSTRUMENTS_BACKOFF_ACTIVE", 503)

    logger.info("[GET_INSTRUMENTS_START] user_id=%s timeout_seconds=%s", user_id, INSTRUMENTS_TIMEOUT_SECONDS)
    started_at = time.monotonic()
    try:
        future = session_manager._market_data_executor.submit(read_assets_uncached, client)
    except Exception:
        state.lock.release()
        raise
    try:
        assets = future.result(timeout=INSTRUMENTS_TIMEOUT_SECONDS)
    except FutureTimeoutError as exc:
        if future.cancel():
            state.lock.release()
        else:
            future.add_done_callback(lambda _: state.lock.release())
        state.failure_count += 1
        state.next_retry_at = time.monotonic() + instruments_backoff_seconds(state.failure_count)
        logger.warning("[GET_INSTRUMENTS_TIMEOUT] user_id=%s timeout_seconds=%s", user_id, INSTRUMENTS_TIMEOUT_SECONDS)
        cached = stale_assets_if_available(state)
        if cached is not None:
            logger.warning("[INSTRUMENTS_CACHE_STALE_USED] user_id=%s reason=timeout", user_id)
            return cached
        raise ServiceError("INSTRUMENTS_TIMEOUT", 504) from exc
    except Exception as exc:
        state.lock.release()
        state.failure_count += 1
        state.next_retry_at = time.monotonic() + instruments_backoff_seconds(state.failure_count)
        logger.warning("[GET_INSTRUMENTS_ERROR] user_id=%s error=%s", user_id, type(exc).__name__, exc_info=True)
        cached = stale_assets_if_available(state)
        if cached is not None:
            logger.warning("[INSTRUMENTS_CACHE_STALE_USED] user_id=%s reason=error", user_id)
            return cached
        raise ServiceError("INSTRUMENTS_TEMPORARY_UNAVAILABLE", 503) from exc

    state.assets = clone_assets(assets) or []
    state.updated_at = time.monotonic()
    state.expires_at = state.updated_at + INSTRUMENTS_CACHE_TTL_SECONDS
    state.failure_count = 0
    state.next_retry_at = 0.0
    state.lock.release()
    logger.info(
        "[GET_INSTRUMENTS_FINISH] user_id=%s assets=%s ms=%s",
        user_id,
        len(state.assets),
        int((time.monotonic() - started_at) * 1000),
    )
    return clone_assets(state.assets) or []


def _payout_turbo_binary(
    profit_map: dict[str, dict[str, float]],
    symbol: str,
    open_turbo: bool | None,
    open_binary: bool | None,
) -> float | None:
    """Payout do canal que esta ABERTO para este ativo.

    Prefere o canal aberto; com os dois abertos fica com o maior. Canal fechado
    nunca entra — anunciar payout de canal fechado e o caminho direto para
    "asset is not available at the moment" na hora da compra.

    Args:
        profit_map: Mapa de ``read_binary_profit_map``.
        symbol: Ativo no nome interno.
        open_turbo: Turbo aberto, fechado ou desconhecido.
        open_binary: Binary aberto, fechado ou desconhecido.

    Returns:
        Payout em percentual, ou ``None`` quando nenhum canal aberto oferece.
    """
    canais = profit_map.get(normalize_binary_active(symbol)) or {}
    candidatos = [
        canais.get(nome)
        for nome, aberto in (("turbo", open_turbo), ("binary", open_binary))
        if aberto and isinstance(canais.get(nome), (int, float))
    ]
    return max(candidatos) if candidatos else None


def read_digital_payout(client: Bullex, active: str) -> int | float | None:
    getter = getattr(client, "get_digital_payout", None)
    if not callable(getter):
        return None
    try:
        payout = getter(active, seconds=3)
    except SESSION_EXCEPTION_TYPES:
        raise
    except Exception:
        logger.exception("falha ao consultar payout digital de %s", active)
        raise ServiceError(SESSION_DISCONNECTED, 409)
    return payout if payout else None


_binary_open_cache: dict[str, tuple[float, dict[str, dict[str, bool]]]] = {}
_binary_open_cache_lock = Lock()


def parse_binary_open_map(init_result: Any) -> dict[str, dict[str, bool]]:
    """Extrai o status de abertura turbo/binary de get_all_init_v2.

    A corretora expõe, por ativo, os flags `enabled` e `is_suspended` nas
    seções `turbo` e `binary`. Um ativo pode estar aberto no canal digital
    (payout retorna valor) mas fechado/suspenso no canal turbo/binary — que é
    exatamente onde `client.buy()` envia a ordem. Essa divergência causava o
    erro "asset is not available at the moment" mesmo com o ativo "aberto".

    Args:
        init_result: Retorno bruto de ``client.get_all_init_v2()``.

    Returns:
        Mapa ``{symbol_normalizado: {"turbo": bool, "binary": bool}}``.
    """
    open_map: dict[str, dict[str, bool]] = {}
    if not isinstance(init_result, dict):
        return open_map
    for option in ("turbo", "binary"):
        section = init_result.get(option)
        if not isinstance(section, dict):
            continue
        actives = section.get("actives")
        if not isinstance(actives, dict):
            continue
        for active in actives.values():
            if not isinstance(active, dict):
                continue
            raw_name = str(active.get("name") or "")
            name = raw_name.split(".", 1)[1] if "." in raw_name else raw_name
            # Indexado pelo NOSSO nome: assim TODA consulta existente
            # (`/payouts`, portao da compra, resolve_channel_open) volta a
            # encontrar o par aberto sem precisar mudar cada uma delas.
            symbol = to_internal_active(name)
            if not symbol:
                continue
            is_open = bool(active.get("enabled")) and not bool(active.get("is_suspended"))
            open_map.setdefault(symbol, {})[option] = is_open
    return open_map


def cached_binary_open_map(user_id: str) -> dict[str, dict[str, bool]]:
    """Retorna o mapa turbo/binary do cache sem tocar a rede.

    Usado no caminho do buy: a compra acontece na janela de 0–5s da vela e não
    pode esperar um get_all_init_v2. Sem cache quente, devolve vazio e o gate
    deixa a corretora decidir.

    Args:
        user_id: Identificador autenticado, chave do cache.

    Returns:
        Mapa de abertura por ativo (vazio se cache frio/expirado).
    """
    now = time.monotonic()
    with _binary_open_cache_lock:
        cached = _binary_open_cache.get(user_id)
        if cached is not None and cached[0] > now:
            return cached[1]
    return {}


def read_binary_open_map(client: Bullex, *, user_id: str) -> dict[str, dict[str, bool]]:
    """Lê (com cache TTL + backoff de falha) o status turbo/binary por ativo.

    Args:
        client: Cliente BullEx conectado da sessão do usuário.
        user_id: Identificador autenticado, usado como chave de cache.

    Returns:
        Mapa de abertura por ativo; vazio quando a corretora não respondeu.
    """
    now = time.monotonic()
    with _binary_open_cache_lock:
        cached = _binary_open_cache.get(user_id)
        if cached is not None and cached[0] > now:
            return cached[1]
    try:
        init_result = client.get_all_init_v2(timeout=BINARY_OPEN_FETCH_TIMEOUT_SECONDS)
    except SESSION_EXCEPTION_TYPES:
        raise
    except Exception:
        logger.warning("[BINARY_OPEN_MAP_ERROR] user_id=%s", user_id, exc_info=True)
        init_result = None
    open_map = parse_binary_open_map(init_result)
    ttl = BINARY_OPEN_TTL_SECONDS if open_map else BINARY_OPEN_FAILURE_TTL_SECONDS
    with _binary_open_cache_lock:
        _binary_open_cache[user_id] = (now + ttl, open_map)
    return open_map


_binary_profit_cache: dict[str, tuple[float, dict[str, dict[str, float]]]] = {}
_binary_profit_cache_lock = Lock()


def parse_binary_profit_map(profit_result: Any) -> dict[str, dict[str, float]]:
    """Converte ``get_all_profit()`` em payout por ativo, no NOSSO nome.

    A corretora devolve fracao (0.84) por canal; aqui vira percentual (84), que
    e a escala que o robo compara com ``min_payout``.

    Args:
        profit_result: Retorno bruto de ``client.get_all_profit()``.

    Returns:
        Mapa ``{ativo: {"turbo": 84.0, "binary": 85.0}}``.
    """
    mapa: dict[str, dict[str, float]] = {}
    if not isinstance(profit_result, dict):
        return mapa
    for nome, canais in profit_result.items():
        if not isinstance(canais, dict):
            continue
        simbolo = to_internal_active(str(nome))
        if not simbolo:
            continue
        for canal in ("turbo", "binary"):
            valor = canais.get(canal)
            if isinstance(valor, (int, float)) and valor > 0:
                mapa.setdefault(simbolo, {})[canal] = round(float(valor) * 100, 2)
    return mapa


def read_binary_profit_map(client: Bullex, *, user_id: str) -> dict[str, dict[str, float]]:
    """Payout turbo/binary por ativo, com o mesmo cache/TTL do mapa de abertura.

    Existe porque ``get_digital_payout`` devolve ``None`` para par de mercado
    aberto — ele consulta o canal digital, e o par aberto e vendido em
    turbo/binary. Sem isto o par abria, tinha payout na corretora e mesmo assim
    morria em PAYOUT_UNAVAILABLE.

    Args:
        client: Cliente BullEx conectado.
        user_id: Chave de cache.

    Returns:
        Mapa de payout por ativo; vazio quando a corretora nao respondeu.
    """
    now = time.monotonic()
    with _binary_profit_cache_lock:
        cached = _binary_profit_cache.get(user_id)
        if cached is not None and cached[0] > now:
            return cached[1]
    try:
        bruto = client.get_all_profit()
    except SESSION_EXCEPTION_TYPES:
        raise
    except Exception:
        logger.warning("[BINARY_PROFIT_MAP_ERROR] user_id=%s", user_id, exc_info=True)
        bruto = None
    mapa = parse_binary_profit_map(bruto)
    ttl = BINARY_OPEN_TTL_SECONDS if mapa else BINARY_OPEN_FAILURE_TTL_SECONDS
    with _binary_profit_cache_lock:
        _binary_profit_cache[user_id] = (now + ttl, mapa)
    return mapa


def clear_binary_open_cache(user_id: str) -> None:
    with _binary_open_cache_lock:
        _binary_open_cache.pop(user_id, None)


def mark_binary_option_closed(
    user_id: str,
    symbol: str,
    expiration_minutes: int | float | None,
    *,
    ttl_seconds: float = 60.0,
) -> None:
    """Força o canal turbo/binary como fechado no cache após rejeição de buy.

    O catálogo ``enabled``/``is_suspended`` às vezes diz aberto e o buyv3 ainda
    devolve "asset is not available". Sem marcar fechado, o próximo /payouts
    reaproveita o mapa quente e a compra falha de novo. TTL curto (~1 vela M1):
    a análise dos demais ativos segue; só este canal fica marcado fechado.

    Args:
        user_id: Identificador autenticado (chave do cache).
        symbol: Ativo rejeitado.
        expiration_minutes: Expiração da ordem (define turbo vs binary).
        ttl_seconds: Quanto tempo o mapa forçado permanece válido.

    Returns:
        None.
    """
    normalized = normalize_binary_active(symbol)
    if not normalized:
        return
    kind = binary_option_kind_for_expiration(expiration_minutes)
    now = time.monotonic()
    with _binary_open_cache_lock:
        cached = _binary_open_cache.get(user_id)
        open_map = dict(cached[1]) if cached is not None else {}
        entry = dict(open_map.get(normalized) or {})
        entry[kind] = False
        open_map[normalized] = entry
        _binary_open_cache[user_id] = (now + float(ttl_seconds), open_map)
    logger.warning(
        "[BINARY_OPTION_MARKED_CLOSED] user_id=%s active=%s channel=%s ttl=%s",
        user_id,
        normalized,
        kind,
        ttl_seconds,
    )


def binary_option_kind_for_expiration(expiration_minutes: int | float | None) -> str:
    """Canal usado pelo buyv3: turbo até 5min, binary acima disso."""
    try:
        minutes = int(expiration_minutes or 0)
    except (TypeError, ValueError):
        minutes = 0
    return "turbo" if 0 < minutes <= 5 else "binary"


def binary_option_open_state(
    open_map: dict[str, dict[str, bool]],
    symbol: str,
    expiration_minutes: int | float | None,
) -> bool | None:
    """Diz se o canal usado na compra está aberto.

    Args:
        open_map: Mapa de ``read_binary_open_map``.
        symbol: Ativo alvo da ordem.
        expiration_minutes: Expiração em minutos (define turbo vs binary).

    Returns:
        ``True``/``False`` quando o status é conhecido; ``None`` quando o ativo
        não aparece no catálogo (não bloqueia, deixa a corretora decidir).
    """
    entry = open_map.get(normalize_binary_active(symbol))
    if not entry:
        return None
    kind = binary_option_kind_for_expiration(expiration_minutes)
    if kind in entry:
        return entry[kind]
    return any(entry.values())


cors_origins = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", CORS_ALLOWED_ORIGINS_DEFAULT).split(",")
    if origin.strip()
]

app = FastAPI(title="bullex-service", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=CORS_ALLOWED_METHODS,
    allow_headers=CORS_ALLOWED_HEADERS,
)
session_manager = SessionManager(create_session_store())


@app.middleware("http")
async def serialize_user_market_requests(request: Request, call_next):
    if request.url.path not in {
        "/sessions/status",
        "/account",
        "/candles",
        "/payouts",
    }:
        return await call_next(request)
    user_id = str(request.headers.get("x-user-id") or "").strip()
    if not user_id:
        return await call_next(request)
    async with session_manager.async_user_lock(user_id):
        return await call_next(request)


@app.on_event("startup")
def startup_without_session_restore() -> None:
    logger.info("[STARTUP_READY] restore disabled")


def ensure_session_alive(user_id: str) -> ManagedSession:
    return session_manager.ensure_session_alive(user_id)


@app.exception_handler(ServiceError)
def service_error_handler(_: Request, exc: ServiceError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=build_error(exc.message, exc.data))


@app.exception_handler(RequestValidationError)
def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    message = "; ".join(error["msg"] for error in exc.errors())
    return JSONResponse(status_code=422, content=build_error(message))


@app.exception_handler(Exception)
def unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("erro nao tratado", exc_info=exc)
    return JSONResponse(status_code=500, content=build_error("erro interno"))


@app.get("/health")
def health() -> dict[str, Any]:
    return build_success({"status": "healthy", "service": "bullex-service"})


@app.post("/sessions/connect")
def connect_session(
    payload: ConnectRequest,
    x_user_id: str | None = Header(default=None),
) -> Any:
    user_id = str(x_user_id or "").strip()
    try:
        user_id = require_user_id(x_user_id)
        payload.account_mode = "REAL"
        session = session_manager.connect(user_id, payload)
        session.desired_mode = "REAL"

        connected = False
        active_mode = session.active_mode or "REAL"
        if not session.requires_2fa:
            if not session.real_mode_confirmed:
                with session_manager._session_context(session):
                    try:
                        active_mode = force_real_mode(session, user_id=user_id)
                    except ServiceError as exc:
                        active_mode = str((exc.data or {}).get("active_mode") or "UNKNOWN").strip().upper()
                        session.active_mode = active_mode
                        session.real_mode_confirmed = False
                        session_manager.clear_user_runtime_cache(user_id)
                        return JSONResponse(
                            status_code=exc.status_code,
                            content=build_error(exc.message, exc.data),
                        )
            connected = active_mode == "REAL"
            if not connected:
                logger.warning("[REAL_MODE_FAILED] user_id=%s active_mode=%s", user_id, active_mode)
                return JSONResponse(
                    status_code=409,
                    content=build_error(
                        BULLEX_ACTIVE_MODE_NOT_REAL,
                        {
                            "connected": True,
                            "active_mode": active_mode,
                            "mode": active_mode,
                            "balance": None,
                        },
                    ),
                )
            logger.info("[REAL_MODE_CONFIRMED] user_id=%s", user_id)

        return with_login_progress(build_success(
            {
                "user_id": user_id,
                "connected": connected,
                "requires_2fa": session.requires_2fa,
                "active_mode": active_mode,
                "active_mode_from_bullex": active_mode,
                "active_mode_real_detected": active_mode == "REAL",
            }
        ), user_id)
    except ServiceError as exc:
        logger.warning(
            "[CONNECT_FAILED_HANDLED] user_id=%s detail=%s",
            user_id or "missing",
            exc.message,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=build_error(exc.message, exc.data),
        )
    except Exception as exc:
        logger.warning(
            "[CONNECT_FAILED_HANDLED] user_id=%s detail=%s",
            user_id or "missing",
            type(exc).__name__,
            exc_info=True,
        )
        try:
            failed_session = session_manager.get(user_id)
            if failed_session is not None:
                session_manager._close_session(failed_session)
            session_manager.remove(user_id)
            session_manager.clear_probe_cache(user_id)
            if session_manager.store is not None:
                session_manager.store.mark_disconnected(user_id, revoke_token=True)
        except Exception:
            logger.warning(
                "[CONNECT_CLEANUP_FAILED] user_id=%s",
                user_id or "missing",
                exc_info=True,
            )
        return JSONResponse(
            status_code=503,
            content=build_error("LOGIN_FAILED"),
        )


def _live_server_timestamp(user_id: str) -> float | None:
    """Relógio da corretora lido direto da sessão viva.

    ``timesync.server_timestamp`` é atributo em memória atualizado pelo
    heartbeat do WS: ler não custa I/O nem passa pelo gate de chamadas.
    """
    try:
        session = session_manager.get(user_id)
        if session is None:
            return None
        timestamp = float(session.client.get_server_timestamp())
    except Exception:
        logger.warning("[LIVE_SERVER_TIME_FAILED] user_id=%s", user_id, exc_info=True)
        return None
    return timestamp if timestamp > 0 else None


def with_fresh_server_time(payload: dict[str, Any], user_id: str) -> dict[str, Any]:
    """Reescreve ``server_time`` com leitura viva antes de responder.

    A resposta de ``/sessions/status`` é cacheada por ``SESSION_STATUS_TTL_SECONDS``
    e re-servida pelo throttle de ``SESSION_STATUS_THROTTLE_SECONDS``. O
    ``server_time`` congelava DENTRO do payload cacheado: medido em produção
    2026-09-01, requisições a cada 3s receberam o MESMO timestamp por 58s
    seguidos (defasagem crescendo de 25s para 73s), enquanto uma leitura fresca
    ficava 0,15–0,86s atrás do relógio local. Como vários robôs consultam este
    endpoint continuamente, o throttle mantinha a amostra congelada
    indefinidamente.

    Esse atraso ia inteiro para a janela de compra: o robô calculava o segundo
    da vela a partir dele e achava que entrava no segundo 1,8 quando entrava, de
    fato, no segundo 10,2 (mediana de 209 ordens). A telemetria era derivada do
    mesmo relógio, então confirmava a si mesma e o defeito ficava invisível.

    O resto do payload continua vindo do cache — ``connected`` e ``active_mode``
    são o que custa chamada à corretora. O timestamp não custa nada.

    Args:
        payload: Resposta de ``/sessions/status`` (fresca ou de cache).
        user_id: Dono da sessão.

    Returns:
        Payload com ``server_time`` vivo e ``server_time_sampled_at`` carimbado.
        Devolve o original quando não há sessão conectada para consultar.
    """
    data = payload.get("data")
    if not isinstance(data, dict) or not data.get("connected"):
        return payload
    live = _live_server_timestamp(user_id)
    if live is None:
        return payload
    return {
        **payload,
        "data": {**data, "server_time": live, "server_time_sampled_at": time.time()},
    }


@app.get("/sessions/status")
def session_status(
    x_user_id: str | None = Header(default=None),
    x_allow_session_restore: str | None = Header(default=None),
) -> JSONResponse:
    user_id = require_user_id(x_user_id)
    allow_session_restore = str(x_allow_session_restore or "").strip().lower() == "true"
    with session_manager.user_lock(user_id):
        try:
            if session_manager.get(user_id) is None and allow_session_restore:
                session_manager.restore_on_demand(user_id)
        except ServiceError as exc:
            if exc.message in {SESSION_NOT_FOUND, SESSION_DISCONNECTED}:
                status_code = 404 if exc.message == SESSION_NOT_FOUND else 409
                payload = {"ok": False, "data": {"connected": False}, "error": exc.message}
                # Restore com SSID inválido (comum após restart do container) não
                # deve travar o usuário em offline_until — ele precisa logar já.
                if allow_session_restore:
                    session_manager.clear_user_runtime_cache(user_id)
                    logger.warning(
                        "[SESSION_RESTORE_FAILED_NO_OFFLINE] user_id=%s reason=%s",
                        user_id,
                        exc.message,
                    )
                else:
                    session_manager._mark_probe_failure(user_id, offline=True)
                return JSONResponse(status_code=status_code, content=with_login_progress(payload, user_id))
            raise

        cached = session_manager.get_cached_probe(user_id, "/sessions/status", path="/sessions/status")
        if cached is not None:
            status_code, payload = cached
            # `server_time` NUNCA sai do cache — ver `with_fresh_server_time`.
            payload = with_fresh_server_time(payload, user_id)
            return JSONResponse(status_code=status_code, content=with_login_progress(payload, user_id))

        def operation(current: ManagedSession) -> dict[str, Any]:
            connected = bool(current.client.check_connect())
            active_mode = current.client.get_balance_mode() if connected and not current.requires_2fa else None
            return {
                "user_id": user_id,
                "connected": connected,
                "requires_2fa": current.requires_2fa,
                "email": current.email,
                "active_mode": active_mode,
                "server_time": current.client.get_server_timestamp() if connected else None,
            }

        try:
            payload = with_login_progress(
                build_success(
                    session_manager.run(
                        user_id,
                        operation,
                        gate_timeout=CALL_GATE_TIMEOUT_SECONDS,
                    )
                ),
                user_id,
            )
            session_manager._mark_probe_success(
                user_id,
                "/sessions/status",
                200,
                payload,
                ttl_seconds=SESSION_STATUS_TTL_SECONDS,
            )
            return JSONResponse(status_code=200, content=with_fresh_server_time(payload, user_id))
        except ServiceError as exc:
            if exc.message in {SESSION_NOT_FOUND, SESSION_DISCONNECTED}:
                status_code = 404 if exc.message == SESSION_NOT_FOUND else 409
                payload = {"ok": False, "data": {"connected": False}, "error": exc.message}
                session_manager._mark_probe_failure(user_id, offline=True)
                return JSONResponse(status_code=status_code, content=with_login_progress(payload, user_id))
            raise


@app.get("/sessions/persistence-debug")
def sessions_persistence_debug() -> dict[str, Any]:
    return session_manager.persistence_debug()


@app.post("/sessions/disconnect")
def disconnect_session(x_user_id: str | None = Header(default=None)) -> dict[str, Any]:
    user_id = require_user_id(x_user_id)
    session_manager.disconnect(user_id)
    session_manager.clear_probe_cache(user_id)
    return build_success({"user_id": user_id, "connected": False})


@app.post("/sessions/reconnect")
def reconnect_session(x_user_id: str | None = Header(default=None)) -> dict[str, Any]:
    user_id = require_user_id(x_user_id)
    session_manager.reconnect(user_id)
    session_manager.clear_probe_cache(user_id)
    return with_login_progress(build_success(session_manager.run(user_id, build_account_payload)), user_id)


@app.get("/account/balance")
def account_balance(x_user_id: str | None = Header(default=None)) -> dict[str, Any]:
    user_id = require_user_id(x_user_id)
    return build_real_only_account_response(
        session_manager.run(user_id, build_account_payload)
    )


@app.get("/account")
def account_overview(x_user_id: str | None = Header(default=None)) -> dict[str, Any]:
    user_id = require_user_id(x_user_id)
    started_at = time.monotonic()
    cached = session_manager.get_cached_probe(user_id, "/account", path="/account")
    if cached is not None:
        _, payload = cached
        log_fetch_metric("/account", started_at, user_id=user_id, source="cache", status_code=cached[0])
        return with_login_progress(
            normalize_real_only_account_payload(payload),
            user_id,
        )
    if session_manager.get(user_id) is None:
        previous = session_manager.get_last_probe(user_id, "/account")
        if previous is not None:
            log_fetch_metric("/account", started_at, user_id=user_id, source="last_cache", status_code=previous[0])
            return with_login_progress(
                normalize_real_only_account_payload(previous[1]),
                user_id,
            )
        payload = with_login_progress(build_success({"connected": False}), user_id)
        log_fetch_metric("/account", started_at, user_id=user_id, source="no_session", status_code=200)
        return payload

    def operation(session: ManagedSession) -> dict[str, Any]:
        return build_account_payload(session)

    try:
        account = session_manager.run(
            user_id,
            operation,
            disconnect_on_error=False,
            gate_timeout=CALL_GATE_TIMEOUT_SECONDS,
        )
        payload = with_login_progress(
            build_real_only_account_response(account),
            user_id,
        )
        if bool((payload.get("data") or {}).get("connected")):
            session_manager._mark_probe_success(
                user_id,
                "/account",
                200,
                payload,
                ttl_seconds=ACCOUNT_TTL_SECONDS,
            )
        else:
            # Conta connected=false sob carga costuma ser modo omitido/stale —
            # não marca offline agressivo se a sessão ainda confirma REAL.
            live = session_manager.get(user_id)
            if live is not None and (
                live.real_mode_confirmed
                or str(live.active_mode or "").upper() == "REAL"
            ):
                logger.warning(
                    "[ACCOUNT_CONNECTED_FALSE_KEEP_SESSION] user_id=%s active_mode=%s",
                    user_id,
                    live.active_mode,
                )
                session_manager._mark_probe_failure(user_id, offline=False)
            else:
                session_manager._mark_probe_failure(user_id, offline=True)
        log_fetch_metric("/account", started_at, user_id=user_id, source="upstream", status_code=200)
        return payload
    except ServiceError as exc:
        if exc.message in {SESSION_NOT_FOUND, SESSION_DISCONNECTED}:
            cached = session_manager.get_last_probe(user_id, "/account")
            if cached is not None:
                log_fetch_metric("/account", started_at, user_id=user_id, source="last_cache", status_code=cached[0])
                return with_login_progress(
                    normalize_real_only_account_payload(cached[1]),
                    user_id,
                )
            log_fetch_metric("/account", started_at, user_id=user_id, source=exc.message, status_code=200)
            return with_login_progress(build_success({"connected": False}), user_id)
        cached = session_manager.get_last_probe(user_id, "/account")
        if cached is not None:
            log_fetch_metric("/account", started_at, user_id=user_id, source="last_cache", status_code=cached[0])
            return with_login_progress(
                normalize_real_only_account_payload(cached[1]),
                user_id,
            )
        log_fetch_metric("/account", started_at, user_id=user_id, source=exc.message)
        return with_login_progress(build_error("ACCOUNT_TEMPORARY_UNAVAILABLE"), user_id)


@app.post("/account/change-mode")
def account_change_mode(payload: ChangeModeRequest, x_user_id: str | None = Header(default=None)) -> dict[str, Any]:
    user_id = require_user_id(x_user_id)
    target_mode = "REAL"

    def operation(session: ManagedSession) -> dict[str, Any]:
        ensure_session_ready(session)
        active_mode = force_real_mode(session, user_id=user_id)
        session.desired_mode = active_mode
        session_manager._persist_connected(session)
        session_manager.clear_probe_cache(user_id)
        return {"mode": active_mode}

    return build_success(session_manager.run(user_id, operation))


@app.get("/assets")
def list_assets(x_user_id: str | None = Header(default=None)) -> dict[str, Any]:
    user_id = require_user_id(x_user_id)
    cache_key = build_cache_key("/assets")
    cached = session_manager.get_cached_probe(user_id, cache_key, path="/assets")
    if cached is not None:
        _, payload = cached
        logger.info("[INSTRUMENTS_CACHE_HIT] user_id=%s source=http_cache", user_id)
        return payload

    def operation(session: ManagedSession) -> list[dict[str, Any]]:
        ensure_session_ready(session)
        try:
            return read_assets(session.client, user_id=user_id)
        except ServiceError:
            raise
        except SESSION_EXCEPTION_TYPES as exc:
            logger.warning("[SESSION-DEAD] %s %s", user_id, type(exc).__name__)
            raise ServiceError(SESSION_DISCONNECTED, 409) from exc
        except Exception as exc:
            logger.exception("falha ao listar ativos")
            raise ServiceError(SESSION_DISCONNECTED, 409) from exc

    try:
        payload = build_success(session_manager.run(user_id, operation, disconnect_on_error=False))
        session_manager._mark_probe_success(
            user_id,
            cache_key,
            200,
            payload,
            ttl_seconds=ASSETS_TTL_SECONDS,
        )
        return payload
    except ServiceError:
        cached = stale_cached_probe(user_id, cache_key, max_age_seconds=INSTRUMENTS_CACHE_TTL_SECONDS * 12)
        if cached is not None:
            logger.warning("[INSTRUMENTS_CACHE_STALE_USED] user_id=%s reason=assets_endpoint_error", user_id)
            return cached[1]
        raise


@app.get("/candles")
def get_candles(
    active: str,
    interval: int,
    count: int,
    endtime: int | None = None,
    x_user_id: str | None = Header(default=None),
) -> dict[str, Any]:
    user_id = require_user_id(x_user_id)
    started_at = time.monotonic()
    active = ensure_binary_asset_allowed(active)
    resolved_endtime = endtime or int(time.time())
    cache_key = build_cache_key(
        "/candles",
        {"active": active, "interval": interval, "count": count, "endtime": resolved_endtime},
    )
    cached = session_manager.get_cached_probe(user_id, cache_key, path="/candles")
    if cached is not None:
        _, payload = cached
        log_fetch_metric("/candles", started_at, user_id=user_id, source="cache", status_code=cached[0])
        return payload

    # Candles do mesmo ativo/intervalo/vela são idênticos para qualquer
    # usuário — reaproveita antes de disputar o _call_gate.
    shared_cached = session_manager.get_shared_market_cache(cache_key)
    if shared_cached is not None:
        _, payload = shared_cached
        log_fetch_metric("/candles", started_at, user_id=user_id, source="shared_cache", status_code=shared_cached[0])
        return payload

    def operation(session: ManagedSession) -> list[dict[str, Any]]:
        ensure_session_ready(session)
        try:
            candles = session.client.get_candles(active, interval, count, resolved_endtime)
        except SESSION_EXCEPTION_TYPES as exc:
            logger.warning("[SESSION-DEAD] %s %s", user_id, type(exc).__name__)
            raise ServiceError(SESSION_DISCONNECTED, 409) from exc
        except Exception as exc:
            logger.exception("falha ao obter candles de %s", active)
            raise ServiceError(SESSION_DISCONNECTED, 409) from exc
        if candles is None:
            raise ServiceError(SESSION_DISCONNECTED, 409)
        return normalize_candles(candles)

    try:
        payload = build_success(
            session_manager.run(
                user_id,
                operation,
                disconnect_on_error=False,
                timeout_seconds=MARKET_DATA_OP_TIMEOUT_SECONDS,
                gate_timeout=CALL_GATE_TIMEOUT_SECONDS,
            )
        )
        session_manager._mark_probe_success(
            user_id,
            cache_key,
            200,
            payload,
            ttl_seconds=CANDLES_TTL_SECONDS,
        )
        session_manager.set_shared_market_cache(cache_key, 200, payload, ttl_seconds=CANDLES_TTL_SECONDS)
        log_fetch_metric("/candles", started_at, user_id=user_id, source="upstream", status_code=200)
        return payload
    except ServiceError:
        cached = stale_cached_probe(user_id, cache_key)
        if cached is not None:
            log_fetch_metric("/candles", started_at, user_id=user_id, source="stale_cache", status_code=cached[0])
            return cached[1]
        # Outro usuário pode ter conseguido buscar o mesmo ativo enquanto
        # este ficou preso no gate — evita devolver erro se já há dado fresco.
        shared_cached = session_manager.get_shared_market_cache(cache_key)
        if shared_cached is not None:
            log_fetch_metric(
                "/candles", started_at, user_id=user_id, source="shared_cache_fallback", status_code=shared_cached[0]
            )
            return shared_cached[1]
        log_fetch_metric("/candles", started_at, user_id=user_id, source="error")
        return build_error("CANDLES_TEMPORARY_UNAVAILABLE")


@app.get("/payouts")
def get_payouts(active: str | None = None, x_user_id: str | None = Header(default=None)) -> dict[str, Any]:
    user_id = require_user_id(x_user_id)
    started_at = time.monotonic()
    active = ensure_binary_asset_allowed(active) if active else None
    cache_key = build_cache_key("/payouts", {"active": active} if active else None)
    cached = session_manager.get_cached_probe(user_id, cache_key, path="/payouts")
    if cached is not None:
        _, payload = cached
        log_fetch_metric("/payouts", started_at, user_id=user_id, source="cache", status_code=cached[0])
        return payload

    # Payout/canal aberto é informação da corretora, igual para qualquer
    # usuário observando o mesmo ativo — reaproveita antes de disputar o
    # _call_gate (mesma lógica de /candles acima).
    shared_cached = session_manager.get_shared_market_cache(cache_key)
    if shared_cached is not None:
        _, payload = shared_cached
        log_fetch_metric("/payouts", started_at, user_id=user_id, source="shared_cache", status_code=shared_cached[0])
        return payload

    def operation(session: ManagedSession) -> list[dict[str, Any]]:
        ensure_session_ready(session)
        if active:
            symbols = [active]
        else:
            try:
                symbols = [asset["symbol"] for asset in read_assets(session.client, user_id=user_id)]
            except ServiceError:
                raise
            except SESSION_EXCEPTION_TYPES as exc:
                logger.warning("[SESSION-DEAD] %s %s", user_id, type(exc).__name__)
                raise ServiceError(SESSION_DISCONNECTED, 409) from exc
            except Exception as exc:
                logger.exception("falha ao listar ativos para payouts")
                raise ServiceError(SESSION_DISCONNECTED, 409) from exc

        open_map = read_binary_open_map(session.client, user_id=user_id)
        # `get_digital_payout` so enxerga o canal digital, e o par de mercado
        # aberto e vendido em turbo/binary — por isso ele voltava None e o par
        # morria em PAYOUT_UNAVAILABLE mesmo estando ABERTO na corretora.
        profit_map = read_binary_profit_map(session.client, user_id=user_id) if active else {}
        result: list[dict[str, Any]] = []
        for symbol in symbols:
            entry = open_map.get(normalize_binary_active(symbol)) or {}
            open_turbo = entry.get("turbo")
            open_binary = entry.get("binary")
            is_open: bool | None = None
            if open_turbo is not None or open_binary is not None:
                is_open = bool(open_turbo) or bool(open_binary)
            result.append(
                {
                    "symbol": symbol,
                    "payout": (
                        read_digital_payout(session.client, symbol)
                        or _payout_turbo_binary(profit_map, symbol, open_turbo, open_binary)
                        if active
                        else None
                    ),
                    "type": "digital",
                    "open_turbo": open_turbo,
                    "open_binary": open_binary,
                    "is_open": is_open,
                }
            )
        return result

    try:
        payload = build_success(
            session_manager.run(
                user_id,
                operation,
                disconnect_on_error=False,
                timeout_seconds=MARKET_DATA_OP_TIMEOUT_SECONDS,
                gate_timeout=CALL_GATE_TIMEOUT_SECONDS,
            )
        )
        session_manager._mark_probe_success(
            user_id,
            cache_key,
            200,
            payload,
            ttl_seconds=PAYOUT_TTL_SECONDS,
        )
        session_manager.set_shared_market_cache(cache_key, 200, payload, ttl_seconds=PAYOUT_TTL_SECONDS)
        log_fetch_metric("/payouts", started_at, user_id=user_id, source="upstream", status_code=200)
        return payload
    except ServiceError:
        cached = stale_cached_probe(user_id, cache_key)
        if cached is not None:
            log_fetch_metric("/payouts", started_at, user_id=user_id, source="stale_cache", status_code=cached[0])
            return cached[1]
        shared_cached = session_manager.get_shared_market_cache(cache_key)
        if shared_cached is not None:
            log_fetch_metric(
                "/payouts", started_at, user_id=user_id, source="shared_cache_fallback", status_code=shared_cached[0]
            )
            return shared_cached[1]
        log_fetch_metric("/payouts", started_at, user_id=user_id, source="error")
        return build_error("PAYOUTS_TEMPORARY_UNAVAILABLE")


@app.post("/orders/buy-demo")
def buy_demo(payload: BuyOrderRequest, x_user_id: str | None = Header(default=None)) -> dict[str, Any]:
    require_user_id(x_user_id)
    raise ServiceError("REAL_MODE_ONLY", 409)


@app.post("/orders/buy-real")
def buy_real(payload: BuyOrderRequest, x_user_id: str | None = Header(default=None)) -> dict[str, Any]:
    user_id = require_user_id(x_user_id)
    payload.action = normalize_action(payload.action)
    logger.info(
        "[REAL MODE DETECTED] user_id=%s confirm_real=%s",
        user_id,
        payload.confirm_real,
    )
    logger.info(
        "[REAL BUY ATTEMPT] user_id=%s active=%s action=%s amount=%s expiration=%s",
        user_id,
        payload.active,
        payload.action,
        payload.amount,
        payload.expiration,
    )
    if not payload.confirm_real:
        reason = "CONFIRM_REAL_REQUIRED"
        logger.warning("[REAL BUY BLOCKED reason=%s] user_id=%s", reason, user_id)
        raise ServiceError(reason, 403)
    if payload.amount <= 0:
        reason = "AMOUNT_MUST_BE_POSITIVE"
        logger.warning("[REAL BUY BLOCKED reason=%s] user_id=%s", reason, user_id)
        raise ServiceError(reason, 403)

    def operation(session: ManagedSession) -> dict[str, Any]:
        ensure_session_ready(session)
        try:
            ensure_mode_matches(session.client, "REAL")
        except ServiceError as exc:
            # Após trades o balance_id às vezes some e get_balance_mode()
            # devolve None → "mode invalido". Reaplica REAL e tenta de novo.
            detail = str(getattr(exc, "message", "") or exc)
            logger.warning(
                "[REAL_MODE_RECOVER_BEFORE_BUY] user_id=%s detail=%s",
                user_id,
                detail,
            )
            try:
                force_real_mode(session, user_id=user_id)
                ensure_mode_matches(session.client, "REAL")
            except ServiceError as recover_exc:
                reason = "ACCOUNT_MODE_NOT_REAL"
                logger.warning(
                    "[REAL BUY BLOCKED reason=%s] user_id=%s detail=%s",
                    reason,
                    user_id,
                    getattr(recover_exc, "message", recover_exc),
                )
                raise ServiceError(reason, 403) from recover_exc

        def place_buy() -> tuple[bool, Any]:
            ensure_real_balance_id_for_buy(session, user_id=user_id)
            # `client.buy` fala com o catalogo turbo/binary, onde o par de
            # mercado aberto se chama `EURUSD-op`. Mandar `EURUSD` e ordem
            # recusada. OTC passa intacto por `to_broker_active`.
            return session.client.buy(
                payload.amount,
                to_broker_active(payload.active),
                payload.action,
                payload.expiration,
            )

        # O payout/scan considera o ativo "aberto" pelo canal DIGITAL, mas a
        # ordem vai pelo canal turbo/binary (buyv3). Se esse canal estiver
        # fechado/suspenso, a corretora rejeita com "asset is not available".
        # Cache-only: a compra vive na janela de 0–5s da vela e não pode
        # esperar um get_all_init_v2 (os /payouts mantêm o cache quente).
        open_map = cached_binary_open_map(user_id)
        option_open = binary_option_open_state(open_map, payload.active, payload.expiration)
        if option_open is False:
            option_kind = binary_option_kind_for_expiration(payload.expiration)
            reason = (
                "asset is not available at the moment "
                f"({option_kind} option closed for {payload.active})"
            )
            logger.warning(
                "[REAL BUY BLOCKED reason=BINARY_OPTION_CLOSED] user_id=%s active=%s channel=%s",
                user_id,
                payload.active,
                option_kind,
            )
            mark_binary_option_closed(user_id, payload.active, payload.expiration)
            raise ServiceError(reason, 409)

        ok, order_id = place_buy()
        if not ok and is_user_balance_not_found_error(order_id):
            # Profile cacheado costuma devolver balance_id REAL obsoleto; o saldo
            # existe, mas o buyv3 manda user_balance_id inválido. Força catálogo
            # fresco (get_balances), pin do id e nova tentativa.
            logger.warning(
                "[REAL_BUY_RETRY_BALANCE] user_id=%s detail=%s",
                user_id,
                order_id,
            )
            try:
                force_real_mode(session, user_id=user_id)
            except ServiceError:
                logger.warning(
                    "[REAL_BUY_RETRY_FORCE_MODE_FAILED] user_id=%s",
                    user_id,
                    exc_info=True,
                )
            # Re-descobre e pina mesmo se force_real reaplicou id velho do profile.
            fresh_real_id, _, _ = discover_balance_ids(
                session.client,
                prefer_fresh=True,
            )
            if fresh_real_id is not None:
                pin_global_balance_id(
                    session.client,
                    fresh_real_id,
                    user_id=user_id,
                    owned_previous=session.state.balance_id,
                )
                session.state.balance_id = fresh_real_id
                logger.warning(
                    "[REAL_BUY_RETRY_PINNED] user_id=%s balance_id=%s",
                    user_id,
                    fresh_real_id,
                )
            ensure_mode_matches(session.client, "REAL")
            ok, order_id = place_buy()

        if not ok:
            reason = f"falha ao criar ordem real: {order_id}"
            detail = str(order_id or "").strip().lower()
            if "asset is not available" in detail or "not available at the moment" in detail:
                mark_binary_option_closed(user_id, payload.active, payload.expiration)
            logger.warning("[REAL BUY BLOCKED reason=%s] user_id=%s", reason, user_id)
            raise ServiceError(reason, 409)
        logger.info("[REAL BUY SUCCESS order_id=%s] user_id=%s", order_id, user_id)
        return {
            "mode": "REAL",
            "order_id": order_id,
            "active": payload.active,
            "amount": payload.amount,
            "action": payload.action,
            "expiration": payload.expiration,
        }

    return build_success(session_manager.run(user_id, operation))


@app.get("/instruments/probe")
def instruments_probe(x_user_id: str | None = Header(default=None)) -> dict[str, Any]:
    """Diz quais canais de instrumento a corretora responde, um a um.

    `update_ACTIVES_OPCODE` pede binary/turbo e depois crypto, forex e cfd em
    sequência, com um deadline compartilhado — se um canal não responde, ele
    consome o orçamento e o `/assets` inteiro estoura. Foi o que aconteceu:
    `INSTRUMENTS_TIMEOUT` em 100% das chamadas, com 8s e também com 25s.

    Esta sonda testa cada canal isolado e com timeout curto, então distingue
    "a corretora é lenta" de "a corretora não oferece este canal" — que é a
    pergunta que decide se dá para operar CFD/forex além da opção binária.

    Args:
        x_user_id: Dono da sessão conectada.

    Returns:
        Envelope com ``{canal: {"respondeu": bool, "itens": int, "erro": str}}``.
    """
    user_id = require_user_id(x_user_id)

    def operation(session: ManagedSession) -> dict[str, Any]:
        ensure_session_ready(session)
        resultado: dict[str, Any] = {}
        for canal in ("crypto", "forex", "cfd"):
            inicio = time.monotonic()
            try:
                dados = session.client.get_instruments(canal, timeout=6)
            except Exception as exc:  # noqa: BLE001 - a sonda nunca derruba a sessão
                resultado[canal] = {
                    "respondeu": False,
                    "itens": 0,
                    "erro": type(exc).__name__,
                    "ms": int((time.monotonic() - inicio) * 1000),
                }
                continue
            itens = 0
            if isinstance(dados, dict):
                lista = (dados.get("instruments") or dados.get("msg") or {})
                if isinstance(lista, dict):
                    lista = lista.get("instruments") or []
                itens = len(lista) if isinstance(lista, list) else 0
            resultado[canal] = {
                "respondeu": dados is not None,
                "itens": itens,
                "erro": None,
                "ms": int((time.monotonic() - inicio) * 1000),
            }
        return resultado

    return build_success(session_manager.run(user_id, operation))


@app.get("/catalog/probe")
def catalog_probe(
    digital_timeout: float = 20.0,
    other_timeout: float = 20.0,
    x_user_id: str | None = Header(default=None),
) -> dict[str, Any]:
    """Dump read-only do catálogo REAL da corretora, canal por canal.

    Responde a pergunta que nenhum log responde: a BullEx oferece par de
    mercado aberto (não-OTC) e, se oferece, em QUAL canal — turbo, binary,
    digital ou só forex/CFD. Até aqui só perguntávamos "o EURUSD está no mapa?"
    e tratávamos a ausência como "fechado"; nunca olhamos o que o mapa TEM.

    Não filtra por ``BINARY_ALLOWED_ASSETS`` de propósito — a allowlist é nossa,
    e o objetivo aqui é justamente ver o que existe fora dela.

    Args:
        digital_timeout: Espera do catálogo digital (a lib usa 30s fixos por
            dentro; este valor limita a nossa thread).
        other_timeout: Orçamento para crypto/forex/cfd somados.
        x_user_id: Dono da sessão conectada.

    Returns:
        Envelope com um bloco por canal: itens, quantos são não-OTC e amostra.
    """
    user_id = require_user_id(x_user_id)

    def _split(nomes: list[str]) -> dict[str, Any]:
        otc = sorted({n for n in nomes if "OTC" in n.upper()})
        aberto = sorted({n for n in nomes if "OTC" not in n.upper()})
        return {
            "total": len(nomes),
            "otc": len(otc),
            "nao_otc": len(aberto),
            "nao_otc_nomes": aberto,
            "otc_amostra": otc[:8],
        }

    def operation(session: ManagedSession) -> dict[str, Any]:
        ensure_session_ready(session)
        client = session.client
        resultado: dict[str, Any] = {}

        # 1) turbo / binary — é o que `client.buy()` usa.
        try:
            init = client.get_all_init_v2(timeout=15)
        except Exception as exc:  # noqa: BLE001 - sonda nunca derruba a sessão
            init = None
            resultado["init_v2_erro"] = type(exc).__name__
        for canal in ("turbo", "binary"):
            secao = (init or {}).get(canal) if isinstance(init, dict) else None
            if not isinstance(secao, dict) or not isinstance(secao.get("actives"), dict):
                resultado[canal] = {"respondeu": False}
                continue
            nomes: list[str] = []
            abertos: list[str] = []
            for ativo in secao["actives"].values():
                if not isinstance(ativo, dict):
                    continue
                bruto = str(ativo.get("name") or "")
                nome = bruto.split(".", 1)[1] if "." in bruto else bruto
                if not nome:
                    continue
                nomes.append(nome)
                if bool(ativo.get("enabled")) and not bool(ativo.get("is_suspended")):
                    abertos.append(nome)
            bloco = {"respondeu": True, **_split(nomes)}
            bloco["abertos_agora"] = len(abertos)
            bloco["abertos_nao_otc"] = sorted(
                {n for n in abertos if "OTC" not in n.upper()}
            )
            resultado[canal] = bloco

        # 2) digital — canal separado, com agenda de abertura por ativo.
        try:
            dados = client.get_digital_underlying_list_data()
        except Exception as exc:  # noqa: BLE001
            dados = None
            resultado["digital_erro"] = type(exc).__name__
        lista = (dados or {}).get("underlying") if isinstance(dados, dict) else None
        if isinstance(lista, list):
            nomes = [str(item.get("underlying") or "") for item in lista if isinstance(item, dict)]
            nomes = [n for n in nomes if n]
            agora = time.time()
            abertos = []
            for item in lista:
                if not isinstance(item, dict):
                    continue
                nome = str(item.get("underlying") or "")
                for janela in item.get("schedule") or []:
                    if not isinstance(janela, dict):
                        continue
                    if float(janela.get("open") or 0) < agora < float(janela.get("close") or 0):
                        abertos.append(nome)
                        break
            bloco = {"respondeu": True, **_split(nomes)}
            bloco["abertos_agora"] = len(set(abertos))
            bloco["abertos_nao_otc"] = sorted(
                {n for n in abertos if "OTC" not in n.upper()}
            )
            resultado["digital"] = bloco
        else:
            resultado["digital"] = {"respondeu": False}

        # 3) forex / cfd / crypto — instrumentos não-binários (o que o dono viu
        #    na tela da corretora). Orçamento compartilhado, como na lib.
        prazo = time.time() + float(other_timeout)
        for canal in ("forex", "cfd", "crypto"):
            restante = prazo - time.time()
            if restante <= 0:
                resultado[canal] = {"respondeu": False, "erro": "SEM_ORCAMENTO"}
                continue
            try:
                dados = client.get_instruments(canal, timeout=restante)
            except Exception as exc:  # noqa: BLE001
                resultado[canal] = {"respondeu": False, "erro": type(exc).__name__}
                continue
            itens = (dados or {}).get("instruments") if isinstance(dados, dict) else None
            if not isinstance(itens, list):
                resultado[canal] = {"respondeu": dados is not None, "total": 0}
                continue
            nomes = [str(i.get("name") or "") for i in itens if isinstance(i, dict)]
            nomes = [n for n in nomes if n]
            agora = time.time()
            abertos = []
            for i in itens:
                if not isinstance(i, dict):
                    continue
                for janela in i.get("schedule") or []:
                    if not isinstance(janela, dict):
                        continue
                    if float(janela.get("open") or 0) < agora < float(janela.get("close") or 0):
                        abertos.append(str(i.get("name") or ""))
                        break
            bloco = {"respondeu": True, **_split(nomes)}
            bloco["abertos_agora"] = len(set(abertos))
            resultado[canal] = bloco

        # 4) payout por ativo, do jeito que o robô pergunta (`get_all_profit`).
        try:
            lucros = client.get_all_profit()
        except Exception as exc:  # noqa: BLE001
            lucros = None
            resultado["profit_erro"] = type(exc).__name__
        if isinstance(lucros, dict):
            amostra: dict[str, Any] = {}
            for nome, valores in lucros.items():
                if not isinstance(valores, dict):
                    continue
                if "OTC" in str(nome).upper() and len(amostra) > 40:
                    continue
                amostra[str(nome)] = {
                    k: (round(float(v) * 100) if isinstance(v, (int, float)) else v)
                    for k, v in valores.items()
                }
            resultado["profit"] = {
                "respondeu": True,
                "total": len(lucros),
                "nao_otc": sorted(
                    {str(n) for n in lucros if "OTC" not in str(n).upper()}
                ),
                "por_ativo": amostra,
            }
        else:
            resultado["profit"] = {"respondeu": False}

        return resultado

    return build_success(
        session_manager.run(
            user_id,
            operation,
            disconnect_on_error=False,
            timeout_seconds=float(digital_timeout) + float(other_timeout) + 40.0,
            gate_timeout=60.0,
        )
    )


@app.post("/orders/buy-digital")
def buy_digital(payload: BuyDigitalRequest, x_user_id: str | None = Header(default=None)) -> dict[str, Any]:
    """Compra no canal DIGITAL — o único aberto nos ativos de mercado aberto.

    O ``/orders/buy-real`` manda por ``client.buy()``, que é turbo/binary. Nos
    pares abertos esse canal fica fechado enquanto o digital responde payout
    82–88, e a ordem morre em "asset is not available at the moment". Este
    endpoint é o caminho que faltava.

    Args:
        payload: Ativo, valor, direção e duração em minutos.
        x_user_id: Dono da sessão.

    Returns:
        Envelope padrão com ``order_id`` e ``channel="digital"``.

    Raises:
        ServiceError: Se o canal digital recusar ou não responder a tempo.
    """
    user_id = require_user_id(x_user_id)
    active = ensure_binary_asset_allowed(payload.active)

    def operation(session: ManagedSession) -> dict[str, Any]:
        ensure_session_ready(session)
        force_real_mode(session, user_id=user_id)
        try:
            active_id = OP_code.ACTIVES[active]
        except KeyError as exc:
            raise ServiceError(f"DIGITAL_ACTIVE_UNKNOWN:{active}", 400) from exc
        try:
            ok, order_id = place_digital_order(
                session.client,
                active=active,
                active_id=active_id,
                amount=float(payload.amount),
                action=str(payload.action),
                duration_minutes=int(payload.duration),
            )
        except DigitalOrderError as exc:
            raise ServiceError(str(exc), 409) from exc
        except ValueError as exc:
            raise ServiceError(str(exc), 400) from exc
        if not ok:
            logger.warning("[DIGITAL_BUY_BLOCKED] user_id=%s active=%s motivo=%s", user_id, active, order_id)
            raise ServiceError(f"DIGITAL_BUY_REJECTED:{order_id}", 409)
        logger.info("[DIGITAL_BUY_SUCCESS] user_id=%s active=%s order_id=%s", user_id, active, order_id)
        return {
            "mode": "REAL",
            "channel": "digital",
            "order_id": order_id,
            "active": active,
            "amount": payload.amount,
            "action": payload.action,
            "expiration": payload.duration,
        }

    return build_success(session_manager.run(user_id, operation))


@app.get("/orders/{order_id}/digital-result")
def digital_result(order_id: str, x_user_id: str | None = Header(default=None)) -> dict[str, Any]:
    """Resultado de uma ordem digital, sem bloquear.

    A ``check_win_digital_v2`` da biblioteca faz espera ocupada até a posição
    fechar. Aqui a leitura é um retrato: devolve ``PENDING_RESULT`` enquanto a
    posição estiver aberta e quem chama volta a perguntar.
    """
    user_id = require_user_id(x_user_id)

    def operation(session: ManagedSession) -> dict[str, Any]:
        ensure_session_ready(session)
        return read_digital_result(session.client, parse_order_id(order_id))

    return build_success(session_manager.run(user_id, operation))


@app.get("/orders/{order_id}/result")
def order_result(order_id: str, x_user_id: str | None = Header(default=None)) -> dict[str, Any]:
    user_id = require_user_id(x_user_id)
    parsed_order_id = parse_order_id(order_id)

    def operation(session: ManagedSession) -> dict[str, Any]:
        ensure_session_ready(session)
        socket_option_closed = getattr(session.client.api, "socket_option_closed", {})
        order_binary = getattr(session.client.api, "order_binary", {})
        closed_order = socket_option_closed.get(parsed_order_id)
        if closed_order is None:
            closed_order = socket_option_closed.get(str(parsed_order_id))
        if closed_order is None:
            closed_order = order_binary.get(parsed_order_id)
        if closed_order is None:
            closed_order = order_binary.get(str(parsed_order_id))
        if not isinstance(closed_order, dict):
            # Ordem do canal DIGITAL não aparece em socket_option_closed nem em
            # order_binary. Sem esta consulta ela ficaria PENDING_RESULT para
            # sempre: o gateway só chama `/orders/{id}/result`, então a
            # operação nunca viraria win/loose no histórico e o ciclo travaria
            # em SKIP_ANALYSIS_WAITING_RESULT.
            digital = read_digital_result(session.client, parsed_order_id)
            if digital.get("result") != "PENDING_RESULT":
                return digital
            return {"order_id": parsed_order_id, "result": "PENDING_RESULT", "profit": None}
        message = closed_order.get("msg") if isinstance(closed_order.get("msg"), dict) else closed_order
        if not isinstance(message, dict):
            return {"order_id": parsed_order_id, "result": "PENDING_RESULT", "profit": None}

        # Cinto de segurança: o próprio payload do fechamento carrega o id da
        # ordem (`id` no canal socket-option-closed, `option_id` no canal
        # binary). Se ele não bater com o order_id pedido, algo escreveu no
        # dict errado — melhor devolver PENDING_RESULT do que arriscar
        # misturar o resultado de uma ordem com o de outra (WIN/LOSS trocado
        # no histórico). Ver ROBO_E_SUPORTE.md.
        own_id = message.get("id", message.get("option_id"))
        if own_id is not None and str(own_id) != str(parsed_order_id):
            logger.warning(
                "[ORDER_RESULT_ID_MISMATCH] requested=%s found_in_message=%s",
                parsed_order_id,
                own_id,
            )
            return {"order_id": parsed_order_id, "result": "PENDING_RESULT", "profit": None}

        result = str(message.get("win") or "").strip().lower()
        if not result:
            return {"order_id": parsed_order_id, "result": "PENDING_RESULT", "profit": None}
        amount = float(message.get("sum") or 0)
        if result == "equal":
            profit = 0.0
        elif result == "loose":
            profit = -amount
        else:
            profit = float(message.get("win_amount") or 0) - amount
        return {"order_id": parsed_order_id, "result": result, "profit": profit}

    return build_success(session_manager.run(user_id, operation))
