"""Timeout de market data no bullex-service evita trava eterna da WS morta."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch


class TestMarketDataOpTimeout(unittest.TestCase):
    def test_run_with_timeout_marks_session_dead_on_hang(self) -> None:
        from bullex_service import main as bullex_main

        manager = MagicMock(spec=bullex_main.SessionManager)
        manager._last_api_call_at = 0.0
        manager.user_lock = MagicMock()
        manager.user_lock.return_value.__enter__ = MagicMock(return_value=None)
        manager.user_lock.return_value.__exit__ = MagicMock(return_value=False)
        manager._session_context = MagicMock()
        manager._session_context.return_value.__enter__ = MagicMock(return_value=None)
        manager._session_context.return_value.__exit__ = MagicMock(return_value=False)

        session = MagicMock()
        session.client.api.close = MagicMock()
        manager.ensure_session_alive.return_value = session

        def hang(_session):
            import time

            time.sleep(5)

        # Bind real run method onto mock-like object with real dependencies
        real_manager = object.__new__(bullex_main.SessionManager)
        real_manager._last_api_call_at = 0.0
        real_manager.locks = {}
        real_manager.async_locks = {}
        real_manager.sessions = {}
        real_manager.store = None
        real_manager._runtime_lock = bullex_main.RLock()
        real_manager.probe_states = {}
        real_manager.login_progress = {}
        real_manager.instruments_cache = {}
        real_manager.api_call_semaphore = MagicMock()

        with (
            patch.object(bullex_main.SessionManager, "ensure_session_alive", return_value=session),
            patch.object(bullex_main.SessionManager, "user_lock", return_value=MagicMock(
                __enter__=MagicMock(return_value=None),
                __exit__=MagicMock(return_value=False),
            )),
            patch.object(bullex_main.SessionManager, "_session_context", return_value=MagicMock(
                __enter__=MagicMock(return_value=None),
                __exit__=MagicMock(return_value=False),
            )),
            patch.object(bullex_main.SessionManager, "_mark_probe_failure") as mark_fail,
        ):
            with self.assertRaises(bullex_main.ServiceError) as ctx:
                bullex_main.SessionManager.run(
                    real_manager,
                    "user-1",
                    hang,
                    disconnect_on_error=False,
                    timeout_seconds=1,
                )
            self.assertEqual(ctx.exception.message, bullex_main.SESSION_DISCONNECTED)
            self.assertEqual(ctx.exception.status_code, 409)
            mark_fail.assert_called()
            session.client.api.close.assert_called()


if __name__ == "__main__":
    unittest.main()
