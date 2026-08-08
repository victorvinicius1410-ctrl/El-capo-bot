"""Estratégias nomeadas do El Capo — detecção e narrativa de entrada.

Três setups explícitos além da confluência clássica:

1. ``RETRACEMENT_SR`` — retração em zonas de suporte/resistência (M1/M5).
2. ``EXHAUSTION_REVERSAL`` — reversão em zonas de exaustão (RSI extremo + rejeição).
3. ``CANDLE_FLOW`` — fluxo de velas / seguimento de força.

Cada match devolve chave, rótulo, resumo curto (balão/TTS) e detalhe completo
(histórico + popup).
"""

from __future__ import annotations

from typing import Any

STRATEGY_RETRACEMENT_SR = "RETRACEMENT_SR"
STRATEGY_EXHAUSTION_REVERSAL = "EXHAUSTION_REVERSAL"
STRATEGY_CANDLE_FLOW = "CANDLE_FLOW"
STRATEGY_CONTINUATION = "CONTINUATION"

STRATEGY_LABELS: dict[str, str] = {
    STRATEGY_RETRACEMENT_SR: "Retração em Zonas de Suporte e Resistência",
    STRATEGY_EXHAUSTION_REVERSAL: "Padrões de Reversão em Zonas de Exaustão",
    STRATEGY_CANDLE_FLOW: "Fluxo de Velas (Seguimento de Força)",
    STRATEGY_CONTINUATION: "Continuação de tendência",
}

# Retração S/R só nos gráficos rápidos (pedido do produto).
RETRACEMENT_SR_TIMEFRAMES = frozenset({"M1", "M5"})


def _candle_shape(candle: dict[str, float]) -> tuple[float, float, float]:
    candle_range = max(float(candle["max"]) - float(candle["min"]), 0.0)
    if candle_range <= 0:
        return 0.0, 0.0, 0.0
    body = abs(float(candle["close"]) - float(candle["open"]))
    upper = max(0.0, float(candle["max"]) - max(float(candle["open"]), float(candle["close"])))
    lower = max(0.0, min(float(candle["open"]), float(candle["close"])) - float(candle["min"]))
    return body / candle_range, upper / candle_range, lower / candle_range


def _is_bullish(candle: dict[str, float]) -> bool:
    return float(candle["close"]) > float(candle["open"])


def _is_bearish(candle: dict[str, float]) -> bool:
    return float(candle["close"]) < float(candle["open"])


def detect_retracement_sr(
    direction: str,
    candles: list[dict[str, float]],
    *,
    timeframe: str,
    near_support: bool,
    near_resistance: bool,
) -> dict[str, Any] | None:
    """Detecta retração com rejeição em suporte (CALL) ou resistência (PUT).

    Args:
        direction: ``CALL`` ou ``PUT``.
        candles: Velas normalizadas (open/close/min/max).
        timeframe: Timeframe operacional (só M1/M5).
        near_support: Preço colado no suporte.
        near_resistance: Preço colado na resistência.

    Returns:
        Match com narrativa ou ``None``.
    """
    tf = str(timeframe or "M1").strip().upper()
    if tf not in RETRACEMENT_SR_TIMEFRAMES or direction not in {"CALL", "PUT"} or len(candles) < 6:
        return None

    last = candles[-1]
    body, upper, lower = _candle_shape(last)
    pullback = candles[-4:-1]

    if direction == "CALL":
        if not near_support:
            return None
        had_pullback = any(_is_bearish(candle) for candle in pullback)
        rejection = (
            _is_bullish(last)
            and lower >= 0.28
            and body >= 0.28
            and upper <= 0.38
        )
        if not (had_pullback and rejection):
            return None
        level_name = "suporte"
        why = (
            f"Preço retestou a zona de {level_name}, houve retração baixista "
            f"e a vela atual rejeitou o nível com pavio inferior ({lower:.0%}) "
            f"fechando a favor da alta."
        )
    else:
        if not near_resistance:
            return None
        had_pullback = any(_is_bullish(candle) for candle in pullback)
        rejection = (
            _is_bearish(last)
            and upper >= 0.28
            and body >= 0.28
            and lower <= 0.38
        )
        if not (had_pullback and rejection):
            return None
        level_name = "resistência"
        why = (
            f"Preço retestou a zona de {level_name}, houve retração altista "
            f"e a vela atual rejeitou o nível com pavio superior ({upper:.0%}) "
            f"fechando a favor da baixa."
        )

    label = STRATEGY_LABELS[STRATEGY_RETRACEMENT_SR]
    summary = f"Retração no {level_name} com rejeição confirmada ({tf})."
    return {
        "key": STRATEGY_RETRACEMENT_SR,
        "label": label,
        "setup": "RETRACEMENT_SR",
        "summary": summary,
        "speech_preview": f"Vou de {direction} por retração em {level_name}.",
        "detail": (
            f"Estratégia: {label} ({tf}). Direção {direction}. {why} "
            f"Corpo da vela {body:.0%}."
        ),
        "sr_zone_exempt": True,
    }


def detect_exhaustion_reversal(
    direction: str,
    candles: list[dict[str, float]],
    *,
    rsi: float,
) -> dict[str, Any] | None:
    """Detecta reversão após exaustão (RSI extremo + vela de rejeição).

    Args:
        direction: ``CALL`` ou ``PUT``.
        candles: Velas normalizadas.
        rsi: RSI(14) atual.

    Returns:
        Match com narrativa ou ``None``.
    """
    if direction not in {"CALL", "PUT"} or len(candles) < 5:
        return None

    last = candles[-1]
    previous = candles[-4:-1]
    body, upper, lower = _candle_shape(last)

    if direction == "CALL":
        exhausted = float(rsi) <= 35 or sum(1 for candle in previous if _is_bearish(candle)) >= 3
        reverse = (
            _is_bullish(last)
            and lower >= 0.32
            and body >= 0.25
            and upper <= 0.4
        )
        if not (exhausted and reverse):
            return None
        why = (
            f"Mercado em exaustão de venda (RSI {rsi:.1f}). "
            f"A vela atual mostra rejeição com pavio inferior ({lower:.0%}) "
            f"e fechamento altista — típico de zona de esgotamento."
        )
    else:
        exhausted = float(rsi) >= 65 or sum(1 for candle in previous if _is_bullish(candle)) >= 3
        reverse = (
            _is_bearish(last)
            and upper >= 0.32
            and body >= 0.25
            and lower <= 0.4
        )
        if not (exhausted and reverse):
            return None
        why = (
            f"Mercado em exaustão de compra (RSI {rsi:.1f}). "
            f"A vela atual mostra rejeição com pavio superior ({upper:.0%}) "
            f"e fechamento baixista — típico de zona de esgotamento."
        )

    label = STRATEGY_LABELS[STRATEGY_EXHAUSTION_REVERSAL]
    summary = f"Reversão por exaustão (RSI {rsi:.0f})."
    return {
        "key": STRATEGY_EXHAUSTION_REVERSAL,
        "label": label,
        "setup": "EXHAUSTION_REVERSAL",
        "summary": summary,
        "speech_preview": f"Vou de {direction} por reversão em zona de exaustão.",
        "detail": f"Estratégia: {label}. Direção {direction}. {why}",
        "sr_zone_exempt": True,
    }


def detect_candle_flow(
    direction: str,
    candles: list[dict[str, float]],
    *,
    near_support: bool,
    near_resistance: bool,
) -> dict[str, Any] | None:
    """Detecta fluxo de força: sequência de velas decisivas na direção.

    Args:
        direction: ``CALL`` ou ``PUT``.
        candles: Velas normalizadas.
        near_support: Evita PUT colado em suporte (conflito).
        near_resistance: Evita CALL colado em resistência (conflito).

    Returns:
        Match com narrativa ou ``None``.
    """
    if direction not in {"CALL", "PUT"} or len(candles) < 4:
        return None

    # Não segue força contra o nível oposto (continuação cega em S/R).
    if direction == "CALL" and near_resistance and not near_support:
        return None
    if direction == "PUT" and near_support and not near_resistance:
        return None

    window = candles[-4:]
    bodies: list[float] = []
    aligned = 0
    for candle in window:
        body, upper, lower = _candle_shape(candle)
        bodies.append(body)
        if direction == "CALL":
            ok = _is_bullish(candle) and body >= 0.45 and upper <= 0.35
        else:
            ok = _is_bearish(candle) and body >= 0.45 and lower <= 0.35
        if ok:
            aligned += 1

    if aligned < 3:
        return None
    # Força crescente ou média alta.
    avg_body = sum(bodies) / len(bodies)
    rising = bodies[-1] >= bodies[0] * 0.9
    if avg_body < 0.48 and not rising:
        return None

    label = STRATEGY_LABELS[STRATEGY_CANDLE_FLOW]
    summary = f"Fluxo de {aligned} velas fortes na direção {direction}."
    why = (
        f"{aligned} das últimas 4 velas fecharam com corpo médio de {avg_body:.0%} "
        f"na direção {direction}, caracterizando seguimento de força."
    )
    return {
        "key": STRATEGY_CANDLE_FLOW,
        "label": label,
        "setup": "CANDLE_FLOW",
        "summary": summary,
        "speech_preview": f"Vou de {direction} seguindo o fluxo de força das velas.",
        "detail": f"Estratégia: {label}. Direção {direction}. {why}",
        "sr_zone_exempt": False,
    }


def detect_named_strategies(
    direction: str,
    candles: list[dict[str, float]],
    *,
    timeframe: str,
    rsi: float,
    near_support: bool,
    near_resistance: bool,
) -> list[dict[str, Any]]:
    """Avalia as três estratégias nomeadas e devolve matches ativos.

    Args:
        direction: Direção candidata.
        candles: Velas normalizadas.
        timeframe: Timeframe da operação.
        rsi: RSI(14).
        near_support: Flag de proximidade ao suporte.
        near_resistance: Flag de proximidade à resistência.

    Returns:
        Lista de matches (pode ser vazia), na ordem de prioridade.
    """
    matches: list[dict[str, Any]] = []
    retracement = detect_retracement_sr(
        direction,
        candles,
        timeframe=timeframe,
        near_support=near_support,
        near_resistance=near_resistance,
    )
    if retracement is not None:
        matches.append(retracement)

    exhaustion = detect_exhaustion_reversal(direction, candles, rsi=rsi)
    if exhaustion is not None:
        matches.append(exhaustion)

    flow = detect_candle_flow(
        direction,
        candles,
        near_support=near_support,
        near_resistance=near_resistance,
    )
    if flow is not None:
        matches.append(flow)

    return matches


def pick_primary_strategy(
    matches: list[dict[str, Any]],
    *,
    fallback_continuation: bool = False,
) -> dict[str, Any] | None:
    """Escolhe a estratégia primária (prioridade: retração > exaustão > fluxo).

    Args:
        matches: Resultado de ``detect_named_strategies``.
        fallback_continuation: Se True e não houver match, devolve CONTINUATION.

    Returns:
        Match primário ou ``None``.
    """
    priority = (
        STRATEGY_RETRACEMENT_SR,
        STRATEGY_EXHAUSTION_REVERSAL,
        STRATEGY_CANDLE_FLOW,
    )
    by_key = {str(item.get("key")): item for item in matches if isinstance(item, dict)}
    for key in priority:
        if key in by_key:
            return by_key[key]
    if fallback_continuation:
        label = STRATEGY_LABELS[STRATEGY_CONTINUATION]
        return {
            "key": STRATEGY_CONTINUATION,
            "label": label,
            "setup": "CONTINUATION",
            "summary": "Continuação de tendência com confluência técnica.",
            "speech_preview": "Entrada por continuação de tendência.",
            "detail": f"Estratégia: {label}. Setup clássico de continuação fora de zona de S/R.",
            "sr_zone_exempt": False,
        }
    return None


def attach_strategy_narration(
    signal: dict[str, Any],
    primary: dict[str, Any] | None,
    matches: list[dict[str, Any]],
) -> dict[str, Any]:
    """Grava no sinal os campos de estratégia/explicação para UI, TTS e histórico.

    Args:
        signal: Sinal técnico em construção.
        primary: Estratégia escolhida.
        matches: Todos os matches detectados.

    Returns:
        O mesmo ``signal`` mutado.
    """
    signal["named_strategies"] = list(matches)
    signal["named_strategy_keys"] = [str(item.get("key")) for item in matches]
    if primary is None:
        signal["primary_strategy"] = None
        signal["strategy_key"] = None
        signal["strategy_summary"] = None
        signal["analysis_detail"] = signal.get("entry_reason") or signal.get("reason")
        signal["speech_preview"] = None
        return signal

    signal["primary_strategy"] = dict(primary)
    signal["strategy_key"] = primary.get("key")
    signal["strategy_name"] = primary.get("label") or signal.get("strategy_name")
    signal["strategy_setup"] = primary.get("setup") or signal.get("strategy_setup")
    signal["strategy_summary"] = primary.get("summary")
    signal["analysis_detail"] = primary.get("detail")
    signal["speech_preview"] = primary.get("speech_preview")
    signal["strategy_reason"] = primary.get("detail")
    # Preferência narrativa: detail da estratégia nomeada.
    if primary.get("detail"):
        signal["entry_reason"] = primary["detail"]
        signal["signal_explanation"] = primary["detail"]
        signal["narrator_text"] = primary.get("speech_preview") or primary["detail"]
    return signal
