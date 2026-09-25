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
        state.wins, state.losses, state.profit = 6, 1, 42.5
        trader.record_trade("u-live", pending_trade("live-loss"))

        finalized, state = trader.finish_trade("u-live", "live-loss", "LOSS", -10.0)

        self.assertTrue(finalized)
        self.assertEqual((state.wins, state.losses), (6, 1))
        self.assertEqual(state.profit, 42.5)
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
        self.assertEqual(state.profit, 0.0)
        self.assertEqual(trader.history("u-live-toggle")["trades"], [])

    def test_win_do_live_permanece_no_placar_e_historico(self) -> None:
        trader = AutoTrader()
        state = trader.start("u-live-win")
        state.live_demo = True
        trader.record_trade("u-live-win", pending_trade("live-win"))

        trader.finish_trade("u-live-win", "live-win", "WIN", 8.7)

        self.assertEqual((state.wins, state.losses), (1, 0))
        self.assertEqual(state.profit, 8.7)
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
        self.assertEqual(state.profit, -10.0)
        self.assertEqual(
            [trade["result"] for trade in trader.history("u-normal")["trades"]],
            ["LOSS"],
        )

    def test_loss_atrasado_no_live_nao_reduz_o_saldo_nem_entra_no_historico(self) -> None:
        trader = AutoTrader()
        state = trader.start("u-live-late")
        state.live_demo = True
        state.profit = 42.5
        trade = pending_trade("late-live-loss")
        trader.record_trade("u-live-late", trade)
        # O espelho técnico é gravado após a abertura e já carrega a marca
        # congelada do LIVE; é esse objeto que o caminho de resultado atrasado
        # recebe da persistência.
        trade = dict(state.last_trade)
        trader.reset_cycle_after_result(user_id="u-live-late")

        fechado = trader.count_late_result("u-live-late", trade, "LOSS", -10.0)

        self.assertIsNotNone(fechado)
        self.assertEqual((state.wins, state.losses, state.profit), (0, 0, 42.5))
        self.assertEqual(trader.history("u-live-late")["trades"], [])

    def test_gale_live_soma_apenas_o_valor_do_win_final(self) -> None:
        trader = AutoTrader()
        state = trader.start("u-live-gale")
        state.enabled = True
        state.live_demo = True
        state.martingale_enabled = True
        state.martingale_steps = 1
        state.wins, state.losses, state.profit = 6, 1, 42.5

        trader.record_trade("u-live-gale", pending_trade("live-gale-loss"))
        finalized, state = trader.finish_trade("u-live-gale", "live-gale-loss", "LOSS", -10.0)
        self.assertFalse(finalized)
        self.assertEqual((state.wins, state.losses, state.profit), (6, 1, 42.5))
        self.assertEqual(trader.history("u-live-gale")["trades"], [])

        gale = {
            **pending_trade("live-gale-win"),
            "amount": 20.0,
            "is_gale": True,
            "gale_step": 1,
            "parent_order_id": "live-gale-loss",
        }
        trader.record_trade("u-live-gale", gale)
        finalized, state = trader.finish_trade("u-live-gale", "live-gale-win", "WIN", 17.4)

        self.assertTrue(finalized)
        self.assertEqual((state.wins, state.losses, state.profit), (7, 1, 59.9))
        self.assertEqual(
            [trade["result"] for trade in trader.history("u-live-gale")["trades"]],
            ["WIN"],
        )

    def test_gale_live_abandonado_nao_debita_o_saldo_do_placar(self) -> None:
        trader = AutoTrader()
        state = trader.start("u-live-gale-abandonado")
        state.enabled = True
        state.live_demo = True
        state.martingale_enabled = True
        state.martingale_steps = 1
        state.wins, state.losses, state.profit = 6, 1, 42.5

        trader.record_trade("u-live-gale-abandonado", pending_trade("live-gale-abandonado"))
        finalized, state = trader.finish_trade(
            "u-live-gale-abandonado", "live-gale-abandonado", "LOSS", -10.0
        )
        self.assertFalse(finalized)

        fechado = trader.close_abandoned_gale("u-live-gale-abandonado")

        self.assertIsNotNone(fechado)
        self.assertEqual((state.wins, state.losses, state.profit), (6, 1, 42.5))
        self.assertEqual(trader.history("u-live-gale-abandonado")["trades"], [])

    def test_replace_history_nao_reintroduz_loss_antigo_do_live(self) -> None:
        trader = AutoTrader()
        trader.replace_history(
            "u-live-replace-history",
            [
                {
                    **pending_trade("loss-persistido-antigo"),
                    "result": "LOSS",
                    "final_result": "LOSS",
                    "profit": -10.0,
                    "live_mode_active": True,
                }
            ],
        )

        self.assertEqual(trader.history("u-live-replace-history")["trades"], [])


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
