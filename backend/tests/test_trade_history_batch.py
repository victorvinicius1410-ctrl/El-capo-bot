"""Testes do carregamento em lote de histórico (dashboard admin)."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from backend.robot_persistence import (
    SQLiteRobotPersistence,
    SupabaseRobotPersistence,
    build_trade_history_item,
)


class TradeHistoryBatchSqliteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "robot.db")
        self.persistence = SQLiteRobotPersistence(self.db_path)
        self.now = datetime.now(timezone.utc)
        self.opened = (self.now - timedelta(minutes=2)).isoformat()
        self.finished = self.now.isoformat()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _trade(self, order_id: str, result: str, profit: float, active: str) -> dict:
        return {
            "order_id": order_id,
            "result": result,
            "profit": profit,
            "amount": 5,
            "active": active,
            "direction": "CALL",
            "confidence": 80,
            "payout": 80,
            "sent_at": self.opened,
            "finished_at": self.finished,
            "expiration": "M1",
        }

    def test_batch_groups_history_by_user_in_one_query(self) -> None:
        self.persistence.save_trade_history(
            "user-a", self._trade("a1", "WIN", 10, "EURUSD-OTC")
        )
        self.persistence.save_trade_history(
            "user-a", self._trade("a2", "LOSS", -5, "GBPUSD-OTC")
        )
        self.persistence.save_trade_history(
            "user-b", self._trade("b1", "WIN", 7, "EURUSD-OTC")
        )

        batch = self.persistence.load_trade_history_for_users(
            ["user-a", "user-b", "user-c"],
            days=30,
        )

        self.assertEqual(len(batch["user-a"]), 2)
        self.assertEqual(len(batch["user-b"]), 1)
        self.assertEqual(batch["user-c"], [])
        self.assertEqual(
            {item["order_id"] for item in batch["user-a"]},
            {"a1", "a2"},
        )

    def test_batch_empty_user_list_returns_empty_dict(self) -> None:
        self.assertEqual(
            self.persistence.load_trade_history_for_users([], days=7),
            {},
        )

    def test_batch_matches_single_loader_contents(self) -> None:
        self.persistence.save_trade_history(
            "user-a", self._trade("a1", "WIN", 10, "EURUSD-OTC")
        )
        single = self.persistence.load_trade_history("user-a", 30)
        batch = self.persistence.load_trade_history_for_users(["user-a"], 30)
        self.assertEqual(
            [item["order_id"] for item in batch["user-a"]],
            [item["order_id"] for item in single],
        )


class TradeHistoryBatchSupabaseTests(unittest.TestCase):
    def test_supabase_batch_uses_in_filter_not_n_calls(self) -> None:
        persistence = SupabaseRobotPersistence(
            "https://example.supabase.co",
            "service-role-key",
        )
        rows = [
            {
                "user_id": "u1",
                "order_id": "1",
                "result": "WIN",
                "profit": 1,
                "active": "EURUSD-OTC",
                "analysis_json": {},
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "id": 1,
            },
            {
                "user_id": "u2",
                "order_id": "2",
                "result": "LOSS",
                "profit": -1,
                "active": "GBPUSD-OTC",
                "analysis_json": {},
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "id": 2,
            },
        ]
        mock_request = MagicMock(return_value=rows)
        with patch.object(persistence, "_request", mock_request):
            result = persistence.load_trade_history_for_users(["u1", "u2"], days=7)

        self.assertEqual(mock_request.call_count, 1)
        method, path = mock_request.call_args.args[:2]
        self.assertEqual(method, "GET")
        self.assertIn("user_id=in.(", path)
        self.assertIn("u1", path)
        self.assertIn("u2", path)
        self.assertEqual(len(result["u1"]), 1)
        self.assertEqual(len(result["u2"]), 1)
        self.assertEqual(result["u1"][0]["order_id"], "1")

    def test_supabase_chunks_large_user_lists(self) -> None:
        persistence = SupabaseRobotPersistence(
            "https://example.supabase.co",
            "service-role-key",
        )
        user_ids = [f"user-{i:03d}" for i in range(100)]
        mock_request = MagicMock(return_value=[])
        with patch.object(persistence, "_request", mock_request):
            persistence.load_trade_history_for_users(user_ids, days=7)
        # 80 + 20 = 2 chunks
        self.assertEqual(mock_request.call_count, 2)


class BuildTradeHistoryItemSanityTests(unittest.TestCase):
    def test_build_item_requires_final_result(self) -> None:
        with self.assertRaises(ValueError):
            build_trade_history_item(
                "u1",
                {
                    "order_id": "x",
                    "result": "PENDING",
                    "sent_at": "2026-01-01T00:00:00+00:00",
                    "finished_at": "2026-01-01T00:01:00+00:00",
                },
            )


if __name__ == "__main__":
    unittest.main()
