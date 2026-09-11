"""Testes da análise contínua do mercado (por vela, não por ciclo 5/15/45)."""

from __future__ import annotations

import unittest
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from backend import main
from backend.auto_trader import STATUS_WAITING_NEXT_CYCLE, utc_now
from backend.signal_engine import (
    cycle_minutes_for_timeframe,
    minimum_operations_per_hour,
    seconds_until_next_analysis,
)


def make_signal(symbol: str = "EURUSD-OTC", direction: str = "CALL", score: int = 95) -> dict:
    return {
        "symbol": symbol,
        "signal": direction,
        "direction": direction,
        "confidence": score,
        "payout": 90,
        "trend": "UP" if direction == "CALL" else "DOWN",
        "strength": 80,
        "strategy_score": score,
        "trade_allowed": True,
        "is_open": True,
        "price_action_setup": "CONTINUATION",
        "blocked_filters": [],
        "approved_filters": ["PRICE_ACTION_SETUP", "MIN_CONFIDENCE", "MIN_PAYOUT"],
    }


class ContinuousCadenceHelpersTests(unittest.TestCase):
    def test_cycle_minutes_match_candle_duration(self) -> None:
        self.assertEqual(cycle_minutes_for_timeframe("M1"), 1)
        self.assertEqual(cycle_minutes_for_timeframe("M5"), 5)
        self.assertEqual(cycle_minutes_for_timeframe("M15"), 15)
        self.assertEqual(cycle_minutes_for_timeframe("m5"), 5)
        self.assertEqual(cycle_minutes_for_timeframe(None), 1)

    def test_hourly_ops_target_reflects_continuous_monitoring(self) -> None:
        self.assertGreaterEqual(minimum_operations_per_hour("M1"), 10)
        self.assertGreaterEqual(minimum_operations_per_hour("M5"), 4)
        self.assertGreaterEqual(minimum_operations_per_hour("M15"), 2)

    def test_next_analysis_waits_for_next_candle_when_inside_window(self) -> None:
        # Segundo 10 da vela M1: já dentro da janela 5–20 → força próxima vela.
        wait = seconds_until_next_analysis("M1", 10.0, force_next_candle=True)
        self.assertGreater(wait, 40)
        self.assertLessEqual(wait, 60)

    def test_next_analysis_opens_soon_before_window(self) -> None:
        wait = seconds_until_next_analysis("M1", 2.0, force_next_candle=False)
        self.assertEqual(wait, 3)

    def test_next_analysis_after_window_goes_to_next_candle(self) -> None:
        wait = seconds_until_next_analysis("M1", 25.0, force_next_candle=False)
        self.assertEqual(wait, 40)

    def test_m5_and_m15_align_to_candle_not_multiples(self) -> None:
        wait_m5 = seconds_until_next_analysis("M5", 100.0, force_next_candle=True)
        wait_m15 = seconds_until_next_analysis("M15", 400.0, force_next_candle=True)
        self.assertLessEqual(wait_m5, 300)
        self.assertLessEqual(wait_m15, 900)
        self.assertGreater(wait_m5, 100)
        self.assertGreater(wait_m15, 400)

    def test_operational_backoff_is_short_not_full_legacy_cycle(self) -> None:
        wait = seconds_until_next_analysis("M15", operational_backoff=True)
        self.assertLessEqual(wait, 60)
        self.assertGreaterEqual(wait, 15)


class ContinuousAutoTraderSchedulingTests(unittest.TestCase):
    def test_start_schedules_immediate_analysis(self) -> None:
        trader = main.AutoTrader()
        state = trader.start("continuous-start")
        payload = state.to_dict()

        self.assertEqual(state.status, STATUS_WAITING_NEXT_CYCLE)
        self.assertEqual(state.cycle_minutes, 1)
        self.assertLessEqual(payload["seconds_until_next_cycle"], 2)

    def test_no_opportunity_retries_within_one_candle(self) -> None:
        trader = main.AutoTrader()
        trader.start("continuous-retry")
        state = trader.schedule_next_analysis_session(
            "continuous-retry",
            analysis_result="NO_OPPORTUNITY_FOUND",
            last_rejection_reason="NO_PATTERN_FOUND",
        )
        payload = state.to_dict()

        self.assertEqual(state.status, STATUS_WAITING_NEXT_CYCLE)
        self.assertLessEqual(payload["seconds_until_next_cycle"], 60)
        self.assertGreater(payload["seconds_until_next_cycle"], 0)

    def test_candles_unavailable_uses_operational_backoff(self) -> None:
        trader = main.AutoTrader()
        trader.start("continuous-ops-backoff")
        state = trader.schedule_next_analysis_session(
            "continuous-ops-backoff",
            analysis_result="NO_OPPORTUNITY_FOUND",
            last_rejection_reason="CANDLES_UNAVAILABLE",
        )
        payload = state.to_dict()

        self.assertLessEqual(payload["seconds_until_next_cycle"], 60)
        self.assertGreaterEqual(payload["seconds_until_next_cycle"], 15)

    def test_reset_after_result_does_not_wait_legacy_five_minutes(self) -> None:
        trader = main.AutoTrader()
        state = trader.start("continuous-reset")
        state.operation_in_progress = True
        state.last_trade = {
            "order_id": "c-1",
            "amount": 5,
            "result": "PENDING_RESULT",
        }
        trader.finish_trade("continuous-reset", "c-1", "WIN", 4.5)
        state.result_display_until = utc_now() - timedelta(seconds=1)
        state = trader.reset_cycle_after_result("continuous-reset")
        payload = state.to_dict()

        self.assertEqual(state.status, STATUS_WAITING_NEXT_CYCLE)
        self.assertLessEqual(payload["seconds_until_next_cycle"], 60)


class ContinuousEntryRulesPreservedTests(unittest.TestCase):
    def test_entry_windows_remain_at_candle_open_for_all_timeframes(self) -> None:
        # Apertada de 0-8s para 0-3s em 2026-08-30: a auditoria mediu que só
        # 33,6% das ordens pegavam o início da vela e metade saía entre 9 e
        # 20s. Ver tests/test_entry_candle_timing.py.
        for timeframe in ("M1", "M5", "M15"):
            start, end = main.ENTRY_WINDOWS[timeframe]
            self.assertEqual(start, 0)
            self.assertEqual(end, 3)

    def test_order_expiration_still_matches_timeframe(self) -> None:
        self.assertEqual(main.TIMEFRAME_SECONDS["M1"], 60)
        self.assertEqual(main.TIMEFRAME_SECONDS["M5"], 300)
        self.assertEqual(main.TIMEFRAME_SECONDS["M15"], 900)

    def test_get_entry_window_still_gates_buy_on_candle_open(self) -> None:
        open_window = main.get_entry_window("M1", 2.0)
        late_ok = main.get_entry_window("M1", 3.0)
        closed_window = main.get_entry_window("M1", 7.0)
        self.assertTrue(open_window["entry_window_open"])
        self.assertTrue(late_ok["entry_window_open"])
        self.assertFalse(closed_window["entry_window_open"])


class ContinuousCycleIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        main.auto_trader = main.AutoTrader()

    async def test_no_candidate_retries_within_candle_not_five_minutes(self) -> None:
        user_id = "continuous-no-candidate"
        state = main.auto_trader.start(user_id)
        state.next_cycle_at = utc_now() - timedelta(seconds=1)

        async def fake_bullex(method, path, call_user_id, json_body=None, params=None):
            if path == "/sessions/status":
                return 200, main.build_success(
                    {"connected": True, "active_mode": "REAL", "server_time": 300.0}
                )
            raise AssertionError(f"unexpected path: {path}")

        with (
            patch.object(main, "call_bullex_service", new=AsyncMock(side_effect=fake_bullex)),
            patch.object(main, "scan_local_signals", new=AsyncMock(return_value=(200, main.build_success([])))),
            patch.object(main, "select_fallback_candidate", new=AsyncMock(return_value=None)),
            patch.object(main, "persist_robot"),
        ):
            status_code, payload = await main.execute_robot_cycle(user_id)

        data = payload["data"]
        self.assertEqual(status_code, 200)
        self.assertEqual(data["status"], STATUS_WAITING_NEXT_CYCLE)
        self.assertLessEqual(data["seconds_until_next_cycle"], 60)
        self.assertIsNone(data["pending_signal"])

    async def test_pattern_found_still_waits_entry_window_before_buy(self) -> None:
        user_id = "continuous-wait-entry"
        state = main.auto_trader.start(user_id)
        state.next_cycle_at = utc_now() - timedelta(seconds=1)

        async def fake_bullex(method, path, call_user_id, json_body=None, params=None):
            if path == "/sessions/status":
                # Segundo 12 da vela M1: fora da janela 0–5s de compra.
                return 200, main.build_success(
                    {"connected": True, "active_mode": "REAL", "server_time": 72.0}
                )
            raise AssertionError(f"unexpected path: {path}")

        with (
            patch.object(main, "call_bullex_service", new=AsyncMock(side_effect=fake_bullex)),
            patch.object(
                main,
                "scan_local_signals",
                new=AsyncMock(return_value=(200, main.build_success([make_signal("GBPUSD-OTC", "PUT")]))),
            ),
            patch.object(main, "persist_robot"),
        ):
            status_code, payload = await main.execute_robot_cycle(user_id)

        data = payload["data"]
        self.assertEqual(status_code, 200)
        self.assertIn(data["status"], {"WAITING_NEXT_CANDLE_ENTRY", "WAITING_ENTRY", "SIGNAL_FOUND"})
        self.assertIsNotNone(data["pending_signal"])
        self.assertFalse(data.get("operation_in_progress"))
        self.assertGreater(data.get("seconds_until_entry_window") or 0, 0)


if __name__ == "__main__":
    unittest.main()
