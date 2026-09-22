"""Stop Win/Loss por valor (money) e por quantidade de operações."""

from __future__ import annotations

import unittest

from backend.auto_trader import (
    STATUS_STOP_LOSS_HIT,
    STATUS_STOP_WIN_HIT,
    AutoTrader,
    RobotConfigUpdate,
    RobotState,
    normalize_stop_mode,
    resolve_robot_stop_reason,
)


class NormalizeStopModeTests(unittest.TestCase):
    def test_defaults_to_money(self) -> None:
        self.assertEqual(normalize_stop_mode(None), "money")
        self.assertEqual(normalize_stop_mode(""), "money")
        self.assertEqual(normalize_stop_mode("valor"), "money")

    def test_accepts_operations_aliases(self) -> None:
        for raw in ("operations", "OPERATIONS", "ops", "count", "quantidade", "qtd"):
            self.assertEqual(normalize_stop_mode(raw), "operations")


class ResolveRobotStopReasonTests(unittest.TestCase):
    def test_money_mode_uses_net_result(self) -> None:
        state = RobotState(stop_win=50, stop_loss=30, stop_win_mode="money", stop_loss_mode="money")
        self.assertEqual(resolve_robot_stop_reason(state, net_profit=50), STATUS_STOP_WIN_HIT)
        self.assertEqual(resolve_robot_stop_reason(state, net_profit=-30), STATUS_STOP_LOSS_HIT)
        self.assertIsNone(resolve_robot_stop_reason(state, net_profit=-29.99))
        self.assertIsNone(resolve_robot_stop_reason(state, net_profit=49.99))

    def test_money_mode_ignores_wins_and_losses_that_se_anulam(self) -> None:
        """Caso real de 21/09/2026: Stop Loss R$20, perdas R$21, ganhos R$11,97.

        O dia fechou em -R$9,03 — longe do limite — e o robô parava assim
        mesmo, porque a conta somava só as ordens perdedoras.
        """
        state = RobotState(stop_win=15, stop_loss=20, stop_win_mode="money", stop_loss_mode="money")
        self.assertIsNone(resolve_robot_stop_reason(state, net_profit=11.97 - 21.0))
        # E continua parando quando o prejuízo líquido chega no limite.
        self.assertEqual(resolve_robot_stop_reason(state, net_profit=-20.0), STATUS_STOP_LOSS_HIT)

    def test_money_mode_net_profit_ignores_stop_offset(self) -> None:
        """`net_profit` já vem sem Shift+O: não pode descontar o offset de novo."""
        state = RobotState(stop_win=50, stop_loss=30, stop_win_mode="money", stop_loss_mode="money")
        state.stop_offset_profit = 500.0
        self.assertEqual(resolve_robot_stop_reason(state, net_profit=-30), STATUS_STOP_LOSS_HIT)

    def test_operations_mode_uses_wins_and_losses(self) -> None:
        state = RobotState(
            stop_win_mode="operations",
            stop_loss_mode="operations",
            stop_win_operations=3,
            stop_loss_operations=2,
            wins=3,
            losses=0,
        )
        self.assertEqual(resolve_robot_stop_reason(state), STATUS_STOP_WIN_HIT)

        state.wins = 1
        state.losses = 2
        self.assertEqual(resolve_robot_stop_reason(state), STATUS_STOP_LOSS_HIT)

        state.losses = 1
        self.assertIsNone(resolve_robot_stop_reason(state))

    def test_loss_priority_over_win(self) -> None:
        state = RobotState(
            stop_win_mode="operations",
            stop_loss_mode="operations",
            stop_win_operations=1,
            stop_loss_operations=1,
            wins=1,
            losses=1,
        )
        self.assertEqual(resolve_robot_stop_reason(state), STATUS_STOP_LOSS_HIT)

    def test_mixed_modes(self) -> None:
        state = RobotState(
            stop_win=100,
            stop_loss=30,
            stop_win_mode="money",
            stop_loss_mode="operations",
            stop_loss_operations=2,
            wins=0,
            losses=2,
            profit=5,
        )
        self.assertEqual(
            resolve_robot_stop_reason(state, net_profit=5),
            STATUS_STOP_LOSS_HIT,
        )


class RobotConfigStopModeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.trader = AutoTrader()

    def test_config_persists_stop_modes_and_operations(self) -> None:
        state = self.trader.update_config(
            "user-stop-modes",
            RobotConfigUpdate.model_validate(
                {
                    "stop_win": 80,
                    "stop_loss": 40,
                    "stop_win_mode": "operations",
                    "stop_loss_mode": "money",
                    "stop_win_operations": 7,
                    "stop_loss_operations": 4,
                }
            ),
        )
        self.assertEqual(state.stop_win_mode, "operations")
        self.assertEqual(state.stop_loss_mode, "money")
        self.assertEqual(state.stop_win_operations, 7)
        self.assertEqual(state.stop_loss_operations, 4)
        self.assertEqual(state.stop_win, 80)
        self.assertEqual(state.stop_loss, 40)

    def test_apply_result_pauses_on_operations_stop_win(self) -> None:
        user_id = "user-ops-win"
        state = self.trader.get(user_id)
        state.enabled = True
        state.stop_win_mode = "operations"
        state.stop_win_operations = 1
        state.stop_loss_mode = "money"
        state.stop_loss = 1000
        state.wins = 0
        state.losses = 0
        state.operation_in_progress = True
        state.last_trade = {
            "order_id": "ord-1",
            "result": "PENDING",
            "amount": 10,
            "profit": 0,
            "is_gale": False,
        }
        applied, updated = self.trader.finish_trade(user_id, "ord-1", "WIN", 8.0)
        self.assertTrue(applied)
        self.assertEqual(updated.status, STATUS_STOP_WIN_HIT)
        self.assertEqual(updated.wins, 1)

    def test_apply_result_pauses_on_operations_stop_loss(self) -> None:
        user_id = "user-ops-loss"
        state = self.trader.get(user_id)
        state.enabled = True
        state.stop_loss_mode = "operations"
        state.stop_loss_operations = 1
        state.stop_win_mode = "money"
        state.stop_win = 1000
        state.wins = 0
        state.losses = 0
        state.operation_in_progress = True
        state.last_trade = {
            "order_id": "ord-2",
            "result": "PENDING",
            "amount": 10,
            "profit": 0,
            "is_gale": False,
        }
        applied, updated = self.trader.finish_trade(user_id, "ord-2", "LOSS", -10.0)
        self.assertTrue(applied)
        self.assertEqual(updated.status, STATUS_STOP_LOSS_HIT)
        self.assertEqual(updated.losses, 1)


if __name__ == "__main__":
    unittest.main()
