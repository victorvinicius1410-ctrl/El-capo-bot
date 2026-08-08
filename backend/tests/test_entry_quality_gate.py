"""Portão de qualidade da entrada: sinais reprovados pela estratégia não operam.

Regressão observada em produção (2026-07-21): candidatos com confiança alta,
mas reprovados pelos filtros (``trade_allowed=False`` com bloqueios críticos
como LEVEL_CONFLICT/DOJI_FILTER), eram executados mesmo assim porque
``candidate_meets_cycle_threshold`` só validava direção, payout e confiança.
"""

import unittest

from backend import main
from backend.auto_trader import AutoTrader


def build_candidate(**overrides):
    candidate = {
        "symbol": "USDCAD-OTC",
        "signal": "CALL",
        "direction": "CALL",
        "confidence": 85,
        "payout": 85.0,
        "strategy_score": 30,
        "trade_allowed": False,
        "blocked_filters": [
            "CANDLE_STRENGTH",
            "DOJI_FILTER",
            "PRICE_ACTION_SETUP",
            "SUPPORT_RESISTANCE",
            "LEVEL_CONFLICT",
            "LEVEL_REJECTION",
        ],
    }
    candidate.update(overrides)
    return candidate


class EntryQualityGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.trader = AutoTrader()
        self.state = self.trader.start("user-quality-gate")
        self.state.min_confidence = 80
        self.state.min_payout = 80.0

    def test_blocked_candidate_never_meets_threshold(self) -> None:
        candidate = build_candidate()
        self.assertFalse(
            main.candidate_meets_cycle_threshold(
                candidate,
                self.state,
                minimum_confidence=int(self.state.min_confidence),
            )
        )

    def test_candidate_without_trade_allowed_flag_is_rejected(self) -> None:
        candidate = build_candidate(blocked_filters=[])
        candidate.pop("trade_allowed")
        self.assertFalse(
            main.candidate_meets_cycle_threshold(
                candidate,
                self.state,
                minimum_confidence=int(self.state.min_confidence),
            )
        )

    def test_candidate_with_critical_block_is_rejected_even_if_allowed_flag_true(self) -> None:
        candidate = build_candidate(
            trade_allowed=True,
            blocked_filters=["LEVEL_CONFLICT"],
        )
        self.assertFalse(
            main.candidate_meets_cycle_threshold(
                candidate,
                self.state,
                minimum_confidence=int(self.state.min_confidence),
            )
        )

    def test_approved_candidate_still_passes(self) -> None:
        candidate = build_candidate(
            trade_allowed=True,
            strategy_score=90,
            confidence=92,
            blocked_filters=[],
        )
        self.assertTrue(
            main.candidate_meets_cycle_threshold(
                candidate,
                self.state,
                minimum_confidence=int(self.state.min_confidence),
            )
        )

    def test_non_critical_penalty_does_not_block(self) -> None:
        candidate = build_candidate(
            trade_allowed=True,
            strategy_score=84,
            confidence=88,
            blocked_filters=["LAST_5_CONFIRMATION", "NO_ALTERNATING_LAST_3"],
        )
        self.assertTrue(
            main.candidate_meets_cycle_threshold(
                candidate,
                self.state,
                minimum_confidence=int(self.state.min_confidence),
            )
        )

    def test_trend_clear_is_now_critical_and_blocks(self) -> None:
        """TREND_CLEAR virou hard block em 2026-08-04 (WR 30% quando presente)."""
        candidate = build_candidate(
            trade_allowed=True,
            strategy_score=90,
            confidence=95,
            blocked_filters=["TREND_CLEAR"],
        )
        self.assertFalse(
            main.candidate_meets_cycle_threshold(
                candidate,
                self.state,
                minimum_confidence=int(self.state.min_confidence),
            )
        )

    def test_resolve_cycle_entry_returns_none_for_blocked_candidates(self) -> None:
        self.state.cycle_best_trade_candidate = build_candidate()
        self.state.cycle_best_candidate = build_candidate(confidence=78)
        self.assertIsNone(main.resolve_cycle_entry_candidate(self.state))

    def test_resolve_cycle_entry_keeps_approved_candidate(self) -> None:
        approved = build_candidate(
            trade_allowed=True,
            strategy_score=90,
            confidence=92,
            blocked_filters=[],
        )
        self.state.cycle_best_trade_candidate = approved
        self.state.cycle_best_candidate = approved
        resolved = main.resolve_cycle_entry_candidate(self.state)
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved["symbol"], "USDCAD-OTC")

    def test_closed_channel_reports_active_closed_not_low_quality(self) -> None:
        """Regressão 2026-07-31: canal fechado na compra virava 'baixa qualidade'."""
        candidate = build_candidate(
            symbol="USDJPY-OTC",
            trade_allowed=False,
            blocked_filters=["ACTIVE_CLOSED"],
            is_open=False,
            confidence=90,
            payout=90.0,
        )
        reason = main.resolve_entry_validation_reason(
            candidate,
            self.state,
            minimum_confidence=int(self.state.min_confidence),
        )
        self.assertEqual(reason, "ACTIVE_CLOSED")

    def test_approved_candidate_has_no_validation_reason(self) -> None:
        candidate = build_candidate(
            trade_allowed=True,
            strategy_score=90,
            confidence=92,
            blocked_filters=[],
            is_open=True,
        )
        self.assertIsNone(
            main.resolve_entry_validation_reason(
                candidate,
                self.state,
                minimum_confidence=int(self.state.min_confidence),
            )
        )

    def test_stale_candidate_reports_stale_market_data(self) -> None:
        candidate = build_candidate(
            trade_allowed=True,
            blocked_filters=[],
            is_open=True,
            from_cache=True,
            confidence=92,
            payout=90.0,
        )
        reason = main.resolve_entry_validation_reason(
            candidate,
            self.state,
            minimum_confidence=int(self.state.min_confidence),
        )
        self.assertEqual(reason, "STALE_MARKET_DATA")


if __name__ == "__main__":
    unittest.main()
