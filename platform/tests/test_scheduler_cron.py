from __future__ import annotations

import datetime as dt

from app.services.scheduler import cron_matches


def test_cron_matches_two_daily_slots():
    assert cron_matches("0 8,18 * * *", dt.datetime(2026, 11, 5, 8, 0))
    assert cron_matches("0 8,18 * * *", dt.datetime(2026, 11, 5, 18, 0))
    assert not cron_matches("0 8,18 * * *", dt.datetime(2026, 11, 5, 9, 0))


def test_cron_matches_weekdays_and_steps():
    assert cron_matches("0 9 * * 1-5", dt.datetime(2026, 11, 5, 9, 0))
    assert not cron_matches("0 9 * * 1-5", dt.datetime(2026, 11, 7, 9, 0))
    assert cron_matches("*/15 * * * *", dt.datetime(2026, 11, 5, 10, 30))


def test_cron_rejects_invalid_expression():
    assert not cron_matches("0 8 * *", dt.datetime(2026, 11, 5, 8, 0))
