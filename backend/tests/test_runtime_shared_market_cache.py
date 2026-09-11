"""Cache compartilhado de candles/payouts no robot-runtime/gateway.

Candles e payouts de um ativo são iguais para todos os usuários. Sem este
cache, N robôs no mesmo ciclo disparam N GETs no pool httpx do runtime
(max 100) — em 2026-08-11 isso saturou `total=100 ativas=100` e os ciclos
fecharam em ANALYSIS_TIMEOUT sem comprar. Ver PERFORMANCE_SISTEMA.md.
"""

from __future__ import annotations

import asyncio
import unittest
from datetime import timedelta
from unittest.mock import patch

import httpx

from backend import main


class RecordingClient:
    """Client httpx falso que registra cada request e pode atrasar a resposta."""

    is_closed = False

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.delay_seconds = 0.0
        self.response_by_user: dict[str, httpx.Response] = {}
        self.default_payload = main.build_success(
            [{"from": 1, "to": 61, "open": 1.1, "close": 1.2, "min": 1.05, "max": 1.25}]
        )
        self.default_response = httpx.Response(200, json=self.default_payload)

    async def request(self, **kwargs):
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        self.requests.append(kwargs)
        user_id = str((kwargs.get("headers") or {}).get("x-user-id") or "")
        return self.response_by_user.get(user_id, self.default_response)

    async def aclose(self) -> None:
        self.is_closed = True


class RuntimeSharedMarketCacheTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        main.session_response_cache.clear()
        main.active_users.clear()
        main.background_refresh_tasks.clear()
        main.reset_shared_market_cache()
        main._bullex_http_client = None
        main._bullex_order_http_client = None
        self.client = RecordingClient()
        main._bullex_http_client = self.client
        # Semáforo do loop deste teste; buy-real não usa, candles sim.
        main._bullex_http_semaphore = None
        main._bullex_http_semaphore_loop = None

    async def asyncTearDown(self) -> None:
        await asyncio.sleep(0)
        main.session_response_cache.clear()
        main.active_users.clear()
        main.background_refresh_tasks.clear()
        main.reset_shared_market_cache()
        main._bullex_http_client = None
        main._bullex_order_http_client = None

    @staticmethod
    def _candle_params() -> dict[str, int | str]:
        return {"active": "EURUSD-OTC", "interval": 60, "count": 100, "endtime": 1_700_000_060}

    async def test_second_user_reuses_shared_candles_without_http(self) -> None:
        first_status, first_payload = await main.call_bullex_service(
            "GET",
            "/candles",
            "user-a",
            params=self._candle_params(),
        )
        second_status, second_payload = await main.call_bullex_service(
            "GET",
            "/candles",
            "user-b",
            params=self._candle_params(),
        )

        self.assertEqual(first_status, 200)
        self.assertEqual(second_status, 200)
        self.assertEqual(first_payload["data"], second_payload["data"])
        self.assertEqual(len(self.client.requests), 1)

    async def test_different_assets_do_not_share_cache(self) -> None:
        await main.call_bullex_service(
            "GET",
            "/candles",
            "user-a",
            params={"active": "EURUSD-OTC", "interval": 60, "count": 1, "endtime": 1_700_000_060},
        )
        await main.call_bullex_service(
            "GET",
            "/candles",
            "user-a",
            params={"active": "GBPUSD-OTC", "interval": 60, "count": 1, "endtime": 1_700_000_060},
        )

        self.assertEqual(len(self.client.requests), 2)

    async def test_account_is_not_shared_between_users(self) -> None:
        self.client.default_response = httpx.Response(
            200,
            json=main.build_success(
                {"connected": True, "active_mode": "REAL", "balance": 100, "email": "a@x.com"}
            ),
        )
        main.mark_user_active("user-a")
        main.mark_user_active("user-b")
        await main.call_bullex_service("GET", "/account", "user-a")
        await main.call_bullex_service("GET", "/account", "user-b")

        self.assertEqual(len(self.client.requests), 2)

    async def test_concurrent_same_candles_single_flight(self) -> None:
        self.client.delay_seconds = 0.05
        results = await asyncio.gather(
            main.call_bullex_service("GET", "/candles", "user-a", params=self._candle_params()),
            main.call_bullex_service("GET", "/candles", "user-b", params=self._candle_params()),
            main.call_bullex_service("GET", "/candles", "user-c", params=self._candle_params()),
        )

        self.assertTrue(all(status == 200 for status, _payload in results))
        self.assertEqual(len(self.client.requests), 1)

    async def test_leader_session_error_does_not_poison_other_user(self) -> None:
        self.client.delay_seconds = 0.05
        self.client.response_by_user["user-a"] = httpx.Response(
            409,
            json=main.build_error(main.SESSION_DISCONNECTED),
        )
        self.client.response_by_user["user-b"] = self.client.default_response

        first, second = await asyncio.gather(
            main.call_bullex_service("GET", "/candles", "user-a", params=self._candle_params()),
            main.call_bullex_service("GET", "/candles", "user-b", params=self._candle_params()),
        )

        self.assertEqual(first[0], 409)
        self.assertEqual(first[1].get("error"), main.SESSION_DISCONNECTED)
        self.assertEqual(second[0], 200)
        self.assertTrue(second[1].get("ok"))
        self.assertGreaterEqual(len(self.client.requests), 2)

    async def test_background_refresh_coalesces_per_asset_not_per_user(self) -> None:
        params = self._candle_params()
        await main.call_bullex_service("GET", "/candles", "user-a", params=params)
        self.assertEqual(len(self.client.requests), 1)

        # Cache ainda fresco: segundo usuário NÃO dispara refresh por usuário.
        await main.call_bullex_service("GET", "/candles", "user-b", params=params)
        await asyncio.sleep(0.05)
        self.assertEqual(len(self.client.requests), 1)

        # Envelhece o cache compartilhado além do limiar de refresh.
        cache_key = main.build_cache_key("/candles", params)
        entry = main.get_shared_market_cache_entry(cache_key)
        self.assertIsNotNone(entry)
        assert entry is not None
        entry.expires_at = main.utc_now() + timedelta(seconds=5)

        await main.call_bullex_service("GET", "/candles", "user-a", params=params)
        await main.call_bullex_service("GET", "/candles", "user-b", params=params)
        await asyncio.sleep(0.1)

        self.assertEqual(len(self.client.requests), 2)

    async def test_timeout_fallback_uses_shared_cache(self) -> None:
        params = self._candle_params()
        await main.call_bullex_service("GET", "/candles", "user-a", params=params)

        async def boom(**_kwargs):
            raise httpx.PoolTimeout("pool full")

        with patch.object(self.client, "request", side_effect=boom):
            status, payload = await main.call_bullex_service(
                "GET",
                "/candles",
                "user-b",
                params=params,
                force_refresh=True,
            )

        self.assertEqual(status, 200)
        self.assertTrue(payload.get("ok"))
        self.assertTrue(payload.get("data"))

    async def test_pool_timeout_recycles_http_client(self) -> None:
        """PoolTimeout deve fechar o client vazado para o próximo GET nascer limpo."""

        async def boom(**_kwargs):
            raise httpx.PoolTimeout("pool full")

        with patch.object(self.client, "request", side_effect=boom):
            status, payload = await main.call_bullex_service(
                "GET",
                "/candles",
                "user-a",
                params=self._candle_params(),
            )

        self.assertEqual(status, 503)
        self.assertEqual(payload.get("error"), main.BULLEX_TEMPORARY_UNAVAILABLE)
        self.assertTrue(self.client.is_closed)
        self.assertIsNone(main._bullex_http_client)

    async def test_connect_timeout_does_not_recycle_http_client(self) -> None:
        """ConnectTimeout não pode aclose o client — isso gerava tempestade de recycle."""

        async def boom(**_kwargs):
            raise httpx.ConnectTimeout("connect")

        with patch.object(self.client, "request", side_effect=boom):
            status, payload = await main.call_bullex_service(
                "GET",
                "/candles",
                "user-a",
                params=self._candle_params(),
            )

        self.assertEqual(status, 503)
        self.assertEqual(payload.get("error"), main.BULLEX_TEMPORARY_UNAVAILABLE)
        self.assertFalse(self.client.is_closed)
        self.assertIs(main._bullex_http_client, self.client)

    async def test_pool_timeout_recycle_respects_cooldown(self) -> None:
        """Dois PoolTimeout seguidos não podem aclose duas vezes (corrida de 40 GETs)."""
        closes = {"count": 0}
        original_aclose = self.client.aclose

        async def counting_aclose() -> None:
            closes["count"] += 1
            await original_aclose()

        self.client.aclose = counting_aclose  # type: ignore[method-assign]

        async def boom(**_kwargs):
            raise httpx.PoolTimeout("pool full")

        with patch.object(self.client, "request", side_effect=boom):
            await asyncio.gather(
                main.call_bullex_service(
                    "GET", "/candles", "user-a", params=self._candle_params()
                ),
                main.call_bullex_service(
                    "GET",
                    "/candles",
                    "user-b",
                    params={"active": "GBPUSD-OTC", "interval": 60, "count": 1, "endtime": 1_700_000_060},
                ),
            )

        self.assertEqual(closes["count"], 1)

    def test_http_client_pool_matches_inflight_semaphore(self) -> None:
        """max_connections não pode ser 100 se o semáforo só libera 40 in-flight."""
        main._bullex_http_client = None
        client = main.get_bullex_http_client()
        try:
            limits = client._transport._pool._max_connections  # noqa: SLF001
        except Exception:
            limits = client._limits.max_connections
        self.assertEqual(limits, main.BULLEX_HTTP_MAX_INFLIGHT)
        self.assertEqual(main.BULLEX_HTTP_MAX_CONNECTIONS, main.BULLEX_HTTP_MAX_INFLIGHT)

    def test_should_recycle_only_on_pool_timeout(self) -> None:
        """ConnectTimeout/TimeoutError não reciclam o client — só PoolTimeout."""
        self.assertTrue(main.should_recycle_bullex_http_client(httpx.PoolTimeout("full")))
        self.assertFalse(main.should_recycle_bullex_http_client(httpx.ConnectTimeout("connect")))
        self.assertFalse(main.should_recycle_bullex_http_client(TimeoutError("wait_for")))

    def test_buy_real_uses_extended_timeout(self) -> None:
        """Compra REAL não pode herdar o timeout curto de 5s das rotas genéricas."""
        self.assertEqual(
            main.bullex_timeout_for_request("POST", "/orders/buy-real"),
            main.BULLEX_BUY_TIMEOUT_SECONDS,
        )
        self.assertGreater(main.BULLEX_BUY_TIMEOUT_SECONDS, main.BULLEX_UPSTREAM_TIMEOUT_SECONDS)
        self.assertGreaterEqual(main.BULLEX_BUY_TIMEOUT_SECONDS, 45.0)
        self.assertEqual(
            main.bullex_timeout_for_request("GET", "/candles"),
            main.BULLEX_MARKET_DATA_TIMEOUT_SECONDS,
        )

    async def test_buy_real_bypasses_market_http_semaphore(self) -> None:
        """Compra REAL não pode esperar o semáforo dos candles (fila 40 cheia)."""
        main._bullex_http_semaphore = asyncio.Semaphore(0)
        main._bullex_http_semaphore_loop = asyncio.get_running_loop()
        self.client.default_payload = main.build_success(
            {"order_id": 99, "mode": "REAL", "active": "EURUSD-OTC"}
        )
        self.client.default_response = httpx.Response(200, json=self.client.default_payload)

        status, payload = await main.call_bullex_service(
            "POST",
            "/orders/buy-real",
            "user-a",
            json_body={"active": "EURUSD-OTC", "action": "call", "amount": 5, "expiration": 1},
        )

        self.assertEqual(status, 200)
        self.assertTrue(payload.get("ok"))
        self.assertEqual((payload.get("data") or {}).get("order_id"), 99)

    async def test_buy_real_uses_dedicated_order_http_client(self) -> None:
        """POST de ordem usa o client próprio — candles saturados não roubam a conexão."""
        order_client = RecordingClient()
        order_client.default_payload = main.build_success({"order_id": 7, "mode": "REAL"})
        order_client.default_response = httpx.Response(200, json=order_client.default_payload)
        main._bullex_order_http_client = order_client

        await main.call_bullex_service(
            "POST",
            "/orders/buy-real",
            "user-a",
            json_body={"active": "EURUSD-OTC", "action": "call", "amount": 5, "expiration": 1},
        )

        self.assertEqual(len(order_client.requests), 1)
        self.assertEqual(len(self.client.requests), 0)
        self.assertTrue(str(order_client.requests[0]["url"]).endswith("/orders/buy-real"))


if __name__ == "__main__":
    unittest.main()
