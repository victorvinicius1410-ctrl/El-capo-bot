"""Testes: reconnect soft (SSID) e start sem marcar ACCOUNT_DISCONNECTED."""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from backend import main


class TestSoftSsidReconnect(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        main.bullex_auto_reconnect_at.clear()
        main.bullex_ssid_reconnect_at.clear()
        main.bullex_login_rate_limited_until = None
        self.user_id = "soft-ssid-user"

    def tearDown(self) -> None:
        main.bullex_auto_reconnect_at.clear()
        main.bullex_ssid_reconnect_at.clear()
        main.bullex_login_rate_limited_until = None

    async def test_auto_reconnect_prefers_ssid_and_skips_password(self) -> None:
        restored = (
            200,
            {
                "ok": True,
                "data": {
                    "connected": True,
                    "status": "CONNECTED",
                    "active_mode": "REAL",
                    "email": "trader@example.com",
                    "balance": 100.0,
                },
            },
        )
        credentials = SimpleNamespace(email="trader@example.com", password="secret")
        with (
            patch.object(main, "call_bullex_service", new=AsyncMock(return_value=restored)) as service,
            patch.object(main, "bullex_credentials_service", SimpleNamespace(load=Mock(return_value=credentials))),
            patch.object(main, "persist_robot"),
        ):
            ok = await main.try_auto_reconnect_with_saved_credentials(self.user_id)

        self.assertTrue(ok)
        paths = [call.args[1] for call in service.await_args_list]
        self.assertEqual(paths, ["/sessions/reconnect"])
        self.assertNotIn("/sessions/connect", paths)
        state = main.auto_trader.get(self.user_id)
        self.assertTrue(state.connected)
        self.assertEqual(state.active_mode, "REAL")

    async def test_auto_reconnect_falls_back_to_password_when_ssid_fails(self) -> None:
        ssid_fail = (404, {"ok": False, "data": {"connected": False}, "error": "SESSION_NOT_FOUND"})
        password_ok = (
            200,
            {
                "ok": True,
                "data": {
                    "connected": True,
                    "status": "CONNECTED",
                    "active_mode": "REAL",
                    "email": "trader@example.com",
                    "balance": 80.0,
                },
            },
        )
        credentials = SimpleNamespace(email="trader@example.com", password="secret")
        service = AsyncMock(side_effect=[ssid_fail, password_ok])
        with (
            patch.object(main, "call_bullex_service", new=service),
            patch.object(main, "bullex_credentials_service", SimpleNamespace(load=Mock(return_value=credentials))),
            patch.object(main, "persist_robot"),
            patch.object(main, "sync_user_store_from_payload"),
            patch.object(main, "seed_connected_session_cache"),
            patch.object(main, "reset_session_connection_cache"),
        ):
            ok = await main.try_auto_reconnect_with_saved_credentials(self.user_id)

        self.assertTrue(ok)
        paths = [call.args[1] for call in service.await_args_list]
        self.assertEqual(paths, ["/sessions/reconnect", "/sessions/connect"])


class TestRobotStartKeepsSession(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        main.bullex_auto_reconnect_at.clear()
        main.bullex_ssid_reconnect_at.clear()

    async def test_start_failure_does_not_mark_account_disconnected(self) -> None:
        """Falha de saldo no start não pode zerar connected/ACCOUNT_DISCONNECTED."""
        user_id = "start-keeps-session"
        state = main.auto_trader.get(user_id)
        state.account_mode = "REAL"
        state.allow_real = True
        state.confirm_real = True
        state.connected = True
        state.active_mode = "REAL"
        state.connection_checked_at = main.utc_now()
        state.enabled = False
        state.status = "STOPPED"

        account_without_balance = main.build_success(
            {
                "connected": True,
                "active_mode_from_bullex": "REAL",
                "balance_real": None,
                "balance": None,
                "mode": "REAL",
            }
        )

        with (
            patch.object(
                main,
                "call_bullex_service",
                new=AsyncMock(return_value=(200, account_without_balance)),
            ),
            patch.object(
                main,
                "try_auto_reconnect_with_saved_credentials",
                new=AsyncMock(return_value=False),
            ),
            patch.object(main, "get_cached_account_snapshot", return_value={}),
            patch.object(main, "memory_account_fallback", return_value=None),
            patch.object(main, "ensure_robot_worker") as worker_start,
            patch.object(main, "persist_robot"),
            patch.object(main.auto_trader, "disconnect_account") as disconnect,
        ):
            response = await main.robot_start({"user_id": user_id})

        payload = json.loads(response.body)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(payload["error"], "BULLEX_NOT_CONNECTED")
        disconnect.assert_not_called()
        worker_start.assert_not_called()
        state_after = main.auto_trader.get(user_id)
        self.assertTrue(state_after.connected)
        self.assertEqual(state_after.active_mode, "REAL")
        self.assertNotEqual(state_after.status, "ACCOUNT_DISCONNECTED")


if __name__ == "__main__":
    unittest.main()
