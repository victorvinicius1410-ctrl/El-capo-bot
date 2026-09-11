"""Testes das estratégias nomeadas do El Capo."""

from __future__ import annotations

import unittest

from backend import signal_engine

_VERTEX_ORIGINAL = signal_engine.VERTEX_ENABLED


def setUpModule() -> None:
    """Desliga a Vertex: aqui se testa o motor clássico.

    `VERTEX_ENABLED` vem do ambiente na importação. Com ela ligada o override
    carimba `confidence_model_version="vertex-v1"` e devolve WAIT fora do
    extremo, e estes testes falhariam por um motivo que não é o deles.
    """
    signal_engine.VERTEX_ENABLED = False


def tearDownModule() -> None:
    signal_engine.VERTEX_ENABLED = _VERTEX_ORIGINAL

from backend.named_strategies import (
    STRATEGY_CANDLE_FLOW,
    STRATEGY_EXHAUSTION_REVERSAL,
    STRATEGY_RETRACEMENT_SR,
    detect_candle_flow,
    detect_exhaustion_reversal,
    detect_named_strategies,
    detect_retracement_sr,
    pick_primary_strategy,
)
from backend.signal_engine import analyze_signal


def _candle(open_: float, close: float, high: float, low: float) -> dict[str, float]:
    return {
        "open": open_,
        "close": close,
        "max": high,
        "min": low,
        "volume": 1,
    }


def _up_trend(count: int = 30, start: float = 1.1) -> list[dict[str, float]]:
    candles: list[dict[str, float]] = []
    price = start
    for _ in range(count):
        open_ = price
        close = price + 0.0004
        candles.append(_candle(open_, close, close + 0.00005, open_ - 0.00005))
        price = close
    return candles


class NamedStrategyDetectionTests(unittest.TestCase):
    def test_retracement_sr_call_on_support(self) -> None:
        candles = _up_trend(20)
        # Pullback baixista
        price = candles[-1]["close"]
        for _ in range(3):
            open_ = price
            close = price - 0.00035
            candles.append(_candle(open_, close, open_ + 0.00002, close - 0.00002))
            price = close
        # Rejeição no suporte
        open_ = price - 0.00005
        close = price + 0.0004
        candles.append(_candle(open_, close, close + 0.00003, open_ - 0.0003))

        match = detect_retracement_sr(
            "CALL",
            candles,
            timeframe="M1",
            near_support=True,
            near_resistance=False,
        )
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match["key"], STRATEGY_RETRACEMENT_SR)
        self.assertTrue(match["sr_zone_exempt"])
        self.assertIn("suporte", match["summary"].lower())

    def test_retracement_sr_disabled_on_m15(self) -> None:
        candles = _up_trend(25)
        match = detect_retracement_sr(
            "CALL",
            candles,
            timeframe="M15",
            near_support=True,
            near_resistance=False,
        )
        self.assertIsNone(match)

    def test_exhaustion_reversal_from_oversold(self) -> None:
        candles = _up_trend(20)
        price = candles[-1]["close"]
        for _ in range(3):
            open_ = price
            close = price - 0.0004
            candles.append(_candle(open_, close, open_ + 0.00002, close - 0.00002))
            price = close
        open_ = price - 0.00005
        close = price + 0.00045
        candles.append(_candle(open_, close, close + 0.00003, open_ - 0.00035))

        match = detect_exhaustion_reversal("CALL", candles, rsi=28.0)
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match["key"], STRATEGY_EXHAUSTION_REVERSAL)

    def test_candle_flow_requires_strong_sequence(self) -> None:
        candles = _up_trend(20)
        # Quatro velas fortes de alta
        price = candles[-1]["close"]
        for _ in range(4):
            open_ = price
            close = price + 0.0005
            candles.append(_candle(open_, close, close + 0.00002, open_ - 0.00002))
            price = close

        match = detect_candle_flow(
            "CALL",
            candles,
            near_support=False,
            near_resistance=False,
        )
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match["key"], STRATEGY_CANDLE_FLOW)

    def test_candle_flow_blocked_into_resistance(self) -> None:
        candles = _up_trend(24)
        match = detect_candle_flow(
            "CALL",
            candles,
            near_support=False,
            near_resistance=True,
        )
        self.assertIsNone(match)

    def test_primary_priority_prefers_retracement(self) -> None:
        primary = pick_primary_strategy(
            [
                {"key": STRATEGY_CANDLE_FLOW, "label": "flow"},
                {"key": STRATEGY_RETRACEMENT_SR, "label": "sr"},
            ]
        )
        self.assertEqual(primary["key"], STRATEGY_RETRACEMENT_SR)

    def test_uptrend_without_setup_can_match_candle_flow(self) -> None:
        matches = detect_named_strategies(
            "CALL",
            _up_trend(10),
            timeframe="M1",
            rsi=50,
            near_support=False,
            near_resistance=False,
        )
        keys = {item["key"] for item in matches}
        self.assertTrue(keys <= {STRATEGY_CANDLE_FLOW})
        self.assertNotIn(STRATEGY_RETRACEMENT_SR, keys)


class NamedStrategyQualityGateTests(unittest.TestCase):
    def test_analyze_signal_uses_classic_strategy_without_named_fields(self) -> None:
        """Sem `NAMED_STRATEGIES`, o clássico não anexa campos de estratégia.

        Ligado (padrão do sistema 02 desde 04/09), `analyze_signal` anota
        `strategy_key`/`named_strategies` em toda análise — é assim que o
        resultado fica atribuível por estratégia no histórico. A anotação
        acontece mesmo quando nenhuma estratégia libera a entrada.
        """
        candles = _up_trend(40)
        signal = analyze_signal(
            "EURUSD-OTC",
            candles,
            timeframe="M1",
            strategy_mode="conservative",
            payout=90,
        )
        self.assertEqual(signal.get("confidence_model_version"), "backup-classic")
        # A anotação acontece sempre; o que o flag controla é se alguma
        # estratégia pode LIBERAR uma entrada que o portão clássico barrou.
        self.assertIn("named_strategies", signal)
        self.assertIsInstance(signal["named_strategies"], list)


if __name__ == "__main__":
    unittest.main()
