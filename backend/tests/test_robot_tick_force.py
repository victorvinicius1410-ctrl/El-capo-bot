"""POST /robot/tick com force=true dispara análise imediata."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch


class TestRobotTickForce(unittest.IsolatedAsyncioTestCase):
    async def test_tick_force_calls_run_analysis_now(self) -> None:
        from backend import main as backend_main

        fake_state = MagicMock()
        fake_state.enabled = True
        fake_state.connected = True
        fake_state.active_mode = "REAL"
        fake_state.operation_in_progress = False
        fake_state.pending_signal = None
        fake_state.next_cycle_at = datetime.now(timezone.utc) + timedelta(minutes=5)
        fake_state.current_candle_seconds = 12.0

        payload = {"ok": True, "data": {"forced": True}, "error": None}

        with (
            patch.object(backend_main.auto_trader, "get", return_value=fake_state),
            patch.object(
                backend_main,
                "run_analysis_now",
                new=AsyncMock(return_value=(200, payload)),
            ) as run_now,
            patch.object(
                backend_main,
                "execute_robot_cycle",
                new=AsyncMock(return_value=(200, {"ok": True, "data": {"forced": False}})),
            ) as run_cycle,
        ):
            response = await backend_main.robot_tick(
                body={"force": True},
                auth={"user_id": "user-1", "api_key": "k"},
            )

        self.assertEqual(response.status_code, 200)
        run_now.assert_awaited_once_with("user-1")
        run_cycle.assert_not_awaited()

    async def test_tick_without_force_uses_normal_cycle(self) -> None:
        from backend import main as backend_main

        payload = {"ok": True, "data": {"forced": False}, "error": None}

        with (
            patch.object(
                backend_main,
                "run_analysis_now",
                new=AsyncMock(return_value=(200, {"ok": True, "data": {"forced": True}})),
            ) as run_now,
            patch.object(
                backend_main,
                "execute_robot_cycle",
                new=AsyncMock(return_value=(200, payload)),
            ) as run_cycle,
        ):
            response = await backend_main.robot_tick(
                body=None,
                auth={"user_id": "user-1", "api_key": "k"},
            )

        self.assertEqual(response.status_code, 200)
        run_cycle.assert_awaited_once_with("user-1")
        run_now.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
