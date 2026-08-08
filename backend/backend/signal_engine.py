import logging
import math
from datetime import datetime, timezone
from typing import Any, Literal


SignalValue = Literal["CALL", "PUT", "WAIT"]
TrendValue = Literal["UP", "DOWN", "SIDEWAYS"]
StrategyMode = Literal["aggressive", "balanced", "conservative"]
OperationTimeframe = Literal["M1", "M5", "M15"]

logger = logging.getLogger("backend-gateway")

STRATEGY_PRICE_ACTION = "Price Action"
STRATEGY_CANDLE_PSYCHOLOGY = "Psicologia de velas"
STRATEGY_CANDLE_PATTERNS = "Padrões de vela"
ACTIVE_ENTRY_STRATEGIES = (
    STRATEGY_PRICE_ACTION,
    STRATEGY_CANDLE_PSYCHOLOGY,
    STRATEGY_CANDLE_PATTERNS,
)

# Duração da vela/ordem (minutos). A IA analisa continuamente a cada vela;
# este valor NÃO é mais um cooldown multiplicado (legado 5/15/45).
CYCLE_MINUTES_BY_TIMEFRAME: dict[str, int] = {
    "M1": 1,
    "M5": 5,
    "M15": 15,
}
TIMEFRAME_SECONDS_BY_NAME: dict[str, int] = {
    "M1": 60,
    "M5": 300,
    "M15": 900,
    "M30": 1800,
}
# Janela em que a análise técnica varre o mercado dentro de cada vela.
ANALYSIS_WINDOW_BOUNDS: tuple[int, int] = (5, 20)
# Backoff curto quando candles/payout/sessão falham (não espera ciclo legado).
OPERATIONAL_RETRY_SECONDS = 30
MIN_OPERATIONS_PER_HOUR_BY_TIMEFRAME: dict[str, int] = {
    "M1": 12,
    "M5": 6,
    "M15": 2,
}
DIRECTION_SCORE_EDGE_MIN = 8

# Política de suporte/resistência (2026-07-29): com o preço colado em suporte
# ou resistência o robô não entra, em nenhuma direção. Backtest walk-forward
# com candles reais (8 ativos OTC, 1000 velas M1 cada) mostrou que as entradas
# nessa região aprovadas pelo portão de confiança do usuário deram 0% de
# acerto, e removê-las elevou o resultado do período de +3,24 para +5,24
# (stake unitário). Ver `docs/ESTRATEGIA.md` §1.
# Desligar (`False`) volta ao comportamento clássico do backup, em que a
# região de nível era permitida com rejeição confirmada (`LEVEL_REJECTION`).
SR_ZONE_HARD_BLOCK = True

# Bloqueios anti-loss (2026-08-04): padrões tóxicos medidos no histórico real
# (WR bem abaixo do empate ~53% com payout 85–88%). Ver `docs/ESTRATEGIA.md`.
LAST_3_ALIGNMENT_HARD_BLOCK = True
CONTINUATION_DEAD_RSI_HARD_BLOCK = True
WEAK_CONTINUATION_PUT_HARD_BLOCK = True
TREND_CLEAR_HARD_BLOCK = True
# RSI "meio morto" em CONTINUATION (amostra 7d: WR 28,6%).
CONTINUATION_DEAD_RSI_MIN = 50.0
CONTINUATION_DEAD_RSI_MAX = 60.0
# CONTINUATION+PUT com WR agregado < 43% no caderno global / histórico.
WEAK_CONTINUATION_PUT_ASSETS = frozenset(
    {
        "EURUSD-OTC",
        "EURUSD",
        "AUDUSD-OTC",
        "AUDUSD",
        "USDCAD-OTC",
        "USDCAD",
        "USDCHF-OTC",
        "USDCHF",
    }
)


def is_weak_continuation_put_asset(symbol: str | None) -> bool:
    """
    Indica se o ativo está na lista tóxica de CONTINUATION+PUT.

    Args:
        symbol: Código do ativo (ex.: ``EURUSD-OTC``).

    Returns:
        True quando CONTINUATION PUT deve ser bloqueado nesse par.
    """
    normalized = str(symbol or "").strip().upper()
    return normalized in WEAK_CONTINUATION_PUT_ASSETS


def cycle_minutes_for_timeframe(timeframe: str | None) -> int:
    """Retorna a duração da vela/ordem do timeframe (1 / 5 / 15 minutos)."""
    normalized = str(timeframe or "M1").strip().upper()
    return CYCLE_MINUTES_BY_TIMEFRAME.get(normalized, 1)


def timeframe_seconds(timeframe: str | None) -> int:
    """Retorna a duração da vela em segundos para o timeframe informado."""
    normalized = str(timeframe or "M1").strip().upper()
    return TIMEFRAME_SECONDS_BY_NAME.get(normalized, 60)


def seconds_until_next_analysis(
    timeframe: str | None,
    server_timestamp: float | None = None,
    *,
    force_next_candle: bool = False,
    operational_backoff: bool = False,
) -> int:
    """
    Calcula quantos segundos faltam para a próxima varredura contínua.

    A IA monitora o mercado a cada vela do timeframe da operação (M1/M5/M15),
    na janela de análise (segundos 5–20). Após um scan sem oportunidade,
    agenda a próxima vela (`force_next_candle=True`) para não martelar a API.
    Falhas operacionais usam backoff curto (`OPERATIONAL_RETRY_SECONDS`).

    Args:
        timeframe: Timeframe da operação (M1, M5 ou M15).
        server_timestamp: Relógio de referência (Unix). Se omitido, usa UTC local.
        force_next_candle: Se True, ignora a janela atual e vai para a próxima vela.
        operational_backoff: Se True, retorna o backoff curto de falha operacional.

    Returns:
        Segundos (>= 1) até a próxima análise permitida.
    """
    if operational_backoff:
        return int(OPERATIONAL_RETRY_SECONDS)

    expiration = timeframe_seconds(timeframe)
    start, end = ANALYSIS_WINDOW_BOUNDS
    if server_timestamp is None:
        server_timestamp = datetime.now(timezone.utc).timestamp()
    seconds_in_candle = float(server_timestamp) % expiration

    if force_next_candle or (start <= seconds_in_candle <= end):
        return max(1, int(math.ceil(expiration - seconds_in_candle + start)))
    if seconds_in_candle < start:
        return max(1, int(math.ceil(start - seconds_in_candle)))
    return max(1, int(math.ceil(expiration - seconds_in_candle + start)))


def minimum_operations_per_hour(timeframe: str | None) -> int:
    """Retorna a meta operacional horária do timeframe selecionado."""
    normalized = str(timeframe or "M1").strip().upper()
    return MIN_OPERATIONS_PER_HOUR_BY_TIMEFRAME.get(normalized, 12)


# Limiares da estratégia clássica (backup-classic) no score bruto aditivo.
# Após N ciclos sem entrada, libera filtros de "seca" (não os anti-loss tóxicos).
FREQUENCY_RECOVERY_AFTER_CYCLES = 2
FREQUENCY_RECOVERY_MIN_SCORE = 70
FREQUENCY_RECOVERY_SOFT_BLOCKS = frozenset(
    {
        "TREND_CLEAR",
        "CANDLE_STRENGTH",
        "DOJI_FILTER",
        "PRICE_ACTION_SETUP",
        "LEVEL_REJECTION",
    }
)

STRATEGY_PROFILES = {
    "aggressive": {"confidence": 70, "payout": 70, "strength": 8, "body_ratio": 0.25},
    "balanced": {"confidence": 80, "payout": 80, "strength": 12, "body_ratio": 0.40},
    # body 0.40 (era 0.55): M1 OTC raramente entrega corpo 55%+; zerava allowed=0.
    "conservative": {"confidence": 85, "payout": 85, "strength": 12, "body_ratio": 0.40},
}

# Gráficos sempre avaliados pela IA técnica, independentemente do timeframe da operação.
ANALYSIS_TIMEFRAMES: tuple[str, ...] = ("M1", "M5", "M15")
MIN_MTF_CONFLUENCE = 2
MTF_VOTE_CONFIDENCE_MIN = 78
CALIBRATED_CONFIDENCE_FLOOR = 50
CALIBRATED_CONFIDENCE_CAP = 92
CONFIDENCE_MODEL_VERSION = "backup-classic"


def analyze_signal(
    symbol: str,
    candles: list[dict[str, Any]],
    timeframe: str = "M1",
    *,
    strategy_mode: StrategyMode = "conservative",
    payout: float | None = None,
    frequency_recovery: bool = False,
) -> dict[str, Any]:
    """Analisa velas e aplica o portão de qualidade.

    Args:
        symbol: Ativo.
        candles: Velas OHLCV.
        timeframe: Timeframe da operação.
        strategy_mode: Perfil aggressive/balanced/conservative.
        payout: Payout do ativo, se conhecido.
        frequency_recovery: Se True, suaviza filtros de seca (não anti-loss).
    """
    normalized = [_normalize_candle(candle) for candle in candles]
    normalized = [candle for candle in normalized if candle is not None]
    last_price = normalized[-1]["close"] if normalized else None
    logger.info(
        "[CANDLE_ANALYSIS] symbol=%s timeframe=%s candles=%s",
        symbol,
        timeframe,
        len(normalized),
    )

    if len(normalized) < 30:
        signal = _build_signal(
            symbol=symbol,
            signal="WAIT",
            confidence=0,
            reason="Menos de 30 candles disponiveis.",
            last_price=last_price,
            trend="SIDEWAYS",
            strength=0,
            timeframe=timeframe,
        )
        signal["insufficient_candles"] = True
        signal["raw_direction_score"] = 0
        signal["confidence_model_version"] = CONFIDENCE_MODEL_VERSION
        _attach_empty_candle_analysis(signal, symbol, timeframe, normalized)
        return _apply_quality_filters(
            signal, normalized, strategy_mode, payout, frequency_recovery=frequency_recovery
        )

    closes = [candle["close"] for candle in normalized]
    ema9 = _ema(closes, 9)
    ema21 = _ema(closes, 21)
    rsi14 = _rsi(closes, 14)
    trend, trend_strength = _trend(ema9[-1], ema21[-1], closes[-1])
    last_candle = normalized[-1]
    avg_range = _average_range(normalized[-20:])
    atr_pct = (avg_range / abs(last_price)) if last_price else 0.0
    extreme_candle = _is_extreme_candle(last_candle, avg_range)

    call_score, call_reasons = _score_direction("CALL", normalized, ema9[-1], ema21[-1], rsi14)
    put_score, put_reasons = _score_direction("PUT", normalized, ema9[-1], ema21[-1], rsi14)

    if call_score >= put_score:
        selected_signal: SignalValue = "CALL"
        confidence = call_score
        reasons = call_reasons
    else:
        selected_signal = "PUT"
        confidence = put_score
        reasons = put_reasons

    if extreme_candle:
        reasons.append("Último candle exagerado reduz o score.")

    signal = _build_signal(
        symbol=symbol,
        signal=selected_signal,
        confidence=confidence,
        reason=" ".join(reasons),
        last_price=last_price,
        trend=trend,
        strength=trend_strength,
        timeframe=timeframe,
    )
    _attach_indicators(signal, normalized, ema9[-1], ema21[-1], rsi14, avg_range, atr_pct)
    _attach_candle_analysis(signal, symbol, timeframe, normalized, ema9[-1], ema21[-1], rsi14, avg_range, atr_pct)
    signal["extreme_candle"] = extreme_candle
    signal["raw_direction_score"] = max(0, int(round(confidence)))
    signal["confidence_model_version"] = CONFIDENCE_MODEL_VERSION
    return _apply_quality_filters(
        signal, normalized, strategy_mode, payout, frequency_recovery=frequency_recovery
    )

def _build_signal(
    *,
    symbol: str,
    signal: SignalValue,
    confidence: int,
    reason: str,
    last_price: float | None,
    trend: TrendValue,
    strength: int,
    timeframe: str,
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "signal": signal,
        "confidence": max(0, min(100, int(round(confidence)))),
        "score": max(0, min(100, int(round(confidence)))),
        "reason": reason,
        "entry_reason": reason,
        "signal_explanation": reason,
        "narrator_text": reason,
        "timeframe": timeframe,
        "last_price": last_price,
        "trend": trend,
        "strength": max(0, min(100, int(round(strength)))),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _calibrate_confidence(raw_score: int | float) -> int:
    """
    Converte o score técnico aditivo para uma escala limitada de confiança.

    O score bruto soma evidências correlacionadas e, portanto, não representa
    probabilidade de acerto. A compressão impede que confluências técnicas
    saturem artificialmente em 100%.
    """
    normalized = max(0.0, min(120.0, float(raw_score))) / 120.0
    calibrated = CALIBRATED_CONFIDENCE_FLOOR + (
        CALIBRATED_CONFIDENCE_CAP - CALIBRATED_CONFIDENCE_FLOOR
    ) * normalized
    return max(0, min(CALIBRATED_CONFIDENCE_CAP, int(round(calibrated))))


def _normalize_candle(candle: dict[str, Any]) -> dict[str, float] | None:
    try:
        low = candle["min"] if "min" in candle else candle["low"]
        high = candle["max"] if "max" in candle else candle["high"]
        return {
            "open": float(candle["open"]),
            "close": float(candle["close"]),
            "min": float(low),
            "max": float(high),
            "volume": float(candle.get("volume") or 0),
        }
    except (KeyError, TypeError, ValueError):
        return None


def _ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    multiplier = 2 / (period + 1)
    result = [values[0]]
    for value in values[1:]:
        result.append((value - result[-1]) * multiplier + result[-1])
    return result


def _rsi(values: list[float], period: int) -> float:
    if len(values) <= period:
        return 50.0

    gains = []
    losses = []
    for index in range(1, period + 1):
        change = values[index] - values[index - 1]
        gains.append(max(change, 0))
        losses.append(abs(min(change, 0)))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    for index in range(period + 1, len(values)):
        change = values[index] - values[index - 1]
        gain = max(change, 0)
        loss = abs(min(change, 0))
        avg_gain = ((avg_gain * (period - 1)) + gain) / period
        avg_loss = ((avg_loss * (period - 1)) + loss) / period

    if avg_loss == 0:
        return 100.0
    relative_strength = avg_gain / avg_loss
    return 100 - (100 / (1 + relative_strength))


def _has_recent_pullback(direction: str, candles: list[dict[str, float]]) -> bool:
    """Confirma uma correção curta antes do candle que retoma a tendência."""
    if len(candles) < 4:
        return False
    previous = candles[-4:-1]
    if direction == "CALL":
        return any(candle["close"] < candle["open"] for candle in previous)
    if direction == "PUT":
        return any(candle["close"] > candle["open"] for candle in previous)
    return False


def _trend(ema9: float, ema21: float, last_price: float) -> tuple[TrendValue, int]:
    if last_price == 0:
        return "SIDEWAYS", 0
    distance_pct = abs(ema9 - ema21) / abs(last_price)
    strength = min(100, distance_pct * 100000)
    if strength < 8:
        return "SIDEWAYS", int(round(strength))
    return ("UP" if ema9 > ema21 else "DOWN"), int(round(strength))


def _average_range(candles: list[dict[str, float]]) -> float:
    ranges = [max(candle["max"] - candle["min"], 0) for candle in candles]
    return sum(ranges) / len(ranges) if ranges else 0


def _is_extreme_candle(candle: dict[str, float], avg_range: float) -> bool:
    candle_range = max(candle["max"] - candle["min"], 0)
    if avg_range <= 0:
        return False
    return candle_range > avg_range * 2.5


def _score_direction(
    direction: SignalValue,
    candles: list[dict[str, float]],
    ema9: float,
    ema21: float,
    rsi: float,
) -> tuple[int, list[str]]:
    score = 0
    reasons = []

    if direction == "CALL":
        if ema9 > ema21:
            score += 30
            reasons.append("EMA9 acima da EMA21.")
        if 55 <= rsi <= 75:
            score += 22
            reasons.append(f"RSI favoravel ({rsi:.1f}).")
    else:
        if ema9 < ema21:
            score += 30
            reasons.append("EMA9 abaixo da EMA21.")
        if 25 <= rsi <= 45:
            score += 22
            reasons.append(f"RSI favoravel ({rsi:.1f}).")

    sequence_score = _sequence_score(direction, candles[-3:])
    score += sequence_score
    if sequence_score:
        reasons.append("Sequência dos últimos 3 candles confirma direção.")

    force_score = _current_candle_force_score(direction, candles[-1])
    score += force_score
    if force_score:
        reasons.append("Último candle tem força na direção.")

    price_action_score = _price_action_setup_score(direction, candles)
    score += price_action_score
    if price_action_score >= 18:
        reasons.append("Setup de reversao confirmado pelos candles.")
    elif price_action_score:
        reasons.append("Setup de continuacao confirmado pelos candles.")

    wick_score = _wick_score(direction, candles[-1])
    score += wick_score
    if wick_score:
        reasons.append("Pavio contra a entrada esta curto.")

    last_5_score = _last_5_score(direction, candles[-5:])
    score += last_5_score
    if last_5_score:
        reasons.append("Últimos 5 candles sustentam a direção.")

    return score, reasons or ["Sem confluencias suficientes."]


def _sequence_score(direction: SignalValue, candles: list[dict[str, float]]) -> int:
    if len(candles) < 3:
        return 0
    if direction == "CALL":
        directional = sum(1 for candle in candles if candle["close"] > candle["open"])
        closes_confirm = candles[-1]["close"] >= candles[0]["close"]
    else:
        directional = sum(1 for candle in candles if candle["close"] < candle["open"])
        closes_confirm = candles[-1]["close"] <= candles[0]["close"]
    if directional == 3 and closes_confirm:
        return 20
    if directional >= 2 and closes_confirm:
        return 14
    return 0


def _attach_indicators(
    signal: dict[str, Any],
    candles: list[dict[str, float]],
    ema9: float,
    ema21: float,
    rsi: float,
    atr: float,
    atr_pct: float,
) -> None:
    last = candles[-1]
    candle_range = max(last["max"] - last["min"], 0)
    body = abs(last["close"] - last["open"])
    upper_wick = last["max"] - max(last["open"], last["close"])
    lower_wick = min(last["open"], last["close"]) - last["min"]
    body_ratio = body / candle_range if candle_range else 0.0
    upper_wick_ratio = upper_wick / candle_range if candle_range else 0.0
    lower_wick_ratio = lower_wick / candle_range if candle_range else 0.0
    direction = signal.get("signal")
    directional_last_5 = _directional_count(str(direction), candles[-5:])
    price_action_setup = _price_action_setup(str(direction), candles)
    opposite_direction = "PUT" if direction == "CALL" else "CALL" if direction == "PUT" else "WAIT"
    level_context = _support_resistance_context(candles)

    signal.update(
        {
            "ema9": round(ema9, 6),
            "ema21": round(ema21, 6),
            "rsi": round(rsi, 2),
            "atr": round(atr, 6),
            "atr_pct": round(atr_pct, 8),
            "body_ratio": round(body_ratio, 4),
            "candle_body": round(body, 6),
            "upper_wick": round(upper_wick, 6),
            "lower_wick": round(lower_wick, 6),
            "upper_wick_ratio": round(upper_wick_ratio, 4),
            "lower_wick_ratio": round(lower_wick_ratio, 4),
            "directional_candles_5": directional_last_5,
            "pullback_confirmed": _has_recent_pullback(str(direction), candles),
            "alternating_last_3": _alternating_last_3(candles[-3:]),
            "current_candle_direction": _candle_direction(last),
            "price_action_setup": price_action_setup,
            "near_support_resistance": _near_support_resistance(str(direction), candles),
            "near_support": bool(level_context.get("near_support")),
            "near_resistance": bool(level_context.get("near_resistance")),
            "support_level": (
                round(float(level_context["support"]), 6)
                if level_context.get("support") is not None
                else None
            ),
            "resistance_level": (
                round(float(level_context["resistance"]), 6)
                if level_context.get("resistance") is not None
                else None
            ),
            "level_conflict": _has_level_conflict(str(direction), level_context),
            "level_rejection_confirmed": _level_rejection_confirmed(str(direction), candles, level_context),
            "zigzag_reversal": _is_reversal_setup(str(direction), candles),
            "reversal_against": _price_action_setup(opposite_direction, candles) == "REVERSAL",
        }
    )


def _apply_quality_filters(
    signal: dict[str, Any],
    candles: list[dict[str, float]],
    strategy_mode: StrategyMode,
    payout: float | None,
    *,
    frequency_recovery: bool = False,
) -> dict[str, Any]:
    mode = strategy_mode if strategy_mode in STRATEGY_PROFILES else "conservative"
    profile = STRATEGY_PROFILES[mode]
    blocked: list[str] = []
    approved: list[str] = []
    direction = str(signal.get("signal") or "WAIT")

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
        "PRICE_ACTION_SETUP": 12,
        "REVERSAL_AGAINST": 15,
        "SUPPORT_RESISTANCE": 8,
        "LEVEL_CONFLICT": 18,
        "LEVEL_REJECTION": 12,
        "SR_ZONE": 20,
        "LAST_3_ALIGNMENT": 20,
        "CONTINUATION_DEAD_RSI": 18,
        "WEAK_CONTINUATION_PUT": 20,
    }

    def check(name: str, passed: bool) -> None:
        if passed:
            approved.append(name)
        else:
            blocked.append(name)

    if direction not in {"CALL", "PUT"}:
        blocked.append("CANDLES_UNAVAILABLE")
    if signal.get("insufficient_candles"):
        blocked.append("CANDLES_UNAVAILABLE")
    check("MIN_CONFIDENCE", int(signal.get("confidence") or 0) >= profile["confidence"])
    if payout is None:
        blocked.append("PAYOUT_UNAVAILABLE")
    else:
        check("MIN_PAYOUT", float(payout) >= profile["payout"])
    ema9 = float(signal.get("ema9") or 0)
    ema21 = float(signal.get("ema21") or 0)
    rsi = float(signal.get("rsi") or 50)
    price_action_setup = str(signal.get("price_action_setup") or "WEAK")
    has_reversal_setup = price_action_setup == "REVERSAL"
    is_continuation = price_action_setup == "CONTINUATION"
    level_context = _support_resistance_context(candles)
    level_conflict = _has_level_conflict(direction, level_context)
    needs_level_rejection = (
        price_action_setup in {"REVERSAL", "SUPPORT_RESISTANCE"}
        or bool(level_context.get("near_support"))
        or bool(level_context.get("near_resistance"))
    )
    level_rejection_confirmed = _level_rejection_confirmed(direction, candles, level_context)
    in_support_resistance_zone = bool(level_context.get("near_support")) or bool(
        level_context.get("near_resistance")
    )
    last_3_direction = str(signal.get("last_3_direction") or "").upper()
    if not last_3_direction and len(candles) >= 3:
        last_3_direction = _direction_label(candles[-3:])
    wanted_last_3 = "UP" if direction == "CALL" else "DOWN" if direction == "PUT" else ""
    last_3_aligned = (not is_continuation) or (last_3_direction == wanted_last_3)
    dead_rsi = is_continuation and CONTINUATION_DEAD_RSI_MIN <= rsi < CONTINUATION_DEAD_RSI_MAX
    weak_continuation_put = (
        is_continuation
        and direction == "PUT"
        and is_weak_continuation_put_asset(str(signal.get("symbol") or ""))
    )
    check(
        "TREND_CLEAR",
        has_reversal_setup or signal.get("trend") != "SIDEWAYS",
    )
    check(
        "TREND_STRENGTH",
        has_reversal_setup
        or signal.get("trend") == "SIDEWAYS"
        or int(signal.get("strength") or 0) >= profile["strength"],
    )
    check("SIDEWAYS_FILTER", has_reversal_setup or signal.get("trend") != "SIDEWAYS")
    if direction == "CALL":
        check("EMA_TREND", ema9 > ema21 or has_reversal_setup)
        check("RSI_RANGE", 55 <= rsi <= 75)
        check("WICK_REJECTION", float(signal.get("upper_wick_ratio") or 1) <= 0.45)
    elif direction == "PUT":
        check("EMA_TREND", ema9 < ema21 or has_reversal_setup)
        check("RSI_RANGE", 25 <= rsi <= 45)
        check("WICK_REJECTION", float(signal.get("lower_wick_ratio") or 1) <= 0.45)

    body_ratio = float(signal.get("body_ratio") or 0)
    check("CANDLE_STRENGTH", body_ratio >= profile["body_ratio"])
    check("DOJI_FILTER", body_ratio >= profile["body_ratio"])
    check("VOLATILITY", float(signal.get("atr_pct") or 0) >= 0.0001)
    check("LAST_5_CONFIRMATION", int(signal.get("directional_candles_5") or 0) >= 3 or has_reversal_setup)
    check("NO_ALTERNATING_LAST_3", not bool(signal.get("alternating_last_3")))
    check("PRICE_ACTION_SETUP", price_action_setup in {"CONTINUATION", "REVERSAL", "SUPPORT_RESISTANCE"})
    check("SUPPORT_RESISTANCE", bool(signal.get("near_support_resistance")) or price_action_setup == "CONTINUATION")
    check("LEVEL_CONFLICT", not level_conflict)
    check("LEVEL_REJECTION", not needs_level_rejection or level_rejection_confirmed)
    check("SR_ZONE", not (SR_ZONE_HARD_BLOCK and in_support_resistance_zone))
    check("REVERSAL_AGAINST", not bool(signal.get("reversal_against")))
    check(
        "LAST_3_ALIGNMENT",
        (not LAST_3_ALIGNMENT_HARD_BLOCK) or last_3_aligned,
    )
    check(
        "CONTINUATION_DEAD_RSI",
        (not CONTINUATION_DEAD_RSI_HARD_BLOCK) or (not dead_rsi),
    )
    check(
        "WEAK_CONTINUATION_PUT",
        (not WEAK_CONTINUATION_PUT_HARD_BLOCK) or (not weak_continuation_put),
    )

    confidence = int(signal.get("confidence") or 0)
    score_blocked = list(blocked)
    if frequency_recovery:
        # Em recovery os soft-blocks não derrubam o score abaixo do portão —
        # senão allowed=True no scan e NO_OPPORTUNITY no ciclo (score < 70).
        score_blocked = [name for name in blocked if name not in FREQUENCY_RECOVERY_SOFT_BLOCKS]
    strategy_score = max(0, confidence - sum(penalties.get(name, 0) for name in score_blocked))
    hard_block_names = {
        "ACCOUNT_DISCONNECTED",
        "STOP_WIN_HIT",
        "STOP_LOSS_HIT",
        "ACTIVE_CLOSED",
        "ACTIVE_SUSPENDED",
        "PAYOUT_UNAVAILABLE",
        "OPERATION_IN_PROGRESS",
        "CANDLES_UNAVAILABLE",
        # CANDLE_STRENGTH / DOJI: soft permanente (corpo alto era raro demais no M1 OTC).
        "PRICE_ACTION_SETUP",
        "REVERSAL_AGAINST",
        "LEVEL_CONFLICT",
        "LEVEL_REJECTION",
        "SR_ZONE",
    }
    if TREND_CLEAR_HARD_BLOCK:
        hard_block_names.add("TREND_CLEAR")
    if LAST_3_ALIGNMENT_HARD_BLOCK:
        hard_block_names.add("LAST_3_ALIGNMENT")
    if WEAK_CONTINUATION_PUT_HARD_BLOCK:
        hard_block_names.add("WEAK_CONTINUATION_PUT")
    if frequency_recovery:
        hard_block_names -= FREQUENCY_RECOVERY_SOFT_BLOCKS
        signal["frequency_recovery"] = True
    hard_blocks = [name for name in blocked if name in hard_block_names]
    quality_score = strategy_score
    trade_allowed = not hard_blocks
    signal.update(
        {
            "strategy_mode": mode,
            "payout": payout,
            "direction": direction,
            "strategy_score": strategy_score,
            "score": strategy_score,
            "quality_score": quality_score,
            "block_reasons": list(blocked),
            "blocked_filters": blocked,
            "approved_filters": approved,
            "trade_allowed": trade_allowed,
            "quality_reason": "OK" if trade_allowed else ",".join(hard_blocks),
            "in_support_resistance_zone": in_support_resistance_zone,
            "frequency_recovery": bool(frequency_recovery),
        }
    )
    if trade_allowed:
        penalties_text = ", ".join(blocked)
        signal["signal_explanation"] = signal.get("reason") or "Sinal aprovado."
        if penalties_text:
            signal["signal_explanation"] += f" Penalizacoes no score: {penalties_text}."
    else:
        filters = ", ".join(hard_blocks) if hard_blocks else "sem direção válida"
        signal["signal_explanation"] = f"Sinal sem entrada: {filters}."
    signal["entry_reason"] = signal["signal_explanation"]
    signal["narrator_text"] = signal["signal_explanation"]
    logger.info(
        "[CANDLE_SCORE_UPDATED] symbol=%s direction=%s score=%s confidence=%s block_reasons=%s",
        signal.get("symbol"),
        signal.get("direction"),
        signal.get("score"),
        signal.get("confidence"),
        signal.get("block_reasons"),
    )
    return signal

def _match_entry_strategies(
    direction: str,
    candles: list[dict[str, float]],
    signal: dict[str, Any],
) -> list[str]:
    """Retorna quais das 3 estratégias de entrada confirmaram a direção."""
    return list(_match_entry_strategy_setups(direction, candles, signal))


def _match_entry_strategy_setups(
    direction: str,
    candles: list[dict[str, float]],
    signal: dict[str, Any],
) -> dict[str, str]:
    """Relaciona cada estratégia aprovada ao setup de continuação ou reversão."""
    matched: dict[str, str] = {}
    if direction not in {"CALL", "PUT"} or len(candles) < 2:
        return matched

    price_action_setup = str(signal.get("price_action_setup") or _price_action_setup(direction, candles))
    if price_action_setup == "CONTINUATION":
        matched[STRATEGY_PRICE_ACTION] = "CONTINUATION"
    elif price_action_setup in {"REVERSAL", "SUPPORT_RESISTANCE"}:
        matched[STRATEGY_PRICE_ACTION] = "REVERSAL"

    psychology_setup = _candle_psychology_setup(direction, candles)
    if psychology_setup is not None:
        matched[STRATEGY_CANDLE_PSYCHOLOGY] = psychology_setup

    if _candle_pattern_match(direction, candles):
        matched[STRATEGY_CANDLE_PATTERNS] = "REVERSAL"

    return matched


def _candle_psychology_match(direction: str, candles: list[dict[str, float]]) -> bool:
    """Psicologia de velas: convicção (corpo forte) ou rejeição (pavio dominante)."""
    return _candle_psychology_setup(direction, candles) is not None


def _candle_psychology_setup(
    direction: str,
    candles: list[dict[str, float]],
) -> str | None:
    """Classifica a psicologia da vela sem misturar continuação e reversão."""
    last = candles[-1]
    body_ratio, upper_wick_ratio, lower_wick_ratio = _candle_shape(last)
    if direction == "CALL":
        decisive = last["close"] > last["open"] and body_ratio >= 0.58 and upper_wick_ratio <= 0.28
        rejection = (
            last["close"] >= last["open"]
            and lower_wick_ratio >= 0.42
            and body_ratio >= 0.18
            and upper_wick_ratio <= 0.3
        )
        if decisive:
            return "CONTINUATION"
        if rejection:
            return "REVERSAL"
        return None
    decisive = last["close"] < last["open"] and body_ratio >= 0.58 and lower_wick_ratio <= 0.28
    rejection = (
        last["close"] <= last["open"]
        and upper_wick_ratio >= 0.42
        and body_ratio >= 0.18
        and lower_wick_ratio <= 0.3
    )
    if decisive:
        return "CONTINUATION"
    if rejection:
        return "REVERSAL"
    return None


def _candle_pattern_match(direction: str, candles: list[dict[str, float]]) -> bool:
    """Padrões clássicos de vela alinhados à direção."""
    return (
        _is_engulfing(direction, candles)
        or _is_hammer_or_pin(direction, candles)
        or _is_shooting_star(direction, candles)
    )


def _is_engulfing(direction: str, candles: list[dict[str, float]]) -> bool:
    if len(candles) < 2:
        return False
    previous, last = candles[-2], candles[-1]
    prev_body = abs(previous["close"] - previous["open"])
    last_body = abs(last["close"] - last["open"])
    if last_body < max(prev_body * 1.05, 1e-9):
        return False
    if direction == "CALL":
        return (
            previous["close"] < previous["open"]
            and last["close"] > last["open"]
            and last["open"] <= previous["close"]
            and last["close"] >= previous["open"]
        )
    return (
        previous["close"] > previous["open"]
        and last["close"] < last["open"]
        and last["open"] >= previous["close"]
        and last["close"] <= previous["open"]
    )


def _is_hammer_or_pin(direction: str, candles: list[dict[str, float]]) -> bool:
    if not candles or direction != "CALL":
        return False
    last = candles[-1]
    body_ratio, upper_wick_ratio, lower_wick_ratio = _candle_shape(last)
    return (
        lower_wick_ratio >= 0.5
        and body_ratio <= 0.35
        and upper_wick_ratio <= 0.25
        and last["close"] >= last["open"]
    )


def _is_shooting_star(direction: str, candles: list[dict[str, float]]) -> bool:
    if not candles or direction != "PUT":
        return False
    last = candles[-1]
    body_ratio, upper_wick_ratio, lower_wick_ratio = _candle_shape(last)
    return (
        upper_wick_ratio >= 0.5
        and body_ratio <= 0.35
        and lower_wick_ratio <= 0.25
        and last["close"] <= last["open"]
    )


def _directional_count(direction: str, candles: list[dict[str, float]]) -> int:
    if direction == "CALL":
        return sum(1 for candle in candles if candle["close"] > candle["open"])
    if direction == "PUT":
        return sum(1 for candle in candles if candle["close"] < candle["open"])
    return 0


def _candle_direction(candle: dict[str, float]) -> str:
    if candle["close"] > candle["open"]:
        return "GREEN"
    if candle["close"] < candle["open"]:
        return "RED"
    return "DOJI"


def _candle_shape(candle: dict[str, float]) -> tuple[float, float, float]:
    candle_range = max(candle["max"] - candle["min"], 0)
    if candle_range <= 0:
        return 0.0, 0.0, 0.0
    body = abs(candle["close"] - candle["open"])
    upper_wick = max(0.0, candle["max"] - max(candle["open"], candle["close"]))
    lower_wick = max(0.0, min(candle["open"], candle["close"]) - candle["min"])
    return body / candle_range, upper_wick / candle_range, lower_wick / candle_range


def _price_action_setup(direction: str, candles: list[dict[str, float]]) -> str:
    if direction not in {"CALL", "PUT"} or len(candles) < 8:
        return "WEAK"
    if _is_reversal_setup(direction, candles):
        return "REVERSAL"
    if _near_support_resistance(direction, candles) and _current_candle_force_score(direction, candles[-1]) >= 14:
        return "SUPPORT_RESISTANCE"
    if _is_continuation_setup(direction, candles):
        return "CONTINUATION"
    return "WEAK"


def _price_action_setup_score(direction: SignalValue, candles: list[dict[str, float]]) -> int:
    setup = _price_action_setup(str(direction), candles)
    if setup == "REVERSAL":
        return 18
    if setup == "SUPPORT_RESISTANCE":
        return 15
    if setup == "CONTINUATION":
        return 12
    return 0


def _is_reversal_setup(direction: str, candles: list[dict[str, float]]) -> bool:
    if len(candles) < 8:
        return False
    previous = candles[-6:-1]
    last = candles[-1]
    body_ratio, upper_wick_ratio, lower_wick_ratio = _candle_shape(last)
    previous_direction = _direction_label(previous)
    previous_red = _directional_count("PUT", previous)
    previous_green = _directional_count("CALL", previous)
    near_level = _near_support_resistance(direction, candles)
    if direction == "CALL":
        return (
            previous_direction == "DOWN"
            and previous_red >= 3
            and last["close"] > last["open"]
            and body_ratio >= 0.4
            and lower_wick_ratio >= 0.2
            and upper_wick_ratio <= 0.35
            and near_level
        )
    return (
        previous_direction == "UP"
        and previous_green >= 3
        and last["close"] < last["open"]
        and body_ratio >= 0.4
        and upper_wick_ratio >= 0.2
        and lower_wick_ratio <= 0.35
        and near_level
    )


def _is_continuation_setup(direction: str, candles: list[dict[str, float]]) -> bool:
    if len(candles) < 5:
        return False
    last = candles[-1]
    body_ratio, upper_wick_ratio, lower_wick_ratio = _candle_shape(last)
    last_5 = candles[-5:]
    direction_label = _direction_label(last_5)
    directional = _directional_count(direction, last_5)
    if direction == "CALL":
        return (
            direction_label == "UP"
            and directional >= 3
            and last["close"] > last["open"]
            and body_ratio >= 0.55
            and upper_wick_ratio <= 0.3
        )
    return (
        direction_label == "DOWN"
        and directional >= 3
        and last["close"] < last["open"]
        and body_ratio >= 0.55
        and lower_wick_ratio <= 0.3
    )


def _near_support_resistance(direction: str, candles: list[dict[str, float]]) -> bool:
    context = _support_resistance_context(candles)
    if direction == "CALL":
        return bool(context.get("near_support"))
    if direction == "PUT":
        return bool(context.get("near_resistance"))
    return False


def _support_resistance_context(candles: list[dict[str, float]]) -> dict[str, Any]:
    if len(candles) < 12:
        return {
            "support": None,
            "resistance": None,
            "tolerance": 0.0,
            "near_support": False,
            "near_resistance": False,
        }
    recent = candles[-24:]
    previous = recent[:-1]
    last = recent[-1]
    recent_range = max(candle["max"] for candle in recent) - min(candle["min"] for candle in recent)
    avg_range = _average_range(recent)
    tolerance = max(recent_range * 0.12, avg_range * 0.65)
    if tolerance <= 0:
        return {
            "support": None,
            "resistance": None,
            "tolerance": 0.0,
            "near_support": False,
            "near_resistance": False,
        }
    support = min(candle["min"] for candle in previous)
    resistance = max(candle["max"] for candle in previous)
    support_distance = min(abs(last["min"] - support), abs(last["close"] - support))
    resistance_distance = min(abs(last["max"] - resistance), abs(last["close"] - resistance))
    return {
        "support": support,
        "resistance": resistance,
        "tolerance": tolerance,
        "near_support": support_distance <= tolerance,
        "near_resistance": resistance_distance <= tolerance,
        "support_distance": support_distance,
        "resistance_distance": resistance_distance,
    }


def _has_level_conflict(direction: str, context: dict[str, Any]) -> bool:
    if direction == "CALL":
        return bool(context.get("near_resistance")) and not bool(context.get("near_support"))
    if direction == "PUT":
        return bool(context.get("near_support")) and not bool(context.get("near_resistance"))
    return False


def _level_rejection_confirmed(
    direction: str,
    candles: list[dict[str, float]],
    context: dict[str, Any] | None = None,
) -> bool:
    if direction not in {"CALL", "PUT"} or not candles:
        return False
    context = context or _support_resistance_context(candles)
    last = candles[-1]
    body_ratio, upper_wick_ratio, lower_wick_ratio = _candle_shape(last)
    if direction == "CALL":
        return (
            bool(context.get("near_support"))
            and last["close"] > last["open"]
            and body_ratio >= 0.35
            and lower_wick_ratio >= 0.2
            and upper_wick_ratio <= 0.4
        )
    return (
        bool(context.get("near_resistance"))
        and last["close"] < last["open"]
        and body_ratio >= 0.35
        and upper_wick_ratio >= 0.2
        and lower_wick_ratio <= 0.4
    )


def _alternating_last_3(candles: list[dict[str, float]]) -> bool:
    if len(candles) < 3:
        return False
    colors = []
    for candle in candles:
        if candle["close"] > candle["open"]:
            colors.append("GREEN")
        elif candle["close"] < candle["open"]:
            colors.append("RED")
        else:
            colors.append("DOJI")
    return colors[0] != "DOJI" and colors[1] != "DOJI" and colors[2] != "DOJI" and colors[0] != colors[1] and colors[1] != colors[2]


def _current_candle_force_score(direction: SignalValue, candle: dict[str, float]) -> int:
    candle_range = max(candle["max"] - candle["min"], 0)
    if candle_range <= 0:
        return 0
    body = abs(candle["close"] - candle["open"])
    body_ratio = body / candle_range
    if direction == "CALL" and candle["close"] <= candle["open"]:
        return 0
    if direction == "PUT" and candle["close"] >= candle["open"]:
        return 0
    if body_ratio >= 0.65:
        return 20
    if body_ratio >= 0.45:
        return 14
    if body_ratio >= 0.25:
        return 8
    return 0


def _wick_score(direction: SignalValue, candle: dict[str, float]) -> int:
    candle_range = max(candle["max"] - candle["min"], 0)
    if candle_range <= 0:
        return 0
    upper_wick = candle["max"] - max(candle["open"], candle["close"])
    lower_wick = min(candle["open"], candle["close"]) - candle["min"]
    against_ratio = (upper_wick if direction == "CALL" else lower_wick) / candle_range
    if against_ratio <= 0.2:
        return 10
    if against_ratio <= 0.35:
        return 6
    if against_ratio <= 0.45:
        return 3
    return 0


def _last_5_score(direction: SignalValue, candles: list[dict[str, float]]) -> int:
    if len(candles) < 5:
        return 0
    directional = _directional_count(direction, candles)
    if directional >= 4:
        return 10
    if directional == 3:
        return 6
    return 0


def _candle_colors(candles: list[dict[str, float]]) -> list[str]:
    colors = []
    for candle in candles:
        if candle["close"] > candle["open"]:
            colors.append("GREEN")
        elif candle["close"] < candle["open"]:
            colors.append("RED")
        else:
            colors.append("DOJI")
    return colors


def _direction_label(candles: list[dict[str, float]]) -> str:
    if not candles:
        return "NEUTRAL"
    first_open = candles[0]["open"]
    last_close = candles[-1]["close"]
    if last_close > first_open:
        return "UP"
    if last_close < first_open:
        return "DOWN"
    return "NEUTRAL"


def _is_sideways(candles: list[dict[str, float]], avg_range: float, last_price: float | None) -> bool:
    if len(candles) < 10 or not last_price:
        return True
    recent = candles[-10:]
    recent_range = max(candle["max"] for candle in recent) - min(candle["min"] for candle in recent)
    recent_range_pct = recent_range / abs(last_price) if last_price else 0.0
    avg_range_pct = avg_range / abs(last_price) if last_price else 0.0
    return recent_range_pct < 0.00035 or avg_range_pct < 0.00008


def _volatility_label(atr_pct: float) -> str:
    if atr_pct < 0.0001:
        return "LOW"
    if atr_pct > 0.0012:
        return "HIGH"
    return "NORMAL"


def _attach_empty_candle_analysis(
    signal: dict[str, Any],
    symbol: str,
    timeframe: str,
    candles: list[dict[str, float]],
) -> None:
    signal.update(
        {
            "used_strategies": ["Candle reading"],
            "candle_reading": "Candles insuficientes para leitura tecnica completa.",
            "entry_reason": signal.get("reason"),
            "block_reasons": ["CANDLES_UNAVAILABLE"],
            "metrics": {
                "symbol": symbol,
                "timeframe": timeframe,
                "candles_count": len(candles),
            },
        }
    )


def _attach_candle_analysis(
    signal: dict[str, Any],
    symbol: str,
    timeframe: str,
    candles: list[dict[str, float]],
    ema9: float,
    ema21: float,
    rsi: float,
    avg_range: float,
    atr_pct: float,
) -> None:
    last = candles[-1]
    candle_range = max(last["max"] - last["min"], 0)
    body = abs(last["close"] - last["open"])
    upper_wick = max(0.0, last["max"] - max(last["open"], last["close"]))
    lower_wick = max(0.0, min(last["open"], last["close"]) - last["min"])
    body_ratio = body / candle_range if candle_range else 0.0
    upper_wick_ratio = upper_wick / candle_range if candle_range else 0.0
    lower_wick_ratio = lower_wick / candle_range if candle_range else 0.0
    last_3 = candles[-3:]
    last_5 = candles[-5:]
    current_direction = "UP" if last["close"] > last["open"] else "DOWN" if last["close"] < last["open"] else "DOJI"
    direction = str(signal.get("signal") or "WAIT")
    price_action_setup = _price_action_setup(direction, candles)
    opposite_direction = "PUT" if direction == "CALL" else "CALL" if direction == "PUT" else "WAIT"
    level_context = _support_resistance_context(candles)
    metrics = {
        "symbol": symbol,
        "timeframe": timeframe,
        "candles_count": len(candles),
        "ema9": round(ema9, 6),
        "ema21": round(ema21, 6),
        "rsi14": round(rsi, 2),
        "current_candle_direction": current_direction,
        "current_candle_strength": round(body_ratio, 4),
        "candle_body": round(body, 6),
        "candle_range": round(candle_range, 6),
        "upper_wick": round(upper_wick, 6),
        "lower_wick": round(lower_wick, 6),
        "upper_wick_ratio": round(upper_wick_ratio, 4),
        "lower_wick_ratio": round(lower_wick_ratio, 4),
        "last_3_direction": _direction_label(last_3),
        "last_3_colors": _candle_colors(last_3),
        "last_5_direction": _direction_label(last_5),
        "last_5_colors": _candle_colors(last_5),
        "price_action_setup": price_action_setup,
        "near_support_resistance": _near_support_resistance(direction, candles),
        "near_support": bool(level_context.get("near_support")),
        "near_resistance": bool(level_context.get("near_resistance")),
        "support_level": (
            round(float(level_context["support"]), 6)
            if level_context.get("support") is not None
            else None
        ),
        "resistance_level": (
            round(float(level_context["resistance"]), 6)
            if level_context.get("resistance") is not None
            else None
        ),
        "support_distance": round(float(level_context.get("support_distance") or 0), 6),
        "resistance_distance": round(float(level_context.get("resistance_distance") or 0), 6),
        "level_conflict": _has_level_conflict(direction, level_context),
        "level_rejection_confirmed": _level_rejection_confirmed(direction, candles, level_context),
        "zigzag_reversal": _is_reversal_setup(direction, candles),
        "reversal_against": _price_action_setup(opposite_direction, candles) == "REVERSAL",
        "sideways": _is_sideways(candles, avg_range, signal.get("last_price")),
        "volatility": _volatility_label(atr_pct),
        "atr": round(avg_range, 6),
        "atr_pct": round(atr_pct, 8),
    }
    signal["metrics"] = metrics
    signal["price_action_setup"] = price_action_setup
    signal["last_3_direction"] = metrics["last_3_direction"]
    signal["last_5_direction"] = metrics["last_5_direction"]
    signal["level_conflict"] = metrics["level_conflict"]
    signal["reversal_against"] = metrics["reversal_against"]
    signal["sideways"] = metrics["sideways"]
    signal["volatility"] = metrics["volatility"]
    signal["used_strategies"] = list(ACTIVE_ENTRY_STRATEGIES)
    signal["candle_reading"] = (
        f"{symbol} {timeframe}: price action {price_action_setup.lower()}, "
        f"candle atual {current_direction.lower()} com corpo de {body_ratio:.0%}, "
        f"últimas 3 velas {_direction_label(last_3).lower()}."
    )
    signal["entry_reason"] = signal.get("reason")


def merge_multi_timeframe_signals(
    primary_timeframe: str,
    signals_by_tf: dict[str, dict[str, Any]],
    *,
    minimum_vote_confidence: int = MTF_VOTE_CONFIDENCE_MIN,
) -> dict[str, Any]:
    """
    Combina leituras de M1/M5/M15 e avalia a operação no timeframe configurado.

    Args:
        primary_timeframe: Timeframe da operação (M1, M5 ou M15).
        signals_by_tf: Mapa timeframe → sinal bruto de ``analyze_signal``.
        minimum_vote_confidence: Confiança mínima para um voto MTF qualificado.

    Returns:
        Sinal final com metadados de confluência multi-timeframe.

    Raises:
        ValueError: Se ``signals_by_tf`` estiver vazio.
    """
    if not signals_by_tf:
        raise ValueError("signals_by_tf nao pode ser vazio")

    normalized_primary = str(primary_timeframe or "M1").strip().upper()
    primary = dict(signals_by_tf.get(normalized_primary) or next(iter(signals_by_tf.values())))
    primary_direction = str(primary.get("signal") or "WAIT").upper()

    votes: dict[str, str] = {}
    for timeframe in ANALYSIS_TIMEFRAMES:
        signal = signals_by_tf.get(timeframe)
        if not isinstance(signal, dict):
            continue
        direction = str(signal.get("signal") or "WAIT").upper()
        if direction in {"CALL", "PUT", "WAIT"}:
            votes[timeframe] = direction

    for timeframe, signal in signals_by_tf.items():
        label = str(timeframe).strip().upper()
        if label in votes or not isinstance(signal, dict):
            continue
        direction = str(signal.get("signal") or "WAIT").upper()
        if direction in {"CALL", "PUT", "WAIT"}:
            votes[label] = direction

    qualified_votes = {
        timeframe: direction
        for timeframe, direction in votes.items()
        if direction in {"CALL", "PUT"}
        and bool((signals_by_tf.get(timeframe) or {}).get("trade_allowed"))
        and int((signals_by_tf.get(timeframe) or {}).get("confidence") or 0)
        >= minimum_vote_confidence
    }
    agreeing = [
        timeframe
        for timeframe, direction in qualified_votes.items()
        if primary_direction in {"CALL", "PUT"} and direction == primary_direction
    ]
    disagreeing = [
        timeframe
        for timeframe, direction in qualified_votes.items()
        if primary_direction in {"CALL", "PUT"}
        and direction in {"CALL", "PUT"}
        and direction != primary_direction
    ]
    confluence_count = len(agreeing)
    analyzed_tfs = [
        timeframe
        for timeframe in ANALYSIS_TIMEFRAMES
        if isinstance(signals_by_tf.get(timeframe), dict)
    ]
    mtf_data_complete = len(analyzed_tfs) >= 2

    summaries: list[str] = []
    for timeframe in ANALYSIS_TIMEFRAMES:
        signal = signals_by_tf.get(timeframe)
        if not isinstance(signal, dict):
            summaries.append(f"{timeframe}: indisponivel")
            continue
        direction = str(signal.get("signal") or "WAIT").upper()
        confidence = int(signal.get("confidence") or 0)
        summaries.append(f"{timeframe} {direction} ({confidence}%)")

    mtf_reading = (
        f"Analise multi-timeframe (operacao {normalized_primary}): "
        + "; ".join(summaries)
        + f". Confluencia {confluence_count}/{max(len(analyzed_tfs), 1)}."
    )

    result = dict(primary)
    result["timeframe"] = normalized_primary
    result["operation_timeframe"] = normalized_primary
    result["mtf_timeframes"] = list(ANALYSIS_TIMEFRAMES)
    result["mtf_votes"] = votes
    result["mtf_qualified_votes"] = qualified_votes
    result["mtf_agreeing"] = agreeing
    result["mtf_disagreeing"] = disagreeing
    result["mtf_confluence"] = confluence_count
    result["mtf_analyzed_count"] = len(analyzed_tfs)
    result["mtf_analysis"] = {
        timeframe: {
            "signal": str((signals_by_tf.get(timeframe) or {}).get("signal") or "WAIT"),
            "confidence": int((signals_by_tf.get(timeframe) or {}).get("confidence") or 0),
            "trend": (signals_by_tf.get(timeframe) or {}).get("trend"),
            "trade_allowed": bool((signals_by_tf.get(timeframe) or {}).get("trade_allowed")),
            "qualified_vote": timeframe in qualified_votes,
        }
        for timeframe in ANALYSIS_TIMEFRAMES
        if isinstance(signals_by_tf.get(timeframe), dict)
    }

    used = list(result.get("used_strategies") or [])
    if "Multi-timeframe 1m/5m/15m" not in used:
        used.append("Multi-timeframe 1m/5m/15m")
    result["used_strategies"] = used

    base_reading = str(result.get("candle_reading") or "").strip()
    result["candle_reading"] = f"{mtf_reading} {base_reading}".strip()

    blocked = [str(item) for item in (result.get("blocked_filters") or [])]
    approved = [str(item) for item in (result.get("approved_filters") or [])]

    if primary_direction not in {"CALL", "PUT"}:
        result["mtf_ready"] = False
        result["blocked_filters"] = blocked
        result["approved_filters"] = approved
        return result

    if not mtf_data_complete:
        if "MTF_DATA_UNAVAILABLE" not in blocked:
            blocked.append("MTF_DATA_UNAVAILABLE")
        result["trade_allowed"] = False
        result["mtf_ready"] = False
        result["blocked_filters"] = blocked
        result["approved_filters"] = approved
        return result

    m15_signal = signals_by_tf.get("M15") or {}
    m15_direction = str(m15_signal.get("signal") or "WAIT").upper()
    m15_trend = str(m15_signal.get("trend") or "").upper()
    opposite_direction = "PUT" if primary_direction == "CALL" else "CALL"
    opposite_trend = "DOWN" if primary_direction == "CALL" else "UP"
    raw_m15_conflict = (
        m15_direction == opposite_direction
        and (
            int(m15_signal.get("confidence") or 0) >= 60
            or m15_trend == opposite_trend
        )
    )
    strong_m15_conflict = "M15" in disagreeing or raw_m15_conflict
    if confluence_count >= MIN_MTF_CONFLUENCE and not strong_m15_conflict:
        if "MTF_CONFLUENCE" not in approved:
            approved.append("MTF_CONFLUENCE")
        blocked = [item for item in blocked if item != "MTF_CONFLUENCE"]
        result["mtf_ready"] = True
        confidence_weights = {"M1": 0.6, "M5": 0.25, "M15": 0.15}
        agreeing_weight = sum(confidence_weights.get(timeframe, 0.0) for timeframe in agreeing)
        if agreeing_weight > 0:
            result["confidence"] = min(
                CALIBRATED_CONFIDENCE_CAP,
                round(
                    sum(
                        int((signals_by_tf.get(timeframe) or {}).get("confidence") or 0)
                        * confidence_weights.get(timeframe, 0.0)
                        for timeframe in agreeing
                    )
                    / agreeing_weight
                ),
            )
            result["score"] = result["confidence"]
        if confluence_count == len(ANALYSIS_TIMEFRAMES):
            reason = str(result.get("reason") or "").strip()
            boost_note = "Confluencia total M1/M5/M15."
            result["reason"] = f"{reason} {boost_note}".strip()
            result["entry_reason"] = result["reason"]
            result["signal_explanation"] = result["reason"]
            result["narrator_text"] = result["reason"]
    else:
        if "MTF_CONFLUENCE" not in blocked:
            blocked.append("MTF_CONFLUENCE")
        approved = [item for item in approved if item != "MTF_CONFLUENCE"]
        result["trade_allowed"] = False
        result["mtf_ready"] = False
        result["quality_reason"] = (
            "Confluencia insuficiente entre graficos de 1m, 5m e 15m."
        )
        reason = str(result.get("reason") or "").strip()
        result["reason"] = (
            f"{reason} Bloqueado: precisa de pelo menos {MIN_MTF_CONFLUENCE} "
            f"timeframes qualificados alinhados (atual {confluence_count})."
        ).strip()
        result["entry_reason"] = result["reason"]
        result["signal_explanation"] = result["reason"]
        result["narrator_text"] = result["reason"]

    if strong_m15_conflict:
        if "MTF_HIGHER_TF_CONFLICT" not in blocked:
            blocked.append("MTF_HIGHER_TF_CONFLICT")
        result["trade_allowed"] = False
        result["mtf_ready"] = False
        if not result.get("quality_reason"):
            result["quality_reason"] = "Tendencia de 15m conflita com a operacao."

    result["blocked_filters"] = blocked
    result["approved_filters"] = approved
    return result
