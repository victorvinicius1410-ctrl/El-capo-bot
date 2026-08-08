"""Testes do cache curto do GET /admin/dashboard."""

from __future__ import annotations

import unittest

from backend.admin_dashboard_cache import (
    ADMIN_DASHBOARD_CACHE_TTL_SECONDS,
    clear_admin_dashboard_cache,
    clear_admin_dashboard_cache_for_company,
    read_admin_dashboard_cache,
    write_admin_dashboard_cache,
)


class AdminDashboardCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_admin_dashboard_cache()

    def tearDown(self) -> None:
        clear_admin_dashboard_cache()

    def test_miss_when_empty(self) -> None:
        self.assertIsNone(read_admin_dashboard_cache("co1", 30, now_monotonic=10.0))

    def test_hit_within_ttl(self) -> None:
        payload = {"active_clients": 3}
        write_admin_dashboard_cache("co1", 30, payload, now_monotonic=100.0)
        self.assertEqual(
            read_admin_dashboard_cache("co1", 30, now_monotonic=100.0 + 10.0),
            payload,
        )

    def test_expire_after_ttl(self) -> None:
        write_admin_dashboard_cache(
            "co1",
            30,
            {"active_clients": 1},
            now_monotonic=100.0,
        )
        expired_at = 100.0 + ADMIN_DASHBOARD_CACHE_TTL_SECONDS + 0.1
        self.assertIsNone(read_admin_dashboard_cache("co1", 30, now_monotonic=expired_at))

    def test_different_days_are_separate_keys(self) -> None:
        write_admin_dashboard_cache("co1", 7, {"period_days": 7}, now_monotonic=1.0)
        write_admin_dashboard_cache("co1", 30, {"period_days": 30}, now_monotonic=1.0)
        self.assertEqual(
            read_admin_dashboard_cache("co1", 7, now_monotonic=2.0)["period_days"],
            7,
        )
        self.assertEqual(
            read_admin_dashboard_cache("co1", 30, now_monotonic=2.0)["period_days"],
            30,
        )

    def test_scoped_company_clear(self) -> None:
        write_admin_dashboard_cache("co1", 30, {"n": 1}, now_monotonic=1.0)
        write_admin_dashboard_cache("co2", 30, {"n": 2}, now_monotonic=1.0)
        clear_admin_dashboard_cache_for_company("co1")
        self.assertIsNone(read_admin_dashboard_cache("co1", 30, now_monotonic=2.0))
        self.assertEqual(
            read_admin_dashboard_cache("co2", 30, now_monotonic=2.0)["n"],
            2,
        )


if __name__ == "__main__":
    unittest.main()
