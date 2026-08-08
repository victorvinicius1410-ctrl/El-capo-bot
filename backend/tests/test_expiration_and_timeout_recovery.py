"""Expiração válida + recuperação de TIMEOUT falso no placar."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from backend import main
from backend.auto_trader import AutoTrader, STATUS_PENDING_RESULT
from backend.trade_result_monitor import TradeResultMonitor


class ExpirationAndTimeoutRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def test_stale_server_time_does_not_expire_before_sent_at(self) -> None:
        sent_at = datetime(2026, 7, 15, 18, 34, 56, tzinfo=timezone.utc)
        # server_time ~110s atrás (bug que gerava expires_at no passado)
        stale_ts = sent_at.timestamp() - 110
        expected, source = main.calculate_expected_expire_at(
            "M1",
            {},
            {"server_timestamp": stale_ts},
            sent_at,
        )
        self.assertGreater(expected, sent_at)
        self.assertIn(source, {"sent_at_realigned", "sent_at_minimum", "server_time_aligned"})
        self.assertGreaterEqual((expected - sent_at).total_seconds(), 60)

    def test_bullex_past_expiration_ignored(self) -> None:
        sent_at = datetime(2026, 7, 15, 18, 34, 56, tzinfo=timezone.utc)
        past = sent_at - timedelta(seconds=55)
        expected, source = main.calculate_expected_expire_at(
            "M1",
            {"expires_at": past.isoformat()},
            {"server_timestamp": sent_at.timestamp()},
            sent_at,
        )
        self.assertGreater(expected, sent_at)
        self.assertNotEqual(source, "expires_at")

    def test_finish_trade_recovers_timeout_into_loss_score(self) -> None:
        trader = AutoTrader()
        trader.start("user-timeout-recover")
        trader.record_trade(
            "user-timeout-recover",
            {
                "order_id": "555",
                "active": "EURGBP-OTC",
                "direction": "CALL",
                "amount": 100.0,
                "result": STATUS_PENDING_RESULT,
            },
        )
        timed_out, state = trader.timeout_trade("user-timeout-recover", "555")
        self.assertTrue(timed_out)
        self.assertEqual(state.wins, 0)
        self.assertEqual(state.losses, 0)
        self.assertEqual(state.last_trade["result"], "TIMEOUT")

        recovered, state = trader.finish_trade("user-timeout-recover", "555", "LOSS", -100.0)
        self.assertTrue(recovered)
        self.assertEqual(state.losses, 1)
        self.assertEqual(state.wins, 0)
        self.assertEqual(state.profit, -100.0)
        self.assertEqual(state.last_trade["result"], "LOSS")
        history = trader.history("user-timeout-recover")
        self.assertEqual(len(history["trades"]), 1)
        self.assertEqual(history["trades"][0]["result"], "LOSS")

    async def test_monitor_finishes_loss_even_with_stale_expiration(self) -> None:
        fetch = AsyncMock(
            return_value=(200, {"ok": True, "data": {"result": "loose", "profit": -100.0}})
        )
        finish = AsyncMock()
        timeout = AsyncMock()
        monitor = TradeResultMonitor(
            fetch_result=fetch,
            finish_trade=finish,
            timeout_trade=timeout,
            poll_seconds=0.01,
            timeout_seconds=5.0,
        )
        past = datetime.now(timezone.utc) - timedelta(seconds=40)
        await monitor._monitor("u1", "9", past.isoformat())
        finish.assert_awaited()
        timeout.assert_not_awaited()
        self.assertEqual(finish.await_args.args[2], "LOSS")


if __name__ == "__main__":
    unittest.main()
