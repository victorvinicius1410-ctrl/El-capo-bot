"""Regressão: worker não analisa quando connected=false sob carga.

``robot_has_recent_real_cache`` exigia ``state.connected=True``, então no
exato momento em que o worker precisa do cache (desconectado transitório)
a função sempre retornava False → loop eterno em
``ROBOT_WORKER_BLOCKED_DISCONNECTED`` sem análise/compra.
"""

from __future__ import annotations

import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from backend import main


class RobotRecentRealCacheTests(unittest.TestCase):
    def test_cache_allows_resume_when_state_disconnected_but_snapshot_real(self) -> None:
        user_id = "resume-disconnected-cache"
        state = SimpleNamespace(
            connected=False,
            active_mode=None,
            account_mode="REAL",
            connection_checked_at=main.utc_now() - timedelta(seconds=30),
            enabled=True,
        )
        with patch.object(
            main,
            "get_cached_account_snapshot",
            return_value={
                "mode": "REAL",
                "balance": 120.0,
                "connected": True,
                "currency": "BRL",
            },
        ), patch.object(
            main,
            "get_user_account_snapshot",
            return_value={
                "mode": "REAL",
                "balance": 120.0,
                "connected": True,
            },
        ):
            self.assertTrue(main.robot_has_recent_real_cache(user_id, state))

    def test_cache_false_without_real_balance(self) -> None:
        user_id = "no-balance-cache"
        state = SimpleNamespace(
            connected=False,
            active_mode=None,
            account_mode="REAL",
            connection_checked_at=main.utc_now(),
            enabled=True,
        )
        with patch.object(
            main,
            "get_cached_account_snapshot",
            return_value={"mode": "REAL", "balance": None, "connected": False},
        ), patch.object(
            main,
            "get_user_account_snapshot",
            return_value={"mode": None, "balance": None, "connected": None},
        ):
            self.assertFalse(main.robot_has_recent_real_cache(user_id, state))


class ResumeRobotFromRealSnapshotTests(unittest.TestCase):
    def test_resume_syncs_connected_real_from_snapshot(self) -> None:
        user_id = "resume-sync-user"
        main.auto_trader.start(user_id)
        state = main.auto_trader.get(user_id)
        state.connected = False
        state.active_mode = None
        state.account_mode = "REAL"
        state.enabled = True

        with patch.object(
            main,
            "robot_has_recent_real_cache",
            return_value=True,
        ), patch.object(
            main,
            "get_cached_account_snapshot",
            return_value={
                "mode": "REAL",
                "balance": 55.0,
                "connected": True,
            },
        ):
            resumed = main.resume_robot_connection_from_real_cache(user_id, state)

        self.assertIsNotNone(resumed)
        assert resumed is not None
        self.assertTrue(resumed.connected)
        self.assertEqual(resumed.active_mode, "REAL")


if __name__ == "__main__":
    unittest.main()
