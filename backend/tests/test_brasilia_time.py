"""Dia civil do El Capo: meia-noite a meia-noite em América/São_Paulo."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timezone

from backend.brasilia_time import (
    BRASILIA_TZ,
    brasilia_today,
    history_cutoff,
    is_on_brasilia_day,
    start_of_brasilia_day,
    to_brasilia_date,
)


class BrasiliaTimeTests(unittest.TestCase):
    def test_after_utc_midnight_still_previous_brasilia_day(self) -> None:
        now = datetime(2026, 8, 19, 1, 30, tzinfo=timezone.utc)
        self.assertEqual(brasilia_today(now), date(2026, 8, 18))
        self.assertEqual(to_brasilia_date(now), date(2026, 8, 18))

    def test_start_of_day_is_midnight_brasilia(self) -> None:
        start = start_of_brasilia_day(date(2026, 8, 18))
        self.assertEqual(start.tzinfo, BRASILIA_TZ)
        self.assertEqual(start.hour, 0)
        self.assertEqual(start.minute, 0)
        self.assertEqual(start.astimezone(timezone.utc), datetime(2026, 8, 18, 3, 0, tzinfo=timezone.utc))

    def test_history_cutoff_today_starts_at_brasilia_midnight(self) -> None:
        now = datetime(2026, 8, 19, 1, 30, tzinfo=timezone.utc)
        cutoff = history_cutoff(1, now)
        self.assertEqual(cutoff, start_of_brasilia_day(date(2026, 8, 18)))

    def test_history_cutoff_seven_days_is_inclusive_calendar(self) -> None:
        now = datetime(2026, 8, 18, 20, 0, tzinfo=timezone.utc)
        cutoff = history_cutoff(7, now)
        self.assertEqual(to_brasilia_date(cutoff), date(2026, 8, 12))

    def test_trade_at_22h_brasilia_belongs_to_that_civil_day(self) -> None:
        finished = datetime(2026, 8, 19, 1, 10, tzinfo=timezone.utc)
        self.assertTrue(is_on_brasilia_day(finished, date(2026, 8, 18)))
        self.assertFalse(is_on_brasilia_day(finished, date(2026, 8, 19)))

    def test_naive_datetime_is_treated_as_utc(self) -> None:
        naive = datetime(2026, 8, 19, 1, 10)
        self.assertEqual(to_brasilia_date(naive), date(2026, 8, 18))


if __name__ == "__main__":
    unittest.main()
