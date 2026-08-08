"""Confiança calibrada e rejeição de dados stale no portão de entrada."""

from __future__ import annotations

import unittest

from backend import main
from backend.auto_trader import AutoTrader
from backend.signal_engine import (
    CONFIDENCE_MODEL_VERSION,
    _calibrate_confidence,
    analyze_signal,
)


def _trend_candles(direction: str = "CALL", count: int = 40) -> list[dict[str, float]]:
    candles: list[dict[str, float]] = []
    price = 1.1000
    for index in range(count):
        step = 0.0008 if direction == "CALL" else -0.0008
        open_price = price
        close_price = price + step
        high = max(open_price, close_price) + 0.0001
        low = min(open_price, close_price) - 0.00005
        candles.append(
            {
                "open": open_price,
                "close": close_price,
                "max": high,
                "min": low,
            }
        )
        price = close_price
    return candles


class ConfidenceCalibrationTests(unittest.TestCase):
    def test_calibrate_compresses_raw_score(self) -> None:
        self.assertEqual(_calibrate_confidence(0), 50)
        self.assertEqual(_calibrate_confidence(120), 92)
        mid = _calibrate_confidence(60)
        self.assertGreaterEqual(mid, 50)
        self.assertLessEqual(mid, 92)

    def test_analyze_signal_uses_backup_classic_raw_confidence(self) -> None:
        """Estratégia clássica: confiança = score bruto (sem calibrated-v1)."""
        signal = analyze_signal(
            "EURUSD-OTC",
            _trend_candles("CALL"),
            timeframe="M1",
            strategy_mode="balanced",
            payout=85.0,
        )
        self.assertEqual(CONFIDENCE_MODEL_VERSION, "backup-classic")
        self.assertEqual(signal.get("confidence_model_version"), "backup-classic")
        self.assertIn("raw_direction_score", signal)
        # Atualizado 2026-08-07: confluências técnicas fortes (ex.: EMA9/21 +
        # RSI + estrutura de candles todos a favor) somam mais que 100 pontos
        # no score aditivo bruto. `raw_direction_score` preserva esse valor
        # sem teto (não é probabilidade, ver docstring de `_calibrate_confidence`
        # em signal_engine.py), enquanto `confidence` é o mesmo score limitado
        # a 100 para uso como percentual (comparações com min_confidence etc.).
        self.assertEqual(int(signal["confidence"]), min(100, int(signal["raw_direction_score"])))
        self.assertGreaterEqual(int(signal["confidence"]), 0)


class StaleEntryGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.trader = AutoTrader()
        self.state = self.trader.start("user-stale-gate")
        self.state.min_confidence = 80
        self.state.min_payout = 80.0

    def test_stale_candidate_never_enters(self) -> None:
        candidate = {
            "symbol": "EURUSD-OTC",
            "signal": "CALL",
            "direction": "CALL",
            "confidence": 90,
            "payout": 85.0,
            "strategy_score": 85,
            "trade_allowed": True,
            "blocked_filters": [],
            "from_cache": True,
            "stale": True,
        }
        self.assertFalse(
            main.candidate_meets_cycle_threshold(
                candidate,
                self.state,
                minimum_confidence=80,
            )
        )


if __name__ == "__main__":
    unittest.main()
