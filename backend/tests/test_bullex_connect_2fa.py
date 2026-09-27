"""Conta BullEx com verificação em 2 etapas: erro próprio, não "aguarde".

Caso real de 26/09/2026: a corretora recusava o pedido de código por SMS
("This verification method is not supported...") e o painel mostrava
"A corretora está temporariamente indisponível" — o cliente esperou um dia
por um erro que só ele podia resolver desligando o 2FA.
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock, patch

from backend import main

BROKER_2FA_DETAIL = (
    "falha ao conectar: This verification method is not supported. "
    "Choose another method or try again later."
)


class TestClassify2FA(unittest.TestCase):
    def test_unsupported_verification_method_is_2fa(self) -> None:
        code, _, _ = main.classify_bullex_connect_error(BROKER_2FA_DETAIL)
        self.assertEqual(code, main.BULLEX_2FA_ENABLED)

    def test_sms_reason_is_2fa(self) -> None:
        code, _, _ = main.classify_bullex_connect_error("2FA")
        self.assertEqual(code, main.BULLEX_2FA_ENABLED)

    def test_unknown_error_still_temporary(self) -> None:
        code, _, _ = main.classify_bullex_connect_error("falha ao conectar: boom")
        self.assertEqual(code, main.BULLEX_TEMPORARY_UNAVAILABLE)

    def test_controlled_error_carries_message_for_old_bundle(self) -> None:
        payload = main.build_controlled_upstream_error(BROKER_2FA_DETAIL)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], main.BULLEX_2FA_ENABLED)
        self.assertIn("verificação em duas etapas", payload["message"])

    def test_other_errors_have_no_message(self) -> None:
        payload = main.build_controlled_upstream_error("falha ao conectar: boom")
        self.assertNotIn("message", payload)


class TestConnect2FA(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        main.bullex_login_rate_limited_until = None
        self.user_id = "twofa-user"
        self._patches = [
            patch.object(main, "set_manual_disconnect"),
            patch.object(main, "mark_user_active"),
            patch.object(main, "reset_session_connection_cache"),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()

    async def _connect(self, upstream: tuple[int, dict]) -> dict:
        with patch.object(main, "call_bullex_service", new=AsyncMock(return_value=upstream)), \
                patch.object(main, "persist_bullex_credentials") as persist:
            response = await main._bullex_connect_impl(
                {"email": "a@b.com", "password": "x"},
                {"user_id": self.user_id},
            )
        self.assertEqual(response.status_code, 200)
        self.persisted = persist.called
        return json.loads(response.body)

    async def test_broker_refuses_verification_method(self) -> None:
        payload = await self._connect((401, {"ok": False, "error": BROKER_2FA_DETAIL}))
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], main.BULLEX_2FA_ENABLED)
        self.assertEqual(payload["message"], main.BULLEX_2FA_ENABLED_MESSAGE)

    async def test_sms_sent_is_not_reported_as_connected(self) -> None:
        payload = await self._connect(
            (200, {"ok": True, "data": {"connected": False, "requires_2fa": True}})
        )
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], main.BULLEX_2FA_ENABLED)
        self.assertFalse(self.persisted)


if __name__ == "__main__":
    unittest.main()
