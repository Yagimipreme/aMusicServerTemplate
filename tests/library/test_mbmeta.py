"""Tests for library/mbmeta.py — resolve a track to canonical MB metadata."""
from unittest.mock import MagicMock

from library import mbmeta
from follow.musicbrainz import MBError


def _client(recordings):
    c = MagicMock()
    c.search_recording.return_value = recordings
    return c


def _recording(score=100, releases=None):
    return {
        "mbid": "rec-1", "score": score, "title": "Teardrop",
        "artist_mbid": "art-1", "artist_name": "Massive Attack",
        "releases": releases if releases is not None else [
            {"mbid": "rel-1", "title": "Mezzanine", "date": "1998-04-20",
             "rg_mbid": "rg-1", "primary_type": "Album", "status": "Official"},
        ],
    }


def test_resolve_returns_canonical_fields():
    meta = mbmeta.resolve(_client([_recording()]), "Massive Attack", "Teardrop", 90)
    assert meta == {
        "score": 100, "recording_mbid": "rec-1", "artist_mbid": "art-1",
        "album": "Mezzanine", "album_artist": "Massive Attack", "year": "1998",
        "release_mbid": "rel-1", "rg_mbid": "rg-1",
    }


def test_resolve_returns_none_below_score():
    assert mbmeta.resolve(_client([_recording(score=50)]), "A", "B", 90) is None


def test_resolve_returns_none_when_no_recordings():
    assert mbmeta.resolve(_client([]), "A", "B", 90) is None


def test_resolve_returns_none_on_mb_error():
    c = MagicMock()
    c.search_recording.side_effect = MBError("boom")
    assert mbmeta.resolve(c, "A", "B", 90) is None


def test_pick_release_prefers_official_album_then_earliest():
    releases = [
        {"mbid": "comp", "title": "Best Of", "date": "2010-01-01",
         "rg_mbid": "rg-c", "primary_type": "Album", "status": "Bootleg"},
        {"mbid": "early", "title": "Mezzanine", "date": "1998-04-20",
         "rg_mbid": "rg-e", "primary_type": "Album", "status": "Official"},
        {"mbid": "late", "title": "Reissue", "date": "2008-01-01",
         "rg_mbid": "rg-l", "primary_type": "Album", "status": "Official"},
    ]
    meta = mbmeta.resolve(_client([_recording(releases=releases)]), "A", "B", 90)
    assert meta["release_mbid"] == "early"
    assert meta["year"] == "1998"


def test_resolve_handles_recording_with_no_releases():
    meta = mbmeta.resolve(_client([_recording(releases=[])]), "A", "B", 90)
    assert meta["album"] == ""
    assert meta["year"] == ""
    assert meta["release_mbid"] == ""
    assert meta["recording_mbid"] == "rec-1"
