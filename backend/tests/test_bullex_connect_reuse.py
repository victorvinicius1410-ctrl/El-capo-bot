"""Testes: SessionManager reusa sessão viva no connect (mesmo email)."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from bullex_service import main as bullex_main


class TestConnectReusesAliveSession(unittest.TestCase):
    def test_connect_reuses_alive_session_same_email_without_closing_ws(self) -> None:
        store = Mock()
        api = SimpleNamespace(close=Mock())
        client = SimpleNamespace(
            api=api,
            check_connect=lambda: True,
            websocket_alive=lambda: True,
            get_balance_mode=lambda: "REAL",
            get_balance=lambda: 100.0,
            get_currency=lambda: "BRL",
        )
        manager = bullex_main.SessionManager(store)
        existing = manager.upsert(
            bullex_main.ManagedSession(
                user_id="reuse-user",
                client=client,
                email="same@example.com",
                desired_mode="REAL",
                real_mode_confirmed=True,
                active_mode="REAL",
            )
        )

        with self.assertLogs("bullex-service", level="INFO") as logs:
            connected = manager.connect(
                "reuse-user",
                bullex_main.ConnectRequest(
                    email="same@example.com",
                    password="secret",
                    account_mode="REAL",
                ),
            )

        self.assertIs(connected, existing)
        api.close.assert_not_called()
        store.mark_disconnected.assert_not_called()
        output = "\n".join(logs.output)
        self.assertIn("[CONNECT_REUSE_ALIVE_SESSION]", output)
        self.assertNotIn("[CONNECT_CLEAR_OLD_SESSION]", output)


if __name__ == "__main__":
    unittest.main()
