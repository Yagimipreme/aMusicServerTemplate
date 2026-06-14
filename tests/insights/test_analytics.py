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


def _seed_with_genres(conn, scrobble_rows, artist_genre):
    conn.executemany(
        "INSERT INTO scrobbles (ts, artist, track) VALUES (?, ?, ?)", scrobble_rows)
    for artist, genre in artist_genre.items():
        conn.execute(
            "INSERT INTO artist_tags (artist, tags_json, primary_genre, fetched_at) "
            "VALUES (?, '[]', ?, 1)", (artist, genre))
    conn.commit()


def test_top_genres_ranks_with_share(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    _seed_with_genres(
        conn,
        [(1700000000, "A", "t1"), (1700000001, "A", "t2"), (1700000002, "B", "t3")],
        {"A": "techno", "B": "house"},
    )
    tg = analytics.top_genres(conn, period="all", tz_offset_min=0, now_ts=NOW, limit=10)
    assert tg[0]["genre"] == "techno"
    assert tg[0]["plays"] == 2
    assert abs(tg[0]["share"] - 2 / 3) < 1e-6


def test_top_genres_excludes_untagged(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    _seed_with_genres(conn, [(1700000000, "A", "t1"), (1700000001, "C", "t2")],
                      {"A": "techno"})
    tg = analytics.top_genres(conn, period="all", tz_offset_min=0, now_ts=NOW, limit=10)
    assert [g["genre"] for g in tg] == ["techno"]


def test_genre_by_hour_shape(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    _seed_with_genres(conn, [(1700000000, "A", "t1")], {"A": "techno"})
    gbh = analytics.genre_by_hour(conn, period="all", tz_offset_min=0, now_ts=NOW, top_n=5)
    assert "techno" in gbh["genres"]
    assert len(gbh["data"]["techno"]) == 24
    assert gbh["data"]["techno"][22] == 1


def test_genre_diversity_counts_distinct_and_entropy(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    _seed_with_genres(
        conn,
        [(1700000000, "A", "t1"), (1700000001, "B", "t2")],
        {"A": "techno", "B": "house"},
    )
    div = analytics.genre_diversity(conn, period="all", tz_offset_min=0, now_ts=NOW)
    assert div["distinct"] == 2
    assert abs(div["normalized_entropy"] - 1.0) < 1e-6


def test_genre_evolution_buckets(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    _seed_with_genres(
        conn,
        [(NOW - 60 * 86400, "A", "t1"), (NOW - 2 * 86400, "A", "t2")],
        {"A": "techno"},
    )
    ev = analytics.genre_evolution(conn, period="all", tz_offset_min=0, now_ts=NOW, top_n=5)
    assert "techno" in ev["genres"]
    assert len(ev["buckets"]) == len(ev["data"]["techno"])
    assert len(ev["buckets"]) >= 1
    # The two plays are ~60 days apart → first lands in the first bucket,
    # most-recent in the last bucket.
    assert ev["data"]["techno"][0] > 0
    assert ev["data"]["techno"][-1] > 0


def test_genre_evolution_single_timestamp(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    _seed_with_genres(conn, [(1700000000, "A", "t1")], {"A": "techno"})
    ev = analytics.genre_evolution(conn, period="all", tz_offset_min=0, now_ts=NOW)
    assert ev["genres"] == ["techno"]
    assert len(ev["buckets"]) == 1
    assert ev["data"]["techno"] == [0.0]
