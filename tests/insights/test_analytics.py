"""Tests for insights/analytics.py — temporal aggregations."""

from insights import db, analytics

# Fixture timestamps (unix UTC):
#   1700000000 = 2023-11-14 22:13:20 UTC  (Tuesday)
#   1700003600 = 2023-11-14 23:13:20 UTC  (Tuesday)
NOW = 1700100000  # 2023-11-16, used as "now" for period cutoffs


def _seed(conn, rows):
    conn.executemany(
        "INSERT INTO scrobbles (ts, artist, track) VALUES (?, ?, ?)", rows
    )
    conn.commit()


def test_listening_clock_buckets_by_local_hour(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    _seed(conn, [(1700000000, "A", "t1"), (1700003600, "A", "t2")])
    clock = analytics.listening_clock(conn, period="all", tz_offset_min=60, now_ts=NOW)
    assert len(clock["hours"]) == 24
    assert clock["hours"][23] == 1
    assert clock["hours"][0] == 1
    clock_utc = analytics.listening_clock(conn, period="all", tz_offset_min=0, now_ts=NOW)
    assert clock_utc["hours"][22] == 1
    assert clock_utc["hours"][23] == 1


def test_listening_clock_respects_period(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    old = NOW - 40 * 86400
    recent = NOW - 2 * 86400
    _seed(conn, [(old, "A", "t1"), (recent, "A", "t2")])
    total_30d = sum(analytics.listening_clock(
        conn, period="30d", tz_offset_min=0, now_ts=NOW)["hours"])
    assert total_30d == 1
    total_all = sum(analytics.listening_clock(
        conn, period="all", tz_offset_min=0, now_ts=NOW)["hours"])
    assert total_all == 2


def test_hour_day_heatmap_shape_and_placement(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    _seed(conn, [(1700000000, "A", "t1")])  # Tue 22:13 UTC → dow '2'
    hm = analytics.hour_day_heatmap(conn, period="all", tz_offset_min=0, now_ts=NOW)
    assert len(hm["matrix"]) == 7
    assert all(len(row) == 24 for row in hm["matrix"])
    assert hm["matrix"][2][22] == 1


def test_weekday_weekend_split(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    _seed(conn, [(1700000000, "A", "t1"), (1699747200, "A", "t2")])
    ww = analytics.weekday_weekend(conn, period="all", tz_offset_min=0, now_ts=NOW)
    assert ww["weekday"] == 1
    assert ww["weekend"] == 1


def test_plays_over_time_daily_buckets(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    _seed(conn, [(1700000000, "A", "t1"), (1700003600, "A", "t2")])
    pot = analytics.plays_over_time(conn, period="all", tz_offset_min=0, now_ts=NOW)
    assert pot == [{"date": "2023-11-14", "plays": 2}]


def test_hour_day_heatmap_tz_crosses_midnight(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    # 1700003600 = 2023-11-14 23:13:20 UTC = Tuesday (dow 2), hour 23.
    # With +60 min it becomes 2023-11-15 00:13 = Wednesday (dow 3), hour 0.
    _seed(conn, [(1700003600, "A", "t1")])
    hm = analytics.hour_day_heatmap(conn, period="all", tz_offset_min=60, now_ts=NOW)
    assert hm["matrix"][3][0] == 1   # Wednesday, hour 0 (local)
    assert hm["matrix"][2][23] == 0  # NOT Tuesday hour 23 anymore
