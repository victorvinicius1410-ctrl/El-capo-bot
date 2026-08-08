"""Testes da limpeza de 'Operação aberta' fantasma."""

from __future__ import annotations

import unittest
from datetime import timedelta
from types import SimpleNamespace

from backend import main
from backend.auto_trader import AutoTrader


class WaitingResultStaleTests(unittest.TestCase):
    def test_stale_when_operation_in_progress_past_ttl(self) -> None:
        """operation_in_progress=True não pode impedir a limpeza após o TTL."""
        state = SimpleNamespace(
            status=main.STATUS_WAITING_RESULT,
            operation_in_progress=True,
            result_waiting=True,
            last_trade={"order_id": "ord-1", "result": "PENDING_RESULT"},
            last_entry_at=main.utc_now() - timedelta(seconds=400),
            current_cycle_started_at=None,
            cycle_minutes=1,
        )
        self.assertTrue(main.waiting_result_stale(state))

    def test_not_stale_while_within_ttl(self) -> None:
        state = SimpleNamespace(
            status=main.STATUS_WAITING_RESULT,
            operation_in_progress=True,
            result_waiting=True,
            last_trade={"order_id": "ord-1", "result": "PENDING_RESULT"},
            last_entry_at=main.utc_now() - timedelta(seconds=30),
            current_cycle_started_at=None,
            cycle_minutes=1,
        )
        self.assertFalse(main.waiting_result_stale(state))

    def test_stale_when_trade_already_has_final_result(self) -> None:
        state = SimpleNamespace(
            status=main.STATUS_WAITING_RESULT,
            operation_in_progress=True,
            result_waiting=True,
            last_trade={"order_id": "ord-1", "result": "WIN"},
            last_entry_at=main.utc_now() - timedelta(seconds=10),
            current_cycle_started_at=None,
            cycle_minutes=1,
        )
        self.assertTrue(main.waiting_result_stale(state))

    def test_idle_state_is_not_stale(self) -> None:
        state = SimpleNamespace(
            status=main.STATUS_STOPPED,
            operation_in_progress=False,
            result_waiting=False,
            last_trade=None,
            last_entry_at=None,
            current_cycle_started_at=None,
            cycle_minutes=1,
        )
        self.assertFalse(main.waiting_result_stale(state))


class BackoffPanelPayloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.user_id = "backoff-panel-user"
        self.old_trader = main.auto_trader
        main.auto_trader = AutoTrader()
        main.session_response_cache.clear()
        main.active_users.clear()

    def tearDown(self) -> None:
        main.auto_trader = self.old_trader
        main.session_response_cache.clear()
        main.active_users.clear()

    def test_resolve_backoff_prefers_memory_account(self) -> None:
        state = main.auto_trader.get(self.user_id)
        state.connected = True
        state.active_mode = "REAL"
        payload = main.build_success(
            {
                "connected": True,
                "active_mode": "REAL",
                "mode": "REAL",
                "balance": 123.45,
                "currency": "BRL",
                "email": "ops@example.com",
            }
        )
        entry = main.BullexResponseCacheEntry(
            status_code=200,
            payload=payload,
            expires_at=main.utc_now() - timedelta(seconds=1),
        )
        cache = main.get_session_cache(self.user_id)
        cache.responses["/account"] = entry
        cache.last_successful_responses["/account"] = entry

        recovered = main.resolve_backoff_panel_payload(self.user_id, path="/account")
        self.assertIsNotNone(recovered)
        assert recovered is not None
        self.assertTrue(recovered["ok"])
        self.assertTrue(recovered["data"]["connected"])
        self.assertEqual(recovered["data"]["email"], "ops@example.com")
        self.assertEqual(recovered["data"]["balance"], 123.45)


if __name__ == "__main__":
    unittest.main()
