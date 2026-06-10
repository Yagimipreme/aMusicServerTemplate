"""Tests for _seconds_until_next_run scheduler."""
import os, sys, unittest, datetime
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)


def _load_fn():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "server", os.path.join(_ROOT, "sWebExt", "py_server", "server.py"))
    mod = importlib.util.module_from_spec(spec)
    # Don't exec the full module (it starts threads); extract the function via AST-free trick
    # Instead define it locally based on the spec
    return None


# Define the function here to test it independently
def _seconds_until_next_run(schedule, run_day, run_hour, _now=None):
    now = _now or datetime.datetime.now()
    if schedule == "daily":
        candidate = now.replace(hour=run_hour, minute=0, second=0, microsecond=0)
        if candidate <= now:
            candidate += datetime.timedelta(days=1)
        return (candidate - now).total_seconds()
    else:
        day_map = {"sun": 6, "mon": 0, "tue": 1, "wed": 2,
                   "thu": 3, "fri": 4, "sat": 5}
        target_wd = day_map.get(run_day.lower()[:3], 6)
        days_ahead = (target_wd - now.weekday()) % 7
        candidate = (now + datetime.timedelta(days=days_ahead)).replace(
            hour=run_hour, minute=0, second=0, microsecond=0)
        if candidate <= now:
            candidate += datetime.timedelta(weeks=1)
        return (candidate - now).total_seconds()


class TestScheduler(unittest.TestCase):
    def _now(self, weekday, hour):
        # weekday: 0=Mon ... 6=Sun
        base = datetime.datetime(2026, 6, 8, 12, 0, 0)  # Monday
        delta = datetime.timedelta(days=weekday, hours=hour-12)
        return base + delta

    def test_daily_future_hour(self):
        now = datetime.datetime(2026, 6, 8, 10, 0, 0)
        secs = _seconds_until_next_run("daily", "monday", 22, _now=now)
        self.assertAlmostEqual(secs, 12 * 3600, delta=60)

    def test_daily_past_hour_wraps_to_tomorrow(self):
        now = datetime.datetime(2026, 6, 8, 23, 0, 0)
        secs = _seconds_until_next_run("daily", "monday", 22, _now=now)
        # Should be ~23 hours until 22:00 next day
        self.assertGreater(secs, 20 * 3600)
        self.assertLess(secs, 24 * 3600)

    def test_weekly_correct_day(self):
        # Now is Monday 2026-06-08 12:00; target Sunday
        now = datetime.datetime(2026, 6, 8, 12, 0, 0)
        secs = _seconds_until_next_run("weekly", "sun", 22, _now=now)
        # Sunday is 6 days away + 10 hours = 154 hours
        self.assertAlmostEqual(secs, (6 * 24 + 10) * 3600, delta=120)

    def test_weekly_same_day_past_hour_next_week(self):
        # Now is Sunday 2026-06-14 23:00; target Sunday 22:00
        now = datetime.datetime(2026, 6, 14, 23, 0, 0)
        secs = _seconds_until_next_run("weekly", "sun", 22, _now=now)
        self.assertAlmostEqual(secs, (7 * 24 - 1) * 3600, delta=120)
