"""Bloqueios anti-loss dos padrões tóxicos medidos em produção (2026-08-04).

Auditoria do histórico real mostrou WR muito abaixo do empate (~53%) em:
CONTINUATION contra as 3 velas, CONTINUATION+PUT em pares fracos, RSI 50–59,
TREND_CLEAR só como soft penalty, confiança bruta alta e reentrada imediata
após LOSS. Este módulo garante que esses contextos não abram ordem.
"""

from __future__ import annotations

import unittest
from datetime import timedelta
from unittest import mock

from backend import main, signal_engine
from backend.auto_trader import AutoTrader, utc_now
from backend.signal_engine import analyze_signal, _apply_quality_filters
from tests.test_candle_analysis import make_candles


def _base_continuation_signal(**overrides) -> dict:
    """Sinal CONTINUATION CALL saudável o bastante para isolar um bloqueio."""
    signal = {
        "symbol": "GBPUSD-OTC",
        "signal": "CALL",
        "direction": "CALL",
        "confidence": 100,
        "trend": "UP",
        "strength": 35,
        "ema9": 1.102,
        "ema21": 1.100,
        "rsi": 62.0,
        "body_ratio": 0.7,
        "upper_wick_ratio": 0.1,
        "lower_wick_ratio": 0.1,
        "atr_pct": 0.001,
        "directional_candles_5": 4,
        "alternating_last_3": False,
        "price_action_setup": "CONTINUATION",
        "last_3_direction": "UP",
        "reversal_against": False,
        "level_conflict": False,
        "near_support": False,
        "near_resistance": False,
    }
    signal.update(overrides)
    return signal


class Last3AlignmentBlockTests(unittest.TestCase):
    """CONTINUATION desalinhada das 3 velas → bloqueio crítico."""

    def test_continuation_against_last_3_is_blocked(self) -> None:
        signal = _base_continuation_signal(
            signal="PUT",
            direction="PUT",
            ema9=1.100,
            ema21=1.102,
            rsi=38.0,
            last_3_direction="UP",
            lower_wick_ratio=0.1,
        )
        result = _apply_quality_filters(signal, make_candles(40), "conservative", 88.0)
        self.assertIn("LAST_3_ALIGNMENT", result["blocked_filters"])
        self.assertFalse(result["trade_allowed"])
        self.assertIn("LAST_3_ALIGNMENT", main.CRITICAL_TRADE_BLOCKS)

    def test_continuation_aligned_last_3_passes_filter(self) -> None:
        signal = _base_continuation_signal(last_3_direction="UP")
        result = _apply_quality_filters(signal, make_candles(40), "conservative", 88.0)
        self.assertIn("LAST_3_ALIGNMENT", result["approved_filters"])
        self.assertNotIn("LAST_3_ALIGNMENT", result["blocked_filters"])


class WeakContinuationPutBlockTests(unittest.TestCase):
    """CONTINUATION PUT nos pares medidos como tóxicos → bloqueio crítico."""

    def test_eurusd_continuation_put_blocked(self) -> None:
        signal = _base_continuation_signal(
            symbol="EURUSD-OTC",
            signal="PUT",
            direction="PUT",
            ema9=1.100,
            ema21=1.102,
            rsi=35.0,
            last_3_direction="DOWN",
            lower_wick_ratio=0.1,
        )
        result = _apply_quality_filters(signal, make_candles(40), "conservative", 88.0)
        self.assertIn("WEAK_CONTINUATION_PUT", result["blocked_filters"])
        self.assertFalse(result["trade_allowed"])

    def test_gbpusd_continuation_put_still_allowed_by_pair_filter(self) -> None:
        """GBPUSD PUT CONTINUATION tinha WR alto — não entra na lista tóxica."""
        signal = _base_continuation_signal(
            symbol="GBPUSD-OTC",
            signal="PUT",
            direction="PUT",
            ema9=1.100,
            ema21=1.102,
            rsi=35.0,
            last_3_direction="DOWN",
            lower_wick_ratio=0.1,
        )
        result = _apply_quality_filters(signal, make_candles(40), "conservative", 88.0)
        self.assertNotIn("WEAK_CONTINUATION_PUT", result["blocked_filters"])


class ContinuationDeadRsiBlockTests(unittest.TestCase):
    """RSI 50–59 em CONTINUATION: soft penalty (não hard) desde 2026-08-04."""

    def test_rsi_mid_range_penalizes_continuation(self) -> None:
        signal = _base_continuation_signal(rsi=55.0)
        result = _apply_quality_filters(signal, make_candles(40), "conservative", 88.0)
        self.assertIn("CONTINUATION_DEAD_RSI", result["blocked_filters"])
        self.assertNotIn("CONTINUATION_DEAD_RSI", main.CRITICAL_TRADE_BLOCKS)
        hard = [name for name in result["blocked_filters"] if name in main.CRITICAL_TRADE_BLOCKS]
        self.assertNotIn("CONTINUATION_DEAD_RSI", hard)
        self.assertLess(result["strategy_score"], result["confidence"])

    def test_rsi_outside_dead_zone_ok(self) -> None:
        signal = _base_continuation_signal(rsi=62.0)
        result = _apply_quality_filters(signal, make_candles(40), "conservative", 88.0)
        self.assertIn("CONTINUATION_DEAD_RSI", result["approved_filters"])


class TrendClearHardBlockTests(unittest.TestCase):
    """TREND_CLEAR crítico só para mercado SIDEWAYS; força fraca é soft."""

    def test_sideways_is_critical(self) -> None:
        self.assertIn("TREND_CLEAR", main.CRITICAL_TRADE_BLOCKS)
        signal = _base_continuation_signal(trend="SIDEWAYS", strength=5)
        result = _apply_quality_filters(signal, make_candles(40), "conservative", 88.0)
        self.assertIn("TREND_CLEAR", result["blocked_filters"])
        self.assertFalse(result["trade_allowed"])

    def test_weak_up_trend_is_soft_strength_penalty(self) -> None:
        signal = _base_continuation_signal(trend="UP", strength=10)
        result = _apply_quality_filters(signal, make_candles(40), "conservative", 88.0)
        self.assertIn("TREND_CLEAR", result["approved_filters"])
        self.assertIn("TREND_STRENGTH", result["blocked_filters"])
        self.assertNotIn("TREND_STRENGTH", main.CRITICAL_TRADE_BLOCKS)
        hard = [name for name in result["blocked_filters"] if name in main.CRITICAL_TRADE_BLOCKS]
        self.assertNotIn("TREND_STRENGTH", hard)
        self.assertLess(result["strategy_score"], result["confidence"])


class ConfidenceNotTrustedTests(unittest.TestCase):
    """Portão usa strategy_score (após penalidades), não confiança bruta."""

    def setUp(self) -> None:
        self.trader = AutoTrader()
        self.state = self.trader.start("user-conf-gate")
        self.state.min_confidence = 80
        self.state.min_payout = 80.0

    def test_high_confidence_low_strategy_score_rejected(self) -> None:
        candidate = {
            "symbol": "EURUSD-OTC",
            "signal": "CALL",
            "direction": "CALL",
            "confidence": 100,
            "strategy_score": 55,
            "payout": 88.0,
            "trade_allowed": True,
            "blocked_filters": ["EMA_TREND", "RSI_RANGE", "LAST_5_CONFIRMATION"],
        }
        self.assertFalse(
            main.candidate_meets_cycle_threshold(
                candidate,
                self.state,
                minimum_confidence=80,
            )
        )

    def test_strategy_score_above_threshold_passes(self) -> None:
        candidate = {
            "symbol": "GBPUSD-OTC",
            "signal": "CALL",
            "direction": "CALL",
            "confidence": 70,
            "strategy_score": 85,
            "payout": 88.0,
            "trade_allowed": True,
            "blocked_filters": [],
        }
        self.assertTrue(
            main.candidate_meets_cycle_threshold(
                candidate,
                self.state,
                minimum_confidence=80,
            )
        )

    def test_candidate_rank_ignores_raw_confidence(self) -> None:
        low_conf_high_score = {
            "strategy_score": 90,
            "confidence": 70,
            "payout": 85.0,
        }
        high_conf_low_score = {
            "strategy_score": 60,
            "confidence": 100,
            "payout": 88.0,
        }
        self.assertGreater(
            main.candidate_rank(low_conf_high_score),
            main.candidate_rank(high_conf_low_score),
        )


class PostLossCooldownTests(unittest.TestCase):
    """Após 1 LOSS, pausa curta antes da próxima entrada."""

    def setUp(self) -> None:
        main.auto_trader = AutoTrader()
        self.user_id = "user-post-loss"
        self.state = main.auto_trader.start(self.user_id)

    def test_single_loss_triggers_cooldown(self) -> None:
        finished = utc_now() - timedelta(seconds=30)
        main.auto_trader.replace_history(
            self.user_id,
            [
                {
                    "result": "LOSS",
                    "finished_at": finished.isoformat(),
                    "active": "EURUSD-OTC",
                    "is_gale": False,
                }
            ],
        )
        self.assertEqual(main.global_loss_cooldown_reason(self.user_id), "GLOBAL_LOSS_COOLDOWN")

    def test_cooldown_expires_after_window(self) -> None:
        finished = utc_now() - timedelta(seconds=main.GLOBAL_LOSS_COOLDOWN_AFTER_ONE_SECONDS + 5)
        main.auto_trader.replace_history(
            self.user_id,
            [
                {
                    "result": "LOSS",
                    "finished_at": finished.isoformat(),
                    "active": "EURUSD-OTC",
                    "is_gale": False,
                }
            ],
        )
        self.assertIsNone(main.global_loss_cooldown_reason(self.user_id))

    def test_two_losses_use_shorter_ten_minute_window(self) -> None:
        """2 LOSSes: 10 min (não 30) — ASSET_COOLDOWN cobre o mesmo ativo."""
        self.assertEqual(main.GLOBAL_LOSS_COOLDOWN_AFTER_TWO_MINUTES, 10)
        latest = utc_now() - timedelta(minutes=11)
        older = latest - timedelta(minutes=5)
        main.auto_trader.replace_history(
            self.user_id,
            [
                {
                    "result": "LOSS",
                    "finished_at": latest.isoformat(),
                    "active": "EURUSD-OTC",
                    "is_gale": False,
                },
                {
                    "result": "LOSS",
                    "finished_at": older.isoformat(),
                    "active": "GBPUSD-OTC",
                    "is_gale": False,
                },
            ],
        )
        self.assertIsNone(main.global_loss_cooldown_reason(self.user_id))

    def test_apply_strategy_guard_wires_post_loss_cooldown(self) -> None:
        finished = utc_now() - timedelta(seconds=20)
        main.auto_trader.replace_history(
            self.user_id,
            [
                {
                    "result": "LOSS",
                    "finished_at": finished.isoformat(),
                    "active": "EURUSD-OTC",
                    "is_gale": False,
                }
            ],
        )
        signal = _base_continuation_signal()
        allowed, selected, reason = main.apply_strategy_guard(
            self.user_id,
            self.state,
            signal,
            payout=88.0,
        )
        self.assertFalse(allowed)
        self.assertIn("GLOBAL_LOSS_COOLDOWN", selected["blocked_filters"])
        self.assertEqual(reason, "GLOBAL_LOSS_COOLDOWN")


class FrequencyRecoveryTests(unittest.TestCase):
    """Após seca longa, libera filtros de frequência (não anti-loss)."""

    def test_recovery_softens_price_action_and_trend_clear(self) -> None:
        # Recovery estrito (2026-09-03): só TREND_CLEAR é dispensado. Com
        # RECOVERY_STRICT=false o comportamento antigo volta e o teste original
        # continua valendo — por isso as duas afirmações são condicionais.
        signal = _base_continuation_signal(
            price_action_setup="WEAK",
            trend="SIDEWAYS",
            strength=5,
            rsi=62.0,
        )
        blocked = _apply_quality_filters(signal, make_candles(40), "conservative", 88.0)
        self.assertFalse(blocked["trade_allowed"])

        recovered = _apply_quality_filters(
            signal,
            make_candles(40),
            "conservative",
            88.0,
            frequency_recovery=True,
        )
        self.assertTrue(recovered.get("frequency_recovery"))
        # Anti-loss estrutural permanece; PRICE_ACTION/TREND_CLEAR saem do hard.
        hard = [
            name
            for name in recovered["blocked_filters"]
            if name in main.effective_critical_trade_blocks(frequency_recovery=True)
        ]
        self.assertNotIn("TREND_CLEAR", hard)
        if signal_engine.RECOVERY_STRICT:
            self.assertIn("PRICE_ACTION_SETUP", hard)
        else:
            self.assertNotIn("PRICE_ACTION_SETUP", hard)
        self.assertNotIn("LAST_3_ALIGNMENT", signal_engine.FREQUENCY_RECOVERY_SOFT_BLOCKS)
        self.assertNotIn("CANDLE_STRENGTH", main.CRITICAL_TRADE_BLOCKS)
        self.assertNotIn("CALL_CHASE", main.CRITICAL_TRADE_BLOCKS)
        self.assertNotIn("REPEAT_ENTRY", main.CRITICAL_TRADE_BLOCKS)
        self.assertNotIn("PUT_CHASE", main.CRITICAL_TRADE_BLOCKS)
        self.assertNotIn("WEAK_PUT", main.CRITICAL_TRADE_BLOCKS)
        self.assertNotIn("DOJI_FILTER", main.RECOVERY_NON_RELAXABLE_TRADE_BLOCKS)
        self.assertFalse(signal_engine.WEAK_PUT_HARD_BLOCK)
        self.assertFalse(signal_engine.CALL_CHASE_HARD_BLOCK)


class ClassifyNoOpportunityReasonTests(unittest.TestCase):
    """PAYOUT_UNAVAILABLE + filtro de qualidade → NO_PATTERN_FOUND, não ACTIVE_CLOSED."""

    def test_strategy_blocks_win_over_payout_unavailable(self) -> None:
        reason = main.classify_no_opportunity_reason(
            {"PAYOUT_UNAVAILABLE", "TREND_CLEAR", "SIDEWAYS_FILTER", "WICK_REJECTION"}
        )
        self.assertEqual(reason, "NO_PATTERN_FOUND")

    def test_pure_active_closed_stays_operational(self) -> None:
        self.assertEqual(
            main.classify_no_opportunity_reason({"ACTIVE_CLOSED"}),
            "ACTIVE_CLOSED",
        )
        self.assertEqual(
            main.classify_no_opportunity_reason({"PAYOUT_UNAVAILABLE"}),
            "ACTIVE_CLOSED",
        )


class AnalyzeSignalIntegrationTests(unittest.TestCase):
    """analyze_signal propaga last_3_direction e respeita os novos hard blocks."""

    def test_signal_exposes_last_3_direction(self) -> None:
        signal = analyze_signal(
            "GBPUSD-OTC",
            make_candles(100),
            timeframe="M1",
            strategy_mode="conservative",
            payout=88,
        )
        self.assertIn("last_3_direction", signal)
        self.assertEqual(signal["last_3_direction"], signal["metrics"]["last_3_direction"])


if __name__ == "__main__":
    unittest.main()
