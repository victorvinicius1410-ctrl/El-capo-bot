"""Testes do ciclo por timeframe e da estratégia clássica de análise."""

from __future__ import annotations

import unittest

from backend import main, signal_engine

_VERTEX_ORIGINAL = signal_engine.VERTEX_ENABLED


def setUpModule() -> None:
    """Desliga a Vertex: aqui se testa o motor clássico. Ver test_named_strategies."""
    signal_engine.VERTEX_ENABLED = False


def tearDownModule() -> None:
    signal_engine.VERTEX_ENABLED = _VERTEX_ORIGINAL
from backend.signal_engine import (
    analyze_signal,
    cycle_minutes_for_timeframe,
)


def make_strong_bull_candles(count: int = 40) -> list[dict[str, float]]:
    candles: list[dict[str, float]] = []
    price = 1.1000
    for _ in range(count):
        open_price = price
        close_price = price + 0.0004
        candles.append(
            {
                "open": round(open_price, 6),
                "close": round(close_price, 6),
                "max": round(close_price + 0.00005, 6),
                "min": round(open_price - 0.00005, 6),
                "volume": 1,
            }
        )
        price = close_price
    return candles


def make_sideways_noise_candles(count: int = 40) -> list[dict[str, float]]:
    candles: list[dict[str, float]] = []
    price = 1.1000
    for index in range(count):
        step = 0.00005 if index % 2 == 0 else -0.00005
        open_price = price
        close_price = price + step
        candles.append(
            {
                "open": round(open_price, 6),
                "close": round(close_price, 6),
                "max": round(max(open_price, close_price) + 0.0002, 6),
                "min": round(min(open_price, close_price) - 0.0002, 6),
                "volume": 1,
            }
        )
        price = close_price
    return candles


class CycleMinutesForTimeframeTests(unittest.TestCase):
    def test_mapping(self) -> None:
        self.assertEqual(cycle_minutes_for_timeframe("M1"), 1)
        self.assertEqual(cycle_minutes_for_timeframe("M5"), 5)
        self.assertEqual(cycle_minutes_for_timeframe("M15"), 15)
        self.assertEqual(cycle_minutes_for_timeframe("m5"), 5)
        self.assertEqual(cycle_minutes_for_timeframe(None), 1)

    def test_main_helper_matches(self) -> None:
        self.assertEqual(main.cycle_minutes_for_timeframe("M15"), 15)

    def test_hourly_operation_targets(self) -> None:
        self.assertEqual(main.minimum_operations_per_hour("M1"), 12)
        self.assertEqual(main.minimum_operations_per_hour("M5"), 6)
        self.assertEqual(main.minimum_operations_per_hour("M15"), 2)
        self.assertEqual(main.minimum_operations_per_hour(None), 12)


class ClassicStrategyEngineTests(unittest.TestCase):
    def test_sideways_noise_blocks_by_price_action_filters(self) -> None:
        signal = analyze_signal("EURUSD-OTC", make_sideways_noise_candles(), payout=85.0)
        self.assertFalse(signal.get("trade_allowed"))
        blocked = set(signal.get("blocked_filters") or [])
        self.assertTrue(
            blocked.intersection(
                {
                    "PRICE_ACTION_SETUP",
                    "SIDEWAYS_FILTER",
                    "CANDLE_STRENGTH",
                    "DOJI_FILTER",
                }
            )
        )

    def test_strong_bull_uses_raw_confidence_and_price_action(self) -> None:
        signal = analyze_signal("EURUSD-OTC", make_strong_bull_candles(), payout=85.0)
        self.assertEqual(signal.get("confidence_model_version"), "backup-classic")
        self.assertIn(signal.get("signal"), {"CALL", "PUT"})
        self.assertGreaterEqual(int(signal.get("confidence") or 0), 70)
        self.assertIn("price_action_setup", signal)


if __name__ == "__main__":
    unittest.main()
