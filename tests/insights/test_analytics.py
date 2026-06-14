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


def test_top_entities_artists_and_tracks(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    _seed(conn, [(1700000000, "A", "t1"), (1700000001, "A", "t1"),
                 (1700000002, "B", "t2")])
    arts = analytics.top_entities(conn, "artist", period="all", tz_offset_min=0,
                                  now_ts=NOW, limit=10)
    assert arts[0] == {"name": "A", "plays": 2}
    tracks = analytics.top_entities(conn, "track", period="all", tz_offset_min=0,
                                    now_ts=NOW, limit=10)
    assert tracks[0]["artist"] == "A" and tracks[0]["track"] == "t1"
    assert tracks[0]["plays"] == 2


def test_new_vs_repeat(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    _seed(conn, [(1700000000, "A", "t1"), (1700000001, "A", "t1"),
                 (1700000002, "B", "t2")])
    nvr = analytics.new_vs_repeat(conn, period="all", tz_offset_min=0, now_ts=NOW)
    assert nvr == {"first": 2, "repeat": 1}


def test_discovery_rate_buckets_first_seen_artists(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    _seed(conn, [(1700000000, "A", "t1"), (1700000001, "A", "t2"),
                 (1700000002, "B", "t3")])
    dr = analytics.discovery_rate(conn, period="all", tz_offset_min=0, now_ts=NOW)
    assert dr == [{"date": "2023-11-14", "new_artists": 2}]


def test_top_entities_albums_excludes_null(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    conn.executemany(
        "INSERT INTO scrobbles (ts, artist, track, album) VALUES (?, ?, ?, ?)",
        [(1700000000, "A", "t1", "Alb1"), (1700000001, "A", "t2", "Alb1"),
         (1700000002, "B", "t3", None)])
    conn.commit()
    albs = analytics.top_entities(conn, "album", period="all", tz_offset_min=0,
                                  now_ts=NOW, limit=10)
    assert albs == [{"album": "Alb1", "plays": 2}]  # NULL album excluded


def test_discovery_rate_finite_period_filters(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    old = NOW - 100 * 86400      # artist X first-seen long ago
    recent = NOW - 5 * 86400     # artist Y first-seen recently (2023-11-11)
    _seed(conn, [(old, "X", "t1"), (recent, "Y", "t2")])
    dr = analytics.discovery_rate(conn, period="30d", tz_offset_min=0, now_ts=NOW)
    assert dr == [{"date": "2023-11-11", "new_artists": 1}]  # only Y within window


def test_overview_summary(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    _seed_with_genres(
        conn,
        [(1700000000, "A", "t1"), (1700000001, "A", "t1"), (1700000002, "B", "t2")],
        {"A": "techno", "B": "house"},
    )
    ov = analytics.overview(conn, period="all", tz_offset_min=0, now_ts=NOW)
    assert ov["total_scrobbles"] == 3
    assert ov["unique_artists"] == 2
    assert ov["unique_tracks"] == 2
    assert ov["top_genre"] == "techno"
    assert ov["first_ts"] == 1700000000
    assert ov["last_ts"] == 1700000002
    assert ov["est_listening_seconds"] == 3 * analytics.AVG_TRACK_SECONDS


def test_all_analytics_handle_empty_db(tmp_path):
    """Every analytics function returns a well-formed empty result on an empty DB.

    The UI hits these endpoints before any sync has run, so empty must never crash.
    """
    conn = db.connect(str(tmp_path / "empty.db"))
    assert analytics.listening_clock(conn, now_ts=NOW)["hours"] == [0] * 24
    assert len(analytics.hour_day_heatmap(conn, now_ts=NOW)["matrix"]) == 7
    assert analytics.weekday_weekend(conn, now_ts=NOW)["weekday"] == 0
    assert analytics.plays_over_time(conn, now_ts=NOW) == []
    assert analytics.top_genres(conn, now_ts=NOW) == []
    assert analytics.genre_by_hour(conn, now_ts=NOW) == {"genres": [], "data": {}}
    assert analytics.genre_diversity(conn, now_ts=NOW) == {
        "distinct": 0, "entropy": 0.0, "normalized_entropy": 0.0}
    assert analytics.genre_evolution(conn, now_ts=NOW) == {
        "buckets": [], "genres": [], "data": {}}
    assert analytics.top_entities(conn, "artist", now_ts=NOW) == []
    assert analytics.new_vs_repeat(conn, now_ts=NOW) == {"first": 0, "repeat": 0}
    assert analytics.discovery_rate(conn, now_ts=NOW) == []
    ov = analytics.overview(conn, now_ts=NOW)
    assert ov["total_scrobbles"] == 0 and ov["top_genre"] is None
    assert ov["first_ts"] is None and ov["est_listening_seconds"] == 0
    assert ov["avg_bpm"] is None
    assert ov["feature_coverage"]["tracks_total"] == 0


def _seed_with_features(conn, scrobble_rows, track_features):
    conn.executemany(
        "INSERT INTO scrobbles (ts, artist, track) VALUES (?, ?, ?)", scrobble_rows)
    for (artist, track), f in track_features.items():
        conn.execute(
            "INSERT INTO track_features (artist, track, bpm, key, scale, mood, source, analyzed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
            (artist, track, f.get("bpm"), f.get("key"), f.get("scale"),
             f.get("mood"), f.get("source", "acousticbrainz")))
    conn.commit()


def test_bpm_curve_by_hour(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    _seed_with_features(conn, [(1700000000, "A", "t1")],
                        {("A", "t1"): {"bpm": 128.0}})
    bc = analytics.bpm_curve(conn, period="all", tz_offset_min=0, now_ts=NOW)
    assert len(bc["hours"]) == 24
    assert bc["hours"][22] == 128.0


def test_bpm_distribution_bins(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    _seed_with_features(conn, [(1700000000, "A", "t1"), (1700000001, "B", "t2")],
                        {("A", "t1"): {"bpm": 125.0}, ("B", "t2"): {"bpm": 128.0}})
    dist = analytics.bpm_distribution(conn, period="all", tz_offset_min=0, now_ts=NOW)
    bin_120 = next(b for b in dist if b["min"] == 120)
    assert bin_120["count"] == 2


def test_key_and_mood_distributions(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    _seed_with_features(conn,
        [(1700000000, "A", "t1"), (1700000001, "A", "t1"), (1700000002, "B", "t2")],
        {("A", "t1"): {"key": "A", "scale": "minor", "mood": "happy"},
         ("B", "t2"): {"key": "C", "scale": "major", "mood": "sad"}})
    keys = analytics.key_distribution(conn, period="all", tz_offset_min=0, now_ts=NOW)
    assert {"key": "A", "scale": "minor", "count": 2} in keys
    moods = analytics.mood_distribution(conn, period="all", tz_offset_min=0, now_ts=NOW)
    assert {"mood": "happy", "count": 2} in moods


def test_mood_by_time_shape(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    _seed_with_features(conn, [(1700000000, "A", "t1")], {("A", "t1"): {"mood": "happy"}})
    mbt = analytics.mood_by_time(conn, period="all", tz_offset_min=0, now_ts=NOW)
    assert "happy" in mbt["moods"]
    assert len(mbt["data"]["happy"]) == 24
    assert mbt["data"]["happy"][22] == 1


def test_feature_coverage(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    _seed_with_features(conn,
        [(1, "A", "t1"), (2, "B", "t2"), (3, "C", "t3")],
        {("A", "t1"): {"bpm": 120.0}, ("B", "t2"): {"bpm": 130.0}})
    cov = analytics.feature_coverage(conn, period="all", tz_offset_min=0, now_ts=NOW)
    assert cov["tracks_total"] == 3
    assert cov["tracks_with_bpm"] == 2
    assert abs(cov["bpm_pct"] - 2 / 3) < 1e-6


def test_overview_includes_avg_bpm_and_coverage(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    _seed_with_features(conn, [(1700000000, "A", "t1"), (1700000001, "B", "t2")],
                        {("A", "t1"): {"bpm": 120.0}, ("B", "t2"): {"bpm": 140.0}})
    ov = analytics.overview(conn, period="all", tz_offset_min=0, now_ts=NOW)
    assert ov["avg_bpm"] == 130.0
    assert "feature_coverage" in ov and ov["feature_coverage"]["tracks_with_bpm"] == 2


def _seed_library(conn, scrobble_rows, library_keys):
    conn.executemany(
        "INSERT INTO scrobbles (ts, artist, track) VALUES (?, ?, ?)", scrobble_rows)
    for a, t in library_keys:
        conn.execute("INSERT OR IGNORE INTO library_tracks (artist, track) VALUES (?, ?)",
                     (a, t))
    conn.commit()


def test_library_overlap(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    _seed_library(conn,
        [(1, "A", "t1"), (2, "A", "t1"), (3, "B", "t2"), (4, "C", "t3")],
        [("a", "t1")])
    ov = analytics.library_overlap(conn, period="all", tz_offset_min=0, now_ts=NOW)
    assert ov["tracks_total"] == 3
    assert ov["tracks_in_library"] == 1
    assert ov["plays_total"] == 4
    assert ov["plays_in_library"] == 2
    assert abs(ov["plays_pct"] - 0.5) < 1e-6


def test_missing_favorites(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    _seed_library(conn,
        [(1, "A", "t1"), (2, "B", "t2"), (3, "B", "t2"), (4, "B", "t2")],
        [("a", "t1")])
    mf = analytics.missing_favorites(conn, period="all", tz_offset_min=0, now_ts=NOW, limit=10)
    assert mf[0] == {"artist": "B", "track": "t2", "plays": 3}
    assert all(not (m["artist"] == "A") for m in mf)
