import pytest
from library.repair import _repair_by_title_parse, _repair_by_lastfm


# ── Stage 1: title parsing ────────────────────────────────────────────────────

def test_stage1_hyphen_separator():
    artist, title = _repair_by_title_parse("Burial - Archangel")
    assert artist == "Burial"
    assert title == "Archangel"


def test_stage1_en_dash():
    artist, title = _repair_by_title_parse("Demdike Stare – Testpressing #7")
    assert artist == "Demdike Stare"
    assert title == "Testpressing #7"


def test_stage1_em_dash():
    artist, title = _repair_by_title_parse("The Bug — Skeng")
    assert artist == "The Bug"
    assert title == "Skeng"


def test_stage1_takes_first_separator_only():
    artist, title = _repair_by_title_parse("The Bug - Skeng - feat. Flowdan")
    assert artist == "The Bug"
    assert title == "Skeng - feat. Flowdan"


def test_stage1_no_separator_returns_none():
    artist, title = _repair_by_title_parse("Archangel")
    assert artist is None
    assert title is None


def test_stage1_strips_whitespace():
    artist, title = _repair_by_title_parse("  Actress  -  Hubble  ")
    assert artist == "Actress"
    assert title == "Hubble"


# ── Stage 2: Last.fm ──────────────────────────────────────────────────────────

class _FakeLFM:
    def __init__(self, artist="Burial", listeners=50000):
        self._artist = artist
        self._listeners = listeners

    def call(self, method, **kwargs):
        return {"results": {"trackmatches": {"track": [
            {"artist": self._artist, "listeners": str(self._listeners)}
        ]}}}


class _EmptyLFM:
    def call(self, method, **kwargs):
        return {"results": {"trackmatches": {"track": []}}}


class _BrokenLFM:
    def call(self, method, **kwargs):
        raise RuntimeError("API error")


def test_stage2_returns_artist_above_floor():
    assert _repair_by_lastfm(_FakeLFM(listeners=50000), "Archangel", 10000) == "Burial"


def test_stage2_returns_none_below_floor():
    assert _repair_by_lastfm(_FakeLFM(listeners=500), "obscure", 10000) is None


def test_stage2_returns_none_on_empty_results():
    assert _repair_by_lastfm(_EmptyLFM(), "unknown", 10000) is None


def test_stage2_returns_none_on_api_error():
    assert _repair_by_lastfm(_BrokenLFM(), "anything", 10000) is None


def test_stage2_handles_dict_track_response():
    """Last.fm sometimes returns a dict instead of list when there's one result."""
    class DictLFM:
        def call(self, method, **kwargs):
            return {"results": {"trackmatches": {
                "track": {"artist": "Burial", "listeners": "99999"}
            }}}
    assert _repair_by_lastfm(DictLFM(), "Archangel", 10000) == "Burial"


from unittest.mock import patch, MagicMock
import json as _json
from library.repair import _repair_by_musicbrainz, repair_missing_artists


# ── Stage 3: MusicBrainz ──────────────────────────────────────────────────────

def _mb_response(artist_name, score=95):
    return _json.dumps({
        "recordings": [{
            "score": score,
            "artist-credit": [{"artist": {"name": artist_name}}]
        }]
    }).encode()


def test_stage3_returns_artist_above_score_threshold():
    mock_resp = MagicMock()
    mock_resp.read.return_value = _mb_response("Burial", score=95)
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = _repair_by_musicbrainz("Archangel", min_score=90)
    assert result == "Burial"


def test_stage3_returns_none_below_score_threshold():
    mock_resp = MagicMock()
    mock_resp.read.return_value = _mb_response("Burial", score=70)
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = _repair_by_musicbrainz("Archangel", min_score=90)
    assert result is None


def test_stage3_returns_none_on_empty_recordings():
    mock_resp = MagicMock()
    mock_resp.read.return_value = _json.dumps({"recordings": []}).encode()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = _repair_by_musicbrainz("unknown track", min_score=90)
    assert result is None


def test_stage3_returns_none_on_network_error():
    with patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
        result = _repair_by_musicbrainz("any title", min_score=90)
    assert result is None


# ── Orchestrator ──────────────────────────────────────────────────────────────

def test_orchestrator_skips_tracks_with_existing_artist(tmp_path):
    """Tracks that already have an artist tag must be counted as skipped."""
    class FakeTag:
        artist = "Burial"
        title = "Archangel"
        def save(self): pass

    class FakeAF:
        tag = FakeTag()

    with patch("library.repair.eyed3.load", return_value=FakeAF()), \
         patch("library.scanner.scan", return_value=[{"path": str(tmp_path / "f.mp3"), "key": "k", "artist": "Burial", "title": "Archangel", "has_tags": True}]):
        stats = repair_missing_artists(str(tmp_path))

    assert stats["skipped"] == 1
    assert stats["repaired_stage1"] == 0
