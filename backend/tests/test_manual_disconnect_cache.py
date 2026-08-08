"""Desconexão manual não pode deixar cache REAL mascarando o painel (LEI 10)."""

from __future__ import annotations

import unittest

from backend.main import (
    apply_manual_disconnect_session_state,
    auto_trader,
    build_success,
    cache_bullex_response,
    get_session_cache,
    recent_real_account_connection_payload,
    reset_session_connection_cache,
)


class ManualDisconnectCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.user_id = "user-disconnect-cache"
        reset_session_connection_cache(self.user_id)
        auto_trader.disconnect_account(self.user_id)

    def test_manual_disconnect_purges_grace_account_cache(self) -> None:
        cache_bullex_response(
            self.user_id,
            "/account",
            200,
            build_success(
                {
                    "connected": True,
                    "active_mode": "REAL",
                    "mode": "REAL",
                    "balance": 250.0,
                    "currency": "BRL",
                    "email": "trader@example.com",
                }
            ),
        )
        auto_trader.sync_connection(
            self.user_id,
            connected=True,
            active_mode="REAL",
            source="test",
            align_status=True,
        )
        self.assertIsNotNone(recent_real_account_connection_payload(self.user_id))

        auto_trader.disconnect_account(self.user_id)
        apply_manual_disconnect_session_state(self.user_id)

        self.assertIsNone(recent_real_account_connection_payload(self.user_id))
        cache = get_session_cache(self.user_id)
        self.assertIsNone(cache.offline_until)
        self.assertEqual(cache.failure_count, 0)
        state = auto_trader.get(self.user_id)
        self.assertFalse(bool(state.connected))
        self.assertEqual(state.connection_status_source, "disconnected")


if __name__ == "__main__":
    unittest.main()
