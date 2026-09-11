"""Snapshot Redis imediato no start/stop (LEI 10 — regressão de latência UI)."""

from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import MagicMock, patch

from backend import main
from backend.main import (
    auto_trader,
    enrich_robot_snapshot_session_score,
    publish_robot_control_snapshot,
    reconcile_session_score_on_gateway,
)


class RobotControlSnapshotTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.user_id = "user-control-snapshot"
        auto_trader.stop(self.user_id)

    def test_stop_snapshot_forces_worker_running_false(self) -> None:
        auto_trader.start(self.user_id)
        published: list[dict] = []

        def _capture(_user_id: str, payload: dict) -> None:
            published.append(payload)

        # Task falsa ainda em robot_tasks (stop publica antes do cancel).
        fake_task = MagicMock()
        fake_task.done.return_value = False
        main.robot_tasks[self.user_id] = fake_task
        try:
            with (
                patch.object(main.robot_bus, "publish_snapshot", side_effect=_capture),
                patch.object(main.robot_state_ws_hub, "has_connections", return_value=False),
            ):
                auto_trader.stop(self.user_id)
                payload = publish_robot_control_snapshot(self.user_id, worker_running=False)
        finally:
            main.robot_tasks.pop(self.user_id, None)

        self.assertTrue(published)
        data = payload["data"]
        self.assertFalse(data["enabled"])
        self.assertFalse(data["worker_running"])
        self.assertEqual(data["status"], "STOPPED")
        self.assertEqual(published[0]["data"]["worker_running"], False)

    def test_start_snapshot_marks_enabled_for_panel(self) -> None:
        published: list[dict] = []

        def _capture(_user_id: str, payload: dict) -> None:
            published.append(payload)

        with (
            patch.object(main.robot_bus, "publish_snapshot", side_effect=_capture),
            patch.object(main.robot_state_ws_hub, "has_connections", return_value=False),
        ):
            auto_trader.start(self.user_id)
            payload = publish_robot_control_snapshot(self.user_id, worker_running=True)

        data = payload["data"]
        self.assertTrue(data["enabled"])
        self.assertTrue(data["worker_running"])
        self.assertTrue(published)

    async def test_robot_stop_impl_publishes_before_worker_stop(self) -> None:
        order: list[str] = []

        async def _fake_stop_worker(_user_id: str) -> None:
            order.append("stop_worker")

        def _fake_publish(user_id: str, *, worker_running: bool | None = None) -> dict:
            order.append(f"publish:{worker_running}")
            return {"ok": True, "data": {"enabled": False, "worker_running": False}}

        auto_trader.start(self.user_id)

        with (
            patch.object(main, "persist_robot", return_value=None),
            patch.object(main, "publish_robot_control_snapshot", side_effect=_fake_publish),
            patch.object(main, "stop_robot_worker", side_effect=_fake_stop_worker),
            patch.object(main, "mark_user_active"),
        ):
            response = await main._robot_stop_impl({"user_id": self.user_id})

        self.assertEqual(order[0], "publish:False")
        self.assertIn("stop_worker", order)
        self.assertEqual(response.status_code, 200)

    async def test_robot_reset_score_publishes_snapshot_and_delegates(self) -> None:
        """Reiniciar placar deve gravar Redis e avisar o runtime (mode=external)."""
        user_id = "user-reset-score-snapshot"
        state = auto_trader.start(user_id)
        state.wins = 4
        state.losses = 1
        state.profit = 22.5
        order: list[str] = []

        def _fake_publish(uid: str, *, worker_running: bool | None = None) -> dict:
            order.append(f"publish:{worker_running}")
            refreshed = auto_trader.get(uid)
            return {
                "ok": True,
                "data": {
                    "wins": refreshed.wins,
                    "losses": refreshed.losses,
                    "profit": refreshed.profit,
                    "enabled": refreshed.enabled,
                },
            }

        with (
            patch.object(main, "persist_robot", return_value=None),
            patch.object(main, "publish_robot_control_snapshot", side_effect=_fake_publish),
            patch.object(main, "robot_runtime_mode", return_value="external"),
            patch.object(main.robot_bus, "publish_command") as publish_cmd,
        ):
            response = await main.robot_reset_score({"user_id": user_id})

        payload = response.body
        data = json.loads(payload)["data"]
        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["wins"], 0)
        self.assertEqual(data["losses"], 0)
        self.assertEqual(data["profit"], 0.0)
        self.assertEqual(order, ["publish:None"])
        publish_cmd.assert_called_once_with(user_id, "reset_score")


class RobotRuntimeStopCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_pops_task_and_publishes_before_await_cancel(self) -> None:
        from backend.robot_runtime_main import _handle_command

        user_id = "user-runtime-stop"
        order: list[str] = []

        async def _slow_cancel() -> None:
            order.append("await_cancel")
            await asyncio.sleep(0)

        task = asyncio.create_task(_slow_cancel())
        gateway = MagicMock()
        gateway.robot_persistence = None
        gateway.auto_trader = auto_trader
        gateway.robot_tasks = {user_id: task}
        auto_trader.start(user_id)

        def _publish(_uid: str, *, worker_running: bool | None = None) -> dict:
            order.append(f"publish:{worker_running}")
            self.assertNotIn(user_id, gateway.robot_tasks)
            return {"ok": True, "data": {}}

        gateway.publish_robot_control_snapshot = _publish

        with patch(
            "backend.robot_runtime_main._hydrate_user_from_persistence",
            return_value=None,
        ):
            await _handle_command(gateway, {"action": "stop", "user_id": user_id})

        self.assertNotIn(user_id, gateway.robot_tasks)
        self.assertEqual(order[0], "publish:False")
        self.assertFalse(auto_trader.get(user_id).enabled)

    async def test_reset_score_command_zeros_memory_and_publishes(self) -> None:
        from backend.robot_runtime_main import _handle_command

        user_id = "user-runtime-reset-score"
        state = auto_trader.start(user_id)
        state.wins = 3
        state.losses = 2
        state.profit = 15.0
        published: list[str] = []

        gateway = MagicMock()
        gateway.robot_persistence = None
        gateway.auto_trader = auto_trader
        gateway.robot_tasks = {}

        def _publish(uid: str, *, worker_running: bool | None = None) -> dict:
            published.append(uid)
            return {"ok": True, "data": {}}

        gateway.publish_robot_control_snapshot = _publish

        await _handle_command(gateway, {"action": "reset_score", "user_id": user_id})

        refreshed = auto_trader.get(user_id)
        self.assertEqual(refreshed.wins, 0)
        self.assertEqual(refreshed.losses, 0)
        self.assertEqual(refreshed.profit, 0.0)
        self.assertEqual(published, [user_id])

    async def test_stop_keeps_live_score_when_persistence_is_behind(self) -> None:
        """Parar operação não pode cair o placar (ex.: 10x12 → 9x12)."""
        from backend.robot_runtime_main import _handle_command

        user_id = "user-stop-keeps-score"
        state = auto_trader.start(user_id)
        state.wins = 10
        state.losses = 12
        state.profit = 40.0

        persistence = MagicMock()
        persistence.load_states.return_value = [
            (
                user_id,
                {
                    "enabled": True,
                    "wins": 9,
                    "losses": 12,
                    "profit": 28.0,
                    "status": "WAITING_NEXT_CYCLE",
                },
            )
        ]
        persistence.load_trades.return_value = []

        gateway = MagicMock()
        gateway.robot_persistence = persistence
        gateway.auto_trader = auto_trader
        gateway.robot_tasks = {}
        gateway.robot_persistence_source = lambda: "runtime"
        gateway.publish_robot_control_snapshot = MagicMock(return_value={"ok": True, "data": {}})

        await _handle_command(gateway, {"action": "stop", "user_id": user_id})

        persistence.load_states.assert_not_called()
        refreshed = auto_trader.get(user_id)
        self.assertFalse(refreshed.enabled)
        self.assertEqual(refreshed.wins, 10)
        self.assertEqual(refreshed.losses, 12)
        self.assertEqual(refreshed.profit, 40.0)

    async def test_forced_hydrate_keeps_live_score_ahead_of_persist(self) -> None:
        from backend.robot_runtime_main import _hydrate_user_from_persistence

        user_id = "user-start-keeps-score"
        state = auto_trader.start(user_id)
        state.wins = 10
        state.losses = 12
        state.profit = 40.0

        persistence = MagicMock()
        persistence.load_states.return_value = [
            (
                user_id,
                {
                    "enabled": True,
                    "wins": 9,
                    "losses": 12,
                    "profit": 28.0,
                    "status": "STOPPED",
                },
            )
        ]
        persistence.load_trades.return_value = []

        gateway = MagicMock()
        gateway.robot_persistence = persistence
        gateway.auto_trader = auto_trader
        gateway.robot_persistence_source = lambda: "runtime"

        _hydrate_user_from_persistence(gateway, user_id, force=True)

        refreshed = auto_trader.get(user_id)
        self.assertEqual(refreshed.wins, 10)
        self.assertEqual(refreshed.losses, 12)
        self.assertEqual(refreshed.profit, 40.0)

    async def test_apply_score_command_sets_memory_and_publishes(self) -> None:
        """Shift+O precisa alinhar o runtime para o overlay deixar de ficar 0-0."""
        from backend.robot_runtime_main import _handle_command

        user_id = "user-runtime-apply-score"
        state = auto_trader.start(user_id)
        state.wins = 0
        state.losses = 0
        state.profit = 0.0
        published: list[str] = []

        gateway = MagicMock()
        gateway.robot_persistence = None
        gateway.auto_trader = auto_trader
        gateway.robot_tasks = {}

        def _publish(uid: str, *, worker_running: bool | None = None) -> dict:
            published.append(uid)
            return {"ok": True, "data": {}}

        gateway.publish_robot_control_snapshot = _publish

        await _handle_command(
            gateway,
            {
                "action": "apply_score",
                "user_id": user_id,
                "wins": 8,
                "losses": 2,
                "profit": 46.4,
            },
        )

        refreshed = auto_trader.get(user_id)
        self.assertEqual(refreshed.wins, 8)
        self.assertEqual(refreshed.losses, 2)
        self.assertEqual(refreshed.profit, 46.4)
        self.assertEqual(published, [user_id])


class MarketingScoreOverlayPublishTests(unittest.IsolatedAsyncioTestCase):
    """Gerar placar no Shift+O tem que chegar no Redis/runtime (mode=external)."""

    def setUp(self) -> None:
        self.user_id = "marketing-overlay-user"
        auto_trader.stop(self.user_id)

    def test_sync_publishes_snapshot_and_apply_score_in_external_mode(self) -> None:
        commands: list[tuple[str, str, dict]] = []
        snapshots: list[str] = []

        def _capture_command(user_id: str, action: str, **extra: object) -> None:
            commands.append((user_id, action, extra))

        def _capture_snapshot(
            user_id: str,
            *,
            worker_running: bool | None = None,
            trust_local_score: bool = False,
        ) -> dict:
            snapshots.append(user_id)
            return {"ok": True, "data": {"wins": 3, "losses": 1}}

        with (
            patch.dict("os.environ", {"ROBOT_RUNTIME_MODE": "external"}, clear=False),
            patch.object(main, "persist_robot", return_value=None),
            patch.object(main, "publish_robot_control_snapshot", side_effect=_capture_snapshot),
            patch.object(main.robot_bus, "publish_command", side_effect=_capture_command),
            patch.object(main.robot_persistence, "load_trade_history", return_value=[]),
            patch.object(main.robot_persistence, "save_trade_history", return_value=None),
        ):
            main.sync_marketing_display_to_robot(
                self.user_id,
                [
                    {
                        "id": "uuid-win",
                        "result": "WIN",
                        "asset": "EURUSD-OTC",
                        "direction": "CALL",
                        "amount": 10,
                        "payout": 87,
                        "profit": 8.7,
                        "created_at": "2026-08-18T12:00:00+00:00",
                    }
                ],
                {"wins": 8, "losses": 2, "profit": 46.4},
            )

        state = auto_trader.get(self.user_id)
        self.assertEqual(state.wins, 8)
        self.assertEqual(state.losses, 2)
        self.assertEqual(state.profit, 46.4)
        self.assertEqual(snapshots, [self.user_id])
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0][0], self.user_id)
        self.assertEqual(commands[0][1], "apply_score")
        self.assertEqual(commands[0][2]["wins"], 8)
        self.assertEqual(commands[0][2]["losses"], 2)
        self.assertEqual(commands[0][2]["profit"], 46.4)

    def test_skip_score_does_not_publish_overlay(self) -> None:
        with (
            patch.dict("os.environ", {"ROBOT_RUNTIME_MODE": "external"}, clear=False),
            patch.object(main, "persist_robot", return_value=None),
            patch.object(main, "publish_robot_control_snapshot") as publish_snapshot,
            patch.object(main.robot_bus, "publish_command") as publish_command,
            patch.object(main.robot_persistence, "load_trade_history", return_value=[]),
            patch.object(main.robot_persistence, "save_trade_history", return_value=None),
        ):
            main.sync_marketing_display_to_robot(
                self.user_id,
                [],
                {"skip_score": True},
            )

        publish_snapshot.assert_not_called()
        publish_command.assert_not_called()


class SessionScoreReconcileTests(unittest.TestCase):
    """Placar no start/stop não pode regredir (ex.: 5x3 → 2x0)."""

    def setUp(self) -> None:
        self.user_id = "user-score-reconcile"
        state = auto_trader.get(self.user_id)
        state.wins = 5
        state.losses = 3
        state.profit = 42.0
        state.stop_reset_at = None

    def test_reconcile_promotes_gateway_when_persistence_is_ahead(self) -> None:
        state = auto_trader.get(self.user_id)
        state.wins = 2
        state.losses = 0
        state.profit = 12.0
        user_id = self.user_id

        class FakePersistence:
            def load_state(self, uid: str):
                assert uid == user_id
                return {"wins": 5, "losses": 3, "profit": 42.0}

        old = main.robot_persistence
        main.robot_persistence = FakePersistence()
        try:
            changed = reconcile_session_score_on_gateway(self.user_id)
        finally:
            main.robot_persistence = old

        self.assertTrue(changed)
        self.assertEqual(state.wins, 5)
        self.assertEqual(state.losses, 3)
        self.assertEqual(state.profit, 42.0)

    def test_enrich_remote_snapshot_patches_stale_redis_score(self) -> None:
        remote = {
            "ok": True,
            "data": {
                "enabled": False,
                "wins": 2,
                "losses": 0,
                "profit": 12.0,
                "status": "STOPPED",
            },
        }

        class FakePersistence:
            def load_state(self, uid: str):
                return {"wins": 5, "losses": 3, "profit": 42.0}

        old = main.robot_persistence
        main.robot_persistence = FakePersistence()
        try:
            enriched = enrich_robot_snapshot_session_score(self.user_id, remote)
        finally:
            main.robot_persistence = old

        data = enriched["data"]
        self.assertEqual(data["wins"], 5)
        self.assertEqual(data["losses"], 3)
        self.assertEqual(data["profit"], 42.0)


if __name__ == "__main__":
    unittest.main()
