import unittest
from unittest.mock import AsyncMock, patch

from backend import main, signal_engine

_VERTEX_ORIGINAL = signal_engine.VERTEX_ENABLED


def setUpModule() -> None:
    """Desliga a Vertex: aqui se testa o motor clássico. Ver test_named_strategies."""
    signal_engine.VERTEX_ENABLED = False


def tearDownModule() -> None:
    signal_engine.VERTEX_ENABLED = _VERTEX_ORIGINAL
from backend.signal_engine import (
    ANALYSIS_TIMEFRAMES,
    MIN_MTF_CONFLUENCE,
    analyze_signal,
    merge_multi_timeframe_signals,
)


def make_directional_candles(*, bullish: bool, count: int = 40) -> list[dict[str, float]]:
    candles = []
    price = 1.1000
    for index in range(count):
        step = 0.0004 if bullish else -0.0004
        if index % 7 == 0:
            step *= 0.2
        open_price = price
        close_price = price + step
        high = max(open_price, close_price) + 0.00005
        low = min(open_price, close_price) - 0.00005
        candles.append(
            {
                "open": round(open_price, 6),
                "close": round(close_price, 6),
                "max": round(high, 6),
                "min": round(low, 6),
                "volume": 1,
            }
        )
        price = close_price
    return candles


def make_signal(
    *,
    direction: str,
    confidence: int = 85,
    trade_allowed: bool = True,
    timeframe: str = "M1",
) -> dict:
    return {
        "symbol": "EURUSD-OTC",
        "signal": direction,
        "confidence": confidence,
        "score": confidence,
        "reason": f"Sinal {direction}",
        "entry_reason": f"Sinal {direction}",
        "timeframe": timeframe,
        "trade_allowed": trade_allowed,
        "blocked_filters": [] if trade_allowed else ["MIN_CONFIDENCE"],
        "approved_filters": ["MIN_CONFIDENCE"] if trade_allowed else [],
        "trend": "UP" if direction == "CALL" else "DOWN" if direction == "PUT" else "SIDEWAYS",
        "used_strategies": ["EMA9/EMA21"],
        "candle_reading": f"{timeframe} leitura local.",
    }


class TestMultiTimeframeAnalysis(unittest.TestCase):
    def test_analysis_timeframes_are_one_five_fifteen(self) -> None:
        self.assertEqual(ANALYSIS_TIMEFRAMES, ("M1", "M5", "M15"))

    def test_merge_requires_majority_confluence(self) -> None:
        signals = {
            "M1": make_signal(direction="CALL", timeframe="M1"),
            "M5": make_signal(direction="PUT", timeframe="M5"),
            "M15": make_signal(direction="PUT", timeframe="M15"),
        }

        result = merge_multi_timeframe_signals("M1", signals)

        self.assertEqual(result["signal"], "CALL")
        self.assertEqual(result["operation_timeframe"], "M1")
        self.assertLess(result["mtf_confluence"], MIN_MTF_CONFLUENCE)
        self.assertFalse(result["trade_allowed"])
        self.assertIn("MTF_CONFLUENCE", result["blocked_filters"])
        self.assertIn("MTF_HIGHER_TF_CONFLICT", result["blocked_filters"])

    def test_merge_approves_when_two_timeframes_agree(self) -> None:
        signals = {
            "M1": make_signal(direction="CALL", timeframe="M1", confidence=82),
            "M5": make_signal(direction="CALL", timeframe="M5", confidence=80),
            "M15": make_signal(direction="WAIT", timeframe="M15", confidence=0, trade_allowed=False),
        }

        result = merge_multi_timeframe_signals("M1", signals)

        self.assertEqual(result["mtf_confluence"], 2)
        self.assertTrue(result["mtf_ready"])
        self.assertIn("MTF_CONFLUENCE", result["approved_filters"])
        self.assertNotIn("MTF_CONFLUENCE", result["blocked_filters"])
        self.assertIn("Multi-timeframe 1m/5m/15m", result["used_strategies"])
        self.assertIn("operacao M1", result["candle_reading"])

    def test_full_agreement_boosts_confidence(self) -> None:
        signals = {
            "M1": make_signal(direction="PUT", timeframe="M1", confidence=84),
            "M5": make_signal(direction="PUT", timeframe="M5", confidence=81),
            "M15": make_signal(direction="PUT", timeframe="M15", confidence=79),
        }

        result = merge_multi_timeframe_signals("M5", signals)

        self.assertEqual(result["operation_timeframe"], "M5")
        self.assertEqual(result["mtf_confluence"], 3)
        self.assertGreaterEqual(result["confidence"], 79)
        self.assertLessEqual(result["confidence"], 84)
        self.assertTrue(result["mtf_ready"])

    def test_low_quality_higher_timeframes_do_not_confirm_primary_signal(self) -> None:
        signals = {
            "M1": make_signal(direction="PUT", timeframe="M1", confidence=85),
            "M5": make_signal(direction="PUT", timeframe="M5", confidence=68),
            "M15": make_signal(direction="PUT", timeframe="M15", confidence=66),
        }

        result = merge_multi_timeframe_signals("M1", signals)

        self.assertEqual(result["mtf_qualified_votes"], {"M1": "PUT"})
        self.assertEqual(result["mtf_confluence"], 1)
        self.assertFalse(result["trade_allowed"])
        self.assertIn("MTF_CONFLUENCE", result["blocked_filters"])

    def test_rejected_higher_timeframe_does_not_count_as_confirmation(self) -> None:
        signals = {
            "M1": make_signal(direction="CALL", timeframe="M1", confidence=86),
            "M5": make_signal(
                direction="CALL",
                timeframe="M5",
                confidence=84,
                trade_allowed=False,
            ),
            "M15": make_signal(direction="WAIT", timeframe="M15", confidence=0, trade_allowed=False),
        }

        result = merge_multi_timeframe_signals("M1", signals)

        self.assertEqual(result["mtf_qualified_votes"], {"M1": "CALL"})
        self.assertFalse(result["mtf_ready"])

    def test_strong_m15_conflict_vetoes_m1_even_when_m5_agrees(self) -> None:
        signals = {
            "M1": make_signal(direction="CALL", timeframe="M1", confidence=87),
            "M5": make_signal(direction="CALL", timeframe="M5", confidence=83),
            "M15": make_signal(direction="PUT", timeframe="M15", confidence=82),
        }

        result = merge_multi_timeframe_signals("M1", signals)

        self.assertFalse(result["trade_allowed"])
        self.assertIn("MTF_HIGHER_TF_CONFLICT", result["blocked_filters"])

    def test_opposite_m15_trend_vetoes_even_without_qualified_vote(self) -> None:
        signals = {
            "M1": make_signal(direction="CALL", timeframe="M1", confidence=92),
            "M5": make_signal(direction="CALL", timeframe="M5", confidence=90),
            "M15": make_signal(
                direction="PUT",
                timeframe="M15",
                confidence=66,
                trade_allowed=False,
            ),
        }

        result = merge_multi_timeframe_signals("M1", signals)

        self.assertEqual(result["mtf_confluence"], 2)
        self.assertFalse(result["trade_allowed"])
        self.assertFalse(result["mtf_ready"])
        self.assertIn("MTF_HIGHER_TF_CONFLICT", result["blocked_filters"])

    def test_empty_signals_raise(self) -> None:
        with self.assertRaises(ValueError):
            merge_multi_timeframe_signals("M1", {})

    def test_analyze_signal_still_works_on_bullish_series(self) -> None:
        signal = analyze_signal(
            "EURUSD-OTC",
            make_directional_candles(bullish=True),
            timeframe="M5",
            strategy_mode="aggressive",
            payout=85,
        )
        self.assertIn(signal["signal"], {"CALL", "PUT", "WAIT"})
        self.assertEqual(signal["timeframe"], "M5")
        self.assertLessEqual(signal["confidence"], 100)
        self.assertGreaterEqual(int(signal["raw_direction_score"]), int(signal["confidence"]))
        self.assertEqual(signal["confidence_model_version"], "backup-classic")

    def test_closed_candle_endtime_aligns_each_timeframe(self) -> None:
        timestamp = 1_700_000_123

        self.assertEqual(
            main.closed_candle_endtime(timestamp, "M1"),
            (timestamp // 60) * 60,
        )
        self.assertEqual(
            main.closed_candle_endtime(timestamp, "M5"),
            (timestamp // 300) * 300,
        )
        self.assertEqual(
            main.closed_candle_endtime(timestamp, "M15"),
            (timestamp // 900) * 900,
        )


class TestClassicStrategyOperationalRanking(unittest.IsolatedAsyncioTestCase):
    def test_final_rank_prioritizes_strategy_score_then_confidence_and_payout(self) -> None:
        weaker = {
            "symbol": "EURUSD-OTC",
            "strategy_score": 84,
            "confidence": 84,
            "payout": 85,
        }
        stronger = {
            "symbol": "GBPUSD-OTC",
            "strategy_score": 92,
            "confidence": 90,
            "payout": 90,
        }

        self.assertGreater(
            main.candidate_rank(stronger),
            main.candidate_rank(weaker),
        )

    def test_resolve_cycle_entry_fallback_respeita_o_piso_do_usuario(self) -> None:
        """Política revogada em 10/09: o fallback NÃO tem mais piso 70 fixo.

        Com o painel em 80, um candidato de 72 executava ordem por este ramo —
        aconteceu em produção com R$ 200 numa conta configurada para 80. O piso
        agora é o do usuário; só a entrada de demonstração desce para 60, via
        `live_min_confidence`.
        """
        state = main.auto_trader.start("user-classic-fallback")
        state.min_confidence = 80
        state.min_payout = 80
        state.cycle_best_trade_candidate = {
            "symbol": "EURUSD-OTC",
            "direction": "CALL",
            "signal": "CALL",
            "confidence": 75,
            "payout": 90,
            "trade_allowed": True,
            "blocked_filters": [],
        }
        state.cycle_best_candidate = {
            "symbol": "GBPUSD-OTC",
            "direction": "PUT",
            "signal": "PUT",
            "confidence": 72,
            "payout": 88,
            "trade_allowed": True,
            "blocked_filters": [],
        }

        # 72 < 80: nenhum dos dois ramos pode liberar.
        self.assertIsNone(main.resolve_cycle_entry_candidate(state))

        # Sem candidato estrito, o fallback passa a valer — e com o painel em
        # 70 o candidato de 72 é liberado por ele.
        state.cycle_best_trade_candidate = None
        state.min_confidence = 70
        selected = main.resolve_cycle_entry_candidate(state)
        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertEqual(selected["symbol"], "GBPUSD-OTC")
        self.assertTrue(selected.get("fallback_candidate_used"))

        # Modo LIVE continua com o piso próprio de 60, mesmo com o painel em 80.
        state.min_confidence = 80
        state.cycle_best_candidate = dict(
            state.cycle_best_candidate, confidence=62, strategy_score=62, live_demo=True
        )
        vivo = main.resolve_cycle_entry_candidate(state)
        self.assertIsNotNone(vivo)

    async def test_ranked_confirmation_skips_mtf_gate_for_classic_strategy(self) -> None:
        candidates = [
            {
                **make_signal(
                    direction="CALL",
                    confidence=88,
                    trade_allowed=True,
                    timeframe="M1",
                ),
                "symbol": "EURUSD-OTC",
                "direction": "CALL",
                "payout": 90,
            },
            {
                **make_signal(
                    direction="PUT",
                    confidence=80,
                    trade_allowed=False,
                    timeframe="M1",
                ),
                "symbol": "GBPUSD-OTC",
                "direction": "PUT",
                "payout": 86,
            },
        ]

        results = await main.confirm_ranked_candidates_multi_timeframe(
            "user-classic-mtf-skip",
            candidates,
            primary_timeframe="M1",
            endtime=1_700_000_000,
            strategy_mode="conservative",
        )

        self.assertEqual(len(results), 2)
        self.assertTrue(all(item.get("mtf_skipped") for item in results))
        self.assertTrue(results[0]["trade_allowed"])
        self.assertFalse(results[1]["trade_allowed"])
        self.assertEqual(results[0]["mtf_confluence"], 2)


if __name__ == "__main__":
    unittest.main()
