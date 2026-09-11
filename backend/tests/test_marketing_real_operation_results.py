"""Operações reais na conta marketing devem atualizar o placar com WIN/LOSS da corretora."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from backend import main
from backend.auto_trader import AutoTrader
from backend.robot_persistence import SQLiteRobotPersistence


# Usuários fixos usados pelos testes desta suíte (estado no Redis é global).
MARKETING_FIXTURE_USER_IDS = (
    "marketing-live-loss",
    "marketing-live-win",
    "marketing-scoreboard",
    "marketing-delete-ghost",
)


class MarketingRealOperationResultTests(unittest.IsolatedAsyncioTestCase):
    """Garante que Shift+O edita métricas, mas operar usa resultado real."""

    async def asyncSetUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.old_persistence = main.robot_persistence
        self.old_trader = main.auto_trader
        self.old_admin = main.admin_repository
        self.old_override = dict(main._marketing_override_by_user)
        main.robot_persistence = SQLiteRobotPersistence(
            str(Path(self.directory.name) / "robot.db")
        )
        main.auto_trader = AutoTrader()
        main._marketing_override_by_user.clear()
        # A marca de baixa intencional vive no Redis compartilhado (TTL 120s):
        # sem limpar, o placar de uma execução anterior vaza para esta.
        for fixture_user in MARKETING_FIXTURE_USER_IDS:
            main.clear_session_score_authority(fixture_user)
        self.saved_trades: list[dict[str, Any]] = []
        saved_trades = self.saved_trades

        class _Repo:
            async def save_simulated_trade(
                self,
                company_id: str,
                user_id: str,
                trade: dict[str, Any],
            ) -> dict[str, Any]:
                stored = {
                    **trade,
                    "company_id": company_id,
                    "user_id": user_id,
                }
                saved_trades.append(stored)
                return stored

        main.admin_repository = _Repo()

    async def asyncTearDown(self) -> None:
        main.robot_persistence = self.old_persistence
        main.auto_trader = self.old_trader
        main.admin_repository = self.old_admin
        main._marketing_override_by_user.clear()
        main._marketing_override_by_user.update(self.old_override)
        self.directory.cleanup()

    async def test_apply_override_keeps_broker_loss_despite_100_win_rate(self) -> None:
        """Taxa alvo não pode sobrescrever LOSS real da corretora."""
        user_id = "marketing-live-loss"
        main.register_marketing_override_context(
            user_id,
            company_id="company-mkt",
            win_rate=100,
        )
        state = main.auto_trader.start(user_id)
        state.wins = 0
        state.losses = 0
        main.auto_trader.record_trade(
            user_id,
            {
                "order_id": "ord-loss-1",
                "mode": "REAL",
                "active": "EURUSD-OTC",
                "direction": "CALL",
                "amount": 10,
                "payout": 85,
                "result": "PENDING_RESULT",
            },
        )

        result, profit = await main.apply_marketing_result_override(
            user_id,
            "LOSS",
            -10.0,
        )

        self.assertEqual(result, "LOSS")
        self.assertEqual(profit, -10.0)
        self.assertEqual(len(self.saved_trades), 1)
        self.assertEqual(self.saved_trades[0]["result"], "LOSS")
        self.assertEqual(self.saved_trades[0]["profit"], -10.0)

    async def test_apply_override_keeps_broker_win_despite_0_win_rate(self) -> None:
        """Taxa alvo não pode sobrescrever WIN real da corretora."""
        user_id = "marketing-live-win"
        main.register_marketing_override_context(
            user_id,
            company_id="company-mkt",
            win_rate=0,
        )
        main.auto_trader.start(user_id)
        main.auto_trader.record_trade(
            user_id,
            {
                "order_id": "ord-win-1",
                "mode": "REAL",
                "active": "GBPUSD-OTC",
                "direction": "PUT",
                "amount": 20,
                "payout": 87,
                "result": "PENDING_RESULT",
            },
        )

        result, profit = await main.apply_marketing_result_override(
            user_id,
            "WIN",
            17.4,
        )

        self.assertEqual(result, "WIN")
        self.assertEqual(profit, 17.4)
        self.assertEqual(self.saved_trades[0]["result"], "WIN")
        self.assertEqual(self.saved_trades[0]["profit"], 17.4)

    async def test_finish_monitored_trade_updates_scoreboard_with_real_loss(self) -> None:
        """Ao fechar operação, placar do robô reflete o resultado real."""
        user_id = "marketing-scoreboard"
        main.register_marketing_override_context(
            user_id,
            company_id="company-mkt",
            win_rate=100,
        )
        state = main.auto_trader.start(user_id)
        # Placar prévio (ex.: gerado no Shift+O)
        state.wins = 5
        state.losses = 1
        state.profit = 42.0
        main.auto_trader.record_trade(
            user_id,
            {
                "order_id": "ord-finish-1",
                "mode": "REAL",
                "active": "EURUSD-OTC",
                "direction": "CALL",
                "amount": 10,
                "payout": 85,
                "result": "PENDING_RESULT",
            },
        )

        await main.finish_monitored_trade(user_id, "ord-finish-1", "LOSS", -10.0)

        self.assertEqual(state.wins, 5)
        self.assertEqual(state.losses, 2)
        self.assertEqual(state.profit, 32.0)
        self.assertEqual((state.last_trade or {}).get("result"), "LOSS")
        self.assertEqual(self.saved_trades[0]["result"], "LOSS")

    def test_delete_robot_history_removes_memory_ghost(self) -> None:
        """
        Após excluir do banco, a linha não pode voltar via auto_trader.history.

        Esse fantasma causava SIMULATED_TRADE_NOT_FOUND no segundo clique.
        """
        user_id = "marketing-delete-ghost"
        order_id = "14105120022"
        main.robot_persistence.save_trade_history(
            user_id,
            {
                "order_id": order_id,
                "active": "EURUSD-OTC",
                "direction": "CALL",
                "amount": 10,
                "payout": 85,
                "result": "WIN",
                "profit": 8.5,
                "sent_at": "2026-07-25T12:00:00+00:00",
                "finished_at": "2026-07-25T12:01:00+00:00",
                "account_mode": "REAL",
            },
        )
        state = main.auto_trader.start(user_id)
        state.wins = 1
        state.profit = 8.5
        main.auto_trader._histories[user_id] = [
            {
                "order_id": order_id,
                "active": "EURUSD-OTC",
                "direction": "CALL",
                "amount": 10,
                "result": "WIN",
                "profit": 8.5,
                "finished_at": "2026-07-25T12:01:00+00:00",
            }
        ]

        deleted = main.delete_marketing_robot_history_item(user_id, order_id)
        self.assertTrue(deleted)

        items = main.load_robot_history_items(user_id, days=1)
        self.assertEqual(items, [])
        self.assertEqual(main.auto_trader.history(user_id)["trades"], [])
        self.assertEqual(state.wins, 0)
        self.assertEqual(state.profit, 0.0)

        # Segundo delete: memória já limpa e banco vazio → False (router trata 204)
        self.assertFalse(main.delete_marketing_robot_history_item(user_id, order_id))


if __name__ == "__main__":
    unittest.main()
