"""Pure SQL aggregations over the insights store.

Every public function takes (conn, period, tz_offset_min, now_ts) and returns
JSON-able data. `ts` is unix-UTC; hour/day buckets are computed in the user's
local time via a tz offset in minutes. `now_ts` defaults to the current time
but is injectable for deterministic tests.
"""

import math
import time

# Period token -> lookback window in days. 'all' (or unknown) -> no cutoff.
_PERIOD_DAYS = {"7d": 7, "30d": 30, "90d": 90, "year": 365}


def _now(now_ts):
    return int(time.time()) if now_ts is None else int(now_ts)


def _period_where(period, now_ts):
    """Return (sql_fragment, params) restricting to the period (or ('', []))."""
    days = _PERIOD_DAYS.get(period)
    if days is None:
        return "", []
    return "ts >= ?", [_now(now_ts) - days * 86400]


def _and(where):
    """Turn a bare period fragment into a usable WHERE clause."""
    return f"WHERE {where}" if where else ""


def _offset_seconds(tz_offset_min):
    return int(tz_offset_min) * 60


def _hour_expr(tz_offset_min):
    return f"strftime('%H', ts + {_offset_seconds(tz_offset_min)}, 'unixepoch')"


def _dow_expr(tz_offset_min):
    return f"strftime('%w', ts + {_offset_seconds(tz_offset_min)}, 'unixepoch')"


def _date_expr(tz_offset_min):
    return f"strftime('%Y-%m-%d', ts + {_offset_seconds(tz_offset_min)}, 'unixepoch')"


def listening_clock(conn, period="all", tz_offset_min=0, now_ts=None):
    """Plays per local hour-of-day. Returns {"hours": [c0..c23]}."""
    where, params = _period_where(period, now_ts)
    rows = conn.execute(
        f"SELECT {_hour_expr(tz_offset_min)} AS h, COUNT(*) AS n "
        f"FROM scrobbles {_and(where)} GROUP BY h",
        params,
    ).fetchall()
    hours = [0] * 24
    for r in rows:
        hours[int(r["h"])] = r["n"]
    return {"hours": hours}


def hour_day_heatmap(conn, period="all", tz_offset_min=0, now_ts=None):
    """7x24 matrix of plays, indexed [day_of_week][hour]. dow 0 = Sunday."""
    where, params = _period_where(period, now_ts)
    rows = conn.execute(
        f"SELECT {_dow_expr(tz_offset_min)} AS d, {_hour_expr(tz_offset_min)} AS h, "
        f"COUNT(*) AS n FROM scrobbles {_and(where)} GROUP BY d, h",
        params,
    ).fetchall()
    matrix = [[0] * 24 for _ in range(7)]
    for r in rows:
        matrix[int(r["d"])][int(r["h"])] = r["n"]
    return {"matrix": matrix, "dow_labels": ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]}


def weekday_weekend(conn, period="all", tz_offset_min=0, now_ts=None):
    """Weekday (Mon-Fri) vs weekend (Sat/Sun) play counts + per-hour curves."""
    where, params = _period_where(period, now_ts)
    rows = conn.execute(
        f"SELECT {_dow_expr(tz_offset_min)} AS d, {_hour_expr(tz_offset_min)} AS h, "
        f"COUNT(*) AS n FROM scrobbles {_and(where)} GROUP BY d, h",
        params,
    ).fetchall()
    weekday = weekend = 0
    weekday_by_hour = [0] * 24
    weekend_by_hour = [0] * 24
    for r in rows:
        d, h, n = int(r["d"]), int(r["h"]), r["n"]
        if d in (0, 6):
            weekend += n
            weekend_by_hour[h] += n
        else:
            weekday += n
            weekday_by_hour[h] += n
    return {
        "weekday": weekday, "weekend": weekend,
        "weekday_by_hour": weekday_by_hour, "weekend_by_hour": weekend_by_hour,
    }


def plays_over_time(conn, period="all", tz_offset_min=0, now_ts=None):
    """Plays per local calendar day. Returns [{"date": "YYYY-MM-DD", "plays": n}]."""
    where, params = _period_where(period, now_ts)
    rows = conn.execute(
        f"SELECT {_date_expr(tz_offset_min)} AS d, COUNT(*) AS n "
        f"FROM scrobbles {_and(where)} GROUP BY d ORDER BY d",
        params,
    ).fetchall()
    return [{"date": r["d"], "plays": r["n"]} for r in rows]
