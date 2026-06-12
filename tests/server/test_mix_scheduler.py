"""Tests for _profile_next_run() scheduler helper in server.py."""
import datetime
import sys
import os
import pytest
from unittest.mock import patch


@pytest.fixture(scope="module")
def server_module():
    """Import server module with threads patched out."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if root not in sys.path:
        sys.path.insert(0, root)
    with patch("threading.Thread"):
        from sWebExt.py_server import server as srv
        return srv


def make_profile(cadence="daily", run_day="sunday", run_hour=7):
    return {
        "id": "test",
        "name": "Test",
        "schedule": {"cadence": cadence, "run_day": run_day, "run_hour": run_hour},
    }


# ── daily cadence ─────────────────────────────────────────────────────────────

def test_daily_future_hour(server_module):
    """If run_hour is in the future today, next run is today at run_hour."""
    now = datetime.datetime(2026, 6, 12, 10, 0, 0)  # 10:00
    profile = make_profile(cadence="daily", run_hour=22)
    result = server_module._profile_next_run(profile, now)
    expected = datetime.datetime(2026, 6, 12, 22, 0, 0)
    assert result == expected


def test_daily_past_hour_wraps_to_tomorrow(server_module):
    """If run_hour has passed today, next run is tomorrow."""
    now = datetime.datetime(2026, 6, 12, 23, 0, 0)  # 23:00
    profile = make_profile(cadence="daily", run_hour=7)
    result = server_module._profile_next_run(profile, now)
    expected = datetime.datetime(2026, 6, 13, 7, 0, 0)
    assert result == expected


def test_daily_exact_hour_wraps_to_tomorrow(server_module):
    """If now == run_hour exactly, next run is tomorrow (not same instant)."""
    now = datetime.datetime(2026, 6, 12, 7, 0, 0)
    profile = make_profile(cadence="daily", run_hour=7)
    result = server_module._profile_next_run(profile, now)
    expected = datetime.datetime(2026, 6, 13, 7, 0, 0)
    assert result == expected


# ── weekly cadence ────────────────────────────────────────────────────────────

def test_weekly_future_day_of_week(server_module):
    """Target day is ahead in the week."""
    # Monday 2026-06-08 12:00; target Sunday
    now = datetime.datetime(2026, 6, 8, 12, 0, 0)
    profile = make_profile(cadence="weekly", run_day="sunday", run_hour=22)
    result = server_module._profile_next_run(profile, now)
    # Sunday is 6 days later → 2026-06-14
    expected = datetime.datetime(2026, 6, 14, 22, 0, 0)
    assert result == expected


def test_weekly_same_day_future_hour(server_module):
    """Same weekday but hour is still ahead today."""
    # Monday 2026-06-08 10:00; target Monday 22:00
    now = datetime.datetime(2026, 6, 8, 10, 0, 0)
    profile = make_profile(cadence="weekly", run_day="monday", run_hour=22)
    result = server_module._profile_next_run(profile, now)
    expected = datetime.datetime(2026, 6, 8, 22, 0, 0)
    assert result == expected


def test_weekly_same_day_past_hour_next_week(server_module):
    """Same weekday but hour already passed → next week."""
    # Sunday 2026-06-14 23:00; target Sunday 22:00
    now = datetime.datetime(2026, 6, 14, 23, 0, 0)
    profile = make_profile(cadence="weekly", run_day="sunday", run_hour=22)
    result = server_module._profile_next_run(profile, now)
    expected = datetime.datetime(2026, 6, 21, 22, 0, 0)
    assert result == expected


# ── run_hour clamping ─────────────────────────────────────────────────────────

def test_run_hour_clamped_below_zero(server_module):
    """run_hour < 0 is clamped to 0."""
    now = datetime.datetime(2026, 6, 12, 10, 0, 0)
    profile = make_profile(cadence="daily", run_hour=-5)
    result = server_module._profile_next_run(profile, now)
    # -5 clamped to 0; 0 is in the past at 10:00 → tomorrow at 0:00
    expected = datetime.datetime(2026, 6, 13, 0, 0, 0)
    assert result == expected


def test_run_hour_clamped_above_23(server_module):
    """run_hour > 23 is clamped to 23."""
    now = datetime.datetime(2026, 6, 12, 22, 0, 0)
    profile = make_profile(cadence="daily", run_hour=25)
    result = server_module._profile_next_run(profile, now)
    # 25 clamped to 23; 23 is in the future at 22:00 → today at 23:00
    expected = datetime.datetime(2026, 6, 12, 23, 0, 0)
    assert result == expected


# ── return type ───────────────────────────────────────────────────────────────

def test_returns_datetime(server_module):
    now = datetime.datetime(2026, 6, 12, 10, 0, 0)
    profile = make_profile(cadence="daily", run_hour=22)
    result = server_module._profile_next_run(profile, now)
    assert isinstance(result, datetime.datetime)


def test_result_always_in_future(server_module):
    now = datetime.datetime(2026, 6, 12, 12, 0, 0)
    for cadence in ("daily", "weekly"):
        profile = make_profile(cadence=cadence, run_day="friday", run_hour=7)
        result = server_module._profile_next_run(profile, now)
        assert result > now, f"result {result} should be > now {now} for cadence={cadence}"
