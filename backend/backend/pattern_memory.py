"""
Memória de padrões do El Capo.

Aprende quais contextos (ativo × hora UTC × setup × direção × timeframe)
trazem mais WIN ou LOSS e veta entradas em padrões fracos — complementar à
estratégia clássica, nunca a substitui.

Regras:
- Fail-open com amostra insuficiente.
- Gale não entra na memória (é consequência da entrada original).
- Caderno global (padrão): todas as contas alimentam e consultam o mesmo
  agregado; cadernos por ``user_id`` permanecem como arquivo/auditoria.
- Persistência opcional em ``robot_pattern_memory`` + ``robot_pattern_memory_global``.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx

logger = logging.getLogger("backend-gateway")

PATTERN_MEMORY_BLOCK = "PATTERN_MEMORY_WEAK"
GLOBAL_PATTERN_OWNER = "__global__"

DEFAULT_MIN_SAMPLES_TO_BLOCK = 12
DEFAULT_MAX_WEAK_WIN_RATE = 52.0
DEFAULT_LOOKBACK_DAYS = 90


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def extract_setup(payload: dict[str, Any] | None) -> str:
    """
    Extrai o setup de price action de um candidato ou trade.

    Args:
        payload: Candidato de entrada ou item de histórico.

    Returns:
        Setup normalizado (ex.: CONTINUATION) ou UNKNOWN.
    """
    if not isinstance(payload, dict):
        return "UNKNOWN"
    for key in ("strategy_setup", "price_action_setup"):
        value = str(payload.get(key) or "").strip().upper()
        if value:
            return value
    setups = payload.get("strategy_setups")
    if isinstance(setups, dict):
        for value in setups.values():
            text = str(value or "").strip().upper()
            if text:
                return text
    metrics = payload.get("metrics")
    if isinstance(metrics, dict):
        value = str(metrics.get("price_action_setup") or "").strip().upper()
        if value:
            return value
    analysis = payload.get("analysis_json")
    if isinstance(analysis, str):
        try:
            import json

            analysis = json.loads(analysis)
        except Exception:
            analysis = None
    if isinstance(analysis, dict):
        nested = extract_setup(analysis)
        if nested != "UNKNOWN":
            return nested
    return "UNKNOWN"


def extract_active(payload: dict[str, Any] | None) -> str:
    """Retorna o símbolo do ativo a partir de candidate/trade."""
    if not isinstance(payload, dict):
        return "UNKNOWN"
    for key in ("symbol", "active"):
        value = str(payload.get(key) or "").strip().upper()
        if value:
            return value
    return "UNKNOWN"


def extract_direction(payload: dict[str, Any] | None) -> str:
    """Retorna CALL/PUT a partir de candidate/trade."""
    if not isinstance(payload, dict):
        return "UNKNOWN"
    for key in ("direction", "signal"):
        value = str(payload.get(key) or "").strip().upper()
        if value in {"CALL", "PUT"}:
            return value
    return "UNKNOWN"


def extract_timeframe(payload: dict[str, Any] | None, default: str = "M1") -> str:
    """Retorna timeframe normalizado (M1/M5/M15)."""
    if not isinstance(payload, dict):
        return default
    for key in ("timeframe", "expiration"):
        value = str(payload.get(key) or "").strip().upper()
        if value in {"M1", "M5", "M15"}:
            return value
        if value in {"1", "60", "1M"}:
            return "M1"
        if value in {"5", "300", "5M"}:
            return "M5"
        if value in {"15", "900", "15M"}:
            return "M15"
    return default


def extract_hour_utc(payload: dict[str, Any] | None, now: datetime | None = None) -> int:
    """
    Hora UTC do contexto.

    Prefere ``opened_at``/``sent_at`` do trade; no candidato usa ``now``.
    """
    if isinstance(payload, dict):
        for key in ("opened_at", "sent_at", "timestamp"):
            raw = payload.get(key)
            if not raw:
                continue
            try:
                text = str(raw).replace("Z", "+00:00")
                dt = datetime.fromisoformat(text)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return int(dt.astimezone(timezone.utc).hour)
            except (TypeError, ValueError):
                continue
    ref = now or datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    return int(ref.astimezone(timezone.utc).hour)


def build_pattern_key(
    *,
    active: str,
    hour_utc: int,
    setup: str,
    direction: str,
    timeframe: str,
) -> str:
    """
    Monta a chave canônica do padrão.

    Args:
        active: Símbolo (ex.: EURUSD-OTC).
        hour_utc: Hora 0–23 em UTC.
        setup: Setup de price action.
        direction: CALL ou PUT.
        timeframe: M1/M5/M15.

    Returns:
        Chave estável ``ATIVO|HH|SETUP|DIR|TF``.
    """
    hour = max(0, min(23, int(hour_utc)))
    return (
        f"{str(active or 'UNKNOWN').strip().upper()}"
        f"|{hour:02d}"
        f"|{str(setup or 'UNKNOWN').strip().upper()}"
        f"|{str(direction or 'UNKNOWN').strip().upper()}"
        f"|{str(timeframe or 'M1').strip().upper()}"
    )


def pattern_key_from_payload(
    payload: dict[str, Any] | None,
    *,
    now: datetime | None = None,
    default_timeframe: str = "M1",
) -> str:
    """Deriva a chave de padrão de um candidato ou trade."""
    return build_pattern_key(
        active=extract_active(payload),
        hour_utc=extract_hour_utc(payload, now=now),
        setup=extract_setup(payload),
        direction=extract_direction(payload),
        timeframe=extract_timeframe(payload, default=default_timeframe),
    )


@dataclass
class PatternStats:
    """Contadores agregados de um padrão."""

    wins: int = 0
    losses: int = 0
    profit: float = 0.0

    @property
    def samples(self) -> int:
        return int(self.wins) + int(self.losses)

    @property
    def win_rate(self) -> float:
        total = self.samples
        if total <= 0:
            return 0.0
        return round((self.wins / total) * 100.0, 2)

    def apply(self, result: str, profit: float) -> None:
        normalized = str(result or "").strip().upper()
        if normalized == "WIN":
            self.wins += 1
        elif normalized == "LOSS":
            self.losses += 1
        else:
            return
        self.profit = round(float(self.profit) + float(profit or 0), 2)

    def merge(self, other: "PatternStats") -> None:
        """Soma contadores de outro agregado (unificação de cadernos)."""
        self.wins += int(other.wins)
        self.losses += int(other.losses)
        self.profit = round(float(self.profit) + float(other.profit or 0), 2)


def unify_pattern_rows(rows: list[dict[str, Any]]) -> dict[str, PatternStats]:
    """
    Unifica linhas de cadernos pessoais no mesmo ``pattern_key``.

    Args:
        rows: Linhas com pattern_key/wins/losses/profit (vários user_id).

    Returns:
        Mapa ``pattern_key -> PatternStats`` somado.
    """
    merged: dict[str, PatternStats] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        key = str(row.get("pattern_key") or "").strip()
        if not key or key.startswith(GLOBAL_PATTERN_OWNER):
            continue
        owner = str(row.get("user_id") or "").strip()
        if owner == GLOBAL_PATTERN_OWNER:
            continue
        stats = merged.setdefault(key, PatternStats())
        stats.wins += int(row.get("wins") or 0)
        stats.losses += int(row.get("losses") or 0)
        stats.profit = round(float(stats.profit) + float(row.get("profit") or 0), 2)
    return merged


def _meta_from_pattern_key(pattern_key: str) -> dict[str, Any]:
    """Extrai dimensões ativas da chave canônica."""
    parts = str(pattern_key or "").split("|")
    return {
        "active": parts[0] if len(parts) > 0 else "UNKNOWN",
        "hour_utc": int(parts[1]) if len(parts) > 1 and str(parts[1]).isdigit() else 0,
        "setup": parts[2] if len(parts) > 2 else "UNKNOWN",
        "direction": parts[3] if len(parts) > 3 else "UNKNOWN",
        "timeframe": parts[4] if len(parts) > 4 else "M1",
    }


@dataclass(frozen=True)
class PatternDecision:
    """Decisão do portão de memória de padrões."""

    allowed: bool
    reason: str
    pattern_key: str
    samples: int = 0
    win_rate: float = 0.0
    wins: int = 0
    losses: int = 0
    profit: float = 0.0


@dataclass
class PatternMemoryService:
    """
    Serviço de memória de padrões.

    Com ``use_global=True`` (padrão), todas as contas compartilham o mesmo
    caderno; os cadernos por usuário continuam sendo gravados como arquivo.
    """

    enabled: bool = field(default_factory=lambda: _env_bool("PATTERN_MEMORY_ENABLED", True))
    use_global: bool = field(default_factory=lambda: _env_bool("PATTERN_MEMORY_GLOBAL", True))
    min_samples_to_block: int = field(
        default_factory=lambda: _env_int("PATTERN_MEMORY_MIN_SAMPLES", DEFAULT_MIN_SAMPLES_TO_BLOCK)
    )
    max_weak_win_rate: float = field(
        default_factory=lambda: _env_float("PATTERN_MEMORY_MAX_WEAK_WIN_RATE", DEFAULT_MAX_WEAK_WIN_RATE)
    )
    lookback_days: int = field(
        default_factory=lambda: _env_int("PATTERN_MEMORY_LOOKBACK_DAYS", DEFAULT_LOOKBACK_DAYS)
    )
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    _by_user: dict[str, dict[str, PatternStats]] = field(default_factory=dict, repr=False)
    _hydrated_users: set[str] = field(default_factory=set, repr=False)
    _store: Any | None = field(default=None, repr=False)
    _unified_once: bool = field(default=False, repr=False)

    def attach_store(self, store: Any | None) -> None:
        """Associa backend de persistência (Supabase/SQLite)."""
        self._store = store

    def is_enabled(self) -> bool:
        """Indica se a memória está ativa."""
        return bool(self.enabled)

    def is_global(self) -> bool:
        """Indica se o portão usa o caderno unificado do sistema."""
        return bool(self.use_global)

    def _scope_id(self, user_id: str) -> str:
        """Retorna o dono do bucket consultado (global ou pessoal)."""
        if self.use_global:
            return GLOBAL_PATTERN_OWNER
        return str(user_id or "").strip()

    def record_outcome(self, user_id: str, trade: dict[str, Any]) -> PatternStats | None:
        """
        Atualiza a memória com o resultado de uma operação finalizada.

        Em modo global grava no caderno do sistema e também no arquivo
        pessoal do usuário (auditoria / re-unificação).

        Args:
            user_id: Dono da operação (nunca confiar no body do front).
            trade: Payload da operação com result/final_result.

        Returns:
            Stats do bucket consultado pelo portão, ou None se ignorado.
        """
        if not self.enabled:
            return None
        normalized_user = str(user_id or "").strip()
        if not normalized_user or not isinstance(trade, dict):
            return None
        if bool(trade.get("is_gale")):
            return None
        result = str(trade.get("final_result") or trade.get("result") or "").strip().upper()
        if result not in {"WIN", "LOSS"}:
            return None

        key = pattern_key_from_payload(trade)
        profit = float(trade.get("profit") or 0)
        with self._lock:
            personal_bucket = self._by_user.setdefault(normalized_user, {})
            personal_stats = personal_bucket.setdefault(key, PatternStats())
            personal_stats.apply(result, profit)
            personal_snapshot = PatternStats(
                wins=personal_stats.wins,
                losses=personal_stats.losses,
                profit=personal_stats.profit,
            )
            self._hydrated_users.add(normalized_user)

            gate_snapshot = personal_snapshot
            if self.use_global:
                global_bucket = self._by_user.setdefault(GLOBAL_PATTERN_OWNER, {})
                global_stats = global_bucket.setdefault(key, PatternStats())
                global_stats.apply(result, profit)
                gate_snapshot = PatternStats(
                    wins=global_stats.wins,
                    losses=global_stats.losses,
                    profit=global_stats.profit,
                )
                self._hydrated_users.add(GLOBAL_PATTERN_OWNER)

        self._persist_upsert(normalized_user, key, trade, personal_snapshot)
        if self.use_global:
            self._persist_upsert_global(key, trade, gate_snapshot)

        logger.info(
            "[PATTERN_MEMORY_RECORDED] user_id=%s scope=%s key=%s result=%s wins=%s losses=%s wr=%s",
            normalized_user,
            "global" if self.use_global else "personal",
            key,
            result,
            gate_snapshot.wins,
            gate_snapshot.losses,
            gate_snapshot.win_rate,
        )
        return gate_snapshot

    def rebuild_from_history(self, user_id: str, history: list[dict[str, Any]]) -> int:
        """
        Reconstrói a memória do usuário a partir do histórico.

        Em modo global, também aplica cada trade no caderno unificado.
        """
        normalized_user = str(user_id or "").strip()
        if not normalized_user:
            return 0
        rebuilt: dict[str, PatternStats] = {}
        counted = 0
        for item in history or []:
            if not isinstance(item, dict) or bool(item.get("is_gale")):
                continue
            result = str(item.get("final_result") or item.get("result") or "").strip().upper()
            if result not in {"WIN", "LOSS"}:
                continue
            key = pattern_key_from_payload(item)
            stats = rebuilt.setdefault(key, PatternStats())
            stats.apply(result, float(item.get("profit") or 0))
            counted += 1
        with self._lock:
            self._by_user[normalized_user] = rebuilt
            self._hydrated_users.add(normalized_user)
            if self.use_global and rebuilt:
                global_bucket = self._by_user.setdefault(GLOBAL_PATTERN_OWNER, {})
                for key, stats in rebuilt.items():
                    target = global_bucket.setdefault(key, PatternStats())
                    target.merge(stats)
                self._hydrated_users.add(GLOBAL_PATTERN_OWNER)
        if self._store is not None and counted:
            try:
                self._store.replace_user_patterns(normalized_user, rebuilt)
            except Exception as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status == 404:
                    logger.info(
                        "[PATTERN_MEMORY_TABLE_MISSING] user_id=%s step=replace hint=run_migration_pattern_memory.sql",
                        normalized_user,
                    )
                else:
                    logger.warning(
                        "[PATTERN_MEMORY_PERSIST_REPLACE_FAILED] user_id=%s",
                        normalized_user,
                        exc_info=True,
                    )
            if self.use_global:
                try:
                    self.unify_all_into_global()
                except Exception:
                    logger.warning(
                        "[PATTERN_MEMORY_GLOBAL_UNIFY_FAILED] user_id=%s",
                        normalized_user,
                        exc_info=True,
                    )
        logger.info(
            "[PATTERN_MEMORY_REBUILT] user_id=%s patterns=%s trades=%s",
            normalized_user,
            len(rebuilt),
            counted,
        )
        return counted

    def unify_all_into_global(self) -> int:
        """
        Soma todos os cadernos pessoais num único caderno global.

        Preserva o histórico: não apaga as linhas por usuário; apenas
        materializa o agregado em ``robot_pattern_memory_global`` / RAM.

        Returns:
            Quantidade de ``pattern_key`` unificados.
        """
        rows: list[dict[str, Any]] = []
        if self._store is not None and hasattr(self._store, "load_all_user_patterns"):
            try:
                rows = list(self._store.load_all_user_patterns() or [])
            except Exception as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status == 404:
                    logger.info(
                        "[PATTERN_MEMORY_TABLE_MISSING] step=unify hint=run_migration_pattern_memory_global.sql"
                    )
                else:
                    logger.warning("[PATTERN_MEMORY_GLOBAL_LOAD_USERS_FAILED]", exc_info=True)
                    rows = []
        if not rows:
            with self._lock:
                for owner, bucket in self._by_user.items():
                    if owner == GLOBAL_PATTERN_OWNER:
                        continue
                    for key, stats in bucket.items():
                        meta = _meta_from_pattern_key(key)
                        rows.append(
                            {
                                "user_id": owner,
                                "pattern_key": key,
                                "wins": stats.wins,
                                "losses": stats.losses,
                                "profit": stats.profit,
                                **meta,
                            }
                        )
        merged = unify_pattern_rows(rows)
        with self._lock:
            self._by_user[GLOBAL_PATTERN_OWNER] = {
                key: PatternStats(wins=s.wins, losses=s.losses, profit=s.profit)
                for key, s in merged.items()
            }
            self._hydrated_users.add(GLOBAL_PATTERN_OWNER)
            self._unified_once = True
        if self._store is not None and hasattr(self._store, "replace_global_patterns"):
            try:
                self._store.replace_global_patterns(merged)
            except Exception as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status == 404:
                    logger.info(
                        "[PATTERN_MEMORY_GLOBAL_TABLE_MISSING] hint=run_migration_pattern_memory_global.sql"
                    )
                else:
                    logger.warning("[PATTERN_MEMORY_GLOBAL_PERSIST_FAILED]", exc_info=True)
        logger.info(
            "[PATTERN_MEMORY_GLOBAL_UNIFIED] patterns=%s source_rows=%s",
            len(merged),
            len(rows),
        )
        return len(merged)

    def ensure_hydrated(
        self,
        user_id: str,
        history_loader: Any | None = None,
    ) -> None:
        """
        Garante padrões carregados (global ou pessoais).

        Args:
            user_id: Usuário autenticado (necessário mesmo no modo global).
            history_loader: Callable ``(user_id, days) -> list[dict]`` opcional.
        """
        normalized_user = str(user_id or "").strip()
        if not normalized_user or not self.enabled:
            return

        if self.use_global:
            with self._lock:
                # A flag sozinha é o marcador de "já tentei carregar" — igual ao
                # caminho pessoal abaixo. Exigir bucket não-vazio anulava o cache
                # enquanto não houvesse padrão nenhum: cada ciclo de cada usuário
                # relia 30 dias de histórico no Supabase com cliente SÍNCRONO,
                # congelando o event loop (queda de 08/08: 2904 leituras em 5min,
                # loop parado por até 12s, pool do httpx em 100/100).
                if GLOBAL_PATTERN_OWNER in self._hydrated_users:
                    return
            loaded = self._load_global_from_store()
            if loaded:
                return
            # Unifica cadernos pessoais existentes (não perde histórico).
            if self.unify_all_into_global() > 0:
                return
            if history_loader is not None:
                try:
                    history = history_loader(normalized_user, self.lookback_days) or []
                    if history:
                        self.rebuild_from_history(normalized_user, history)
                        return
                except Exception:
                    logger.warning(
                        "[PATTERN_MEMORY_HISTORY_HYDRATE_FAILED] user_id=%s",
                        normalized_user,
                        exc_info=True,
                    )
            with self._lock:
                self._by_user.setdefault(GLOBAL_PATTERN_OWNER, {})
                self._hydrated_users.add(GLOBAL_PATTERN_OWNER)
            return

        with self._lock:
            if normalized_user in self._hydrated_users:
                return
        loaded = False
        if self._store is not None:
            try:
                rows = self._store.load_user_patterns(normalized_user)
                if rows:
                    bucket = {
                        str(row["pattern_key"]): PatternStats(
                            wins=int(row.get("wins") or 0),
                            losses=int(row.get("losses") or 0),
                            profit=float(row.get("profit") or 0),
                        )
                        for row in rows
                        if row.get("pattern_key")
                    }
                    with self._lock:
                        self._by_user[normalized_user] = bucket
                        self._hydrated_users.add(normalized_user)
                    loaded = True
            except Exception as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status == 404:
                    logger.info(
                        "[PATTERN_MEMORY_TABLE_MISSING] user_id=%s hint=run_migration_pattern_memory.sql",
                        normalized_user,
                    )
                else:
                    logger.warning(
                        "[PATTERN_MEMORY_LOAD_FAILED] user_id=%s",
                        normalized_user,
                        exc_info=True,
                    )
        if loaded:
            return
        if history_loader is not None:
            try:
                history = history_loader(normalized_user, self.lookback_days) or []
                self.rebuild_from_history(normalized_user, history)
                return
            except Exception:
                logger.warning(
                    "[PATTERN_MEMORY_HISTORY_HYDRATE_FAILED] user_id=%s",
                    normalized_user,
                    exc_info=True,
                )
        with self._lock:
            self._by_user.setdefault(normalized_user, {})
            self._hydrated_users.add(normalized_user)

    def _load_global_from_store(self) -> bool:
        if self._store is None or not hasattr(self._store, "load_global_patterns"):
            return False
        try:
            rows = self._store.load_global_patterns() or []
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status == 404:
                logger.info(
                    "[PATTERN_MEMORY_GLOBAL_TABLE_MISSING] hint=run_migration_pattern_memory_global.sql"
                )
            else:
                logger.warning("[PATTERN_MEMORY_GLOBAL_LOAD_FAILED]", exc_info=True)
            return False
        if not rows:
            return False
        bucket = {
            str(row["pattern_key"]): PatternStats(
                wins=int(row.get("wins") or 0),
                losses=int(row.get("losses") or 0),
                profit=float(row.get("profit") or 0),
            )
            for row in rows
            if row.get("pattern_key")
        }
        with self._lock:
            self._by_user[GLOBAL_PATTERN_OWNER] = bucket
            self._hydrated_users.add(GLOBAL_PATTERN_OWNER)
        logger.info("[PATTERN_MEMORY_GLOBAL_LOADED] patterns=%s", len(bucket))
        return True

    def evaluate(
        self,
        user_id: str,
        candidate: dict[str, Any] | None,
        *,
        now: datetime | None = None,
        default_timeframe: str = "M1",
        history_loader: Any | None = None,
    ) -> PatternDecision:
        """
        Decide se o candidato passa pelo filtro de memória.

        Args:
            user_id: Dono da sessão.
            candidate: Candidato de entrada.
            now: Relógio de referência (testes).
            default_timeframe: TF padrão se o candidate omitir.
            history_loader: Loader para hidratar sob demanda.

        Returns:
            ``PatternDecision`` (fail-open se desabilitado / amostra baixa).
        """
        key = pattern_key_from_payload(
            candidate,
            now=now,
            default_timeframe=default_timeframe,
        )
        if not self.enabled:
            return PatternDecision(allowed=True, reason="DISABLED", pattern_key=key)
        normalized_user = str(user_id or "").strip()
        if not normalized_user or not isinstance(candidate, dict):
            return PatternDecision(allowed=True, reason="NO_CONTEXT", pattern_key=key)

        self.ensure_hydrated(normalized_user, history_loader=history_loader)
        scope = self._scope_id(normalized_user)
        with self._lock:
            stats = self._by_user.get(scope, {}).get(key) or PatternStats()
            samples = stats.samples
            win_rate = stats.win_rate
            wins = stats.wins
            losses = stats.losses
            profit = stats.profit

        if samples < int(self.min_samples_to_block):
            return PatternDecision(
                allowed=True,
                reason="INSUFFICIENT_SAMPLE",
                pattern_key=key,
                samples=samples,
                win_rate=win_rate,
                wins=wins,
                losses=losses,
                profit=profit,
            )
        if win_rate < float(self.max_weak_win_rate):
            return PatternDecision(
                allowed=False,
                reason=PATTERN_MEMORY_BLOCK,
                pattern_key=key,
                samples=samples,
                win_rate=win_rate,
                wins=wins,
                losses=losses,
                profit=profit,
            )
        return PatternDecision(
            allowed=True,
            reason="PATTERN_OK",
            pattern_key=key,
            samples=samples,
            win_rate=win_rate,
            wins=wins,
            losses=losses,
            profit=profit,
        )

    def apply_to_candidate(
        self,
        user_id: str,
        candidate: dict[str, Any],
        *,
        now: datetime | None = None,
        default_timeframe: str = "M1",
        history_loader: Any | None = None,
    ) -> PatternDecision:
        """
        Avalia e, se bloquear, marca o candidato com filtro crítico.

        Args:
            user_id: Dono da sessão.
            candidate: Candidato mutável.
            now: Relógio de referência.
            default_timeframe: TF padrão.
            history_loader: Loader opcional.

        Returns:
            Decisão aplicada.
        """
        decision = self.evaluate(
            user_id,
            candidate,
            now=now,
            default_timeframe=default_timeframe,
            history_loader=history_loader,
        )
        candidate["pattern_memory_key"] = decision.pattern_key
        candidate["pattern_memory_samples"] = decision.samples
        candidate["pattern_memory_win_rate"] = decision.win_rate
        candidate["pattern_memory_reason"] = decision.reason
        candidate["pattern_memory_scope"] = "global" if self.use_global else "personal"
        if not decision.allowed:
            blocked = [str(item) for item in (candidate.get("blocked_filters") or [])]
            if PATTERN_MEMORY_BLOCK not in blocked:
                blocked.append(PATTERN_MEMORY_BLOCK)
            candidate["blocked_filters"] = blocked
            candidate["trade_allowed"] = False
            logger.info(
                "[PATTERN_MEMORY_BLOCKED] user_id=%s scope=%s key=%s samples=%s wr=%s",
                user_id,
                "global" if self.use_global else "personal",
                decision.pattern_key,
                decision.samples,
                decision.win_rate,
            )
        return decision

    def snapshot_for_user(self, user_id: str) -> list[dict[str, Any]]:
        """
        Lista padrões visíveis para o usuário.

        Em modo global retorna o caderno unificado do sistema.
        """
        if self.use_global:
            return self.snapshot_global()
        normalized_user = str(user_id or "").strip()
        with self._lock:
            bucket = dict(self._by_user.get(normalized_user) or {})
        return self._snapshot_bucket(bucket)

    def snapshot_global(self) -> list[dict[str, Any]]:
        """Lista o caderno global ordenado por amostras (desc)."""
        with self._lock:
            bucket = dict(self._by_user.get(GLOBAL_PATTERN_OWNER) or {})
        return self._snapshot_bucket(bucket)

    def _snapshot_bucket(self, bucket: dict[str, PatternStats]) -> list[dict[str, Any]]:
        rows = [
            {
                "pattern_key": key,
                "wins": stats.wins,
                "losses": stats.losses,
                "samples": stats.samples,
                "win_rate": stats.win_rate,
                "profit": stats.profit,
            }
            for key, stats in bucket.items()
        ]
        return sorted(rows, key=lambda row: (-int(row["samples"]), str(row["pattern_key"])))

    def _persist_upsert(
        self,
        user_id: str,
        pattern_key: str,
        trade: dict[str, Any],
        stats: PatternStats,
    ) -> None:
        if self._store is None:
            return
        try:
            self._store.upsert_pattern(
                user_id=user_id,
                pattern_key=pattern_key,
                active=extract_active(trade),
                direction=extract_direction(trade),
                setup=extract_setup(trade),
                timeframe=extract_timeframe(trade),
                hour_utc=extract_hour_utc(trade),
                wins=stats.wins,
                losses=stats.losses,
                profit=stats.profit,
            )
        except Exception:
            logger.warning(
                "[PATTERN_MEMORY_PERSIST_UPSERT_FAILED] user_id=%s key=%s",
                user_id,
                pattern_key,
                exc_info=True,
            )

    def _persist_upsert_global(
        self,
        pattern_key: str,
        trade: dict[str, Any],
        stats: PatternStats,
    ) -> None:
        if self._store is None or not hasattr(self._store, "upsert_global_pattern"):
            return
        try:
            self._store.upsert_global_pattern(
                pattern_key=pattern_key,
                active=extract_active(trade),
                direction=extract_direction(trade),
                setup=extract_setup(trade),
                timeframe=extract_timeframe(trade),
                hour_utc=extract_hour_utc(trade),
                wins=stats.wins,
                losses=stats.losses,
                profit=stats.profit,
            )
        except Exception:
            logger.warning(
                "[PATTERN_MEMORY_GLOBAL_UPSERT_FAILED] key=%s",
                pattern_key,
                exc_info=True,
            )


class InMemoryPatternStore:
    """Store mínimo para testes (sem I/O)."""

    def __init__(self) -> None:
        self._rows: dict[str, dict[str, dict[str, Any]]] = {}
        self._global: dict[str, dict[str, Any]] = {}

    def load_user_patterns(self, user_id: str) -> list[dict[str, Any]]:
        return list(self._rows.get(user_id, {}).values())

    def load_all_user_patterns(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for owner, bucket in self._rows.items():
            if owner == GLOBAL_PATTERN_OWNER:
                continue
            rows.extend(bucket.values())
        return rows

    def load_global_patterns(self) -> list[dict[str, Any]]:
        return list(self._global.values())

    def upsert_pattern(self, **payload: Any) -> None:
        user_id = str(payload["user_id"])
        key = str(payload["pattern_key"])
        self._rows.setdefault(user_id, {})[key] = dict(payload)

    def upsert_global_pattern(self, **payload: Any) -> None:
        key = str(payload["pattern_key"])
        self._global[key] = dict(payload)

    def replace_global_patterns(self, patterns: dict[str, PatternStats]) -> None:
        bucket: dict[str, dict[str, Any]] = {}
        for key, stats in patterns.items():
            meta = _meta_from_pattern_key(key)
            bucket[key] = {
                "pattern_key": key,
                **meta,
                "wins": stats.wins,
                "losses": stats.losses,
                "profit": stats.profit,
            }
        self._global = bucket

    def replace_user_patterns(self, user_id: str, patterns: dict[str, PatternStats]) -> None:
        bucket: dict[str, dict[str, Any]] = {}
        for key, stats in patterns.items():
            meta = _meta_from_pattern_key(key)
            bucket[key] = {
                "user_id": user_id,
                "pattern_key": key,
                **meta,
                "wins": stats.wins,
                "losses": stats.losses,
                "profit": stats.profit,
            }
        self._rows[user_id] = bucket


class SQLitePatternStore:
    """Persistência local da memória de padrões (dev/testes)."""

    def __init__(self, database_path: str) -> None:
        self.database_path = database_path
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                create table if not exists robot_pattern_memory (
                    user_id text not null,
                    pattern_key text not null,
                    active text not null,
                    direction text not null,
                    setup text not null,
                    timeframe text not null,
                    hour_utc integer not null,
                    wins integer not null default 0,
                    losses integer not null default 0,
                    profit real not null default 0,
                    updated_at text not null,
                    primary key (user_id, pattern_key)
                )
                """
            )
            connection.execute(
                """
                create table if not exists robot_pattern_memory_global (
                    pattern_key text primary key,
                    active text not null,
                    direction text not null,
                    setup text not null,
                    timeframe text not null,
                    hour_utc integer not null,
                    wins integer not null default 0,
                    losses integer not null default 0,
                    profit real not null default 0,
                    updated_at text not null
                )
                """
            )

    def load_user_patterns(self, user_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "select * from robot_pattern_memory where user_id = ?",
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def load_all_user_patterns(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "select * from robot_pattern_memory where user_id != ?",
                (GLOBAL_PATTERN_OWNER,),
            ).fetchall()
        return [dict(row) for row in rows]

    def load_global_patterns(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "select * from robot_pattern_memory_global"
            ).fetchall()
        return [dict(row) for row in rows]

    def upsert_pattern(self, **payload: Any) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                insert into robot_pattern_memory (
                    user_id, pattern_key, active, direction, setup, timeframe,
                    hour_utc, wins, losses, profit, updated_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(user_id, pattern_key) do update set
                    wins = excluded.wins,
                    losses = excluded.losses,
                    profit = excluded.profit,
                    updated_at = excluded.updated_at
                """,
                (
                    payload["user_id"],
                    payload["pattern_key"],
                    payload["active"],
                    payload["direction"],
                    payload["setup"],
                    payload["timeframe"],
                    int(payload["hour_utc"]),
                    int(payload["wins"]),
                    int(payload["losses"]),
                    float(payload["profit"]),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def upsert_global_pattern(self, **payload: Any) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                insert into robot_pattern_memory_global (
                    pattern_key, active, direction, setup, timeframe,
                    hour_utc, wins, losses, profit, updated_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(pattern_key) do update set
                    wins = excluded.wins,
                    losses = excluded.losses,
                    profit = excluded.profit,
                    updated_at = excluded.updated_at
                """,
                (
                    payload["pattern_key"],
                    payload["active"],
                    payload["direction"],
                    payload["setup"],
                    payload["timeframe"],
                    int(payload["hour_utc"]),
                    int(payload["wins"]),
                    int(payload["losses"]),
                    float(payload["profit"]),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def replace_global_patterns(self, patterns: dict[str, PatternStats]) -> None:
        with self._connect() as connection:
            connection.execute("delete from robot_pattern_memory_global")
            now = datetime.now(timezone.utc).isoformat()
            for key, stats in patterns.items():
                meta = _meta_from_pattern_key(key)
                connection.execute(
                    """
                    insert into robot_pattern_memory_global (
                        pattern_key, active, direction, setup, timeframe,
                        hour_utc, wins, losses, profit, updated_at
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        key,
                        meta["active"],
                        meta["direction"],
                        meta["setup"],
                        meta["timeframe"],
                        int(meta["hour_utc"]),
                        stats.wins,
                        stats.losses,
                        stats.profit,
                        now,
                    ),
                )

    def replace_user_patterns(self, user_id: str, patterns: dict[str, PatternStats]) -> None:
        with self._connect() as connection:
            connection.execute(
                "delete from robot_pattern_memory where user_id = ?",
                (user_id,),
            )
            now = datetime.now(timezone.utc).isoformat()
            for key, stats in patterns.items():
                meta = _meta_from_pattern_key(key)
                connection.execute(
                    """
                    insert into robot_pattern_memory (
                        user_id, pattern_key, active, direction, setup, timeframe,
                        hour_utc, wins, losses, profit, updated_at
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        key,
                        meta["active"],
                        meta["direction"],
                        meta["setup"],
                        meta["timeframe"],
                        int(meta["hour_utc"]),
                        stats.wins,
                        stats.losses,
                        stats.profit,
                        now,
                    ),
                )


class SupabasePatternStore:
    """Persistência PostgREST da memória de padrões."""

    def __init__(self, supabase_url: str, service_role_key: str) -> None:
        self.rest_url = supabase_url.rstrip("/") + "/rest/v1"
        self.headers = {
            "apikey": service_role_key,
            "Authorization": f"Bearer {service_role_key}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        }
        self._global_table_available: bool | None = None
        self._ensure_global_owner_user()

    def _ensure_global_owner_user(self) -> None:
        """Garante o usuário sintético do caderno global (FK de robot_pattern_memory)."""
        try:
            existing = self._request(
                "GET",
                f"/users?id=eq.{quote(GLOBAL_PATTERN_OWNER, safe='')}&select=id",
            )
            if existing:
                return
            self._request(
                "POST",
                "/users",
                json={"id": GLOBAL_PATTERN_OWNER},
                extra_headers={"Prefer": "resolution=ignore-duplicates,return=minimal"},
            )
            logger.info("[PATTERN_MEMORY_GLOBAL_OWNER_READY] user_id=%s", GLOBAL_PATTERN_OWNER)
        except Exception:
            logger.warning("[PATTERN_MEMORY_GLOBAL_OWNER_ENSURE_FAILED]", exc_info=True)

    def _global_table_ok(self) -> bool:
        if self._global_table_available is not None:
            return self._global_table_available
        try:
            self._request(
                "GET",
                "/robot_pattern_memory_global?select=pattern_key&limit=1",
            )
            self._global_table_available = True
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            self._global_table_available = False
            if status == 404:
                logger.info(
                    "[PATTERN_MEMORY_GLOBAL_TABLE_MISSING] fallback=robot_pattern_memory:%s",
                    GLOBAL_PATTERN_OWNER,
                )
            else:
                logger.warning(
                    "[PATTERN_MEMORY_GLOBAL_TABLE_PROBE_FAILED] fallback=personal_owner",
                    exc_info=True,
                )
        return bool(self._global_table_available)

    def load_user_patterns(self, user_id: str) -> list[dict[str, Any]]:
        path = (
            f"/robot_pattern_memory?user_id=eq.{quote(user_id, safe='')}"
            "&select=user_id,pattern_key,active,direction,setup,timeframe,hour_utc,wins,losses,profit"
        )
        return self._request("GET", path)

    def load_all_user_patterns(self) -> list[dict[str, Any]]:
        path = (
            "/robot_pattern_memory?select=user_id,pattern_key,active,direction,setup,"
            f"timeframe,hour_utc,wins,losses,profit&user_id=neq.{quote(GLOBAL_PATTERN_OWNER, safe='')}"
            "&limit=10000"
        )
        return self._request("GET", path)

    def load_global_patterns(self) -> list[dict[str, Any]]:
        if self._global_table_ok():
            path = (
                "/robot_pattern_memory_global?select=pattern_key,active,direction,setup,"
                "timeframe,hour_utc,wins,losses,profit&limit=10000"
            )
            return self._request("GET", path)
        return self.load_user_patterns(GLOBAL_PATTERN_OWNER)

    def upsert_pattern(self, **payload: Any) -> None:
        body = {
            "user_id": payload["user_id"],
            "pattern_key": payload["pattern_key"],
            "active": payload["active"],
            "direction": payload["direction"],
            "setup": payload["setup"],
            "timeframe": payload["timeframe"],
            "hour_utc": int(payload["hour_utc"]),
            "wins": int(payload["wins"]),
            "losses": int(payload["losses"]),
            "profit": float(payload["profit"]),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._request(
            "POST",
            "/robot_pattern_memory?on_conflict=user_id,pattern_key",
            json=body,
            extra_headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
        )

    def upsert_global_pattern(self, **payload: Any) -> None:
        if self._global_table_ok():
            body = {
                "pattern_key": payload["pattern_key"],
                "active": payload["active"],
                "direction": payload["direction"],
                "setup": payload["setup"],
                "timeframe": payload["timeframe"],
                "hour_utc": int(payload["hour_utc"]),
                "wins": int(payload["wins"]),
                "losses": int(payload["losses"]),
                "profit": float(payload["profit"]),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            self._request(
                "POST",
                "/robot_pattern_memory_global?on_conflict=pattern_key",
                json=body,
                extra_headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
            )
            return
        self.upsert_pattern(user_id=GLOBAL_PATTERN_OWNER, **payload)

    def replace_global_patterns(self, patterns: dict[str, PatternStats]) -> None:
        if self._global_table_ok():
            self._request("DELETE", "/robot_pattern_memory_global?wins=gte.0")
            if not patterns:
                return
            now = datetime.now(timezone.utc).isoformat()
            rows = []
            for key, stats in patterns.items():
                meta = _meta_from_pattern_key(key)
                rows.append(
                    {
                        "pattern_key": key,
                        "active": meta["active"],
                        "direction": meta["direction"],
                        "setup": meta["setup"],
                        "timeframe": meta["timeframe"],
                        "hour_utc": int(meta["hour_utc"]),
                        "wins": stats.wins,
                        "losses": stats.losses,
                        "profit": stats.profit,
                        "updated_at": now,
                    }
                )
            self._request(
                "POST",
                "/robot_pattern_memory_global",
                json=rows,
                extra_headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
            )
            return
        self.replace_user_patterns(GLOBAL_PATTERN_OWNER, patterns)

    def replace_user_patterns(self, user_id: str, patterns: dict[str, PatternStats]) -> None:
        self._request(
            "DELETE",
            f"/robot_pattern_memory?user_id=eq.{quote(user_id, safe='')}",
        )
        if not patterns:
            return
        rows = []
        now = datetime.now(timezone.utc).isoformat()
        for key, stats in patterns.items():
            meta = _meta_from_pattern_key(key)
            rows.append(
                {
                    "user_id": user_id,
                    "pattern_key": key,
                    "active": meta["active"],
                    "direction": meta["direction"],
                    "setup": meta["setup"],
                    "timeframe": meta["timeframe"],
                    "hour_utc": int(meta["hour_utc"]),
                    "wins": stats.wins,
                    "losses": stats.losses,
                    "profit": stats.profit,
                    "updated_at": now,
                }
            )
        self._request(
            "POST",
            "/robot_pattern_memory",
            json=rows,
            extra_headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
        )

    def _request(
        self,
        method: str,
        path: str,
        json: Any = None,
        extra_headers: dict[str, str] | None = None,
    ) -> Any:
        headers = dict(self.headers)
        if extra_headers:
            headers.update(extra_headers)
        with httpx.Client(timeout=20.0) as client:
            response = client.request(
                method,
                f"{self.rest_url}{path}",
                headers=headers,
                json=json,
            )
        response.raise_for_status()
        return response.json() if response.content else []


def create_pattern_store() -> Any | None:
    """
    Cria o store persistente conforme env.

    Returns:
        Store Supabase, SQLite ou None (só memória RAM).
    """
    supabase_url = os.getenv("SUPABASE_URL", "").strip()
    service_role_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if supabase_url and service_role_key:
        return SupabasePatternStore(supabase_url, service_role_key)
    db_path = os.getenv("PATTERN_MEMORY_DB_PATH", "").strip()
    if db_path:
        return SQLitePatternStore(db_path)
    return None


def create_pattern_memory_service() -> PatternMemoryService:
    """
    Factory padrão usada pelo gateway.

    Em modo global, unifica imediatamente os cadernos pessoais existentes
    para não perder histórico ao ativar o caderno único.
    """
    service = PatternMemoryService()
    store = create_pattern_store()
    if store is not None:
        service.attach_store(store)
    if service.is_enabled() and service.is_global():
        try:
            service.unify_all_into_global()
        except Exception:
            logger.warning("[PATTERN_MEMORY_GLOBAL_STARTUP_UNIFY_FAILED]", exc_info=True)
    return service
