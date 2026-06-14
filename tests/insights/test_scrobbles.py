"""Tests for insights/scrobbles.py — parsing and incremental sync."""

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
