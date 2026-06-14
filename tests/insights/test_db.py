"""Tests for insights/db.py — schema init and state store."""

from insights import db


def _tables(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    return {r[0] for r in rows}


def test_connect_creates_all_tables(tmp_path):
    conn = db.connect(str(tmp_path / "insights.db"))
    names = _tables(conn)
    assert {"scrobbles", "artist_tags", "track_features", "sync_state"} <= names


def test_init_schema_is_idempotent(tmp_path):
    path = str(tmp_path / "insights.db")
    conn = db.connect(path)
    conn.execute(
        "INSERT INTO scrobbles (ts, artist, track) VALUES (1, 'A', 'T')"
    )
    conn.commit()
    db.init_schema(conn)
    count = conn.execute("SELECT COUNT(*) FROM scrobbles").fetchone()[0]
    assert count == 1


def test_state_get_set_roundtrip(tmp_path):
    conn = db.connect(str(tmp_path / "insights.db"))
    assert db.get_state(conn, "last_ts") is None
    assert db.get_state(conn, "last_ts", "0") == "0"
    db.set_state(conn, "last_ts", "12345")
    assert db.get_state(conn, "last_ts") == "12345"
    db.set_state(conn, "last_ts", "67890")
    assert db.get_state(conn, "last_ts") == "67890"


def test_connect_creates_indexes(tmp_path):
    conn = db.connect(str(tmp_path / "insights.db"))
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"
    ).fetchall()
    assert {r[0] for r in rows} == {"idx_scrobbles_ts", "idx_scrobbles_artist"}
