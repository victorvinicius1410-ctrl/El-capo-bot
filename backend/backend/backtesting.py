from __future__ import annotations

from typing import Any

from backend.signal_engine import (
    CONFIDENCE_MODEL_VERSION,
    STRATEGY_PROFILES,
    analyze_signal,
)


TIMEFRAME_SECONDS = {"M1": 60, "M5": 300, "M15": 900}
MINIMUM_HISTORY_CANDLES = 30


def candle_timestamp(candle: dict[str, Any]) -> int:
    """
    Extrai o início temporal de uma vela histórica.

    Args:
        candle: Vela contendo ``from``, ``timestamp`` ou ``time``.

    Returns:
        Unix timestamp inteiro do início da vela.

    Raises:
        ValueError: Se nenhum timestamp válido estiver disponível.
    """
    for field in ("from", "timestamp", "time"):
        try:
            value = int(float(candle[field]))
        except (KeyError, TypeError, ValueError):
            continue
        if value > 0:
            return value
    raise ValueError("BACKTEST_CANDLE_TIMESTAMP_REQUIRED")


def candles_closed_by(
    candles: list[dict[str, Any]],
    *,
    timeframe: str,
    cutoff: int,
) -> list[dict[str, Any]]:
    """
    Seleciona somente velas encerradas até o instante de análise.

    Args:
        candles: Série histórica ordenada cronologicamente.
        timeframe: Timeframe da série.
        cutoff: Horário máximo de fechamento permitido.

    Returns:
        Velas cujo fechamento não ultrapassa o cutoff.

    Raises:
        ValueError: Se o timeframe ou timestamps forem inválidos.
    """
    normalized_timeframe = str(timeframe or "").strip().upper()
    if normalized_timeframe not in TIMEFRAME_SECONDS:
        raise ValueError(f"BACKTEST_TIMEFRAME_UNSUPPORTED:{timeframe}")
    interval = TIMEFRAME_SECONDS[normalized_timeframe]
    return [
        candle
        for candle in candles
        if candle_timestamp(candle) + interval <= cutoff
    ]


def run_walk_forward_backtest(
    *,
    symbol: str,
    candles_by_timeframe: dict[str, list[dict[str, Any]]],
    primary_timeframe: str = "M1",
    payout: float = 85,
    strategy_mode: str = "conservative",
) -> dict[str, Any]:
    """
    Executa backtest walk-forward sem usar a vela de entrada na análise.

    Args:
        symbol: Ativo avaliado, inclusive símbolos OTC.
        candles_by_timeframe: Séries M1, M5 e M15 em ordem cronológica.
        primary_timeframe: Timeframe da entrada.
        payout: Retorno percentual de uma operação vencedora.
        strategy_mode: Perfil técnico aplicado ao motor de sinais.

    Returns:
        Resumo de desempenho e lista auditável de operações simuladas.

    Raises:
        ValueError: Se configuração, histórico ou timestamps forem inválidos.
    """
    normalized_primary = str(primary_timeframe or "").strip().upper()
    if normalized_primary not in TIMEFRAME_SECONDS:
        raise ValueError(f"BACKTEST_TIMEFRAME_UNSUPPORTED:{primary_timeframe}")
    mode = str(strategy_mode or "conservative").strip().lower()
    if mode not in STRATEGY_PROFILES:
        raise ValueError(f"BACKTEST_STRATEGY_MODE_UNSUPPORTED:{strategy_mode}")
    primary_candles = list(candles_by_timeframe.get(normalized_primary) or [])
    if len(primary_candles) <= MINIMUM_HISTORY_CANDLES:
        raise ValueError("BACKTEST_PRIMARY_HISTORY_INSUFFICIENT")
    for candle in primary_candles:
        candle_timestamp(candle)

    trades: list[dict[str, Any]] = []
    for entry_index in range(MINIMUM_HISTORY_CANDLES, len(primary_candles)):
        entry_candle = primary_candles[entry_index]
        entry_time = candle_timestamp(entry_candle)
        closed = candles_closed_by(
            primary_candles,
            timeframe=normalized_primary,
            cutoff=entry_time,
        )
        if len(closed) < MINIMUM_HISTORY_CANDLES:
            continue
        signal = analyze_signal(
            symbol,
            closed,
            timeframe=normalized_primary,
            strategy_mode=mode,
            payout=payout,
        )
        if not bool(signal.get("trade_allowed")):
            continue
        if str(signal.get("signal") or "").upper() not in {"CALL", "PUT"}:
            continue

        direction = str(signal.get("signal") or "").upper()
        open_price = float(entry_candle["open"])
        close_price = float(entry_candle["close"])
        won = (direction == "CALL" and close_price > open_price) or (
            direction == "PUT" and close_price < open_price
        )
        profit = round(float(payout) / 100, 4) if won else -1.0
        trades.append(
            {
                "entry_time": entry_time,
                "analysis_endtime": entry_time,
                "analysis_candles_includes_entry": False,
                "direction": direction,
                "confidence": int(signal.get("confidence") or 0),
                "strategy_setup": signal.get("price_action_setup"),
                "mtf_qualified_votes": {},
                "result": "WIN" if won else "LOSS",
                "profit": profit,
            }
        )

    wins = sum(1 for trade in trades if trade["result"] == "WIN")
    losses = sum(1 for trade in trades if trade["result"] == "LOSS")
    total = wins + losses
    net_profit = round(sum(float(trade["profit"]) for trade in trades), 4)
    gross_profit = sum(max(0.0, float(trade["profit"])) for trade in trades)
    gross_loss = abs(sum(min(0.0, float(trade["profit"])) for trade in trades))
    return {
        "symbol": symbol,
        "primary_timeframe": normalized_primary,
        "strategy_mode": mode,
        "model_version": CONFIDENCE_MODEL_VERSION,
        "payout": float(payout),
        "wins": wins,
        "losses": losses,
        "total_trades": total,
        "win_rate": round((wins / total) * 100, 2) if total else 0.0,
        "net_profit_units": net_profit,
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss else round(gross_profit, 2),
        "trades": trades,
    }
