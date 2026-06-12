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
