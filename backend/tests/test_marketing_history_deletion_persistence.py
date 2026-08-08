"""Garante que operação excluída do histórico não volte no próximo GET/F5.

O endpoint ``GET /robot/history`` mescla ``robot_trade_history`` (banco) com o
histórico em memória do ``auto_trader``. Sem alinhar as duas fontes (mais o
espelho ``robot_trades`` usado na restauração), a linha apagada reaparecia
assim que a tela era recarregada.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from backend import main
from backend.auto_trader import AutoTrader
from backend.robot_persistence import SQLiteRobotPersistence


def finished_trade(
    order_id: str,
    result: str,
    profit: float,
    *,
    finished_at: datetime,
) -> dict[str, Any]:
    """Monta uma operação finalizada mínima aceita pelo histórico do robô."""
    return {
        "order_id": order_id,
        "mode": "REAL",
        "account_mode": "REAL",
        "active": "EURUSD-OTC",
        "direction": "CALL",
        "amount": 10.0,
        "payout": 87,
        "result": result,
        "profit": profit,
        "sent_at": (finished_at - timedelta(minutes=1)).isoformat(),
        "finished_at": finished_at.isoformat(),
    }


def simulated_trade(
    trade_id: str,
    result: str,
    profit: float,
    *,
    created_at: datetime,
    broker_order_id: str | None = None,
) -> dict[str, Any]:
    """Monta um item do histórico editável Shift+O (marketing)."""
    trade: dict[str, Any] = {
        "id": trade_id,
        "result": result,
        "asset": "EURUSD-OTC",
        "direction": "CALL",
        "amount": 10.0,
        "payout": 87,
        "profit": profit,
        "created_at": created_at.isoformat(),
    }
    if broker_order_id:
        trade["broker_order_id"] = broker_order_id
    return trade


class MarketingHistoryDeletionPersistenceTests(unittest.IsolatedAsyncioTestCase):
    """Exclusão precisa valer para banco, memória e espelho de restauração."""

    async def asyncSetUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.old_persistence = main.robot_persistence
        self.old_trader = main.auto_trader
        main.robot_persistence = SQLiteRobotPersistence(
            str(Path(self.directory.name) / "robot.db")
        )
        main.auto_trader = AutoTrader()
        self.user_id = "marketing-user"
        self.now = datetime.now(timezone.utc)

    async def asyncTearDown(self) -> None:
        main.robot_persistence = self.old_persistence
        main.auto_trader = self.old_trader
        self.directory.cleanup()

    async def history_order_ids(self, days: int = 30) -> list[str]:
        """Executa o mesmo caminho do F5 da tela `/history`."""
        response = await main.robot_history(days, {"user_id": self.user_id})
        payload = json.loads(response.body)
        return [item["order_id"] for item in payload["data"]["items"]]

    async def test_sync_removes_deleted_trade_from_in_memory_history(self) -> None:
        """Sem alinhar a memória, o item apagado voltava no próximo GET."""
        kept = simulated_trade("uuid-kept", "WIN", 8.7, created_at=self.now)
        deleted = simulated_trade(
            "uuid-deleted",
            "LOSS",
            -10.0,
            created_at=self.now - timedelta(minutes=5),
        )
        main.sync_marketing_display_to_robot(
            self.user_id,
            [deleted, kept],
            {"wins": 1, "losses": 1, "profit": -1.3},
        )
        self.assertEqual(
            sorted(await self.history_order_ids()),
            ["uuid-deleted", "uuid-kept"],
        )

        main.sync_marketing_display_to_robot(
            self.user_id,
            [kept],
            {"wins": 1, "losses": 0, "profit": 8.7},
        )

        self.assertEqual(await self.history_order_ids(), ["uuid-kept"])

    async def test_sync_clears_restore_mirror_so_reload_does_not_resurrect(self) -> None:
        """`robot_trades` alimenta o restore e não pode manter o item apagado."""
        main.robot_persistence.save_trade(
            self.user_id,
            finished_trade("14100935220", "WIN", 8.7, finished_at=self.now),
        )
        kept = simulated_trade("uuid-kept", "WIN", 8.7, created_at=self.now)

        main.sync_marketing_display_to_robot(
            self.user_id,
            [kept],
            {"wins": 1, "losses": 0, "profit": 8.7},
        )

        self.assertEqual(main.robot_persistence.load_trades(self.user_id), [])
        self.assertEqual(await self.history_order_ids(), ["uuid-kept"])

    async def test_delete_live_order_removes_database_memory_and_mirror(self) -> None:
        """Exclusão por order_id Bullex limpa as três fontes do histórico."""
        trade = finished_trade("14100935220", "WIN", 8.7, finished_at=self.now)
        main.robot_persistence.save_trade_history(self.user_id, trade)
        main.robot_persistence.save_trade(self.user_id, trade)
        main.auto_trader.replace_history(self.user_id, [trade])

        deleted = main.delete_marketing_robot_history_item(self.user_id, "14100935220")

        self.assertTrue(deleted)
        self.assertEqual(main.robot_persistence.load_trade_history(self.user_id, 30), [])
        self.assertEqual(main.robot_persistence.load_trades(self.user_id), [])
        self.assertEqual(await self.history_order_ids(), [])

    async def test_deleted_live_order_stays_deleted_after_state_restore(self) -> None:
        """Depois de reiniciar o backend o item apagado não pode voltar."""
        trade = finished_trade("14100935220", "LOSS", -10.0, finished_at=self.now)
        main.robot_persistence.save_trade_history(self.user_id, trade)
        main.robot_persistence.save_trade(self.user_id, trade)
        main.auto_trader.replace_history(self.user_id, [trade])

        main.delete_marketing_robot_history_item(self.user_id, "14100935220")
        main.auto_trader.restore(
            self.user_id,
            {"enabled": False},
            main.robot_persistence.load_trades(self.user_id),
        )

        self.assertEqual(await self.history_order_ids(), [])

    async def test_sync_uses_broker_order_id_to_avoid_duplicate_live_trade(self) -> None:
        """Espelho e operação ao vivo compartilham o mesmo identificador."""
        live = finished_trade("14100935220", "WIN", 8.7, finished_at=self.now)
        main.robot_persistence.save_trade_history(self.user_id, live)
        main.auto_trader.replace_history(self.user_id, [live])
        mirror = simulated_trade(
            "uuid-mirror",
            "WIN",
            8.7,
            created_at=self.now,
            broker_order_id="14100935220",
        )

        main.sync_marketing_display_to_robot(
            self.user_id,
            [mirror],
            {"wins": 1, "losses": 0, "profit": 8.7},
        )

        self.assertEqual(await self.history_order_ids(), ["14100935220"])


class AutoTraderHistoryReplacementTests(unittest.TestCase):
    """`replace_history` é a fonte única do histórico em memória."""

    def setUp(self) -> None:
        self.trader = AutoTrader()
        self.now = datetime.now(timezone.utc)

    def test_replace_history_keeps_only_finished_trades(self) -> None:
        self.trader.replace_history(
            "user-a",
            [
                finished_trade("done", "WIN", 8.7, finished_at=self.now),
                {"order_id": "pending", "result": "PENDING_RESULT"},
            ],
        )

        trades = self.trader.history("user-a")["trades"]

        self.assertEqual([trade["order_id"] for trade in trades], ["done"])

    def test_replace_history_preserves_completed_order_ids(self) -> None:
        """Esquecer ordens concluídas reabriria resultados já processados."""
        self.trader.replace_history(
            "user-a",
            [finished_trade("first", "WIN", 8.7, finished_at=self.now)],
        )
        self.trader.replace_history(
            "user-a",
            [finished_trade("second", "LOSS", -10.0, finished_at=self.now)],
        )

        completed = self.trader._completed_order_ids["user-a"]

        self.assertEqual(completed, {"first", "second"})

    def test_replace_history_ignores_blank_user(self) -> None:
        self.trader.replace_history("", [finished_trade("x", "WIN", 1, finished_at=self.now)])

        self.assertEqual(self.trader.history("")["trades"], [])


if __name__ == "__main__":
    unittest.main()
