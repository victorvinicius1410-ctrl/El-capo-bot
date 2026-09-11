import asyncio
import unittest
from datetime import timedelta
from unittest.mock import patch

from backend import main


class RobotMarketDataResilienceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        main.active_cooldowns.clear()
        main.payout_cooldowns.clear()
        main.session_response_cache.clear()
        main.analysis_asset_queue_offsets.clear()
        main.reset_shared_market_cache()

    def tearDown(self) -> None:
        main.active_cooldowns.clear()
        main.payout_cooldowns.clear()
        main.session_response_cache.clear()
        main.analysis_asset_queue_offsets.clear()
        main.reset_shared_market_cache()

    async def test_scan_continues_when_one_active_times_out(self) -> None:
        async def fake_analyze_active_signal(
            user_id: str,
            symbol: str,
            timeframe: str = "M1",
            endtime: int | None = None,
            strategy_mode: str = "conservative",
        ):
            if symbol == "EURUSD-OTC":
                await asyncio.sleep(1)
            if symbol == "GBPUSD-OTC":
                return 200, main.build_success(
                    {
                        "symbol": symbol,
                        "signal": "CALL",
                        "direction": "CALL",
                        "confidence": 88,
                        "strategy_score": 88,
                        "payout": 82,
                        "trade_allowed": True,
                    }
                )
            return 200, main.build_success(
                {
                    "symbol": symbol,
                    "signal": "WAIT",
                    "direction": "WAIT",
                    "confidence": 0,
                    "strategy_score": 0,
                    "payout": None,
                    "trade_allowed": False,
                }
            )

        with (
            patch.object(main, "ANALYSIS_ASSETS", ["EURUSD-OTC", "GBPUSD-OTC", "USDJPY-OTC"]),
            patch.object(main, "ROBOT_ANALYSIS_ASSET_TIMEOUT_SECONDS", 0.05),
            patch.object(main, "analyze_active_signal", side_effect=fake_analyze_active_signal),
            self.assertLogs("backend-gateway", level="INFO") as logs,
        ):
            status_code, payload = await main.scan_local_signals(
                "resilient-user",
                limit=10,
                include_wait=False,
                max_assets=10,
            )

        self.assertEqual(status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(len(payload["data"]), 1)
        self.assertEqual(payload["data"][0]["symbol"], "GBPUSD-OTC")
        output = "\n".join(logs.output)
        self.assertIn("[ACTIVE_TIMEOUT]", output)
        self.assertIn("[ASSET_SCORE]", output)
        self.assertIn("[SIGNAL SCAN SUMMARY]", output)

    async def test_batch_timeout_does_not_cooldown_every_active(self) -> None:
        async def fake_analyze_active_signal(
            user_id: str,
            symbol: str,
            timeframe: str = "M1",
            endtime: int | None = None,
            strategy_mode: str = "conservative",
        ):
            await asyncio.sleep(1)

        assets = ["EURUSD-OTC", "GBPUSD-OTC", "USDJPY-OTC"]
        with (
            patch.object(main, "ANALYSIS_ASSETS", assets),
            patch.object(main, "ROBOT_ANALYSIS_ASSET_TIMEOUT_SECONDS", 0.02),
            patch.object(main, "analyze_active_signal", side_effect=fake_analyze_active_signal),
            self.assertLogs("backend-gateway", level="INFO") as logs,
        ):
            status_code, payload = await main.scan_local_signals(
                "batch-timeout-user",
                limit=10,
                include_wait=False,
                max_assets=10,
            )

        self.assertEqual(status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"], [])
        self.assertEqual(main.active_cooldowns.get("batch-timeout-user"), None)
        output = "\n".join(logs.output)
        self.assertIn("[ANALYSIS_BATCH_TIMEOUT]", output)

    async def test_scan_analyzes_assets_sequentially(self) -> None:
        call_order: list[str] = []

        async def fake_analyze_active_signal(
            user_id: str,
            symbol: str,
            timeframe: str = "M1",
            endtime: int | None = None,
            strategy_mode: str = "conservative",
        ):
            call_order.append(symbol)
            await asyncio.sleep(0.01)
            return 200, main.build_success(
                {
                    "symbol": symbol,
                    "signal": "WAIT",
                    "confidence": 0,
                    "trade_allowed": False,
                }
            )

        assets = ["EURUSD-OTC", "GBPUSD-OTC", "USDJPY-OTC"]
        with (
            patch.object(main, "ANALYSIS_ASSETS", assets),
            patch.object(main, "analyze_active_signal", side_effect=fake_analyze_active_signal),
        ):
            status_code, payload = await main.scan_local_signals(
                "sequential-user",
                limit=10,
                include_wait=True,
                max_assets=10,
                market_mode="OTC",
            )

        self.assertEqual(status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(call_order, assets)

    async def test_scan_refreshes_worker_heartbeat_before_each_asset(self) -> None:
        user_id = "scan-heartbeat-user"
        stale_tick = main.utc_now() - timedelta(minutes=10)
        observed_fresh_heartbeat: list[bool] = []

        async def fake_analyze_active_signal(
            _user_id: str,
            symbol: str,
            timeframe: str = "M1",
            endtime: int | None = None,
            strategy_mode: str = "conservative",
        ):
            del symbol, timeframe, endtime, strategy_mode
            current_tick = main.robot_worker_last_tick_at.get(user_id)
            observed_fresh_heartbeat.append(
                current_tick is not None and current_tick > stale_tick
            )
            main.robot_worker_last_tick_at[user_id] = stale_tick
            return 200, main.build_success(
                {
                    "signal": "WAIT",
                    "confidence": 0,
                    "trade_allowed": False,
                }
            )

        main.robot_worker_last_tick_at[user_id] = stale_tick
        with (
            patch.object(main, "ANALYSIS_ASSETS", ["EURUSD-OTC", "GBPUSD-OTC"]),
            patch.object(main, "analyze_active_signal", side_effect=fake_analyze_active_signal),
        ):
            await main.scan_local_signals(
                user_id,
                include_wait=True,
                max_assets=2,
                market_mode="OTC",
            )

        self.assertEqual(observed_fresh_heartbeat, [True, True])
        self.assertGreater(main.robot_worker_last_tick_at[user_id], stale_tick)

    async def test_cancelled_scan_resumes_from_first_unfinished_asset(self) -> None:
        user_id = "scan-resume-user"
        assets = ["EURUSD-OTC", "GBPUSD-OTC", "USDJPY-OTC"]
        blocked_asset_started = asyncio.Event()
        release_blocked_asset = asyncio.Event()
        first_call_order: list[str] = []

        async def blocking_analysis(
            _user_id: str,
            symbol: str,
            timeframe: str = "M1",
            endtime: int | None = None,
            strategy_mode: str = "conservative",
        ):
            del timeframe, endtime, strategy_mode
            first_call_order.append(symbol)
            if symbol == "GBPUSD-OTC":
                blocked_asset_started.set()
                await release_blocked_asset.wait()
            return 200, main.build_success(
                {
                    "symbol": symbol,
                    "signal": "WAIT",
                    "confidence": 0,
                    "trade_allowed": False,
                }
            )

        with (
            patch.object(main, "ANALYSIS_ASSETS", assets),
            patch.object(main, "analyze_active_signal", side_effect=blocking_analysis),
        ):
            scan_task = asyncio.create_task(
                main.scan_local_signals(
                    user_id,
                    include_wait=True,
                    max_assets=len(assets),
                    market_mode="OTC",
                )
            )
            await asyncio.wait_for(blocked_asset_started.wait(), timeout=1)
            scan_task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await scan_task

        resumed_call_order: list[str] = []

        async def resumed_analysis(
            _user_id: str,
            symbol: str,
            timeframe: str = "M1",
            endtime: int | None = None,
            strategy_mode: str = "conservative",
        ):
            del timeframe, endtime, strategy_mode
            resumed_call_order.append(symbol)
            return 200, main.build_success(
                {
                    "symbol": symbol,
                    "signal": "WAIT",
                    "confidence": 0,
                    "trade_allowed": False,
                }
            )

        with (
            patch.object(main, "ANALYSIS_ASSETS", assets),
            patch.object(main, "analyze_active_signal", side_effect=resumed_analysis),
        ):
            await main.scan_local_signals(
                user_id,
                include_wait=True,
                max_assets=len(assets),
                market_mode="OTC",
            )

        self.assertEqual(first_call_order, ["EURUSD-OTC", "GBPUSD-OTC"])
        self.assertEqual(resumed_call_order[0], "GBPUSD-OTC")
        self.assertEqual(set(resumed_call_order), set(assets))

    async def test_scan_stops_after_first_trade_allowed(self) -> None:
        """Early stop, quando ligado, encerra a varredura no 1º aprovado.

        Desde 2026-09-03 o padrão é varrer os 10 ativos
        (``ANALYSIS_EARLY_STOP=false``): em produção, 602 de 1.493 varreduras
        pontuavam 1 ativo só e o ranking de substituição ficava sem candidato
        para comparar. O teste liga o early stop explicitamente para continuar
        cobrindo o caminho antigo.
        """
        """Com CALL/PUT aprovado, o scan não pode gastar o ciclo nos ativos restantes."""
        called: list[str] = []

        async def fake_analyze_active_signal(
            user_id: str,
            symbol: str,
            timeframe: str = "M1",
            endtime: int | None = None,
            strategy_mode: str = "conservative",
        ):
            del user_id, timeframe, endtime, strategy_mode
            called.append(symbol)
            if symbol == "GBPUSD-OTC":
                return 200, main.build_success(
                    {
                        "symbol": symbol,
                        "signal": "CALL",
                        "direction": "CALL",
                        "confidence": 92,
                        "strategy_score": 92,
                        "payout": 85,
                        "trade_allowed": True,
                    }
                )
            return 200, main.build_success(
                {
                    "symbol": symbol,
                    "signal": "WAIT",
                    "direction": "WAIT",
                    "confidence": 0,
                    "strategy_score": 0,
                    "payout": None,
                    "trade_allowed": False,
                }
            )

        with (
            patch.object(main, "ANALYSIS_ASSETS", ["EURUSD-OTC", "GBPUSD-OTC", "USDJPY-OTC"]),
            patch.object(main, "ANALYSIS_EARLY_STOP_ENABLED", True),
            patch.object(main, "analyze_active_signal", side_effect=fake_analyze_active_signal),
            self.assertLogs("backend-gateway", level="INFO") as logs,
        ):
            status_code, payload = await main.scan_local_signals(
                "early-stop-user",
                limit=10,
                include_wait=True,
                max_assets=10,
                market_mode="OTC",
            )

        self.assertEqual(status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(called, ["EURUSD-OTC", "GBPUSD-OTC"])
        self.assertEqual(
            [item["symbol"] for item in payload["data"] if item.get("trade_allowed")],
            ["GBPUSD-OTC"],
        )
        self.assertIn("[ANALYSIS_EARLY_STOP]", "\n".join(logs.output))

    async def test_scan_varre_todos_os_ativos_por_padrao(self) -> None:
        """Com o padrão novo, o 1º aprovado não encerra a varredura."""
        called: list[str] = []

        async def fake_analyze_active_signal(user_id, symbol, **kwargs):
            called.append(symbol)
            return 200, {
                "ok": True,
                "data": {
                    "symbol": symbol,
                    "signal": "CALL",
                    "trade_allowed": True,
                    "confidence": 90,
                    "strategy_score": 90,
                    "payout": 87.0,
                },
            }

        with (
            patch.object(main, "ANALYSIS_ASSETS", ["EURUSD-OTC", "GBPUSD-OTC", "USDJPY-OTC"]),
            patch.object(main, "ANALYSIS_EARLY_STOP_ENABLED", False),
            patch.object(main, "analyze_active_signal", side_effect=fake_analyze_active_signal),
        ):
            status_code, payload = await main.scan_local_signals(
                "full-scan-user",
                limit=10,
                include_wait=True,
                max_assets=10,
                market_mode="OTC",
            )

        self.assertEqual(status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(called, ["EURUSD-OTC", "GBPUSD-OTC", "USDJPY-OTC"])

    def test_scan_budget_fits_inside_entry_window_worst_case(self) -> None:
        """ROBOT_ANALYSIS_SCAN_BUDGET_SECONDS não pode estourar a janela de compra.

        A análise pode começar em qualquer segundo entre
        ``ANALYSIS_WINDOWS["M1"]`` (5–20s da vela) e só pode comprar nos
        primeiros ``ENTRY_WINDOWS["M1"]`` (0–3s desde 2026-08-30) da vela
        SEGUINTE. No pior caso (início no segundo 20), o orçamento de scan tem
        que caber em ``(60 - 20) + 3 = 43s`` para o candidato ainda disparar a
        compra na janela imediatamente seguinte, sem cair num
        `[ENTRY_WINDOW_MISSED]` sistemático (18/08 ~19h: 85s de orçamento
        terminava 25–40s dentro da vela seguinte).
        """
        timeframe_seconds = main.TIMEFRAME_SECONDS["M1"]
        _, analysis_window_end = main.ANALYSIS_WINDOWS["M1"]
        _, entry_window_end = main.ENTRY_WINDOWS["M1"]
        worst_case_budget = (timeframe_seconds - analysis_window_end) + entry_window_end

        self.assertLess(main.ROBOT_ANALYSIS_SCAN_BUDGET_SECONDS, worst_case_budget)

    def test_payout_cache_ttl_survives_more_than_one_analysis_cycle(self) -> None:
        """PAYOUT_CACHE_TTL_SECONDS tem que atravessar um ciclo inteiro.

        Ler `/payouts` upstream custa ~4s por ativo: a lib da corretora faz
        busy-wait em `get_digital_payout(active, seconds=3)` esperando o
        websocket. Com TTL de 60s o cache expirava dentro do próprio ciclo
        seguinte (vela de 60s + até 45s de scan = 105s de distância entre a
        leitura de um ativo e a releitura dele), então TODO ciclo repagava os
        ~4s por ativo. Somado ao orçamento de 45s do scan, só 5–6 dos 10–20
        ativos eram avaliados por ciclo (`[ANALYSIS_SCAN_BUDGET] scanned=5`,
        115 estouros em 10 min em 18/08 ~23h30) e a frequência de operações
        da plataforma caiu de 12–34/hora (17/08, orçamento de 85s) para
        1–8/hora.

        O TTL precisa cobrir vela + scan para o ativo lido no ciclo N ainda
        estar quente no ciclo N+1. Ver PERFORMANCE_SISTEMA.md e
        ROBO_E_SUPORTE.md (incidente 2026-08-18 ~23h30).
        """
        timeframe_seconds = main.TIMEFRAME_SECONDS["M1"]
        one_cycle_plus_scan = timeframe_seconds + main.ROBOT_ANALYSIS_SCAN_BUDGET_SECONDS

        self.assertGreater(main.PAYOUT_CACHE_TTL_SECONDS, one_cycle_plus_scan)

    def test_payout_cache_ttl_still_forces_buy_time_channel_revalidation(self) -> None:
        """Cache longo na análise não pode virar compra em canal fechado.

        `refresh_candidate_execution_channel` só confia no cache de payout
        quando ele tem menos de `CHANNEL_CACHE_MAX_AGE_SECONDS`; acima disso
        busca dado fresco (teto `CHANNEL_REVALIDATION_TIMEOUT_SECONDS`). Como
        o TTL da análise é maior que essa idade máxima, a revalidação na
        compra continua acontecendo — é ela que garante que o candidato não
        entre num ativo que fechou durante a validade do cache.
        """
        self.assertGreater(
            main.PAYOUT_CACHE_TTL_SECONDS,
            main.CHANNEL_CACHE_MAX_AGE_SECONDS,
        )
        self.assertGreater(main.CHANNEL_REVALIDATION_TIMEOUT_SECONDS, 0)

    async def test_scan_stops_when_cycle_budget_exhausted(self) -> None:
        """BOTH com 20 ativos não pode varrer até estourar o wait_for de 110s."""
        called: list[str] = []

        async def fake_analyze_active_signal(
            user_id: str,
            symbol: str,
            timeframe: str = "M1",
            endtime: int | None = None,
            strategy_mode: str = "conservative",
        ):
            del user_id, timeframe, endtime, strategy_mode
            called.append(symbol)
            await asyncio.sleep(0.04)
            return 200, main.build_success(
                {
                    "symbol": symbol,
                    "signal": "WAIT",
                    "direction": "WAIT",
                    "confidence": 0,
                    "trade_allowed": False,
                }
            )

        with (
            patch.object(main, "ANALYSIS_ASSETS", ["EURUSD-OTC", "GBPUSD-OTC", "USDJPY-OTC"]),
            patch.object(main, "ROBOT_ANALYSIS_SCAN_BUDGET_SECONDS", 0.05),
            patch.object(main, "analyze_active_signal", side_effect=fake_analyze_active_signal),
            self.assertLogs("backend-gateway", level="WARNING") as logs,
        ):
            status_code, payload = await main.scan_local_signals(
                "scan-budget-user",
                include_wait=True,
                max_assets=10,
                market_mode="OTC",
            )

        self.assertEqual(status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertLess(len(called), 3)
        self.assertIn("[ANALYSIS_SCAN_BUDGET]", "\n".join(logs.output))

    async def test_worker_cycle_timeout_finishes_and_reschedules_analysis(self) -> None:
        user_id = "cycle-timeout-user"
        state = main.auto_trader.start(user_id)
        state.next_cycle_at = main.utc_now()

        async def blocked_cycle(_user_id: str):
            await asyncio.sleep(1)

        with (
            patch.object(main, "ROBOT_CYCLE_TIMEOUT_SECONDS", 0.01),
            patch.object(main, "execute_robot_cycle", side_effect=blocked_cycle),
        ):
            await main.execute_robot_worker_cycle(user_id)

        refreshed = main.auto_trader.get(user_id)
        self.assertEqual(refreshed.status, main.STATUS_WAITING_NEXT_CYCLE)
        self.assertEqual(refreshed.analysis_result, "ANALYSIS_TIMEOUT")
        self.assertEqual(refreshed.last_analysis_result, "ANALYSIS_TIMEOUT")
        self.assertIsNotNone(refreshed.last_analysis_at)
        self.assertGreater(refreshed.next_cycle_at, main.utc_now())

    async def test_worker_cycle_timeout_keeps_pending_signal(self) -> None:
        """Timeout do ciclo não pode apagar um sinal já travado para compra."""
        user_id = "cycle-timeout-keep-user"
        state = main.auto_trader.start(user_id)
        state.next_cycle_at = main.utc_now()
        state.pending_signal = {
            "symbol": "EURUSD-OTC",
            "signal": "CALL",
            "direction": "CALL",
            "confidence": 90,
            "payout": 85,
            "trade_allowed": True,
        }

        async def blocked_cycle(_user_id: str):
            await asyncio.sleep(1)

        with (
            patch.object(main, "ROBOT_CYCLE_TIMEOUT_SECONDS", 0.01),
            patch.object(main, "execute_robot_cycle", side_effect=blocked_cycle),
            patch.object(main, "persist_robot"),
        ):
            await main.execute_robot_worker_cycle(user_id)

        refreshed = main.auto_trader.get(user_id)
        self.assertIsNotNone(refreshed.pending_signal)
        self.assertEqual(refreshed.pending_signal["symbol"], "EURUSD-OTC")
        self.assertNotEqual(refreshed.analysis_result, "ANALYSIS_TIMEOUT")

    async def test_active_cooldown_hard_skips_even_with_cache(self) -> None:
        """Cooldown duro: cache de candles/payout não pode relançar o ativo."""
        user_id = "cache-cooldown-user"
        symbol = "EURUSD-OTC"
        now = main.utc_now()
        cache = main.get_session_cache(user_id)
        candle_params = {
            "active": symbol,
            "interval": main.TIMEFRAME_SECONDS["M1"],
            "count": main.ROBOT_CANDLE_COUNT,
            "endtime": 60,
        }
        payout_params = {"active": symbol}
        candles_payload = main.build_success(
            [{"open": 1.0, "close": 1.1}, {"open": 1.1, "close": 1.2}]
        )
        payout_payload = main.build_success([{"symbol": symbol, "payout": 88}])
        cache.last_successful_responses[main.build_cache_key("/candles", candle_params)] = (
            main.BullexResponseCacheEntry(200, candles_payload, now + timedelta(seconds=60))
        )
        cache.last_successful_responses[main.build_cache_key("/payouts", payout_params)] = (
            main.BullexResponseCacheEntry(200, payout_payload, now + timedelta(seconds=60))
        )
        main.set_named_cooldown(
            main.active_cooldowns,
            user_id,
            symbol,
            seconds=15,
            log_label="ACTIVE_TIMEOUT",
            status=main.STATUS_ACTIVE_COOLDOWN,
            reason="ACTIVE_TIMEOUT",
        )

        with (
            patch.object(main, "call_bullex_service", side_effect=AssertionError("should skip")),
            patch.object(main, "analyze_signal", side_effect=AssertionError("should skip")),
            self.assertLogs("backend-gateway", level="WARNING") as logs,
        ):
            status_code, payload = await main.analyze_active_signal(
                user_id,
                symbol,
                timeframe="M1",
                endtime=60,
            )

        self.assertEqual(status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["symbol"], symbol)
        self.assertEqual(payload["data"]["signal"], "WAIT")
        self.assertFalse(payload["data"]["trade_allowed"])
        self.assertEqual(payload["data"]["quality_reason"], "ACTIVE_COOLDOWN")
        self.assertIn("ACTIVE_COOLDOWN", payload["data"]["blocked_filters"])
        output = "\n".join(logs.output)
        self.assertIn("[ACTIVE_SKIPPED]", output)
        self.assertIn("ACTIVE_COOLDOWN", output)

    async def test_analysis_timeout_keeps_fresh_cache_tradeable(self) -> None:
        """Timeout do analyze não pode carimbar STALE em cache ainda no TTL.

        Incidente 2026-08-19 ~02h: conta de teste achava GBPUSD CALL 100/87
        no cache compartilhado (mesmo dado que outras contas compraram) e
        o ``wait_for`` de 5s estourava; o fallback forçava
        ``trade_allowed=False`` + ``STALE_MARKET_DATA`` → ``NO_TRADE``.
        """
        user_id = "fresh-cache-timeout-user"
        symbol = "GBPUSD-OTC"
        endtime = 1_787_105_400
        candles = [
            {"from": 1, "to": 61, "open": 1.1, "close": 1.2, "min": 1.05, "max": 1.25}
        ]
        candle_params = {
            "active": symbol,
            "interval": 60,
            "count": main.ROBOT_CANDLE_COUNT,
            "endtime": endtime,
        }
        candle_key = main.build_cache_key("/candles", candle_params)
        candle_payload = main.build_success(candles)
        main.store_shared_market_cache(candle_key, 200, candle_payload, 60)
        payout_key = main.build_cache_key("/payouts", {"active": symbol})
        payout_payload = main.build_success([{"symbol": symbol, "payout": 87.0}])
        main.store_shared_market_cache(payout_key, 200, payout_payload, 60)

        async def slow_analyze(*_args, **_kwargs):
            await asyncio.sleep(1)

        def fake_analyze_signal(*_args, **kwargs):
            return {
                "symbol": symbol,
                "signal": "CALL",
                "direction": "CALL",
                "confidence": 100,
                "strategy_score": 90,
                "payout": kwargs.get("payout") or 87.0,
                "trade_allowed": True,
                "blocked_filters": [],
                "approved_filters": [],
            }

        with (
            patch.object(main, "ANALYSIS_ASSETS", [symbol]),
            patch.object(main, "ROBOT_ANALYSIS_ASSET_TIMEOUT_SECONDS", 0.05),
            patch.object(main, "analyze_active_signal", side_effect=slow_analyze),
            patch.object(main, "analyze_signal", side_effect=fake_analyze_signal),
        ):
            status_code, payload = await main.scan_local_signals(
                user_id,
                limit=10,
                include_wait=False,
                max_assets=10,
                endtime=endtime,
            )

        self.assertEqual(status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(len(payload["data"]), 1)
        signal = payload["data"][0]
        self.assertEqual(signal["symbol"], symbol)
        self.assertEqual(signal["signal"], "CALL")
        self.assertTrue(signal["trade_allowed"])
        self.assertNotEqual(signal.get("quality_reason"), "STALE_MARKET_DATA")
        self.assertNotIn("STALE_MARKET_DATA", signal.get("blocked_filters") or [])
        self.assertFalse(signal.get("stale"))
        self.assertFalse(signal.get("market_data_stale"))

    async def test_analysis_timeout_still_blocks_expired_cache(self) -> None:
        """Cache fora do TTL continua STALE e não vira entrada."""
        user_id = "stale-cache-timeout-user"
        symbol = "EURUSD-OTC"
        endtime = 1_787_105_400
        candles = [
            {"from": 1, "to": 61, "open": 1.1, "close": 1.2, "min": 1.05, "max": 1.25}
        ]
        candle_params = {
            "active": symbol,
            "interval": 60,
            "count": main.ROBOT_CANDLE_COUNT,
            "endtime": endtime,
        }
        candle_key = main.build_cache_key("/candles", candle_params)
        expired = main.BullexResponseCacheEntry(
            status_code=200,
            payload=main.build_success(candles),
            expires_at=main.utc_now() - timedelta(seconds=10),
        )
        main.get_session_cache(user_id).last_successful_responses[candle_key] = expired

        async def slow_analyze(*_args, **_kwargs):
            await asyncio.sleep(1)

        def fake_analyze_signal(*_args, **kwargs):
            return {
                "symbol": symbol,
                "signal": "CALL",
                "direction": "CALL",
                "confidence": 100,
                "strategy_score": 90,
                "payout": 87.0,
                "trade_allowed": True,
                "blocked_filters": [],
                "approved_filters": [],
            }

        with (
            patch.object(main, "ANALYSIS_ASSETS", [symbol]),
            patch.object(main, "ROBOT_ANALYSIS_ASSET_TIMEOUT_SECONDS", 0.05),
            patch.object(main, "analyze_active_signal", side_effect=slow_analyze),
            patch.object(main, "analyze_signal", side_effect=fake_analyze_signal),
        ):
            status_code, payload = await main.scan_local_signals(
                user_id,
                limit=10,
                include_wait=True,
                max_assets=10,
                endtime=endtime,
            )

        self.assertEqual(status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(len(payload["data"]), 1)
        signal = payload["data"][0]
        self.assertFalse(signal["trade_allowed"])
        self.assertIn("STALE_MARKET_DATA", signal.get("blocked_filters") or [])


if __name__ == "__main__":
    unittest.main()
