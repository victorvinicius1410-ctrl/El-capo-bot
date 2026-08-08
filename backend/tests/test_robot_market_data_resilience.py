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

    def tearDown(self) -> None:
        main.active_cooldowns.clear()
        main.payout_cooldowns.clear()
        main.session_response_cache.clear()
        main.analysis_asset_queue_offsets.clear()

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


if __name__ == "__main__":
    unittest.main()
