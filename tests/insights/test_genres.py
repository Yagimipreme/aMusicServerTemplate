"""Tests for insights/genres.py — artist genre cache."""

from unittest.mock import MagicMock

from insights import db, genres


def test_primary_genre_for_picks_highest_weight():
    tags = [{"name": "techno", "weight": 100}, {"name": "house", "weight": 40}]
    assert genres.primary_genre_for(tags) == "techno"


def test_primary_genre_for_empty_is_none():
    assert genres.primary_genre_for([]) is None


def test_ensure_artist_tags_fetches_and_caches(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    conn.execute("INSERT INTO scrobbles (ts, artist, track) VALUES (1, 'Surgeon', 'X')")
    conn.commit()

    client = MagicMock()
    client.call.return_value = {"toptags": {"tag": [
        {"name": "techno", "count": 100},
        {"name": "seen live", "count": 90},
        {"name": "industrial techno", "count": 50},
    ]}}

    n = genres.ensure_artist_tags(client, conn, ["Surgeon"])
    assert n == 1
    row = conn.execute(
        "SELECT primary_genre, tags_json, fetched_at FROM artist_tags WHERE artist='Surgeon'"
    ).fetchone()
    assert row["primary_genre"] == "techno"
    assert "techno" in row["tags_json"]
    assert row["fetched_at"] is not None


def test_ensure_artist_tags_skips_already_cached(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    conn.execute(
        "INSERT INTO artist_tags (artist, tags_json, primary_genre, fetched_at) "
        "VALUES ('Surgeon', '[]', 'techno', 123)"
    )
    conn.commit()
    client = MagicMock()
    n = genres.ensure_artist_tags(client, conn, ["Surgeon"])
    assert n == 0
    client.call.assert_not_called()


def test_ensure_artist_tags_caches_empty_result_to_avoid_refetch(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    client = MagicMock()
    client.call.return_value = {"toptags": {"tag": []}}
    n = genres.ensure_artist_tags(client, conn, ["Unknown Artist"])
    assert n == 1
    row = conn.execute(
        "SELECT primary_genre, tags_json FROM artist_tags WHERE artist='Unknown Artist'"
    ).fetchone()
    assert row["primary_genre"] is None
    assert row["tags_json"] == "[]"
    genres.ensure_artist_tags(client, conn, ["Unknown Artist"])
    assert client.call.call_count == 1
