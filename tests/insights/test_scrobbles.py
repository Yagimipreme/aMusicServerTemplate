"""Tests for insights/scrobbles.py — parsing and incremental sync."""

from unittest.mock import MagicMock

from insights import db, scrobbles


# A representative getRecentTracks page: one normal track, one now-playing
# (must be skipped), plus @attr pagination metadata.
_PAGE = {
    "recenttracks": {
        "track": [
            {
                "artist": {"#text": "Aphex Twin", "mbid": "am-1"},
                "name": "Xtal",
                "album": {"#text": "Selected Ambient Works"},
                "mbid": "rec-1",
                "date": {"uts": "1700000000", "#text": "..."},
            },
            {
                "artist": {"#text": "Boards of Canada", "mbid": ""},
                "name": "Roygbiv",
                "album": {"#text": ""},
                "mbid": "",
                "@attr": {"nowplaying": "true"},
            },
        ],
        "@attr": {"page": "1", "totalPages": "3", "total": "120"},
    }
}


def test_parse_skips_nowplaying_and_extracts_fields():
    rows = scrobbles.parse_recent_tracks(_PAGE)
    assert len(rows) == 1
    r = rows[0]
    assert r["ts"] == 1700000000
    assert r["artist"] == "Aphex Twin"
    assert r["track"] == "Xtal"
    assert r["album"] == "Selected Ambient Works"
    assert r["artist_mbid"] == "am-1"
    assert r["recording_mbid"] == "rec-1"


def test_parse_blank_optional_fields_become_none():
    page = {
        "recenttracks": {
            "track": {
                "artist": {"#text": "X", "mbid": ""},
                "name": "Y",
                "album": {"#text": ""},
                "mbid": "",
                "date": {"uts": "100"},
            }
        }
    }
    rows = scrobbles.parse_recent_tracks(page)
    assert len(rows) == 1
    assert rows[0]["album"] is None
    assert rows[0]["artist_mbid"] is None
    assert rows[0]["recording_mbid"] is None


def test_parse_empty_page_returns_empty_list():
    assert scrobbles.parse_recent_tracks({}) == []
    assert scrobbles.parse_recent_tracks({"recenttracks": {"track": []}}) == []


def test_total_pages_reads_attr():
    assert scrobbles.total_pages(_PAGE) == 3
    assert scrobbles.total_pages({}) == 1


def test_parse_skips_empty_artist():
    page = {"recenttracks": {"track": [
        {"artist": {"#text": ""}, "name": "Orphan", "album": {"#text": ""},
         "mbid": "", "date": {"uts": "500"}},
    ]}}
    assert scrobbles.parse_recent_tracks(page) == []


def test_insert_scrobbles_dedups_on_primary_key(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    rows = [{"ts": 1, "artist": "A", "track": "T", "album": None,
             "artist_mbid": None, "recording_mbid": None}]
    assert scrobbles.insert_scrobbles(conn, rows) == 1
    # Same PK again → ignored.
    assert scrobbles.insert_scrobbles(conn, rows) == 0
    assert conn.execute("SELECT COUNT(*) FROM scrobbles").fetchone()[0] == 1


def _page(tracks, total_pages):
    return {"recenttracks": {"track": tracks,
                             "@attr": {"totalPages": str(total_pages)}}}


def _track(uts, name):
    return {"artist": {"#text": "A", "mbid": ""}, "name": name,
            "album": {"#text": ""}, "mbid": "", "date": {"uts": str(uts)}}


def test_sync_walks_all_pages_and_records_last_ts(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    client = MagicMock()
    client.call.side_effect = [
        _page([_track(300, "c"), _track(250, "b")], 2),
        _page([_track(100, "a")], 2),
    ]
    result = scrobbles.sync_scrobbles(client, "user", conn, page_limit=2)
    assert result["inserted"] == 3
    assert result["pages"] == 2
    assert db.get_state(conn, "last_ts") == "300"
    assert conn.execute("SELECT COUNT(*) FROM scrobbles").fetchone()[0] == 3


def test_sync_resumes_from_stored_last_ts(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    db.set_state(conn, "last_ts", "200")
    client = MagicMock()
    client.call.return_value = _page([_track(300, "c")], 1)
    scrobbles.sync_scrobbles(client, "user", conn, page_limit=50)
    # The 'from' parameter must be passed so we only fetch newer plays.
    _, kwargs = client.call.call_args
    assert kwargs.get("from") == 200
    assert db.get_state(conn, "last_ts") == "300"
