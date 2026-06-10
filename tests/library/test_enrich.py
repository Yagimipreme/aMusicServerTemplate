"""Tests for library/enrich.py — genre tagging with eyed3 fixture files."""

from unittest.mock import MagicMock, patch, call

import pytest

from library.enrich import run


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_tag(genre_name=None, artist="Aphex Twin", title="Windowlicker"):
    """Return a MagicMock tag object."""
    tag = MagicMock()
    tag.artist = artist
    tag.title = title
    if genre_name is None:
        tag.genre = None
    else:
        g = MagicMock()
        g.name = genre_name
        g.id = None
        tag.genre = g
    return tag


def _make_audio(tag):
    audio = MagicMock()
    audio.tag = tag
    return audio


def _mock_scanner_with(records):
    """Patch library.enrich.scan to return given records."""
    return patch("library.enrich.scan", return_value=records)


def _make_lastfm_client(track_tags=None, artist_tags=None):
    """Build a mock LastFMClient that returns given tags."""
    from lastfm.client import LastFMNotFound
    client = MagicMock()

    def fake_call(method, **params):
        if method == "track.getTopTags":
            if track_tags is None:
                raise LastFMNotFound(6, "not found")
            return {"toptags": {"tag": track_tags}}
        if method == "artist.getTopTags":
            if artist_tags is None:
                return {"toptags": {"tag": []}}
            return {"toptags": {"tag": artist_tags}}
        return {}

    client.call.side_effect = fake_call
    return client


# ── Test: file with no genre gets tagged ───────────────────────────────────────

def test_file_with_no_genre_gets_tagged(tmp_path):
    mp3 = tmp_path / "song.mp3"
    mp3.write_bytes(b"")

    tag = _make_tag(genre_name=None)
    audio = _make_audio(tag)

    records = [{"path": str(mp3), "artist": "Aphex Twin", "title": "Windowlicker"}]
    client = _make_lastfm_client(
        track_tags=[{"name": "electronic", "count": "80"}]
    )

    with _mock_scanner_with(records):
        with patch("library.enrich.eyed3.load", return_value=audio):
            result = run(str(tmp_path), client, only_missing_genre=True)

    assert result["tagged"] == 1
    assert result["errors"] == 0
    tag.save.assert_called_once()
    # Genre was set
    assert tag.genre is not None


def test_file_with_existing_genre_skipped_when_only_missing_true(tmp_path):
    mp3 = tmp_path / "song.mp3"
    mp3.write_bytes(b"")

    tag = _make_tag(genre_name="Electronic")
    audio = _make_audio(tag)

    records = [{"path": str(mp3), "artist": "Aphex Twin", "title": "Windowlicker"}]
    client = _make_lastfm_client(
        track_tags=[{"name": "electronic", "count": "80"}]
    )

    with _mock_scanner_with(records):
        with patch("library.enrich.eyed3.load", return_value=audio):
            result = run(str(tmp_path), client, only_missing_genre=True)

    assert result["skipped"] == 1
    assert result["tagged"] == 0
    tag.save.assert_not_called()


def test_file_with_genre_gets_retagged_when_only_missing_false(tmp_path):
    mp3 = tmp_path / "song.mp3"
    mp3.write_bytes(b"")

    tag = _make_tag(genre_name="Rock")
    audio = _make_audio(tag)

    records = [{"path": str(mp3), "artist": "Aphex Twin", "title": "Windowlicker"}]
    client = _make_lastfm_client(
        track_tags=[{"name": "electronic", "count": "80"}]
    )

    with _mock_scanner_with(records):
        with patch("library.enrich.eyed3.load", return_value=audio):
            result = run(str(tmp_path), client, only_missing_genre=False)

    assert result["tagged"] == 1
    tag.save.assert_called_once()


# ── Test: track not found falls back to artist tags ───────────────────────────

def test_fallback_to_artist_tags_when_track_not_found(tmp_path):
    mp3 = tmp_path / "song.mp3"
    mp3.write_bytes(b"")

    tag = _make_tag(genre_name=None)
    audio = _make_audio(tag)

    records = [{"path": str(mp3), "artist": "Aphex Twin", "title": "Obscure B-Side"}]
    # track_tags=None → LastFMNotFound; artist_tags returns something
    client = _make_lastfm_client(
        track_tags=None,
        artist_tags=[{"name": "idm", "count": "75"}],
    )

    with _mock_scanner_with(records):
        with patch("library.enrich.eyed3.load", return_value=audio):
            result = run(str(tmp_path), client, only_missing_genre=True)

    assert result["tagged"] == 1


def test_skipped_when_both_track_and_artist_tags_empty(tmp_path):
    mp3 = tmp_path / "song.mp3"
    mp3.write_bytes(b"")

    tag = _make_tag(genre_name=None)
    audio = _make_audio(tag)

    records = [{"path": str(mp3), "artist": "Unknown Artist", "title": "Unknown Track"}]
    client = _make_lastfm_client(track_tags=None, artist_tags=None)

    with _mock_scanner_with(records):
        with patch("library.enrich.eyed3.load", return_value=audio):
            result = run(str(tmp_path), client, only_missing_genre=True)

    assert result["tagged"] == 0
    tag.save.assert_not_called()


# ── Test: limit parameter ──────────────────────────────────────────────────────

def test_limit_caps_processed_files(tmp_path):
    records = [
        {"path": f"/fake/song{i}.mp3", "artist": "A", "title": f"T{i}"}
        for i in range(5)
    ]
    client = _make_lastfm_client(
        track_tags=[{"name": "electronic", "count": "80"}]
    )
    tag = _make_tag(genre_name=None)
    audio = _make_audio(tag)

    with _mock_scanner_with(records):
        with patch("library.enrich.eyed3.load", return_value=audio):
            result = run("/fake", client, only_missing_genre=True, limit=2)

    # Only 2 files processed (limited)
    assert result["tagged"] + result["skipped"] + result["errors"] <= 2


# ── Test: file with no artist/title tags is skipped ───────────────────────────

def test_file_with_no_id3_artist_title_is_skipped(tmp_path):
    mp3 = tmp_path / "song.mp3"
    mp3.write_bytes(b"")

    tag = _make_tag(genre_name=None, artist="", title="")
    audio = _make_audio(tag)

    records = [{"path": str(mp3), "artist": "", "title": ""}]
    client = _make_lastfm_client(
        track_tags=[{"name": "electronic", "count": "80"}]
    )

    with _mock_scanner_with(records):
        with patch("library.enrich.eyed3.load", return_value=audio):
            result = run(str(tmp_path), client, only_missing_genre=True)

    assert result["tagged"] == 0


# ── Test: returns correct result dict structure ────────────────────────────────

def test_run_returns_expected_keys(tmp_path):
    with _mock_scanner_with([]):
        result = run(str(tmp_path), MagicMock(), only_missing_genre=True)
    assert set(result.keys()) == {"processed", "tagged", "skipped", "errors"}
