"""Tests for insights/features.py — MBID resolution + feature orchestration."""

from insights import db, features


def _seed(conn, rows):
    conn.executemany(
        "INSERT INTO scrobbles (ts, artist, track, recording_mbid) VALUES (?, ?, ?, ?)",
        rows)
    conn.commit()


def test_resolve_prefers_stored_mbid(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    _seed(conn, [(1, "A", "t1", "stored-mbid")])
    assert features.resolve_recording_mbid(conn, "A", "t1") == "stored-mbid"


def test_resolve_falls_back_to_search(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    _seed(conn, [(1, "A", "t1", None)])
    assert features.resolve_recording_mbid(
        conn, "A", "t1", mb_search=lambda ar, tr: "searched-mbid") == "searched-mbid"


def test_ensure_writes_acousticbrainz_features(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    _seed(conn, [(1, "A", "t1", "mbid-1")])
    ab_fetch = lambda m: {"bpm": 120.0, "key": "C", "scale": "major",
                          "mood": "happy", "mood_scores": {"mood_happy": {}},
                          "danceability": 0.7}
    n = features.ensure_track_features(conn, ab_fetch=ab_fetch)
    assert n == 1
    row = conn.execute("SELECT bpm, key, mood, source FROM track_features "
                       "WHERE artist='A' AND track='t1'").fetchone()
    assert row["bpm"] == 120.0 and row["mood"] == "happy"
    assert row["source"] == "acousticbrainz"


def test_ensure_negative_caches_misses(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    _seed(conn, [(1, "A", "t1", "mbid-1")])
    calls = {"n": 0}
    def ab_fetch(m):
        calls["n"] += 1
        return None
    features.ensure_track_features(conn, ab_fetch=ab_fetch)
    row = conn.execute("SELECT source FROM track_features WHERE artist='A'").fetchone()
    assert row["source"] is None
    features.ensure_track_features(conn, ab_fetch=ab_fetch)
    assert calls["n"] == 1


def test_ensure_uses_local_fallback_on_ab_miss(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    _seed(conn, [(1, "A", "t1", "mbid-1")])
    local = lambda ar, tr: {"bpm": 90.0, "key": "G", "scale": None,
                            "mood": "calm", "mood_scores": None, "danceability": None}
    features.ensure_track_features(conn, ab_fetch=lambda m: None, local_analyze=local)
    row = conn.execute("SELECT bpm, source FROM track_features WHERE artist='A'").fetchone()
    assert row["bpm"] == 90.0 and row["source"] == "librosa"


def test_ensure_respects_limit(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    _seed(conn, [(1, "A", "t1", "m1"), (2, "B", "t2", "m2"), (3, "C", "t3", "m3")])
    n = features.ensure_track_features(conn, ab_fetch=lambda m: None, limit=2)
    assert n == 2


def test_resolve_no_row_returns_none(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    assert features.resolve_recording_mbid(conn, "Nobody", "Nothing") is None
