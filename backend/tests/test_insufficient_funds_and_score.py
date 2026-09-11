"""Saldo insuficiente na compra REAL deve pausar o robô e avisar o painel.

Antes: a corretora respondia ``Insufficient funds`` e o ciclo só fazia
``reject_order`` + próxima vela — overlay seguia em “analisando” e o cliente
não via aviso de saldo. Ver ROBO_E_SUPORTE.md / OVERLAY_ROBO.md.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from backend import main
from backend.auto_trader import AutoTrader
from backend.status import STATUS_INSUFFICIENT_BALANCE

# Estes testes exercitam a MECÂNICA de compra com uma corretora falsa que não
# devolve velas. Desde 10/09/2026 a reconferência de nível no disparo é fechada
# (`SR_ZONE_SEM_VERIFICACAO`: sem velas, não opera), e toda compra daqui caía
# nela. A regra de S/R tem os testes dela (`test_sr_entry_recheck`); aqui a
# flag é fixada para medir só o que o módulo mede. Mesma lição da Vertex:
# teste que não é de estratégia fixa as flags, não herda do ambiente.
_FAIL_CLOSED_ORIGINAL = main.SR_ENTRY_RECHECK_FAIL_CLOSED


def setUpModule() -> None:
    main.SR_ENTRY_RECHECK_FAIL_CLOSED = False


def tearDownModule() -> None:
    main.SR_ENTRY_RECHECK_FAIL_CLOSED = _FAIL_CLOSED_ORIGINAL


SERVER_TIME_M1_OPEN = 60.0


class InsufficientFundsBuyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.old_trader = main.auto_trader
        main.auto_trader = AutoTrader()
        main.session_response_cache.clear()
        main.active_users.clear()
        main.robot_tasks.clear()

    def tearDown(self) -> None:
        main.auto_trader = self.old_trader
        main.session_response_cache.clear()
        main.active_users.clear()
        main.robot_tasks.clear()

    def test_is_insufficient_funds_error_detects_broker_message(self) -> None:
        self.assertTrue(
            main.is_insufficient_funds_error(
                "falha ao criar ordem real: Insufficient funds for this transaction."
            )
        )
        self.assertTrue(main.is_insufficient_funds_error("INSUFFICIENT_FUNDS"))
        self.assertTrue(main.is_insufficient_funds_error("Saldo insuficiente para a entrada"))
        self.assertFalse(
            main.is_insufficient_funds_error(
                "Cannot purchase an option (the asset is not available at the moment)."
            )
        )

    def test_readable_order_error_maps_insufficient_funds(self) -> None:
        message = main.readable_order_error(
            "falha ao criar ordem real: Insufficient funds for this transaction."
        )
        self.assertIn("saldo", message.lower())
        self.assertNotIn("indisponivel", message.lower())

    async def test_buy_insufficient_funds_stops_robot_with_status(self) -> None:
        user_id = "user-insufficient-funds"
        state = main.auto_trader.start(user_id)
        state.account_mode = "REAL"
        state.allow_real = True
        state.confirm_real = True
        state.connected = True
        state.active_mode = "REAL"
        state.entry_value = 5.0
        main.auto_trader.set_pending_signal(
            user_id,
            {
                "symbol": "EURUSD-OTC",
                "signal": "CALL",
                "direction": "CALL",
                "confidence": 94,
                "payout": 90,
                "strategy_score": 94,
                "trade_allowed": True,
            },
        )
        main.mark_user_active(user_id)

        async def fake_bullex(method, path, call_user_id, json_body=None, params=None, **_kwargs):
            if path == "/sessions/status":
                return 200, main.build_success(
                    {
                        "connected": True,
                        "active_mode": "REAL",
                        "mode": "REAL",
                        "server_time": SERVER_TIME_M1_OPEN,
                        # Sem balance — gap histórico: só entry>balance parava o robô.
                        "balance": None,
                        "currency": "BRL",
                    }
                )
            if path == "/orders/buy-real":
                return 409, main.build_error(
                    "falha ao criar ordem real: Insufficient funds for this transaction."
                )
            raise AssertionError(f"unexpected path: {path}")

        with (
            patch.object(main, "call_bullex_service", side_effect=fake_bullex),
            patch.object(main, "persist_robot", return_value=None),
            patch.object(main, "stop_robot_worker", new=AsyncMock()) as stop_worker,
            patch.object(main.trade_result_monitor, "start", return_value=True),
        ):
            status, payload = await main.execute_robot_cycle(user_id)

        self.assertEqual(payload["data"]["status"], STATUS_INSUFFICIENT_BALANCE)
        self.assertFalse(payload["data"]["enabled"])
        detail = (
            payload["data"].get("status_message")
            or payload["data"].get("operation_message")
            or payload["data"].get("last_order_error")
            or ""
        )
        self.assertIn("saldo", detail.lower())
        stop_worker.assert_awaited()
        self.assertIn(status, {200, 402, 409})


class PersistedScoreRehydrateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.old_trader = main.auto_trader
        main.auto_trader = AutoTrader()

    def tearDown(self) -> None:
        main.auto_trader = self.old_trader

    def test_blank_memory_score_rehydrates_from_persistence(self) -> None:
        user_id = "user-score-blank"
        state = main.auto_trader.get(user_id)
        state.wins = 0
        state.losses = 0
        state.profit = 0.0

        class FakePersistence:
            def load_state(self, uid: str):
                assert uid == user_id
                return {"wins": 7, "losses": 3, "profit": 12.5}

        old = main.robot_persistence
        main.robot_persistence = FakePersistence()
        try:
            changed = main.rehydrate_score_from_persistence_if_blank(user_id)
        finally:
            main.robot_persistence = old

        self.assertTrue(changed)
        self.assertEqual(state.wins, 7)
        self.assertEqual(state.losses, 3)
        self.assertEqual(state.profit, 12.5)

    def test_live_score_is_not_overwritten(self) -> None:
        user_id = "user-score-live"
        state = main.auto_trader.get(user_id)
        state.wins = 4
        state.losses = 1
        state.profit = 8.0

        class FakePersistence:
            def load_state(self, uid: str):
                return {"wins": 99, "losses": 99, "profit": -999.0}

        old = main.robot_persistence
        main.robot_persistence = FakePersistence()
        try:
            changed = main.rehydrate_score_from_persistence_if_blank(user_id)
        finally:
            main.robot_persistence = old

        self.assertFalse(changed)
        self.assertEqual(state.wins, 4)
        self.assertEqual(state.losses, 1)

    def test_intentional_reset_score_skips_rehydrate(self) -> None:
        """Após Reiniciar placar, não reidratar wins/losses antigos da DB."""
        from backend.auto_trader import utc_now

        user_id = "user-score-after-reset"
        state = main.auto_trader.get(user_id)
        state.wins = 0
        state.losses = 0
        state.profit = 0.0
        state.stop_reset_at = utc_now()

        class FakePersistence:
            def load_state(self, uid: str):
                return {"wins": 9, "losses": 2, "profit": 40.0}

        old = main.robot_persistence
        main.robot_persistence = FakePersistence()
        try:
            changed = main.rehydrate_score_from_persistence_if_blank(user_id)
        finally:
            main.robot_persistence = old

        self.assertFalse(changed)
        self.assertEqual(state.wins, 0)
        self.assertEqual(state.losses, 0)
        self.assertEqual(state.profit, 0.0)


if __name__ == "__main__":
    unittest.main()
