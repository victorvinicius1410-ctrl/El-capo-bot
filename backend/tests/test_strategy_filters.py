import json
import unittest
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from backend import main
from backend.auto_trader import AutoTrader, utc_now


class StrategyFilterTests(unittest.TestCase):
    def setUp(self) -> None:
        main.auto_trader = AutoTrader()

    def test_sideways_market_blocks_trade(self) -> None:
        signal = {
            "symbol": "EURUSD-OTC",
            "signal": "CALL",
            "confidence": 95,
            "trend": "SIDEWAYS",
            "strength": 5,
            "body_ratio": 0.7,
            "upper_wick_ratio": 0.1,
            "lower_wick_ratio": 0.1,
            "price_action_setup": "CONTINUATION",
        }

        allowed, selected, _ = main.apply_strategy_guard(
            "user-trend",
            main.auto_trader.start("user-trend"),
            signal,
            payout=90,
        )

        self.assertFalse(allowed)
        self.assertFalse(selected["trade_allowed"])
        self.assertIn("TREND_CLEAR", selected["blocked_filters"])
        self.assertIn("SIDEWAYS_FILTER", selected["blocked_filters"])
        self.assertLess(selected["strategy_score"], selected["confidence"])

    def test_neutral_rsi_on_continuation_penalizes_score(self) -> None:
        """RSI 50–59 em CONTINUATION: soft (penaliza), não hard block."""
        signal = {
            "symbol": "GBPUSD-OTC",
            "signal": "CALL",
            "confidence": 95,
            "trend": "UP",
            "strength": 35,
            "ema9": 1.02,
            "ema21": 1.01,
            "rsi": 50,
            "body_ratio": 0.7,
            "upper_wick_ratio": 0.1,
            "lower_wick_ratio": 0.1,
            "atr_pct": 0.001,
            "directional_candles_5": 4,
            "alternating_last_3": False,
            "price_action_setup": "CONTINUATION",
            "last_3_direction": "UP",
        }

        allowed, selected, _ = main.apply_strategy_guard(
            "user-rsi",
            main.auto_trader.start("user-rsi"),
            signal,
            payout=90,
        )

        self.assertTrue(allowed)
        self.assertIn("CONTINUATION_DEAD_RSI", selected["blocked_filters"])
        self.assertIn("RSI_RANGE", selected["blocked_filters"])
        self.assertLess(selected["strategy_score"], selected["confidence"])
        self.assertNotIn("CONTINUATION_DEAD_RSI", main.CRITICAL_TRADE_BLOCKS)

    def test_wick_against_direction_penalizes_but_does_not_hard_block(self) -> None:
        """Pavio contrário só penaliza o score (2026-08-04) — TREND_CLEAR cobre lateral."""
        signal = {
            "symbol": "EURUSD-OTC",
            "signal": "CALL",
            "confidence": 95,
            "trend": "UP",
            "strength": 35,
            "ema9": 1.02,
            "ema21": 1.01,
            "rsi": 60,
            "body_ratio": 0.6,
            "upper_wick_ratio": 0.6,
            "lower_wick_ratio": 0.1,
            "atr_pct": 0.001,
            "directional_candles_5": 4,
            "alternating_last_3": False,
            "price_action_setup": "CONTINUATION",
            "last_3_direction": "UP",
        }

        allowed, selected, _ = main.apply_strategy_guard(
            "user-wick",
            main.auto_trader.start("user-wick"),
            signal,
            payout=90,
        )

        self.assertTrue(allowed)
        self.assertTrue(selected["trade_allowed"])
        self.assertIn("WICK_REJECTION", selected["blocked_filters"])
        self.assertNotIn("WICK_REJECTION", main.CRITICAL_TRADE_BLOCKS)
        self.assertLess(selected["strategy_score"], selected["confidence"])

    def test_support_resistance_conflict_blocks_trade(self) -> None:
        signal = {
            "symbol": "EURUSD-OTC",
            "signal": "CALL",
            "confidence": 95,
            "trend": "UP",
            "strength": 35,
            "ema9": 1.02,
            "ema21": 1.01,
            "rsi": 60,
            "body_ratio": 0.7,
            "upper_wick_ratio": 0.1,
            "lower_wick_ratio": 0.1,
            "atr_pct": 0.001,
            "directional_candles_5": 4,
            "alternating_last_3": False,
            "price_action_setup": "CONTINUATION",
            "level_conflict": True,
        }

        allowed, selected, reason = main.apply_strategy_guard(
            "user-level-conflict",
            main.auto_trader.start("user-level-conflict"),
            signal,
            payout=90,
        )

        self.assertFalse(allowed)
        self.assertEqual(reason, "LEVEL_CONFLICT")
        self.assertIn("LEVEL_CONFLICT", selected["blocked_filters"])

    def test_two_consecutive_losses_trigger_global_and_asset_cooldown(self) -> None:
        """2 LOSSes → ASSET_COOLDOWN + GLOBAL_LOSS_COOLDOWN (hard block)."""
        user_id = "user-cooldown"
        trader = main.auto_trader
        state = trader.start(user_id)
        for index in range(2):
            trader.record_trade(
                user_id,
                {
                    "order_id": f"loss-{index}",
                    "active": "EURUSD-OTC",
                    "direction": "CALL",
                    "amount": 2,
                    "sent_at": (utc_now() - timedelta(minutes=1)).isoformat(),
                },
            )
            trader.finish_trade(user_id, f"loss-{index}", "LOSS", -2)

        allowed, selected, reason = main.apply_strategy_guard(
            user_id,
            state,
            {
                "symbol": "EURUSD-OTC",
                "signal": "CALL",
                "confidence": 95,
                "trade_allowed": True,
                "blocked_filters": [],
                "approved_filters": [],
                "quality_score": 95,
                "trend": "UP",
                "strength": 35,
                "body_ratio": 0.7,
                "upper_wick_ratio": 0.1,
                "lower_wick_ratio": 0.1,
                "rsi": 62,
                "price_action_setup": "CONTINUATION",
                "last_3_direction": "UP",
            },
            payout=90,
        )

        self.assertFalse(allowed)
        self.assertIn("ASSET_COOLDOWN", selected["blocked_filters"])
        self.assertIn("GLOBAL_LOSS_COOLDOWN", selected["blocked_filters"])
        self.assertIn(reason, {"ASSET_COOLDOWN", "GLOBAL_LOSS_COOLDOWN"})
        self.assertLess(selected["strategy_score"], selected["confidence"])

    def test_daily_stop_loss_stops_robot(self) -> None:
        user_id = "user-daily-loss"
        trader = main.auto_trader
        state = trader.start(user_id)
        state.stop_loss = 4
        for index in range(2):
            trader.record_trade(
                user_id,
                {"order_id": f"daily-loss-{index}", "active": "EURUSD-OTC", "amount": 2},
            )
            trader.finish_trade(user_id, f"daily-loss-{index}", "LOSS", -2)

        self.assertEqual(main.daily_stop_reason(user_id, state), "STOP_LOSS_HIT")

    def test_daily_stop_win_stops_robot(self) -> None:
        user_id = "user-daily-win"
        trader = main.auto_trader
        state = trader.start(user_id)
        state.stop_win = 3
        trader.record_trade(
            user_id,
            {"order_id": "daily-win-loss-1", "active": "EURUSD-OTC", "amount": 2},
        )
        trader.finish_trade(user_id, "daily-win-loss-1", "LOSS", -2)
        trader.record_trade(
            user_id,
            {"order_id": "daily-win-1", "active": "EURUSD-OTC", "amount": 2},
        )
        trader.finish_trade(user_id, "daily-win-1", "WIN", 3)

        self.assertEqual(main.daily_stop_reason(user_id, state), "STOP_WIN_HIT")

    def test_daily_management_summary_uses_gross_profit_and_loss(self) -> None:
        user_id = "user-daily-management"
        trader = main.auto_trader
        state = trader.start(user_id)
        state.stop_win = 4
        state.stop_loss = 3
        trader.record_trade(
            user_id,
            {"order_id": "daily-management-loss", "active": "EURUSD-OTC", "amount": 3},
        )
        trader.finish_trade(user_id, "daily-management-loss", "LOSS", -3)
        trader.record_trade(
            user_id,
            {"order_id": "daily-management-win", "active": "EURUSD-OTC", "amount": 4},
        )
        trader.finish_trade(user_id, "daily-management-win", "WIN", 4)

        summary = main.build_management_summary(user_id, state)

        self.assertEqual(summary["gross_profit"], 4.0)
        self.assertEqual(summary["gross_loss"], 3.0)
        self.assertEqual(summary["net_profit"], 1.0)


if __name__ == "__main__":
    unittest.main()
