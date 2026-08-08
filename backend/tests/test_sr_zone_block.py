"""Política SR_ZONE: o robô não entra com o preço em suporte/resistência.

Pedido do cliente em 2026-07-29 ("não pegar operações em momentos que o ativo
está em suporte e resistência"). O backtest walk-forward com candles reais
(8 ativos OTC, 1000 velas M1) mostrou que as entradas dentro da zona de nível
aprovadas pelo portão de confiança do usuário deram 0% de acerto, então o
bloqueio é crítico (reprova a entrada) e não apenas uma penalidade de score.

Documentação: `docs/ESTRATEGIA.md` §1 e §"Política de suporte / resistência".
"""

import unittest
from unittest import mock

from backend import main, signal_engine
from backend.auto_trader import AutoTrader
from backend.signal_engine import analyze_signal
from tests.test_candle_analysis import make_support_reversal_candles


def make_mid_range_candles() -> list[dict[str, float]]:
    """
    Série com range definido (1.1000–1.1114) e preço subindo pelo meio dele.

    O último fechamento fica a mais de 40 pips do topo e do fundo, bem acima da
    tolerância de zona (12% do range), então nenhum nível está "colado".

    Returns:
        Lista de velas em ordem cronológica pronta para `analyze_signal`.
    """
    candles: list[dict[str, float]] = []
    for index in range(12):
        going_up = index % 2 == 0
        low = 1.10000 + 0.00090 * index
        high = low + 0.00150
        candles.append(
            {
                "open": round(low if going_up else high, 6),
                "close": round(high if going_up else low, 6),
                "max": round(high, 6),
                "min": round(low, 6),
                "volume": 1,
            }
        )
    price = 1.10450
    for _ in range(12):
        open_price = price
        close_price = open_price + 0.00018
        candles.append(
            {
                "open": round(open_price, 6),
                "close": round(close_price, 6),
                "max": round(close_price + 0.00002, 6),
                "min": round(open_price - 0.00002, 6),
                "volume": 1,
            }
        )
        price = close_price
    return candles


class SrZoneSignalTests(unittest.TestCase):
    def test_price_inside_sr_zone_blocks_entry(self) -> None:
        signal = analyze_signal(
            "EURUSD-OTC",
            make_support_reversal_candles(),
            timeframe="M1",
            strategy_mode="conservative",
            payout=90,
        )

        self.assertTrue(signal["in_support_resistance_zone"])
        self.assertIn("SR_ZONE", signal["blocked_filters"])
        self.assertNotIn("SR_ZONE", signal["approved_filters"])
        self.assertFalse(signal["trade_allowed"])
        self.assertIn("SR_ZONE", signal["quality_reason"])

    def test_price_outside_sr_zone_keeps_filter_approved(self) -> None:
        signal = analyze_signal(
            "EURUSD-OTC",
            make_mid_range_candles(),
            timeframe="M1",
            strategy_mode="conservative",
            payout=90,
        )

        self.assertFalse(signal["in_support_resistance_zone"])
        self.assertIn("SR_ZONE", signal["approved_filters"])
        self.assertNotIn("SR_ZONE", signal["blocked_filters"])

    def test_disabling_flag_restores_classic_behaviour(self) -> None:
        with mock.patch.object(signal_engine, "SR_ZONE_HARD_BLOCK", False):
            signal = analyze_signal(
                "EURUSD-OTC",
                make_support_reversal_candles(),
                timeframe="M1",
                strategy_mode="conservative",
                payout=90,
            )

        self.assertTrue(signal["in_support_resistance_zone"])
        self.assertNotIn("SR_ZONE", signal["blocked_filters"])
        self.assertTrue(signal["trade_allowed"])


class SrZoneGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.trader = AutoTrader()
        self.state = self.trader.start("user-sr-zone")
        self.state.min_confidence = 80
        self.state.min_payout = 80.0

    def test_sr_zone_is_a_critical_block(self) -> None:
        self.assertIn("SR_ZONE", main.CRITICAL_TRADE_BLOCKS)

    def test_sr_zone_is_not_relaxed_in_recovery_mode(self) -> None:
        self.assertIn("SR_ZONE", main.RECOVERY_NON_RELAXABLE_TRADE_BLOCKS)

    def test_candidate_in_sr_zone_never_reaches_order(self) -> None:
        candidate = {
            "symbol": "EURUSD-OTC",
            "signal": "CALL",
            "direction": "CALL",
            "confidence": 100,
            "payout": 90.0,
            "strategy_score": 80,
            "trade_allowed": True,
            "blocked_filters": ["SR_ZONE"],
            "in_support_resistance_zone": True,
        }

        self.assertFalse(
            main.candidate_meets_cycle_threshold(
                candidate,
                self.state,
                minimum_confidence=int(self.state.min_confidence),
            )
        )


if __name__ == "__main__":
    unittest.main()
