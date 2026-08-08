"""Backoff da reconciliação de TIMEOUT (evita martelar o _call_gate da
Bullex quando a ordem nunca resolve — ex.: sessão do usuário offline).

Sem este backoff, `reconcile_timeout_last_trade` era chamado em todo
`GET /robot/state` (poll a cada 2,5-8s) e, para ordens permanentemente
irrecuperáveis, gerava chamadas de rede infinitas a `/orders/{id}/result`.
Ver PERFORMANCE_SISTEMA.md ("CALL_GATE_TIMEOUT sob carga").
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from backend import main
from backend.auto_trader import AutoTrader, STATUS_PENDING_RESULT


class TimeoutReconcileBackoffTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.old_auto_trader = main.auto_trader
        main.auto_trader = AutoTrader()
        main._timeout_reconcile_backoff.clear()

    def tearDown(self) -> None:
        main.auto_trader = self.old_auto_trader
        main._timeout_reconcile_backoff.clear()

    def _seed_timeout_trade(self, user_id: str, order_id: str) -> None:
        main.auto_trader.start(user_id)
        main.auto_trader.record_trade(
            user_id,
            {
                "order_id": order_id,
                "active": "EURUSD-OTC",
                "direction": "CALL",
                "amount": 10.0,
                "result": STATUS_PENDING_RESULT,
            },
        )
        main.auto_trader.timeout_trade(user_id, order_id)

    async def test_second_call_within_window_skips_network_after_failure(self) -> None:
        user_id = "timeout-backoff-user"
        order_id = "999001"
        self._seed_timeout_trade(user_id, order_id)

        pending_payload = (200, {"ok": True, "data": {"result": "PENDING_RESULT", "profit": None}})
        with patch.object(main, "fetch_trade_result", AsyncMock(return_value=pending_payload)) as mocked:
            first = await main.reconcile_timeout_last_trade(user_id)
            second = await main.reconcile_timeout_last_trade(user_id)

        self.assertFalse(first)
        self.assertFalse(second)
        # A 2ª chamada, dentro da janela de 30s, não deve bater na rede de novo.
        mocked.assert_awaited_once()

    async def test_retries_again_after_backoff_window_expires(self) -> None:
        user_id = "timeout-backoff-user-2"
        order_id = "999002"
        self._seed_timeout_trade(user_id, order_id)

        pending_payload = (200, {"ok": True, "data": {"result": "PENDING_RESULT", "profit": None}})
        with patch.object(main, "fetch_trade_result", AsyncMock(return_value=pending_payload)) as mocked:
            await main.reconcile_timeout_last_trade(user_id)
            # Simula passagem do tempo além do TIMEOUT_RECONCILE_RETRY_SECONDS.
            key = (user_id, order_id)
            main._timeout_reconcile_backoff[key]["next_retry_at"] = 0.0
            await main.reconcile_timeout_last_trade(user_id)

        self.assertEqual(mocked.await_count, 2)

    async def test_gives_up_permanently_after_max_attempts(self) -> None:
        user_id = "timeout-backoff-user-3"
        order_id = "999003"
        self._seed_timeout_trade(user_id, order_id)
        key = (user_id, order_id)
        main._timeout_reconcile_backoff[key] = {
            "attempts": main.TIMEOUT_RECONCILE_MAX_ATTEMPTS - 1,
            "next_retry_at": 0.0,
        }

        pending_payload = (200, {"ok": True, "data": {"result": "PENDING_RESULT", "profit": None}})
        with patch.object(main, "fetch_trade_result", AsyncMock(return_value=pending_payload)) as mocked:
            await main.reconcile_timeout_last_trade(user_id)
            self.assertTrue(main._timeout_reconcile_backoff[key]["given_up"])
            # Depois de desistir, nunca mais tenta de novo (mesmo sem esperar).
            await main.reconcile_timeout_last_trade(user_id)

        mocked.assert_awaited_once()

    async def test_successful_reconciliation_clears_backoff_state(self) -> None:
        user_id = "timeout-backoff-user-4"
        order_id = "999004"
        self._seed_timeout_trade(user_id, order_id)
        key = (user_id, order_id)
        main._timeout_reconcile_backoff[key] = {"attempts": 3, "next_retry_at": 0.0}

        win_payload = (200, {"ok": True, "data": {"result": "win", "profit": 8.7}})
        with patch.object(main, "fetch_trade_result", AsyncMock(return_value=win_payload)):
            recovered = await main.reconcile_timeout_last_trade(user_id)

        self.assertTrue(recovered)
        self.assertNotIn(key, main._timeout_reconcile_backoff)
        state = main.auto_trader.get(user_id)
        self.assertEqual(state.last_trade["result"], "WIN")


if __name__ == "__main__":
    unittest.main()
