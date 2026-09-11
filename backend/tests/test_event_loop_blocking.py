"""Guarda contra I/O síncrono no event loop do robot-runtime.

Incidente de 10/09/2026: o `_snapshot_publisher` montava o snapshot de cada
usuário a cada 1s e, pelo caminho `real_block_reason` →
`resolve_user_account_currency` → `user_store.get_user`, fazia 2 chamadas HTTPS
síncronas ao Supabase por usuário por segundo. O event loop ficava ~60% do
tempo travado e 63% das entradas se perdiam por atraso (ENTRY_WINDOW_MISSED /
ENTRY_SEND_TOO_LATE). Estes testes falham se esse caminho voltar a fazer I/O a
cada chamada.
"""

import asyncio
import logging
import time
import unittest
from unittest.mock import Mock, patch

from backend import main
from backend.auto_trader import AutoTrader
from backend.loop_watchdog import EventLoopWatchdog, format_blocking_stack
from backend.user_store import SupabaseUserStore

USER = "0f5b7a52-6d1e-4a51-9d8a-3f1f6f0c0a11"


class CountingUserStore:
    """Store falso que só conta idas ao 'Supabase'."""

    def __init__(self, currency: str | None = "USD") -> None:
        self.calls = 0
        self.currency = currency

    def get_user(self, user_id):
        self.calls += 1
        return Mock(
            data={"email": "x@example.com", "balance": 100.0, "currency": self.currency, "connected": True},
            email="x@example.com",
            balance=100.0,
            currency=self.currency,
            connected=True,
            mode="REAL",
            active_mode="REAL",
        )

    def __getattr__(self, name):  # qualquer outra leitura também conta
        def _call(*args, **kwargs):
            self.calls += 1
            return None

        return _call


class AccountCurrencyCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        main.invalidate_account_currency_cache()

    def tearDown(self) -> None:
        main.invalidate_account_currency_cache()

    def test_supabase_lookup_happens_once_per_ttl(self) -> None:
        lookup = Mock(return_value={"currency": "USD"})
        with (
            patch.object(main, "get_cached_account_snapshot", return_value={"currency": None}),
            patch.object(main, "get_user_account_snapshot", lookup),
        ):
            self.assertEqual(
                [main.resolve_user_account_currency(USER) for _ in range(50)],
                ["USD"] * 50,
            )
        self.assertEqual(lookup.call_count, 1)

    def test_lookup_repeats_after_ttl(self) -> None:
        lookup = Mock(side_effect=[{"currency": "USD"}, {"currency": "BRL"}])
        agora = [1000.0]
        with (
            patch.object(main, "get_cached_account_snapshot", return_value={"currency": None}),
            patch.object(main, "get_user_account_snapshot", lookup),
            patch.object(main, "monotonic", side_effect=lambda: agora[0]),
        ):
            self.assertEqual(main.resolve_user_account_currency(USER), "USD")
            agora[0] += main.ACCOUNT_CURRENCY_CACHE_TTL_SECONDS + 1
            self.assertEqual(main.resolve_user_account_currency(USER), "BRL")
        self.assertEqual(lookup.call_count, 2)

    def test_missing_currency_is_also_cached_and_defaults_to_brl(self) -> None:
        lookup = Mock(return_value={"currency": None})
        with (
            patch.object(main, "get_cached_account_snapshot", return_value={"currency": None}),
            patch.object(main, "get_user_account_snapshot", lookup),
        ):
            self.assertEqual(main.resolve_user_account_currency(USER), "BRL")
            self.assertEqual(main.resolve_user_account_currency(USER), "BRL")
        self.assertEqual(lookup.call_count, 1)

    def test_account_cache_still_wins_over_memo(self) -> None:
        with (
            patch.object(main, "get_cached_account_snapshot", return_value={"currency": None}),
            patch.object(main, "get_user_account_snapshot", return_value={"currency": "BRL"}),
        ):
            self.assertEqual(main.resolve_user_account_currency(USER), "BRL")
        with patch.object(main, "get_cached_account_snapshot", return_value={"currency": "USD"}):
            self.assertEqual(main.resolve_user_account_currency(USER), "USD")


class EnsureUserRowTests(unittest.TestCase):
    def test_users_upsert_runs_once_for_repeated_reads(self) -> None:
        store = SupabaseUserStore("https://example.supabase.co", "service-key")
        store._request = Mock(return_value=[])
        for _ in range(10):
            store.get_user(USER)
        posts = [c for c in store._request.call_args_list if c.args[0] == "POST"]
        gets = [c for c in store._request.call_args_list if c.args[0] == "GET"]
        self.assertEqual(len(posts), 1)
        self.assertEqual(len(gets), 10)

    def test_users_upsert_repeats_after_ttl_and_per_user(self) -> None:
        store = SupabaseUserStore("https://example.supabase.co", "service-key")
        store._request = Mock(return_value=[])
        agora = [500.0]
        with patch("backend.user_store.monotonic", side_effect=lambda: agora[0]):
            store.get_user(USER)
            store.get_user("outro-usuario")
            agora[0] += 601
            store.get_user(USER)
        posts = [c for c in store._request.call_args_list if c.args[0] == "POST"]
        self.assertEqual(len(posts), 3)

    def test_failed_upsert_is_retried_next_time(self) -> None:
        store = SupabaseUserStore("https://example.supabase.co", "service-key")
        store._request = Mock(side_effect=[RuntimeError("down"), [], []])
        with self.assertRaises(RuntimeError):
            store.get_user(USER)
        store.get_user(USER)
        posts = [c for c in store._request.call_args_list if c.args[0] == "POST"]
        self.assertEqual(len(posts), 2)


class DailyHistoryCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        main._daily_history_cache.clear()
        main._daily_history_generation.clear()
        main._daily_history_refreshing.clear()

    tearDown = setUp

    def _wait_refresh(self) -> None:
        limite = time.monotonic() + 2
        while main._daily_history_refreshing and time.monotonic() < limite:
            time.sleep(0.01)

    def test_stale_list_is_served_while_thread_reloads(self) -> None:
        loads = Mock(side_effect=[["v1"], ["v2"]])
        agora = [100.0]
        with (
            patch.object(main, "load_robot_history_items", loads),
            patch.object(main, "monotonic", side_effect=lambda: agora[0]),
        ):
            self.assertEqual(main.load_daily_history_cached(USER), ["v1"])
            agora[0] += main.DAILY_HISTORY_CACHE_TTL_SECONDS + 1
            # Vencido: devolve na hora a lista anterior, sem ler no chamador.
            self.assertEqual(main.load_daily_history_cached(USER), ["v1"])
            self._wait_refresh()
            self.assertEqual(main.load_daily_history_cached(USER), ["v2"])
        self.assertEqual(loads.call_count, 2)

    def test_invalidation_forces_fresh_read(self) -> None:
        loads = Mock(side_effect=[["antes"], ["depois"]])
        with patch.object(main, "load_robot_history_items", loads):
            self.assertEqual(main.load_daily_history_cached(USER), ["antes"])
            main.invalidate_daily_history_cache(USER)
            # Logo depois de gravar operação: leitura síncrona, stop win/loss
            # enxergam o resultado na hora (comportamento de antes).
            self.assertEqual(main.load_daily_history_cached(USER), ["depois"])

    def test_reload_started_before_invalidation_does_not_overwrite(self) -> None:
        liberar = __import__("threading").Event()

        def lento(user_id, days):
            liberar.wait(2)
            return ["velho"]

        agora = [100.0]
        main._daily_history_cache[USER] = (agora[0], ["inicial"])
        with (
            patch.object(main, "load_robot_history_items", side_effect=lento),
            patch.object(main, "monotonic", side_effect=lambda: agora[0]),
        ):
            agora[0] += main.DAILY_HISTORY_CACHE_TTL_SECONDS + 1
            main.load_daily_history_cached(USER)  # dispara releitura lenta
            main.invalidate_daily_history_cache(USER)  # operação gravada no meio
            liberar.set()
            self._wait_refresh()
        self.assertNotIn(USER, main._daily_history_cache)

    def test_very_old_list_is_read_synchronously(self) -> None:
        loads = Mock(return_value=["novo"])
        agora = [100.0]
        main._daily_history_cache[USER] = (agora[0], ["antigo"])
        with (
            patch.object(main, "load_robot_history_items", loads),
            patch.object(main, "monotonic", side_effect=lambda: agora[0]),
        ):
            agora[0] += main.DAILY_HISTORY_MAX_STALE_SECONDS + 1
            self.assertEqual(main.load_daily_history_cached(USER), ["novo"])


class SnapshotPublisherNoIoTests(unittest.TestCase):
    """O que o `_snapshot_publisher` roda a cada 1s por usuário."""

    def setUp(self) -> None:
        self.old_trader = main.auto_trader
        main.auto_trader = AutoTrader()
        main.invalidate_account_currency_cache()
        main._daily_history_cache.clear()
        main._daily_history_generation.clear()
        main._daily_history_refreshing.clear()
        state = main.auto_trader.get(USER)
        state.account_mode = "REAL"
        state.active_mode = "REAL"
        state.connected = True
        state.entry_value = 10.0

    def tearDown(self) -> None:
        main.auto_trader = self.old_trader
        main.invalidate_account_currency_cache()
        main._daily_history_cache.clear()

    def test_repeated_snapshots_do_not_touch_supabase(self) -> None:
        store = CountingUserStore(currency="USD")
        persistence = Mock()
        persistence.load_trade_history.return_value = []
        persistence.load_trades.return_value = []
        with (
            patch.object(main, "user_store", store),
            patch.object(main, "robot_persistence", persistence),
            patch.object(main, "robot_runtime_mode", return_value="worker"),
        ):
            main.build_robot_state_snapshot_payload(USER)
            store_calls = store.calls
            history_loads = persistence.load_trade_history.call_count
            for _ in range(30):
                main.build_robot_state_snapshot_payload(USER)
        self.assertLessEqual(store_calls, 1)
        self.assertEqual(store.calls, store_calls, "snapshot voltou a ler o Supabase a cada chamada")
        self.assertEqual(
            persistence.load_trade_history.call_count,
            history_loads,
            "snapshot voltou a ler o histórico a cada chamada",
        )


class EventLoopWatchdogTests(unittest.IsolatedAsyncioTestCase):
    async def test_blocking_call_is_reported_with_its_stack(self) -> None:
        logger = logging.getLogger("test-loop-watchdog")
        watchdog = EventLoopWatchdog(logger, stall_seconds=0.2, beat_seconds=0.05, summary_seconds=0.3)
        stop = asyncio.Event()

        def chamada_sincrona_que_trava():
            time.sleep(0.6)

        with self.assertLogs(logger, level="INFO") as logs:
            task = asyncio.create_task(watchdog.run(stop))
            await asyncio.sleep(0.15)
            chamada_sincrona_que_trava()
            await asyncio.sleep(0.4)
            stop.set()
            await task
        saida = "\n".join(logs.output)
        self.assertIn("[EVENT_LOOP_BLOCKED]", saida)
        self.assertIn("chamada_sincrona_que_trava", saida)
        self.assertIn("[EVENT_LOOP_LAG]", saida)

    async def test_healthy_loop_reports_nothing_blocked(self) -> None:
        logger = logging.getLogger("test-loop-watchdog-ok")
        watchdog = EventLoopWatchdog(logger, stall_seconds=0.3, beat_seconds=0.05, summary_seconds=10)
        stop = asyncio.Event()
        task = asyncio.create_task(watchdog.run(stop))
        await asyncio.sleep(0.4)
        self.assertIsNone(watchdog.check_stall())
        stop.set()
        await task

    def test_stack_summary_keeps_project_frames_and_leaf(self) -> None:
        import sys

        assinatura, pilha = format_blocking_stack(sys._getframe())
        self.assertIn("test_stack_summary_keeps_project_frames_and_leaf", pilha)
        self.assertTrue(assinatura)


if __name__ == "__main__":
    unittest.main()
