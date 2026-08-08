"""Testes do warmer periódico do cache GET /admin/dashboard."""

from __future__ import annotations

import asyncio
import unittest
from typing import Any

from backend.admin_dashboard_cache import (
    clear_admin_dashboard_cache,
    read_admin_dashboard_cache,
)
from backend.admin_dashboard_warm import AdminDashboardWarmer


class AdminDashboardWarmerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        clear_admin_dashboard_cache()
        self._clock = 1_000.0
        self._compute_calls: list[tuple[str, int]] = []

    def tearDown(self) -> None:
        clear_admin_dashboard_cache()

    def _now(self) -> float:
        return self._clock

    async def _compute(self, company_id: str, days: int) -> dict[str, Any]:
        self._compute_calls.append((company_id, days))
        return {"company_id": company_id, "period_days": days, "active_clients": days}

    def _make_warmer(
        self,
        *,
        interval_seconds: float = 30.0,
        sleep: Any | None = None,
    ) -> AdminDashboardWarmer:
        return AdminDashboardWarmer(
            self._compute,
            interval_seconds=interval_seconds,
            days=(7, 30),
            sleep=sleep,
            now_monotonic=self._now,
        )

    async def test_register_company_ignores_blank(self) -> None:
        warmer = self._make_warmer()
        warmer.register_company("")
        warmer.register_company("   ")
        warmer.register_company("co1")
        self.assertEqual(warmer.registered_companies, frozenset({"co1"}))

    async def test_warm_once_writes_cache_for_registered_days(self) -> None:
        warmer = self._make_warmer()
        warmer.register_company("co1")
        await warmer.warm_once()

        self.assertEqual(
            sorted(self._compute_calls),
            [("co1", 7), ("co1", 30)],
        )
        self.assertEqual(
            read_admin_dashboard_cache("co1", 7, now_monotonic=self._clock)["period_days"],
            7,
        )
        self.assertEqual(
            read_admin_dashboard_cache("co1", 30, now_monotonic=self._clock)["period_days"],
            30,
        )
        self.assertEqual(warmer.warm_count, 1)
        self.assertEqual(warmer.last_warm_at, self._clock)

    async def test_warm_once_skips_when_no_companies(self) -> None:
        warmer = self._make_warmer()
        await warmer.warm_once()
        self.assertEqual(self._compute_calls, [])
        self.assertIsNone(read_admin_dashboard_cache("co1", 30, now_monotonic=self._clock))
        self.assertEqual(warmer.warm_count, 1)

    async def test_warm_once_isolates_tenants(self) -> None:
        warmer = self._make_warmer()
        warmer.register_company("co1")
        warmer.register_company("co2")
        await warmer.warm_once()
        self.assertEqual(
            read_admin_dashboard_cache("co1", 30, now_monotonic=self._clock)["company_id"],
            "co1",
        )
        self.assertEqual(
            read_admin_dashboard_cache("co2", 7, now_monotonic=self._clock)["company_id"],
            "co2",
        )

    async def test_cache_expires_after_ttl_despite_prior_warm(self) -> None:
        warmer = self._make_warmer()
        warmer.register_company("co1")
        await warmer.warm_once()
        self.assertIsNotNone(
            read_admin_dashboard_cache("co1", 30, now_monotonic=self._clock)
        )
        expired_at = self._clock + 75.0 + 0.1
        self.assertIsNone(
            read_admin_dashboard_cache("co1", 30, now_monotonic=expired_at)
        )

    async def test_interval_loop_calls_warm_then_sleeps(self) -> None:
        sleep_calls: list[float] = []
        entered_sleep = asyncio.Event()

        async def fake_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)
            entered_sleep.set()
            # Bloqueia até aclose cancelar a task (sem sleep real de 30s).
            await asyncio.Event().wait()

        warmer = self._make_warmer(interval_seconds=30.0, sleep=fake_sleep)
        warmer.register_company("co1")
        warmer.start()
        await asyncio.wait_for(entered_sleep.wait(), timeout=1.0)

        self.assertEqual(sleep_calls, [30.0])
        self.assertEqual(warmer.warm_count, 1)
        self.assertEqual(
            sorted(self._compute_calls),
            [("co1", 7), ("co1", 30)],
        )
        await warmer.aclose()
        self.assertTrue(warmer._task is None or warmer._task.done())

    async def test_start_is_idempotent(self) -> None:
        blocked = asyncio.Event()

        async def fake_sleep(_seconds: float) -> None:
            await blocked.wait()

        warmer = self._make_warmer(sleep=fake_sleep)
        warmer.start()
        first_task = warmer._task
        warmer.start()
        self.assertIs(warmer._task, first_task)
        await warmer.aclose()

    async def test_compute_failure_does_not_block_other_companies(self) -> None:
        async def flaky_compute(company_id: str, days: int) -> dict[str, Any]:
            if company_id == "bad":
                raise RuntimeError("boom")
            return {"company_id": company_id, "period_days": days}

        warmer = AdminDashboardWarmer(
            flaky_compute,
            days=(30,),
            now_monotonic=self._now,
        )
        warmer.register_company("bad")
        warmer.register_company("good")
        await warmer.warm_once()
        self.assertIsNone(read_admin_dashboard_cache("bad", 30, now_monotonic=self._clock))
        self.assertEqual(
            read_admin_dashboard_cache("good", 30, now_monotonic=self._clock)["company_id"],
            "good",
        )


if __name__ == "__main__":
    unittest.main()
