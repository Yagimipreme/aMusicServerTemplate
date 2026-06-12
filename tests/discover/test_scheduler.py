"""Tests for _profile_next_run from sWebExt/py_server/server.py.

Previously tested a local copy of _seconds_until_next_run (now removed from server.py).
Updated to import _profile_next_run directly from the server module (Issue 12).
"""
import os
import sys
import datetime
import unittest
from unittest.mock import patch

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)


def _get_profile_next_run():
    """Import _profile_next_run from server module (threads patched out)."""
    with patch("threading.Thread"):
        from sWebExt.py_server import server as srv
        return srv._profile_next_run


def _make_profile(cadence="daily", run_day="sunday", run_hour=7):
    return {
        "id": "test", "name": "Test",
        "schedule": {"cadence": cadence, "run_day": run_day, "run_hour": run_hour},
    }


class TestProfileNextRun(unittest.TestCase):
    """Mirrors the old _seconds_until_next_run tests using _profile_next_run."""

    def setUp(self):
        self.fn = _get_profile_next_run()

    def test_daily_future_hour(self):
        now = datetime.datetime(2026, 6, 8, 10, 0, 0)
        profile = _make_profile(cadence="daily", run_hour=22)
        result = self.fn(profile, now)
        expected = datetime.datetime(2026, 6, 8, 22, 0, 0)
        self.assertEqual(result, expected)

    def test_daily_past_hour_wraps_to_tomorrow(self):
        now = datetime.datetime(2026, 6, 8, 23, 0, 0)
        profile = _make_profile(cadence="daily", run_hour=22)
        result = self.fn(profile, now)
        expected = datetime.datetime(2026, 6, 9, 22, 0, 0)
        self.assertEqual(result, expected)

    def test_weekly_correct_day(self):
        # Monday 2026-06-08 12:00; target Sunday
        now = datetime.datetime(2026, 6, 8, 12, 0, 0)
        profile = _make_profile(cadence="weekly", run_day="sunday", run_hour=22)
        result = self.fn(profile, now)
        expected = datetime.datetime(2026, 6, 14, 22, 0, 0)  # Sunday
        self.assertEqual(result, expected)

    def test_weekly_same_day_past_hour_next_week(self):
        # Sunday 2026-06-14 23:00; target Sunday 22:00
        now = datetime.datetime(2026, 6, 14, 23, 0, 0)
        profile = _make_profile(cadence="weekly", run_day="sunday", run_hour=22)
        result = self.fn(profile, now)
        expected = datetime.datetime(2026, 6, 21, 22, 0, 0)
        self.assertEqual(result, expected)

    def test_result_is_always_in_future(self):
        now = datetime.datetime(2026, 6, 12, 12, 0, 0)
        for cadence in ("daily", "weekly"):
            profile = _make_profile(cadence=cadence, run_day="friday", run_hour=7)
            result = self.fn(profile, now)
            self.assertGreater(result, now)
