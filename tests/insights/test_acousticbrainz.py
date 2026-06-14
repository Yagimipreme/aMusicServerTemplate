"""Tests for insights/acousticbrainz.py — feature lookup by recording MBID."""

from unittest.mock import MagicMock

from insights import acousticbrainz as ab


def _resp(status, payload):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = payload
    r.raise_for_status.return_value = None
    return r


_LOWLEVEL = {"rhythm": {"bpm": 128.4}, "tonal": {"key_key": "A", "key_scale": "minor"}}
_HIGHLEVEL = {"highlevel": {
    "mood_happy": {"value": "happy", "probability": 0.81, "all": {"happy": 0.81, "not happy": 0.19}},
    "mood_sad": {"value": "not sad", "probability": 0.7, "all": {"sad": 0.3, "not sad": 0.7}},
    "danceability": {"value": "danceable", "probability": 0.9, "all": {"danceable": 0.9, "not_danceable": 0.1}},
}}


def test_fetch_features_parses_bpm_key_mood():
    session = MagicMock()
    session.get.side_effect = [_resp(200, _LOWLEVEL), _resp(200, _HIGHLEVEL)]
    feat = ab.fetch_features("mbid-1", session=session)
    assert feat["bpm"] == 128.4
    assert feat["key"] == "A" and feat["scale"] == "minor"
    assert feat["mood"] == "happy"
    assert feat["danceability"] == 0.9
    assert "mood_happy" in feat["mood_scores"]


def test_fetch_features_returns_none_on_404():
    session = MagicMock()
    session.get.return_value = _resp(404, {"message": "Not found"})
    assert ab.fetch_features("missing", session=session) is None


def test_fetch_features_returns_none_on_error():
    session = MagicMock()
    session.get.side_effect = RuntimeError("network")
    assert ab.fetch_features("x", session=session) is None


def test_primary_mood_ignores_negatives():
    hl = {"mood_aggressive": {"value": "not aggressive", "probability": 0.95},
          "mood_relaxed": {"value": "relaxed", "probability": 0.6}}
    assert ab._primary_mood(hl) == "relaxed"
