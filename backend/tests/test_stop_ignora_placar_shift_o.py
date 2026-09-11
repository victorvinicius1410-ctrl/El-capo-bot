"""O stop win/loss conta só ordem real — placar do Shift+O é vitrine.

Em 10/09/2026 19:53:41 um "gerar placar" 8x2 +R$480 numa conta marketing
virou ``apply_score`` no robot-runtime e disparou ``STOP_WIN_HIT`` de verdade:
o robô desligou por um placar que nunca passou pela corretora. Pedido do dono:
"stop win está 3 win e no placar está 5 win gerados — não pode parar de pegar
operação por conta do placar".
"""

from __future__ import annotations

import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from backend import main
from backend import robot_runtime_main
from backend.auto_trader import (
    AutoTrader,
    RobotState,
    is_synthetic_trade,
    resolve_robot_stop_reason,
    set_display_score,
    utc_now,
)
from backend.status import STATUS_STOP_LOSS_HIT, STATUS_STOP_WIN_HIT
from tests.test_marketing_score_authority import ScoreAuthorityTestCase, finished_trade


UUID_ID = "834cd52c-950b-43b1-b906-a9ab81d6de0a"


def operations_state(wins_stop: int = 3, losses_stop: int = 5) -> RobotState:
    state = RobotState()
    state.stop_win_mode = "operations"
    state.stop_win_operations = wins_stop
    state.stop_loss_mode = "operations"
    state.stop_loss_operations = losses_stop
    return state


class SyntheticTradeTests(unittest.TestCase):
    def test_uuid_is_synthetic(self) -> None:
        self.assertTrue(is_synthetic_trade({"order_id": UUID_ID}))

    def test_broker_numeric_id_is_real(self) -> None:
        self.assertFalse(is_synthetic_trade({"order_id": "14250270143"}))

    def test_mirrored_live_trade_keeps_counting(self) -> None:
        # Operação real espelhada no painel marketing: id local sintético, mas
        # com o id da corretora guardado.
        self.assertFalse(
            is_synthetic_trade({"order_id": "synthetic-live-1a2b", "broker_order_id": "14250270143"})
        )

    def test_missing_id_counts_as_real(self) -> None:
        self.assertFalse(is_synthetic_trade({"order_id": None}))


class StopDiscountsShiftOScoreTests(unittest.TestCase):
    def test_owner_example_generated_wins_do_not_stop(self) -> None:
        # Stop em 3 wins, 1 win real, placar gerado mostra 5 wins.
        state = operations_state(wins_stop=3)
        state.wins = 1
        set_display_score(state, 5, 0, 500.0)
        self.assertEqual(state.wins, 5)
        self.assertIsNone(resolve_robot_stop_reason(state))

    def test_real_wins_still_reach_the_stop(self) -> None:
        state = operations_state(wins_stop=3)
        state.wins = 1
        set_display_score(state, 5, 0, 500.0)
        state.wins += 1  # 2 reais
        self.assertIsNone(resolve_robot_stop_reason(state))
        state.wins += 1  # 3 reais
        self.assertEqual(resolve_robot_stop_reason(state), STATUS_STOP_WIN_HIT)

    def test_incident_of_10_09_does_not_stop(self) -> None:
        # Sergio: stop por operações em 5; placar real 0x3 -R$600, gerado 8x2.
        state = operations_state(wins_stop=5, losses_stop=5)
        state.wins, state.losses, state.profit = 0, 3, -600.0
        set_display_score(state, 8, 2, 480.0)
        self.assertIsNone(resolve_robot_stop_reason(state, profit=state.profit))

    def test_generated_losses_do_not_trigger_stop_loss(self) -> None:
        state = operations_state(wins_stop=5, losses_stop=3)
        set_display_score(state, 0, 4, -400.0)
        self.assertIsNone(resolve_robot_stop_reason(state))

    def test_money_mode_ignores_generated_profit(self) -> None:
        state = RobotState()
        state.stop_win_mode = "money"
        state.stop_win = 100.0
        state.profit = 20.0
        set_display_score(state, 8, 2, 480.0)
        self.assertIsNone(resolve_robot_stop_reason(state, profit=state.profit))
        state.profit += 80.0  # +80 real → 100 real
        self.assertEqual(
            resolve_robot_stop_reason(state, profit=state.profit), STATUS_STOP_WIN_HIT
        )

    def test_hiding_a_real_loss_does_not_hide_it_from_the_stop(self) -> None:
        # Excluir do placar não devolve o dinheiro na corretora.
        state = operations_state(wins_stop=5, losses_stop=2)
        state.losses = 2
        set_display_score(state, 0, 1, 0.0)
        self.assertEqual(resolve_robot_stop_reason(state), STATUS_STOP_LOSS_HIT)

    def test_reset_score_clears_the_offset(self) -> None:
        trader = AutoTrader()
        state = trader.get("u1")
        set_display_score(state, 8, 2, 480.0)
        trader.reset_score("u1")
        state = trader.get("u1")
        self.assertEqual(
            (state.stop_offset_wins, state.stop_offset_losses, state.stop_offset_profit),
            (0, 0, 0.0),
        )

    def test_offset_survives_persistence_round_trip(self) -> None:
        trader = AutoTrader()
        state = trader.get("u1")
        set_display_score(state, 8, 2, 480.0)
        payload = state.to_dict()
        restored = AutoTrader().restore("u1", payload, [])
        self.assertEqual(restored.stop_offset_wins, 8)
        self.assertEqual(restored.stop_offset_losses, 2)


class HistoryBasedTotalsTests(unittest.TestCase):
    def test_management_totals_skip_synthetic_rows(self) -> None:
        trader = AutoTrader()
        trader.get("u1")
        now = utc_now()
        trader._histories["u1"] = [
            finished_trade("14250270143", "WIN", 80.0, finished_at=now),
            finished_trade(UUID_ID, "WIN", 480.0, finished_at=now),
        ]
        totals = trader.management_totals("u1")
        self.assertEqual(totals["gross_profit"], 80.0)

    def test_restore_sets_offset_from_synthetic_rows(self) -> None:
        now = utc_now()
        trades = [
            finished_trade("14250270143", "WIN", 80.0, finished_at=now - timedelta(minutes=3)),
            finished_trade(UUID_ID, "WIN", 90.0, finished_at=now - timedelta(minutes=2)),
            finished_trade("8ebb48c5-1a12-4030-80ef-e4221a8ccc26", "LOSS", -100.0, finished_at=now),
        ]
        state = AutoTrader().restore("u1", {"stop_win_mode": "operations", "stop_win_operations": 2}, trades)
        self.assertEqual((state.wins, state.losses), (2, 1))
        self.assertEqual((state.stop_offset_wins, state.stop_offset_losses), (1, 1))
        self.assertIsNone(resolve_robot_stop_reason(state))


class RuntimeApplyScoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_apply_score_from_shift_o_does_not_stop_the_robot(self) -> None:
        trader = AutoTrader()
        gateway = SimpleNamespace(
            auto_trader=trader,
            mark_session_score_authority=MagicMock(),
            persist_robot=MagicMock(),
            publish_robot_control_snapshot=MagicMock(),
        )
        state = trader.get("u1")
        # Configuração real do Sergio em 10/09: stop por operações, 5 e 5.
        state.stop_win_mode = "operations"
        state.stop_win_operations = 5
        state.stop_loss_mode = "operations"
        state.stop_loss_operations = 5
        state.wins, state.losses, state.profit = 0, 3, -600.0
        await robot_runtime_main._handle_command(
            gateway,
            {"user_id": "u1", "action": "apply_score", "wins": 8, "losses": 2, "profit": 480.0},
        )
        state = trader.get("u1")
        self.assertEqual((state.wins, state.losses, state.profit), (8, 2, 480.0))
        self.assertIsNone(resolve_robot_stop_reason(state, profit=state.profit))


class GatewayShiftOTests(ScoreAuthorityTestCase):
    user_id = "marketing-stop-offset-user"

    async def test_generate_scoreboard_goes_to_offset(self) -> None:
        state = self.set_score(1, 0, 170.0)
        state.stop_win_mode = "operations"
        state.stop_win_operations = 3
        with (
            patch.object(main, "persist_robot", return_value=None),
            patch.object(main, "publish_marketing_score_to_overlay", return_value=None),
        ):
            main.sync_marketing_display_to_robot(
                self.user_id, [], {"wins": 5, "losses": 0, "profit": 500.0}
            )
        state = main.auto_trader.get(self.user_id)
        self.assertEqual(state.wins, 5)
        self.assertEqual(state.stop_offset_wins, 4)
        self.assertIsNone(resolve_robot_stop_reason(state))

    async def test_accumulate_goes_to_offset(self) -> None:
        state = self.set_score(2, 0, 170.0)
        with (
            patch.object(main, "persist_robot", return_value=None),
            patch.object(main, "publish_marketing_score_to_overlay", return_value=None),
        ):
            main.sync_marketing_display_to_robot(
                self.user_id, [], {"wins": 3, "losses": 1, "profit": 200.0, "accumulate": True}
            )
        state = main.auto_trader.get(self.user_id)
        self.assertEqual((state.wins, state.losses), (5, 1))
        self.assertEqual((state.stop_offset_wins, state.stop_offset_losses), (3, 1))

    async def test_deleting_a_win_keeps_real_count(self) -> None:
        self.set_score(3, 0, 26.1)
        self.delete_win()
        state = main.auto_trader.get(self.user_id)
        self.assertEqual(state.wins, 2)
        self.assertEqual(state.stop_offset_wins, -1)
        state.stop_win_mode = "operations"
        state.stop_win_operations = 3
        self.assertEqual(resolve_robot_stop_reason(state), STATUS_STOP_WIN_HIT)


if __name__ == "__main__":
    unittest.main()
