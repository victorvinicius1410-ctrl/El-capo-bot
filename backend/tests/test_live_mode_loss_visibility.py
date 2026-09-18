"""Contratos de placar e Histórico para perdas do Modo LIVE."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from backend import main
from backend.auto_trader import AutoTrader


def pending_trade(order_id: str) -> dict:
    return {
        "order_id": order_id,
        "active": "EURUSD-OTC",
        "direction": "CALL",
        "amount": 10.0,
        "result": "PENDING_RESULT",
        "expiration": "M1",
    }


class LiveModeLossVisibilityTests(unittest.TestCase):
    def test_loss_do_live_nao_entra_no_placar_nem_historico_em_memoria(self) -> None:
        trader = AutoTrader()
        state = trader.start("u-live")
        state.live_demo = True
        trader.record_trade("u-live", pending_trade("live-loss"))

        finalized, state = trader.finish_trade("u-live", "live-loss", "LOSS", -10.0)

        self.assertTrue(finalized)
        self.assertEqual((state.wins, state.losses), (0, 0))
        self.assertEqual(trader.history("u-live")["trades"], [])
        self.assertEqual(state.last_trade["result"], "LOSS")

    def test_loss_da_ordem_aberta_no_live_permanece_oculto_apos_desligar(self) -> None:
        trader = AutoTrader()
        state = trader.start("u-live-toggle")
        state.live_demo = True
        trader.record_trade("u-live-toggle", pending_trade("live-toggle-loss"))
        state.live_demo = False

        trader.finish_trade("u-live-toggle", "live-toggle-loss", "LOSS", -10.0)

        self.assertEqual((state.wins, state.losses), (0, 0))
        self.assertEqual(trader.history("u-live-toggle")["trades"], [])

    def test_win_do_live_permanece_no_placar_e_historico(self) -> None:
        trader = AutoTrader()
        state = trader.start("u-live-win")
        state.live_demo = True
        trader.record_trade("u-live-win", pending_trade("live-win"))

        trader.finish_trade("u-live-win", "live-win", "WIN", 8.7)

        self.assertEqual((state.wins, state.losses), (1, 0))
        self.assertEqual(
            [trade["result"] for trade in trader.history("u-live-win")["trades"]],
            ["WIN"],
        )

    def test_sem_live_loss_continua_normal(self) -> None:
        trader = AutoTrader()
        state = trader.start("u-normal")
        trader.record_trade("u-normal", pending_trade("normal-loss"))

        trader.finish_trade("u-normal", "normal-loss", "LOSS", -10.0)

        self.assertEqual((state.wins, state.losses), (0, 1))
        self.assertEqual(
            [trade["result"] for trade in trader.history("u-normal")["trades"]],
            ["LOSS"],
        )


class LiveModeHistoryPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.old_trader = main.auto_trader
        self.old_persistence = main.robot_persistence
        main.auto_trader = AutoTrader()
        self.persistence = MagicMock()
        main.robot_persistence = self.persistence
        self.persist = patch.object(main, "persist_robot", return_value=None)
        self.persist.start()

    async def asyncTearDown(self) -> None:
        self.persist.stop()
        main.auto_trader = self.old_trader
        main.robot_persistence = self.old_persistence

    async def test_loss_do_live_nao_e_gravado_no_historico_persistido(self) -> None:
        state = main.auto_trader.start("u-live-persist")
        state.live_demo = True
        main.auto_trader.record_trade("u-live-persist", pending_trade("persist-live-loss"))

        await main.finish_monitored_trade("u-live-persist", "persist-live-loss", "LOSS", -10.0)

        self.persistence.save_trade_history.assert_not_called()
        self.persistence.save_trade.assert_called_once()


if __name__ == "__main__":
    unittest.main()
