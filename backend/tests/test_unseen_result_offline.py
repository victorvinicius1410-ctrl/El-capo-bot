"""Resultado WIN/LOSS com tela fechada deve permanecer no placar ao voltar."""

from __future__ import annotations

import unittest
from datetime import timedelta

from backend.auto_trader import AutoTrader, STATUS_WAITING_NEXT_CYCLE, STATUS_WIN, utc_now


class UnseenResultOfflineTests(unittest.TestCase):
    def test_finish_trade_marks_unseen_result(self) -> None:
        trader = AutoTrader()
        user_id = "user-unseen"
        state = trader.start(user_id)
        state.last_trade = {
            "order_id": "ord-1",
            "active": "EURUSD-OTC",
            "direction": "CALL",
            "amount": 50.0,
            "result": "PENDING_RESULT",
        }
        state.operation_in_progress = True

        finalized, state = trader.finish_trade(user_id, "ord-1", "WIN", 44.5)
        state.unseen_result = True
        state.result_client_seen_at = None

        self.assertTrue(finalized)
        self.assertTrue(state.unseen_result)
        self.assertEqual(state.wins, 1)
        self.assertEqual(state.last_trade["result"], "WIN")
        self.assertEqual(state.last_trade["profit"], 44.5)

    def test_to_dict_keeps_win_after_display_window_when_unseen(self) -> None:
        trader = AutoTrader()
        user_id = "user-unseen-display"
        state = trader.start(user_id)
        state.last_trade = {
            "order_id": "ord-2",
            "active": "GBPUSD-OTC",
            "direction": "PUT",
            "amount": 50.0,
            "result": "PENDING_RESULT",
        }
        state.operation_in_progress = True
        trader.finish_trade(user_id, "ord-2", "WIN", 42.5)

        state = trader.get(user_id)
        state.result_display_until = utc_now() - timedelta(seconds=1)
        state.status = STATUS_WAITING_NEXT_CYCLE
        state.unseen_result = True
        state.cycle_result = "WIN"

        payload = state.to_dict()

        self.assertTrue(payload["unseen_result"])
        self.assertEqual(payload["status"], STATUS_WIN)
        self.assertEqual(payload["cycle_result"], "WIN")
        self.assertEqual(payload["operation_message"], "WIN")
        self.assertIsNotNone(payload["last_trade"])
        self.assertEqual(payload["last_trade"]["profit"], 42.5)

    def test_acknowledge_clears_unseen_after_hold(self) -> None:
        trader = AutoTrader()
        user_id = "user-unseen-ack"
        trader.start(user_id)
        state = trader.get(user_id)
        state.unseen_result = True
        state.last_trade = {"order_id": "ord-3", "result": "LOSS", "profit": -50.0}

        first = trader.acknowledge_unseen_result(user_id, hold_seconds=8.0)
        self.assertTrue(first.unseen_result)
        self.assertIsNotNone(first.result_client_seen_at)

        state = trader.get(user_id)
        state.result_client_seen_at = utc_now() - timedelta(seconds=9)
        cleared = trader.acknowledge_unseen_result(user_id, hold_seconds=8.0)

        self.assertFalse(cleared.unseen_result)
        self.assertIsNone(cleared.result_client_seen_at)

    def test_to_dict_does_not_refresh_win_forever(self) -> None:
        trader = AutoTrader()
        user_id = "user-unseen-expired"
        state = trader.start(user_id)
        state.last_trade = {
            "order_id": "ord-4",
            "active": "EURUSD-OTC",
            "direction": "CALL",
            "amount": 50.0,
            "result": "PENDING_RESULT",
        }
        state.operation_in_progress = True
        trader.finish_trade(user_id, "ord-4", "LOSS", -50.0)

        state = trader.get(user_id)
        stale = (utc_now() - timedelta(seconds=61)).isoformat()
        state.last_trade["finished_at"] = stale
        state.result_received_at = utc_now() - timedelta(seconds=61)
        state.result_display_until = utc_now() - timedelta(seconds=1)
        state.status = STATUS_WAITING_NEXT_CYCLE
        state.unseen_result = True
        state.cycle_result = "LOSS"

        payload = state.to_dict()
        self.assertEqual(payload["status"], STATUS_WAITING_NEXT_CYCLE)
        self.assertNotEqual(payload["status"], "LOSS")


if __name__ == "__main__":
    unittest.main()
