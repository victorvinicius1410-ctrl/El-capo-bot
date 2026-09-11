"""Testes: auto-reconexão Bullex ao entrar no painel (status/account/reconnect)."""

from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock, patch

from backend import main


class TestBullexPanelAutoReconnect(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        main.bullex_auto_reconnect_at.clear()
        main.bullex_ssid_reconnect_at.clear()
        self.user_id = "panel-auto-reconnect-user"
        # Regra de 10/08: o poll do painel só reconecta sozinho com o ROBÔ
        # LIGADO (`panel_auto_reconnect_allowed`). Antes bastava ter
        # credencial salva, e abrir Configurações logava na corretora sozinho.
        # Os testes abaixo cobrem o caso legítimo — robô operando, sessão caiu
        # por conta da corretora. O caso bloqueado tem teste próprio.
        main.bullex_manual_disconnect.discard(self.user_id)
        self.addCleanup(main.bullex_manual_disconnect.discard, self.user_id)
        main.auto_trader.get(self.user_id).enabled = True

    async def test_status_auto_reconnects_when_session_dead_and_credentials_saved(self) -> None:
        disconnected = (
            404,
            {"ok": False, "data": {"connected": False}, "error": "SESSION_NOT_FOUND"},
        )
        connected = (
            200,
            {
                "ok": True,
                "data": {
                    "connected": True,
                    "status": "CONNECTED",
                    "active_mode": "REAL",
                    "email": "trader@example.com",
                },
            },
        )
        service = AsyncMock(side_effect=[disconnected, connected])

        with (
            patch.object(main, "call_bullex_service", new=service),
            patch.object(main, "try_auto_reconnect_with_saved_credentials", new=AsyncMock(return_value=True)) as auto,
        ):
            response = await main._bullex_status_impl({"user_id": self.user_id})

        payload = json.loads(response.body)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["data"]["connected"])
        auto.assert_awaited_once_with(self.user_id)
        self.assertEqual(service.await_count, 2)

    async def test_status_stays_disconnected_without_saved_credentials(self) -> None:
        disconnected = (
            404,
            {"ok": False, "data": {"connected": False}, "error": "SESSION_NOT_FOUND"},
        )

        with (
            patch.object(main, "call_bullex_service", new=AsyncMock(return_value=disconnected)),
            patch.object(main, "try_auto_reconnect_with_saved_credentials", new=AsyncMock(return_value=False)) as auto,
            patch.object(main, "memory_status_fallback", return_value=None),
        ):
            response = await main._bullex_status_impl({"user_id": self.user_id})

        payload = json.loads(response.body)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["data"]["connected"])
        self.assertIn("credentials_saved", payload["data"])
        auto.assert_awaited_once_with(self.user_id)

    async def test_account_auto_reconnects_when_session_dead_and_credentials_saved(self) -> None:
        disconnected = (
            404,
            {"ok": False, "data": {"connected": False}, "error": "SESSION_NOT_FOUND"},
        )
        connected_account = (
            200,
            {
                "ok": True,
                "data": {
                    "connected": True,
                    "active_mode": "REAL",
                    "mode": "REAL",
                    "balance": 150.0,
                    "balance_real": 150.0,
                    "currency": "BRL",
                    "email": "trader@example.com",
                },
            },
        )
        service = AsyncMock(side_effect=[disconnected, connected_account])

        with (
            patch.object(main, "call_bullex_service", new=service),
            patch.object(main, "try_auto_reconnect_with_saved_credentials", new=AsyncMock(return_value=True)) as auto,
            patch.object(main, "memory_account_fallback", return_value=None),
        ):
            response = await main._bullex_account_impl({"user_id": self.user_id})

        payload = json.loads(response.body)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["data"]["connected"])
        auto.assert_awaited_once_with(self.user_id)
        self.assertEqual(service.await_count, 2)

    async def test_status_does_not_auto_reconnect_when_robot_is_off(self) -> None:
        """Regressão do relato do dono (09/08, repetido em 10/08).

        "Entro no ElCapo, vou na página de configurações e mesmo sem clicar no
        botão 'Entrar na Bullex' ele conecta sozinho depois de uns segundos."
        Com o robô desligado, o poll do painel não pode logar na corretora —
        nem tendo credencial salva.
        """
        main.auto_trader.get(self.user_id).enabled = False
        disconnected = (
            404,
            {"ok": False, "data": {"connected": False}, "error": "SESSION_NOT_FOUND"},
        )

        with (
            patch.object(main, "call_bullex_service", new=AsyncMock(return_value=disconnected)),
            patch.object(main, "try_auto_reconnect_with_saved_credentials", new=AsyncMock(return_value=True)) as auto,
            patch.object(main, "memory_status_fallback", return_value=None),
            patch.object(main.robot_bus, "is_manual_disconnect", return_value=False),
        ):
            response = await main._bullex_status_impl({"user_id": self.user_id})

        payload = json.loads(response.body)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(payload["data"]["connected"])
        auto.assert_not_awaited()

    async def test_account_does_not_auto_reconnect_when_robot_is_off(self) -> None:
        """Mesma regra no /bullex/account — o painel consulta os dois no poll."""
        main.auto_trader.get(self.user_id).enabled = False
        disconnected = (
            404,
            {"ok": False, "data": {"connected": False}, "error": "SESSION_NOT_FOUND"},
        )

        with (
            patch.object(main, "call_bullex_service", new=AsyncMock(return_value=disconnected)),
            patch.object(main, "try_auto_reconnect_with_saved_credentials", new=AsyncMock(return_value=True)) as auto,
            patch.object(main, "memory_account_fallback", return_value=None),
            patch.object(main.robot_bus, "is_manual_disconnect", return_value=False),
        ):
            await main._bullex_account_impl({"user_id": self.user_id})

        auto.assert_not_awaited()

    async def test_reconnect_refuses_after_manual_disconnect(self) -> None:
        """``POST /bullex/reconnect`` é chamado AUTOMATICAMENTE pelo painel.

        Por isso não pode valer como intenção do cliente: era ele que apagava
        a marca durável no Redis e trazia a conta de volta depois do
        logout/login (quando o `sessionStorage` do front já tinha sumido).
        Só ``POST /bullex/connect`` desfaz a desconexão manual.
        """
        with (
            patch.object(main, "call_bullex_service", new=AsyncMock()) as service,
            patch.object(main, "try_auto_reconnect_with_saved_credentials", new=AsyncMock(return_value=True)) as auto,
            patch.object(main.robot_bus, "is_manual_disconnect", return_value=True),
            patch.object(main.robot_bus, "set_manual_disconnect") as clear_mark,
        ):
            response = await main.bullex_reconnect({"user_id": self.user_id})

        payload = json.loads(response.body)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(payload["data"]["connected"])
        service.assert_not_awaited()
        auto.assert_not_awaited()
        clear_mark.assert_not_called()

    async def test_reconnect_uses_saved_credentials_even_without_session_error_code(self) -> None:
        """Fallback de login salvo deve rodar sempre que a sessão não estiver conectada."""
        failed_ssid = (
            200,
            {"ok": False, "data": {"connected": False}, "error": "RECONNECT_FAILED"},
        )
        restored = (
            200,
            {
                "ok": True,
                "data": {
                    "connected": True,
                    "status": "CONNECTED",
                    "active_mode": "REAL",
                },
            },
        )
        service = AsyncMock(side_effect=[failed_ssid, restored])

        with (
            patch.object(main, "call_bullex_service", new=service),
            patch.object(main, "try_auto_reconnect_with_saved_credentials", new=AsyncMock(return_value=True)) as auto,
            patch.object(main, "sync_user_store_from_payload"),
            patch.object(main, "clear_session_backoff"),
        ):
            response = await main.bullex_reconnect({"user_id": self.user_id})

        payload = json.loads(response.body)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["ok"])
        auto.assert_awaited_once_with(self.user_id)

    async def test_reconnect_never_leaks_raw_upstream_status_when_fallback_fails(self) -> None:
        """Regressão: sessão ausente (404) + login salvo falho não pode virar
        404/409 cru pro frontend — o painel trata isso como erro genérico e
        não tenta reconectar de novo sozinho (usuário precisa clicar manual).
        """
        session_not_found = (
            404,
            {"ok": False, "data": {"connected": False}, "error": "SESSION_NOT_FOUND"},
        )

        with (
            patch.object(main, "call_bullex_service", new=AsyncMock(return_value=session_not_found)) as service,
            patch.object(main, "try_auto_reconnect_with_saved_credentials", new=AsyncMock(return_value=False)) as auto,
            patch.object(main, "sync_user_store_from_payload"),
            patch.object(main, "clear_session_backoff"),
        ):
            response = await main.bullex_reconnect({"user_id": self.user_id})

        payload = json.loads(response.body)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(payload["ok"])
        self.assertIsInstance(payload.get("data"), dict)
        self.assertIn("retry_after_seconds", payload["data"])
        auto.assert_awaited_once_with(self.user_id)
        service.assert_awaited_once_with("POST", "/sessions/reconnect", self.user_id)

    async def test_reconnect_leaks_nothing_even_on_409_disconnected(self) -> None:
        """Mesma regra para SESSION_DISCONNECTED (409) do bullex-service."""
        disconnected_409 = (
            409,
            {"ok": False, "data": {"connected": False}, "error": "SESSION_DISCONNECTED"},
        )

        with (
            patch.object(main, "call_bullex_service", new=AsyncMock(return_value=disconnected_409)),
            patch.object(main, "try_auto_reconnect_with_saved_credentials", new=AsyncMock(return_value=False)),
            patch.object(main, "sync_user_store_from_payload"),
            patch.object(main, "clear_session_backoff"),
        ):
            response = await main.bullex_reconnect({"user_id": self.user_id})

        payload = json.loads(response.body)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(payload["ok"])


class TestStatusOmitsActiveModeKeepsRobotRunning(unittest.IsolatedAsyncioTestCase):
    """Regressão: poll de status sem active_mode não pode desligar o robô."""

    def setUp(self) -> None:
        self.user_id = "status-omitted-mode-user"
        state = main.auto_trader.get(self.user_id)
        state.enabled = True
        state.connected = True
        state.active_mode = "REAL"
        state.account_mode = "REAL"
        state.status = "WAITING_NEXT_CYCLE"
        state.allow_real = True
        state.confirm_real = True

    async def test_status_with_null_active_mode_does_not_stop_robot(self) -> None:
        status_payload = {
            "ok": True,
            "data": {
                "connected": True,
                "status": "CONNECTED",
                "active_mode": None,
            },
        }

        with (
            patch.object(
                main,
                "call_bullex_service",
                new=AsyncMock(return_value=(200, status_payload)),
            ),
            patch.object(main, "stop_robot_worker", new=AsyncMock()) as stop_worker,
            patch.object(main, "persist_robot"),
        ):
            response = await main._bullex_status_impl({"user_id": self.user_id})

        payload = json.loads(response.body)
        state = main.auto_trader.get(self.user_id)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["data"]["connected"])
        self.assertTrue(state.enabled)
        self.assertEqual(state.active_mode, "REAL")
        self.assertNotEqual(state.status, "BULLEX_ACTIVE_MODE_NOT_REAL")
        stop_worker.assert_not_awaited()
        robot = payload["data"].get("robot") or {}
        self.assertTrue(robot.get("enabled"))
        self.assertEqual(robot.get("active_mode"), "REAL")

    async def test_status_with_practice_mode_still_stops_robot(self) -> None:
        status_payload = {
            "ok": True,
            "data": {
                "connected": True,
                "status": "CONNECTED",
                "active_mode": "PRACTICE",
            },
        }

        with (
            patch.object(
                main,
                "call_bullex_service",
                new=AsyncMock(return_value=(200, status_payload)),
            ),
            patch.object(main, "stop_robot_worker", new=AsyncMock()) as stop_worker,
            patch.object(main, "persist_robot"),
        ):
            response = await main._bullex_status_impl({"user_id": self.user_id})

        payload = json.loads(response.body)
        state = main.auto_trader.get(self.user_id)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(state.enabled)
        self.assertEqual(state.status, "BULLEX_ACTIVE_MODE_NOT_REAL")
        stop_worker.assert_awaited()


if __name__ == "__main__":
    unittest.main()
