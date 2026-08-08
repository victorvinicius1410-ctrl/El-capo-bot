import asyncio
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from backend import main as gateway_main
from bullex_service import main as bullex_main


def _init_v2(turbo_open: dict[str, bool], binary_open: dict[str, bool]) -> dict:
    """Monta um retorno de get_all_init_v2 no formato da corretora."""

    def section(open_by_symbol: dict[str, bool]) -> dict:
        actives = {}
        for idx, (symbol, is_open) in enumerate(open_by_symbol.items(), start=1):
            actives[str(idx)] = {
                "name": f"pair.{symbol}",
                "enabled": True,
                "is_suspended": not is_open,
            }
        return {"actives": actives}

    return {"turbo": section(turbo_open), "binary": section(binary_open)}


class BinaryOpenMapHelperTests(unittest.TestCase):
    def test_parse_binary_open_map_reads_enabled_and_suspended(self) -> None:
        init = _init_v2(
            turbo_open={"GBPJPY-OTC": False, "EURUSD-OTC": True},
            binary_open={"GBPJPY-OTC": True},
        )

        result = bullex_main.parse_binary_open_map(init)

        self.assertEqual(result["GBPJPY-OTC"], {"turbo": False, "binary": True})
        self.assertEqual(result["EURUSD-OTC"], {"turbo": True})

    def test_parse_binary_open_map_handles_garbage(self) -> None:
        self.assertEqual(bullex_main.parse_binary_open_map(None), {})
        self.assertEqual(bullex_main.parse_binary_open_map({"turbo": 5}), {})

    def test_option_kind_for_expiration(self) -> None:
        self.assertEqual(bullex_main.binary_option_kind_for_expiration(1), "turbo")
        self.assertEqual(bullex_main.binary_option_kind_for_expiration(5), "turbo")
        self.assertEqual(bullex_main.binary_option_kind_for_expiration(15), "binary")
        self.assertEqual(bullex_main.binary_option_kind_for_expiration(None), "binary")

    def test_option_open_state_prefers_channel(self) -> None:
        open_map = {"GBPJPY-OTC": {"turbo": False, "binary": True}}
        self.assertIs(bullex_main.binary_option_open_state(open_map, "GBPJPY-OTC", 1), False)
        self.assertIs(bullex_main.binary_option_open_state(open_map, "GBPJPY-OTC", 15), True)

    def test_option_open_state_unknown_asset_returns_none(self) -> None:
        self.assertIsNone(bullex_main.binary_option_open_state({}, "GBPJPY-OTC", 1))

    def test_read_binary_open_map_caches_result(self) -> None:
        user_id = "open-map-cache"
        bullex_main.clear_binary_open_cache(user_id)
        client = SimpleNamespace(
            get_all_init_v2=Mock(
                return_value=_init_v2(turbo_open={"EURUSD-OTC": True}, binary_open={})
            )
        )

        first = bullex_main.read_binary_open_map(client, user_id=user_id)
        second = bullex_main.read_binary_open_map(client, user_id=user_id)

        self.assertEqual(first, second)
        self.assertEqual(client.get_all_init_v2.call_count, 1)
        bullex_main.clear_binary_open_cache(user_id)

    def test_read_binary_open_map_swallows_errors_and_backs_off(self) -> None:
        user_id = "open-map-error"
        bullex_main.clear_binary_open_cache(user_id)
        client = SimpleNamespace(get_all_init_v2=Mock(side_effect=AttributeError("boom")))

        self.assertEqual(bullex_main.read_binary_open_map(client, user_id=user_id), {})
        # Falha entra em backoff: não refaz o init_v2 a cada payout.
        self.assertEqual(bullex_main.read_binary_open_map(client, user_id=user_id), {})
        self.assertEqual(client.get_all_init_v2.call_count, 1)
        bullex_main.clear_binary_open_cache(user_id)

    def test_cached_binary_open_map_never_touches_network(self) -> None:
        user_id = "open-map-cold"
        bullex_main.clear_binary_open_cache(user_id)

        self.assertEqual(bullex_main.cached_binary_open_map(user_id), {})


class BuyRealTurboClosedGateTests(unittest.TestCase):
    def _make_session(self, user_id: str, buy_mock: Mock, init_mock: Mock) -> "bullex_main.ManagedSession":
        client = SimpleNamespace(
            check_connect=lambda: True,
            get_balance_mode=Mock(return_value="REAL"),
            get_all_init_v2=init_mock,
            buy=buy_mock,
        )
        return bullex_main.ManagedSession(
            user_id=user_id,
            client=client,
            desired_mode="REAL",
            real_mode_confirmed=True,
            active_mode="REAL",
        )

    def test_buy_real_blocks_when_turbo_channel_closed(self) -> None:
        buy_mock = Mock()
        init_mock = Mock(
            return_value=_init_v2(
                turbo_open={"GBPJPY-OTC": False},
                binary_open={"GBPJPY-OTC": True},
            )
        )
        session = self._make_session("buy-turbo-closed", buy_mock, init_mock)
        bullex_main.clear_binary_open_cache(session.user_id)
        # Cache quente (como os /payouts fazem em produção).
        bullex_main.read_binary_open_map(session.client, user_id=session.user_id)
        manager = bullex_main.SessionManager(None)
        manager.upsert(session)

        payload = bullex_main.BuyOrderRequest(
            active="GBPJPY-OTC",
            amount=100.0,
            action="put",
            expiration=1,
            confirm_real=True,
        )

        with patch.object(bullex_main, "session_manager", manager):
            with self.assertRaises(bullex_main.ServiceError) as raised:
                bullex_main.buy_real(payload, x_user_id=session.user_id)

        self.assertIn("asset is not available", str(raised.exception.message).lower())
        buy_mock.assert_not_called()
        bullex_main.clear_binary_open_cache(session.user_id)

    def test_buy_real_with_cold_cache_does_not_block_or_fetch(self) -> None:
        buy_mock = Mock(return_value=(True, "order-777"))
        init_mock = Mock()
        session = self._make_session("buy-cold-cache", buy_mock, init_mock)
        bullex_main.clear_binary_open_cache(session.user_id)
        manager = bullex_main.SessionManager(None)
        manager.upsert(session)

        payload = bullex_main.BuyOrderRequest(
            active="GBPJPY-OTC",
            amount=100.0,
            action="put",
            expiration=1,
            confirm_real=True,
        )

        with patch.object(bullex_main, "session_manager", manager):
            with patch.object(bullex_main, "ensure_real_balance_id_for_buy", return_value=1):
                result = bullex_main.buy_real(payload, x_user_id=session.user_id)

        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["order_id"], "order-777")
        # Buy não pode pagar latência de init_v2 (janela de 0-5s).
        init_mock.assert_not_called()
        bullex_main.clear_binary_open_cache(session.user_id)


class GatewayExtractAssetOpenTests(unittest.TestCase):
    def _payload(self, **flags) -> dict:
        return {"ok": True, "data": [{"symbol": "GBPJPY-OTC", **flags}]}

    def test_uses_turbo_channel_for_short_timeframe(self) -> None:
        payload = self._payload(open_turbo=False, open_binary=True)
        self.assertIs(gateway_main.extract_asset_open(payload, "GBPJPY-OTC", "M1"), False)

    def test_uses_binary_channel_for_long_timeframe(self) -> None:
        payload = self._payload(open_turbo=False, open_binary=True)
        self.assertIs(gateway_main.extract_asset_open(payload, "GBPJPY-OTC", "M15"), True)

    def test_falls_back_to_is_open_flag(self) -> None:
        payload = self._payload(is_open=False)
        self.assertIs(gateway_main.extract_asset_open(payload, "GBPJPY-OTC", "M1"), False)

    def test_missing_symbol_returns_none(self) -> None:
        payload = {"ok": True, "data": [{"symbol": "EURUSD-OTC", "open_turbo": True}]}
        self.assertIsNone(gateway_main.extract_asset_open(payload, "GBPJPY-OTC", "M1"))

    def test_apply_execution_channel_open_blocks_closed_turbo(self) -> None:
        signal = {
            "symbol": "GBPJPY-OTC",
            "trade_allowed": True,
            "blocked_filters": [],
        }
        gateway_main.apply_execution_channel_open(
            signal,
            channel_open=False,
            timeframe="M1",
        )
        self.assertIs(signal["is_open"], False)
        self.assertIs(signal["trade_allowed"], False)
        self.assertIn("ACTIVE_CLOSED", signal["blocked_filters"])

    def test_closed_channel_fails_pre_order_and_threshold(self) -> None:
        state = SimpleNamespace(min_confidence=80, min_payout=80.0)
        candidate = {
            "symbol": "USDJPY-OTC",
            "direction": "CALL",
            "signal": "CALL",
            "confidence": 90,
            "payout": 90.0,
            "trade_allowed": True,
            "blocked_filters": [],
            "is_open": False,
        }
        self.assertEqual(
            gateway_main.candidate_pre_order_block_reason(candidate),
            "ACTIVE_CLOSED",
        )
        # Após anotar o canal fechado, o portão também barra.
        gateway_main.apply_execution_channel_open(
            candidate,
            channel_open=False,
            timeframe="M1",
        )
        self.assertFalse(
            gateway_main.candidate_meets_cycle_threshold(
                candidate,
                state,
                minimum_confidence=80,
            )
        )


class PreOrderChannelRevalidationTests(unittest.IsolatedAsyncioTestCase):
    """Revalidação do canal na hora da compra (2026-07-29).

    O sinal trava na vela anterior à entrada, então o cache de `/payouts` pode
    ter até 60s quando a ordem sai — tempo suficiente para o canal turbo fechar
    e a corretora rejeitar com "asset is not available".
    """

    def setUp(self) -> None:
        self.user_id = "channel-revalidation"
        gateway_main.session_response_cache.pop(self.user_id, None)
        self.candidate = {
            "symbol": "GBPJPY-OTC",
            "signal": "CALL",
            "direction": "CALL",
            "trade_allowed": True,
            "blocked_filters": [],
            "is_open": True,
        }

    def tearDown(self) -> None:
        gateway_main.session_response_cache.pop(self.user_id, None)

    def _seed_payout_cache(self, *, age_seconds: float, open_turbo: bool) -> None:
        cache = gateway_main.get_session_cache(self.user_id)
        cache_key = gateway_main.build_cache_key("/payouts", {"active": "GBPJPY-OTC"})
        remaining = gateway_main.PAYOUT_CACHE_TTL_SECONDS - age_seconds
        cache.responses[cache_key] = gateway_main.BullexResponseCacheEntry(
            status_code=200,
            payload={
                "ok": True,
                "data": [{"symbol": "GBPJPY-OTC", "open_turbo": open_turbo, "open_binary": True}],
            },
            expires_at=gateway_main.utc_now() + timedelta(seconds=remaining),
        )
        cache.last_successful_responses[cache_key] = cache.responses[cache_key]

    def _payouts_response(self, *, open_turbo: bool) -> tuple[int, dict]:
        return 200, {
            "ok": True,
            "data": [{"symbol": "GBPJPY-OTC", "open_turbo": open_turbo, "open_binary": True}],
        }

    async def test_stale_cache_triggers_fresh_fetch_and_blocks_closed_channel(self) -> None:
        self._seed_payout_cache(age_seconds=45.0, open_turbo=True)
        call_mock = AsyncMock(return_value=self._payouts_response(open_turbo=False))

        with patch.object(gateway_main, "call_bullex_service", call_mock):
            refreshed = await gateway_main.refresh_candidate_execution_channel(
                self.user_id,
                self.candidate,
                "M1",
            )

        call_mock.assert_awaited_once()
        self.assertTrue(call_mock.await_args.kwargs["force_refresh"])
        self.assertIs(refreshed["is_open"], False)
        self.assertIs(refreshed["trade_allowed"], False)
        self.assertEqual(
            gateway_main.candidate_pre_order_block_reason(refreshed),
            "ACTIVE_CLOSED",
        )

    async def test_recent_cache_skips_network(self) -> None:
        """Dado recém-lido não paga latência dentro da janela de 0-5s."""
        self._seed_payout_cache(age_seconds=2.0, open_turbo=True)
        call_mock = AsyncMock()

        with patch.object(gateway_main, "call_bullex_service", call_mock):
            refreshed = await gateway_main.refresh_candidate_execution_channel(
                self.user_id,
                self.candidate,
                "M1",
            )

        call_mock.assert_not_awaited()
        self.assertIs(refreshed["is_open"], True)

    async def test_timeout_keeps_cache_and_does_not_block_the_order(self) -> None:
        self._seed_payout_cache(age_seconds=45.0, open_turbo=True)

        async def never_answers(*args, **kwargs):
            await asyncio.sleep(5)
            raise AssertionError("não deveria completar")

        with patch.object(gateway_main, "call_bullex_service", never_answers):
            refreshed = await gateway_main.refresh_candidate_execution_channel(
                self.user_id,
                self.candidate,
                "M1",
                fresh_timeout_seconds=0.05,
            )

        self.assertIs(refreshed["is_open"], True)
        self.assertIsNone(gateway_main.candidate_pre_order_block_reason(refreshed))

    async def test_exhausted_budget_uses_cache_without_network(self) -> None:
        """Orçamento zerado (candidato de fallback) não consulta a corretora."""
        self._seed_payout_cache(age_seconds=45.0, open_turbo=True)
        call_mock = AsyncMock()

        with patch.object(gateway_main, "call_bullex_service", call_mock):
            refreshed = await gateway_main.refresh_candidate_execution_channel(
                self.user_id,
                self.candidate,
                "M1",
                fresh_timeout_seconds=0.0,
            )

        call_mock.assert_not_awaited()
        self.assertIs(refreshed["is_open"], True)

    async def test_cold_cache_uses_fresh_answer(self) -> None:
        call_mock = AsyncMock(return_value=self._payouts_response(open_turbo=True))

        with patch.object(gateway_main, "call_bullex_service", call_mock):
            refreshed = await gateway_main.refresh_candidate_execution_channel(
                self.user_id,
                self.candidate,
                "M1",
            )

        call_mock.assert_awaited_once()
        self.assertIs(refreshed["is_open"], True)

    async def test_error_from_broker_falls_back_to_cache(self) -> None:
        self._seed_payout_cache(age_seconds=45.0, open_turbo=False)
        call_mock = AsyncMock(side_effect=RuntimeError("upstream caiu"))

        with patch.object(gateway_main, "call_bullex_service", call_mock):
            refreshed = await gateway_main.refresh_candidate_execution_channel(
                self.user_id,
                self.candidate,
                "M1",
            )

        # Cache dizia fechado: continua bloqueando (não relaxa por erro de rede).
        self.assertIs(refreshed["is_open"], False)

    def test_cached_payout_age_seconds_reports_age(self) -> None:
        self._seed_payout_cache(age_seconds=30.0, open_turbo=True)
        age = gateway_main.cached_payout_age_seconds(self.user_id, "GBPJPY-OTC")
        self.assertIsNotNone(age)
        self.assertAlmostEqual(age, 30.0, delta=1.0)

    def test_cached_payout_age_seconds_none_without_cache(self) -> None:
        self.assertIsNone(gateway_main.cached_payout_age_seconds(self.user_id, "GBPJPY-OTC"))


class MarkUnavailableAfterBrokerRejectTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.user_id = "mark-unavailable-user"
        gateway_main.session_response_cache.pop(self.user_id, None)
        gateway_main.active_cooldowns.pop(self.user_id, None)

    def tearDown(self) -> None:
        gateway_main.session_response_cache.pop(self.user_id, None)
        gateway_main.active_cooldowns.pop(self.user_id, None)

    def _seed_open_payout(self) -> None:
        now = gateway_main.utc_now()
        payload = gateway_main.build_success(
            [
                {
                    "symbol": "GBPJPY-OTC",
                    "payout": 87,
                    "open_turbo": True,
                    "open_binary": True,
                    "is_open": True,
                }
            ]
        )
        key = gateway_main.build_cache_key("/payouts", {"active": "GBPJPY-OTC"})
        entry = gateway_main.BullexResponseCacheEntry(200, payload, now + timedelta(seconds=60))
        cache = gateway_main.get_session_cache(self.user_id)
        cache.responses[key] = entry
        cache.last_successful_responses[key] = entry

    def test_mark_execution_channel_unavailable_closes_cache_and_sets_cooldown(self) -> None:
        self._seed_open_payout()
        self.assertIs(
            gateway_main.cached_asset_open_for_active(self.user_id, "GBPJPY-OTC", "M1"),
            True,
        )

        gateway_main.mark_execution_channel_unavailable(
            self.user_id,
            "GBPJPY-OTC",
            "M1",
            seconds=60,
        )

        self.assertIs(
            gateway_main.cached_asset_open_for_active(self.user_id, "GBPJPY-OTC", "M1"),
            False,
        )
        remaining = gateway_main.active_cooldown_remaining(self.user_id, "GBPJPY-OTC")
        self.assertIsNotNone(remaining)
        self.assertGreater(remaining, 40)
        self.assertLessEqual(remaining, 60)

    def test_active_cooldown_is_critical_trade_block(self) -> None:
        self.assertIn("ACTIVE_COOLDOWN", gateway_main.CRITICAL_TRADE_BLOCKS)
        self.assertEqual(
            gateway_main.candidate_pre_order_block_reason(
                {
                    "symbol": "GBPJPY-OTC",
                    "blocked_filters": ["ACTIVE_COOLDOWN"],
                    "is_open": True,
                }
            ),
            "ACTIVE_COOLDOWN",
        )

    def test_mark_binary_option_closed_blocks_buy_gate(self) -> None:
        user_id = "bullex-mark-closed"
        bullex_main.clear_binary_open_cache(user_id)
        bullex_main.mark_binary_option_closed(user_id, "GBPJPY-OTC", 1, ttl_seconds=120)
        open_map = bullex_main.cached_binary_open_map(user_id)
        self.assertIs(bullex_main.binary_option_open_state(open_map, "GBPJPY-OTC", 1), False)
        bullex_main.clear_binary_open_cache(user_id)


if __name__ == "__main__":
    unittest.main()
