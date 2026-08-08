"""Testes do cache curto de GET /admin/clients."""

from __future__ import annotations

import unittest

from backend.admin_clients_cache import (
    ADMIN_CLIENTS_CACHE_TTL_SECONDS,
    clear_admin_clients_cache,
    clear_admin_clients_cache_for_company,
    read_admin_clients_cache,
    write_admin_clients_cache,
)


class AdminClientsCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_admin_clients_cache()

    def tearDown(self) -> None:
        clear_admin_clients_cache()

    def test_miss_when_empty(self) -> None:
        self.assertIsNone(
            read_admin_clients_cache("co1", "pending", 0, 10, now_monotonic=1.0)
        )

    def test_hit_within_ttl(self) -> None:
        payload = {"items": [{"id": "u1"}], "has_more": False, "next_offset": 1}
        write_admin_clients_cache(
            "co1", "pending", 0, 10, payload, now_monotonic=100.0
        )
        self.assertEqual(
            read_admin_clients_cache(
                "co1", "pending", 0, 10, now_monotonic=100.0 + 5.0
            ),
            payload,
        )

    def test_expire_after_ttl(self) -> None:
        write_admin_clients_cache(
            "co1",
            "pending",
            0,
            10,
            {"items": [], "has_more": False, "next_offset": 0},
            now_monotonic=100.0,
        )
        expired_at = 100.0 + ADMIN_CLIENTS_CACHE_TTL_SECONDS + 0.1
        self.assertIsNone(
            read_admin_clients_cache(
                "co1", "pending", 0, 10, now_monotonic=expired_at
            )
        )

    def test_company_isolation_and_scoped_clear(self) -> None:
        write_admin_clients_cache(
            "co1",
            "pending",
            0,
            10,
            {"items": [1], "has_more": False, "next_offset": 0},
            now_monotonic=1.0,
        )
        write_admin_clients_cache(
            "co2",
            "pending",
            0,
            10,
            {"items": [2], "has_more": False, "next_offset": 0},
            now_monotonic=1.0,
        )
        clear_admin_clients_cache_for_company("co1")
        self.assertIsNone(
            read_admin_clients_cache("co1", "pending", 0, 10, now_monotonic=2.0)
        )
        self.assertEqual(
            read_admin_clients_cache("co2", "pending", 0, 10, now_monotonic=2.0)[
                "items"
            ],
            [2],
        )

    def test_search_term_isolates_cache_key(self) -> None:
        write_admin_clients_cache(
            "co1",
            "active",
            0,
            10,
            {"items": ["sem_busca"], "has_more": False, "next_offset": 0},
            now_monotonic=1.0,
        )
        write_admin_clients_cache(
            "co1",
            "active",
            0,
            10,
            {"items": ["joao"], "has_more": False, "next_offset": 0},
            search="joao",
            now_monotonic=1.0,
        )
        self.assertEqual(
            read_admin_clients_cache("co1", "active", 0, 10, now_monotonic=2.0)[
                "items"
            ],
            ["sem_busca"],
        )
        self.assertEqual(
            read_admin_clients_cache(
                "co1", "active", 0, 10, search="joao", now_monotonic=2.0
            )["items"],
            ["joao"],
        )
        self.assertIsNone(
            read_admin_clients_cache(
                "co1", "active", 0, 10, search="maria", now_monotonic=2.0
            )
        )

    def test_search_term_is_case_insensitive_for_cache_key(self) -> None:
        write_admin_clients_cache(
            "co1",
            "active",
            0,
            10,
            {"items": ["joao"], "has_more": False, "next_offset": 0},
            search="Joao",
            now_monotonic=1.0,
        )
        self.assertEqual(
            read_admin_clients_cache(
                "co1", "active", 0, 10, search="joao", now_monotonic=2.0
            )["items"],
            ["joao"],
        )


if __name__ == "__main__":
    unittest.main()
