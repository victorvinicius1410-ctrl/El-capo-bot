"""Testes: rate limit de login Bullex + pool de proxy."""

from __future__ import annotations

import json
import unittest
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from backend import main
from bullex_service import proxy_config, rate_limit


class TestRateLimitParsing(unittest.TestCase):
    def setUp(self) -> None:
        rate_limit.clear_login_block()

    def tearDown(self) -> None:
        rate_limit.clear_login_block()

    def test_detects_requests_limit_json_blob(self) -> None:
        detail = (
            'falha ao conectar: {"code":"requests_limit_exceeded",'
            '"message":"The number of requests has been exceeded. Try again in 60 minutes.",'
            '"ttl":3600}'
        )
        self.assertTrue(rate_limit.is_requests_limit_error(detail))
        self.assertEqual(rate_limit.extract_requests_limit_ttl(detail), 3600)

    def test_note_requests_limit_sets_global_gate(self) -> None:
        remaining = rate_limit.note_requests_limit(
            {"code": "requests_limit_exceeded", "ttl": 120}
        )
        self.assertGreaterEqual(remaining, 60)
        self.assertGreater(rate_limit.login_block_remaining_seconds(), 0)


class TestProxyConfig(unittest.TestCase):
    def test_parse_http_proxy(self) -> None:
        endpoint = proxy_config.parse_proxy_url("http://user:pass@10.0.0.2:8080")
        assert endpoint is not None
        self.assertEqual(endpoint.host, "10.0.0.2")
        self.assertEqual(endpoint.port, 8080)
        self.assertEqual(endpoint.username, "user")
        self.assertEqual(endpoint.as_requests_dict()["https"], endpoint.url)
        self.assertEqual(endpoint.websocket_kwargs()["http_proxy_host"], "10.0.0.2")

    def test_load_proxy_urls_from_env(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "BULLEX_PROXY_URL": "",
                "BULLEX_PROXY_URLS": "http://a:1,http://b:2,not-a-url",
            },
            clear=False,
        ):
            endpoints = proxy_config.load_proxy_endpoints()
            self.assertEqual(len(endpoints), 2)
            self.assertTrue(proxy_config.proxies_configured())


class TestGatewayConnectErrorClassification(unittest.TestCase):
    def test_classify_rate_limit_from_raw_detail(self) -> None:
        code, retry_after, _ = main.classify_bullex_connect_error(
            'falha ao conectar: {"code":"requests_limit_exceeded","ttl":3600}'
        )
        self.assertEqual(code, main.BULLEX_REQUESTS_LIMIT_EXCEEDED)
        self.assertEqual(retry_after, 3600)

    def test_classify_invalid_credentials_from_raw_detail(self) -> None:
        code, _, _ = main.classify_bullex_connect_error(
            'falha ao conectar: {"code":"invalid_credentials",'
            '"message":"You entered the wrong credentials."}'
        )
        self.assertEqual(code, "invalid_credentials")

    def test_build_controlled_preserves_rate_limit_code(self) -> None:
        payload = main.build_controlled_upstream_error(
            main.BULLEX_REQUESTS_LIMIT_EXCEEDED,
            data={"retry_after_seconds": 1800},
        )
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], main.BULLEX_REQUESTS_LIMIT_EXCEEDED)
        self.assertEqual(payload["data"]["retry_after_seconds"], 1800)


class TestAutoReconnectRespectsRateLimit(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        main.bullex_auto_reconnect_at.clear()
        main.bullex_ssid_reconnect_at.clear()
        main.bullex_login_rate_limited_until = None
        self.user_id = "rate-limit-user"

    def tearDown(self) -> None:
        # `bullex_login_rate_limited_until` é um gate GLOBAL (main.py ~linha
        # 1979) que passa a bloquear /bullex/connect de qualquer usuário até
        # expirar. Sem resetar aqui, o valor setado por um teste desta classe
        # "vaza" para outros arquivos de teste executados depois no mesmo
        # processo (ex.: tests.test_gateway_fast_fallback,
        # tests.test_supabase_resilience via `unittest discover`).
        main.bullex_auto_reconnect_at.clear()
        main.bullex_ssid_reconnect_at.clear()
        main.bullex_login_rate_limited_until = None

    async def test_auto_reconnect_skips_while_global_block_active(self) -> None:
        """Gate de login bloqueia senha; SSID soft ainda pode tentar (não é login HTTP)."""
        main.bullex_login_rate_limited_until = main.utc_now() + timedelta(minutes=30)
        ssid_fail = (404, {"ok": False, "data": {"connected": False}, "error": "SESSION_NOT_FOUND"})
        with patch.object(main, "call_bullex_service", new=AsyncMock(return_value=ssid_fail)) as service:
            ok = await main.try_auto_reconnect_with_saved_credentials(self.user_id)
        self.assertFalse(ok)
        paths = [call.args[1] for call in service.await_args_list]
        self.assertIn("/sessions/reconnect", paths)
        self.assertNotIn("/sessions/connect", paths)

    async def test_connect_gate_returns_rate_limit_without_upstream(self) -> None:
        main.bullex_login_rate_limited_until = main.utc_now() + timedelta(seconds=900)
        with patch.object(main, "call_bullex_service", new=AsyncMock()) as service:
            response = await main._bullex_connect_impl(
                {"email": "a@b.com", "password": "x"},
                {"user_id": self.user_id},
            )
        payload = json.loads(response.body)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], main.BULLEX_REQUESTS_LIMIT_EXCEEDED)
        self.assertGreaterEqual(payload["data"]["retry_after_seconds"], 1)
        service.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
