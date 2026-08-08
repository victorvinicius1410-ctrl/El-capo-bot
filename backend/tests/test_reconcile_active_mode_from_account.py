"""Status connected sem active_mode deve resolver o modo via /account.

Regressão: /sessions/status às vezes devolve connected=true com active_mode=null
(get_balance_mode falha), o painel mostra conectado via /account (REAL + saldo),
mas POST /robot/start bloqueava com robot_connection_unavailable (active_mode None).
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from backend import main


class ReconcileActiveModeFromAccountTests(unittest.IsolatedAsyncioTestCase):
    """Garante fallback de active_mode quando o status omite o modo."""

    def setUp(self) -> None:
        self.user_id = "reconcile-mode-user"
        # Estado limpo: desconecta e zera modo antes de cada caso.
        state = main.auto_trader.get(self.user_id)
        state.connected = False
        state.active_mode = None
        state.enabled = False
        state.status = "STOPPED"

    async def test_connected_status_without_mode_resolves_real_from_account(self) -> None:
        status_payload = main.build_success(
            {
                "connected": True,
                "email": "Shopbrasiliana@gmai.com",
                "active_mode": None,
            }
        )
        account_payload = main.build_success(
            {
                "connected": True,
                "active_mode": "REAL",
                "mode": "REAL",
                "balance": 10347.46,
                "balance_real": 10347.46,
                "currency": "BRL",
                "email": "Shopbrasiliana@gmai.com",
            }
        )

        with patch.object(
            main,
            "call_bullex_service",
            new=AsyncMock(return_value=(200, account_payload)),
        ) as account_call:
            state, connected, active_mode, source = await main.reconcile_robot_connection_from_payload(
                self.user_id,
                status_payload,
            )

        self.assertTrue(connected)
        self.assertEqual(active_mode, "REAL")
        self.assertEqual(state.active_mode, "REAL")
        self.assertTrue(state.connected)
        self.assertFalse(main.robot_connection_unavailable(connected, active_mode))
        self.assertEqual(source, "bullex_service")
        account_call.assert_awaited()

    async def test_connected_status_with_real_mode_skips_account_lookup(self) -> None:
        status_payload = main.build_success(
            {
                "connected": True,
                "active_mode": "REAL",
                "email": "ok@example.com",
            }
        )

        with patch.object(main, "call_bullex_service", new=AsyncMock()) as account_call:
            state, connected, active_mode, source = await main.reconcile_robot_connection_from_payload(
                self.user_id,
                status_payload,
            )

        self.assertTrue(connected)
        self.assertEqual(active_mode, "REAL")
        self.assertEqual(state.active_mode, "REAL")
        account_call.assert_not_awaited()
        self.assertEqual(source, "bullex_service")

    async def test_sync_connection_preserves_mode_when_status_omits_it(self) -> None:
        main.auto_trader.sync_connection(
            self.user_id,
            connected=True,
            active_mode="REAL",
            source="bullex_service",
        )
        state = main.auto_trader.sync_connection(
            self.user_id,
            connected=True,
            active_mode=None,
            source="bullex_service",
        )
        self.assertEqual(state.active_mode, "REAL")
        self.assertTrue(state.connected)


if __name__ == "__main__":
    unittest.main()
