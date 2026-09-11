"""Ranking anti-loss + substitution: sobe WR sem cortar volume do ciclo.

Regras (2026-08-13):
- Setup WEAK perde o slot para CONTINUATION/REVERSAL/S-R no mesmo ciclo.
- CONTINUATION+PUT em EURGBP/AUDJPY entra no hard block tóxico.
- Em FREQUENCY_RECOVERY, PRICE_ACTION_SETUP continua penalizando o score
  (ainda pode liberar trade_allowed, mas não compete em igualdade).
- WEAK sozinho ainda pode operar (fallback de volume).
"""

from __future__ import annotations

import unittest
from unittest import mock

from backend import main, signal_engine
from backend.signal_engine import _apply_quality_filters
from tests.test_candle_analysis import make_candles
from tests.test_loss_pattern_blocks import _base_continuation_signal


def _candidate(**overrides) -> dict:
    """Candidato mínimo elegível para ranking/portão."""
    base = {
        "symbol": "GBPUSD-OTC",
        "direction": "CALL",
        "signal": "CALL",
        "strategy_score": 88,
        "score": 88,
        "confidence": 90,
        "payout": 87.0,
        "trade_allowed": True,
        "price_action_setup": "CONTINUATION",
        "blocked_filters": [],
        "timeframe": "M1",
    }
    base.update(overrides)
    return base


class CandidateQualityTierTests(unittest.TestCase):
    """Tier de qualidade: WEAK < setups estruturados."""

    def test_weak_tier_below_continuation(self) -> None:
        weak = _candidate(price_action_setup="WEAK", strategy_score=100, confidence=100)
        cont = _candidate(price_action_setup="CONTINUATION", strategy_score=80, confidence=80)
        self.assertLess(main.candidate_quality_tier(weak), main.candidate_quality_tier(cont))
        self.assertGreater(main.candidate_rank(cont), main.candidate_rank(weak))

    def test_substitution_prefers_continuation_over_weak(self) -> None:
        """Mesmo ciclo: WEAK top score bruto perde para CONTINUATION."""
        weak = _candidate(
            symbol="AUDJPY-OTC",
            direction="PUT",
            price_action_setup="WEAK",
            strategy_score=96,
            confidence=96,
            payout=88.0,
        )
        cont = _candidate(
            symbol="USDCHF-OTC",
            direction="CALL",
            price_action_setup="CONTINUATION",
            strategy_score=82,
            confidence=82,
            payout=87.0,
        )
        selected = max([weak, cont], key=main.candidate_rank)
        self.assertEqual(selected["symbol"], "USDCHF-OTC")
        self.assertEqual(selected["price_action_setup"], "CONTINUATION")

    def test_weak_alone_still_ranks_valid(self) -> None:
        """Sem substituto: WEAK permanece escolhível (não zera volume)."""
        weak = _candidate(price_action_setup="WEAK", strategy_score=85, confidence=96)
        self.assertEqual(main.candidate_quality_tier(weak), 0)
        self.assertGreater(main.candidate_rank(weak)[0], -1)

    def test_audusd_demoted_vs_strong_asset(self) -> None:
        aud = _candidate(
            symbol="AUDUSD-OTC",
            price_action_setup="CONTINUATION",
            strategy_score=90,
            confidence=90,
        )
        chf = _candidate(
            symbol="USDCHF-OTC",
            price_action_setup="CONTINUATION",
            strategy_score=88,
            confidence=88,
        )
        self.assertGreater(main.candidate_rank(chf), main.candidate_rank(aud))

    def test_setup_from_metrics_when_top_level_missing(self) -> None:
        candidate = _candidate(price_action_setup=None)
        candidate.pop("price_action_setup", None)
        candidate["metrics"] = {"price_action_setup": "WEAK"}
        self.assertEqual(main.candidate_quality_tier(candidate), 0)


class WeakContinuationPutExpansionTests(unittest.TestCase):
    """EURGBP/AUDJPY entram na lista tóxica CONTINUATION+PUT."""

    def test_eurgbp_continuation_put_blocked(self) -> None:
        signal = _base_continuation_signal(
            symbol="EURGBP-OTC",
            signal="PUT",
            direction="PUT",
            ema9=1.100,
            ema21=1.102,
            rsi=40.0,
            last_3_direction="DOWN",
            lower_wick_ratio=0.1,
        )
        result = _apply_quality_filters(signal, make_candles(40), "conservative", 88.0)
        self.assertIn("WEAK_CONTINUATION_PUT", result["blocked_filters"])
        self.assertFalse(result["trade_allowed"])

    def test_audjpy_continuation_put_blocked(self) -> None:
        signal = _base_continuation_signal(
            symbol="AUDJPY-OTC",
            signal="PUT",
            direction="PUT",
            ema9=112.0,
            ema21=112.2,
            rsi=38.0,
            last_3_direction="DOWN",
            lower_wick_ratio=0.1,
        )
        result = _apply_quality_filters(signal, make_candles(40), "conservative", 88.0)
        self.assertIn("WEAK_CONTINUATION_PUT", result["blocked_filters"])
        self.assertFalse(result["trade_allowed"])

    def test_assets_listed_in_frozenset(self) -> None:
        for symbol in ("EURGBP-OTC", "EURGBP", "AUDJPY-OTC", "AUDJPY"):
            self.assertTrue(signal_engine.is_weak_continuation_put_asset(symbol))


class RecoveryKeepsPriceActionScorePenaltyTests(unittest.TestCase):
    """Recovery estrito (2026-09-03): WEAK NÃO é mais liberado pelo recovery.

    A regra antiga liberava o hard block e mantinha só a penalidade no score.
    A reauditoria das 11.628 ops limpas mostrou que essa liberação respondia
    por 64,4% das ordens executadas, com 48,38% de acerto contra 50,60% das
    demais — a única diferença de todo o levantamento que sobreviveu ao
    holdout. Agora o recovery só dispensa ``TREND_CLEAR``.
    """

    def test_recovery_nao_libera_mais_weak(self) -> None:
        signal = _base_continuation_signal(
            price_action_setup="WEAK",
            trend="UP",
            strength=35,
            rsi=62.0,
            confidence=100,
            near_support_resistance=True,
            body_ratio=0.7,
            atr_pct=0.001,
        )
        empty_levels = {
            "near_support": False,
            "near_resistance": False,
            "support": None,
            "resistance": None,
        }
        with (
            mock.patch(
                "backend.signal_engine._support_resistance_context",
                return_value=empty_levels,
            ),
            mock.patch(
                "backend.signal_engine._has_level_conflict",
                return_value=False,
            ),
            mock.patch(
                "backend.signal_engine._level_rejection_confirmed",
                return_value=False,
            ),
        ):
            recovered = _apply_quality_filters(
                dict(signal),
                make_candles(40),
                "conservative",
                88.0,
                frequency_recovery=True,
            )
        if signal_engine.RECOVERY_STRICT:
            self.assertFalse(recovered["trade_allowed"])
            self.assertIn("PRICE_ACTION_SETUP", recovered["blocked_filters"])
            self.assertIn("PRICE_ACTION_SETUP", str(recovered["quality_reason"]))
        else:
            self.assertTrue(recovered["trade_allowed"])
            self.assertIn("PRICE_ACTION_SETUP", recovered["blocked_filters"])

    def test_price_action_fora_dos_soft_blocks_em_modo_estrito(self) -> None:
        if not signal_engine.RECOVERY_STRICT:
            self.skipTest("RECOVERY_STRICT desligado neste ambiente")
        self.assertNotIn("PRICE_ACTION_SETUP", signal_engine.FREQUENCY_RECOVERY_SOFT_BLOCKS)
        # A constante de penalidade continua existindo: ela volta a valer se
        # alguém religar o recovery permissivo com RECOVERY_STRICT=false.
        self.assertIn(
            "PRICE_ACTION_SETUP",
            signal_engine.FREQUENCY_RECOVERY_KEEP_SCORE_PENALTIES,
        )


class WeakPutHardBlockTests(unittest.TestCase):
    """Cortes 15/08 desligados: WEAK PUT não é barrado pela regra WEAK_PUT.

    O que cada teste afirma é que os cortes de 15/08 continuam off — não que a
    entrada seja aprovada. Desde o recovery estrito de 2026-09-03, WEAK cai no
    hard block ``PRICE_ACTION_SETUP``, então ``trade_allowed`` só é verdadeiro
    com ``RECOVERY_STRICT=false``.
    """

    def test_weak_put_blocked_without_recovery(self) -> None:
        signal = _base_continuation_signal(
            price_action_setup="WEAK",
            signal="PUT",
            direction="PUT",
            ema9=1.100,
            ema21=1.102,
            rsi=40.0,
            last_3_direction="DOWN",
            lower_wick_ratio=0.1,
        )
        result = _apply_quality_filters(signal, make_candles(40), "conservative", 88.0)
        self.assertNotIn("WEAK_PUT", result["blocked_filters"])
        self.assertFalse(signal_engine.WEAK_PUT_HARD_BLOCK)

    def test_weak_put_blocked_even_in_frequency_recovery(self) -> None:
        signal = _base_continuation_signal(
            price_action_setup="WEAK",
            signal="PUT",
            direction="PUT",
            trend="DOWN",
            ema9=1.100,
            ema21=1.102,
            rsi=40.0,
            last_3_direction="DOWN",
            lower_wick_ratio=0.1,
            confidence=100,
            near_support_resistance=True,
            body_ratio=0.7,
            atr_pct=0.001,
        )
        empty_levels = {
            "near_support": False,
            "near_resistance": False,
            "support": None,
            "resistance": None,
        }
        with (
            mock.patch(
                "backend.signal_engine._support_resistance_context",
                return_value=empty_levels,
            ),
            mock.patch(
                "backend.signal_engine._has_level_conflict",
                return_value=False,
            ),
            mock.patch(
                "backend.signal_engine._level_rejection_confirmed",
                return_value=False,
            ),
        ):
            recovered = _apply_quality_filters(
                dict(signal),
                make_candles(40),
                "conservative",
                88.0,
                frequency_recovery=True,
            )
        self.assertNotIn("WEAK_PUT", recovered["blocked_filters"])
        self.assertFalse(signal_engine.WEAK_PUT_HARD_BLOCK)
        self.assertEqual(
            bool(recovered["trade_allowed"]), not signal_engine.RECOVERY_STRICT
        )

    def test_weak_call_still_allowed_in_recovery(self) -> None:
        signal = _base_continuation_signal(
            price_action_setup="WEAK",
            trend="UP",
            strength=35,
            rsi=62.0,
            confidence=100,
            near_support_resistance=True,
            body_ratio=0.7,
            atr_pct=0.001,
        )
        empty_levels = {
            "near_support": False,
            "near_resistance": False,
            "support": None,
            "resistance": None,
        }
        with (
            mock.patch(
                "backend.signal_engine._support_resistance_context",
                return_value=empty_levels,
            ),
            mock.patch(
                "backend.signal_engine._has_level_conflict",
                return_value=False,
            ),
            mock.patch(
                "backend.signal_engine._level_rejection_confirmed",
                return_value=False,
            ),
        ):
            recovered = _apply_quality_filters(
                dict(signal),
                make_candles(40),
                "conservative",
                88.0,
                frequency_recovery=True,
            )
        self.assertNotIn("WEAK_PUT", recovered["blocked_filters"])
        self.assertEqual(
            bool(recovered["trade_allowed"]), not signal_engine.RECOVERY_STRICT
        )

    def test_cycle_gate_rejects_weak_put_even_if_trade_allowed_flag(self) -> None:
        state = mock.Mock(min_payout=80, timeframe="M1")
        candidate = _candidate(
            direction="PUT",
            signal="PUT",
            price_action_setup="WEAK",
            trade_allowed=True,
            strategy_score=90,
            confidence=90,
            payout=88.0,
            blocked_filters=[],
        )
        self.assertTrue(
            main.candidate_meets_cycle_threshold(
                candidate, state, minimum_confidence=80, user_id=None
            )
        )

    def test_cycle_gate_keeps_weak_call(self) -> None:
        state = mock.Mock(min_payout=80, timeframe="M1")
        candidate = _candidate(
            direction="CALL",
            signal="CALL",
            price_action_setup="WEAK",
            trade_allowed=True,
            strategy_score=90,
            confidence=90,
            payout=88.0,
            blocked_filters=[],
        )
        self.assertTrue(
            main.candidate_meets_cycle_threshold(
                candidate, state, minimum_confidence=80, user_id=None
            )
        )

    def test_weak_body_blocked_even_in_recovery(self) -> None:
        signal = _base_continuation_signal(
            price_action_setup="WEAK",
            body_ratio=0.30,
            confidence=100,
            near_support_resistance=True,
        )
        empty_levels = {
            "near_support": False,
            "near_resistance": False,
            "support": None,
            "resistance": None,
        }
        with (
            mock.patch(
                "backend.signal_engine._support_resistance_context",
                return_value=empty_levels,
            ),
            mock.patch(
                "backend.signal_engine._has_level_conflict",
                return_value=False,
            ),
            mock.patch(
                "backend.signal_engine._level_rejection_confirmed",
                return_value=False,
            ),
        ):
            recovered = _apply_quality_filters(
                dict(signal),
                make_candles(40),
                "conservative",
                88.0,
                frequency_recovery=True,
            )
        self.assertIn("CANDLE_STRENGTH", recovered["blocked_filters"])
        self.assertFalse(signal_engine.CANDLE_WEAK_HARD_BLOCK)
        self.assertEqual(
            bool(recovered["trade_allowed"]), not signal_engine.RECOVERY_STRICT
        )

    def test_doji_body_blocked_even_in_recovery(self) -> None:
        signal = _base_continuation_signal(
            price_action_setup="WEAK",
            body_ratio=0.08,
            confidence=100,
            near_support_resistance=True,
        )
        empty_levels = {
            "near_support": False,
            "near_resistance": False,
            "support": None,
            "resistance": None,
        }
        with (
            mock.patch(
                "backend.signal_engine._support_resistance_context",
                return_value=empty_levels,
            ),
            mock.patch(
                "backend.signal_engine._has_level_conflict",
                return_value=False,
            ),
            mock.patch(
                "backend.signal_engine._level_rejection_confirmed",
                return_value=False,
            ),
        ):
            recovered = _apply_quality_filters(
                dict(signal),
                make_candles(40),
                "conservative",
                88.0,
                frequency_recovery=True,
            )
        self.assertIn("DOJI_FILTER", recovered["blocked_filters"])
        self.assertFalse(signal_engine.DOJI_HARD_BLOCK)
        self.assertEqual(
            bool(recovered["trade_allowed"]), not signal_engine.RECOVERY_STRICT
        )

    def test_cycle_gate_rejects_weak_body(self) -> None:
        state = mock.Mock(min_payout=80, timeframe="M1")
        candidate = _candidate(
            direction="CALL",
            trade_allowed=True,
            strategy_score=90,
            payout=88.0,
            blocked_filters=[],
            body_ratio=0.30,
        )
        self.assertTrue(
            main.candidate_meets_cycle_threshold(
                candidate, state, minimum_confidence=80, user_id=None
            )
        )


class ChooseBetterCandidateSubstitutionTests(unittest.TestCase):
    def test_choose_better_swaps_weak_for_continuation(self) -> None:
        current = _candidate(
            symbol="EURGBP-OTC",
            price_action_setup="WEAK",
            strategy_score=100,
            confidence=100,
        )
        incoming = _candidate(
            symbol="EURUSD-OTC",
            price_action_setup="CONTINUATION",
            strategy_score=78,
            confidence=78,
        )
        chosen = main.choose_better_candidate(current, incoming)
        self.assertIsNotNone(chosen)
        assert chosen is not None
        self.assertEqual(chosen["symbol"], "EURUSD-OTC")


class CallNotStolenByPutTests(unittest.TestCase):
    """Ranking neutro: qualidade do setup, não lado CALL vs PUT."""

    def test_continuation_put_beats_weak_call_on_quality(self) -> None:
        put = _candidate(
            symbol="GBPUSD-OTC",
            direction="PUT",
            signal="PUT",
            price_action_setup="CONTINUATION",
            strategy_score=96,
            confidence=96,
            body_ratio=0.85,
            last_3_colors=["GREEN", "RED", "RED"],
        )
        call = _candidate(
            symbol="USDCHF-OTC",
            direction="CALL",
            signal="CALL",
            price_action_setup="WEAK",
            strategy_score=80,
            confidence=80,
            body_ratio=0.55,
        )
        chosen = main.pick_best_candidate([put, call])
        self.assertEqual(chosen["direction"], "PUT")
        self.assertEqual(chosen["symbol"], "GBPUSD-OTC")

    def test_put_selected_when_no_call(self) -> None:
        put = _candidate(
            symbol="GBPUSD-OTC",
            direction="PUT",
            signal="PUT",
            price_action_setup="CONTINUATION",
            strategy_score=88,
            body_ratio=0.72,
            last_3_colors=["GREEN", "RED", "RED"],
        )
        chosen = main.pick_best_candidate([put])
        self.assertEqual(chosen["direction"], "PUT")


class PutQualityBlockTests(unittest.TestCase):
    """Cortes PUT 15/08 desligados: chase/corpo/pavio não são hard block."""

    def _run(self, signal: dict) -> dict:
        empty_levels = {
            "near_support": False,
            "near_resistance": False,
            "support": None,
            "resistance": None,
        }
        with (
            mock.patch(
                "backend.signal_engine._support_resistance_context",
                return_value=empty_levels,
            ),
            mock.patch(
                "backend.signal_engine._has_level_conflict",
                return_value=False,
            ),
            mock.patch(
                "backend.signal_engine._level_rejection_confirmed",
                return_value=False,
            ),
        ):
            return _apply_quality_filters(
                dict(signal),
                make_candles(40),
                "conservative",
                88.0,
                frequency_recovery=True,
            )

    def test_three_red_continuation_put_blocked(self) -> None:
        signal = _base_continuation_signal(
            symbol="GBPUSD-OTC",
            signal="PUT",
            direction="PUT",
            ema9=1.100,
            ema21=1.102,
            rsi=42.0,
            body_ratio=0.80,
            lower_wick_ratio=0.05,
            last_3_direction="DOWN",
            last_3_colors=["RED", "RED", "RED"],
            trend="DOWN",
            near_support_resistance=True,
        )
        filtered = self._run(signal)
        self.assertNotIn("PUT_CHASE", filtered["blocked_filters"])
        self.assertTrue(filtered["trade_allowed"])
        self.assertFalse(signal_engine.PUT_CHASE_HARD_BLOCK)

    def test_green_red_red_put_allowed(self) -> None:
        signal = _base_continuation_signal(
            symbol="GBPUSD-OTC",
            signal="PUT",
            direction="PUT",
            ema9=1.100,
            ema21=1.102,
            rsi=42.0,
            body_ratio=0.80,
            lower_wick_ratio=0.05,
            last_3_direction="DOWN",
            last_3_colors=["GREEN", "RED", "RED"],
            trend="DOWN",
            near_support_resistance=True,
        )
        filtered = self._run(signal)
        self.assertNotIn("PUT_CHASE", filtered["blocked_filters"])
        self.assertTrue(filtered["trade_allowed"])

    def test_put_body_below_60_blocked(self) -> None:
        signal = _base_continuation_signal(
            symbol="GBPUSD-OTC",
            signal="PUT",
            direction="PUT",
            ema9=1.100,
            ema21=1.102,
            rsi=42.0,
            body_ratio=0.50,
            lower_wick_ratio=0.05,
            last_3_direction="DOWN",
            last_3_colors=["GREEN", "RED", "RED"],
            trend="DOWN",
            near_support_resistance=True,
        )
        filtered = self._run(signal)
        self.assertNotIn("PUT_BODY", filtered["blocked_filters"])
        self.assertTrue(filtered["trade_allowed"])

    def test_put_long_lower_wick_blocked(self) -> None:
        signal = _base_continuation_signal(
            symbol="GBPUSD-OTC",
            signal="PUT",
            direction="PUT",
            ema9=1.100,
            ema21=1.102,
            rsi=42.0,
            body_ratio=0.80,
            lower_wick_ratio=0.22,
            last_3_direction="DOWN",
            last_3_colors=["GREEN", "RED", "RED"],
            trend="DOWN",
            near_support_resistance=True,
        )
        filtered = self._run(signal)
        self.assertNotIn("PUT_WICK", filtered["blocked_filters"])
        self.assertTrue(filtered["trade_allowed"])

    def test_cycle_gate_rejects_chase_put(self) -> None:
        state = mock.Mock(min_payout=80, timeframe="M1")
        candidate = _candidate(
            direction="PUT",
            signal="PUT",
            price_action_setup="CONTINUATION",
            trade_allowed=True,
            strategy_score=90,
            payout=88.0,
            blocked_filters=[],
            body_ratio=0.80,
            last_3_colors=["RED", "RED", "RED"],
        )
        self.assertTrue(
            main.candidate_meets_cycle_threshold(
                candidate, state, minimum_confidence=80, user_id=None
            )
        )


class CallChaseAndRepeatEntryTests(unittest.TestCase):
    """CALL chase e REPEAT desligados (volta 13–14/08)."""

    def test_three_green_continuation_call_blocked(self) -> None:
        empty_levels = {
            "near_support": False,
            "near_resistance": False,
            "support": None,
            "resistance": None,
        }
        signal = _base_continuation_signal(
            last_3_colors=["GREEN", "GREEN", "GREEN"],
            last_3_direction="UP",
            body_ratio=0.85,
            near_support_resistance=True,
        )
        with (
            mock.patch(
                "backend.signal_engine._support_resistance_context",
                return_value=empty_levels,
            ),
            mock.patch(
                "backend.signal_engine._has_level_conflict",
                return_value=False,
            ),
            mock.patch(
                "backend.signal_engine._level_rejection_confirmed",
                return_value=False,
            ),
        ):
            filtered = _apply_quality_filters(
                dict(signal),
                make_candles(40),
                "conservative",
                88.0,
                frequency_recovery=True,
            )
        self.assertNotIn("CALL_CHASE", filtered["blocked_filters"])
        self.assertTrue(filtered["trade_allowed"])
        self.assertFalse(signal_engine.CALL_CHASE_HARD_BLOCK)

    def test_cycle_gate_rejects_call_chase(self) -> None:
        state = mock.Mock(min_payout=80, timeframe="M1")
        candidate = _candidate(
            direction="CALL",
            last_3_colors=["GREEN", "GREEN", "GREEN"],
            body_ratio=0.85,
        )
        self.assertTrue(
            main.candidate_meets_cycle_threshold(
                candidate, state, minimum_confidence=80, user_id=None
            )
        )

    def test_repeat_entry_cooldown_blocks_same_symbol(self) -> None:
        main.repeat_entry_cooldowns.clear()
        main.mark_repeat_entry_cooldown("user-repeat", "EURGBP-OTC", "M1")
        state = mock.Mock(min_payout=80, timeframe="M1")
        candidate = _candidate(symbol="EURGBP-OTC", body_ratio=0.85)
        self.assertTrue(
            main.candidate_meets_cycle_threshold(
                candidate, state, minimum_confidence=80, user_id="user-repeat"
            )
        )
        self.assertFalse(signal_engine.REPEAT_ENTRY_HARD_BLOCK)
        other = _candidate(symbol="AUDJPY-OTC", body_ratio=0.85)
        self.assertTrue(
            main.candidate_meets_cycle_threshold(
                other, state, minimum_confidence=80, user_id="user-repeat"
            )
        )


if __name__ == "__main__":
    unittest.main()
