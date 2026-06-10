"""Tests for lastfm/similar.py — genre profile building, dot-product scoring, empty fallback."""

from unittest.mock import MagicMock

from lastfm.similar import get_artist_top_tracks, get_similar_artists, score_candidates


def _client_with(data):
    client = MagicMock()
    client.call.return_value = data
    return client


# ── get_similar_artists ────────────────────────────────────────────────────────

def test_get_similar_artists_returns_name_and_match():
    client = _client_with({
        "similarartists": {
            "artist": [
                {"name": "Boards of Canada", "match": "0.95"},
                {"name": "Plaid", "match": "0.80"},
            ]
        }
    })
    result = get_similar_artists(client, "Aphex Twin", limit=10)
    assert result[0]["name"] == "Boards of Canada"
    assert abs(result[0]["match"] - 0.95) < 1e-6
    assert result[1]["name"] == "Plaid"


def test_get_similar_artists_sorted_by_match_desc():
    client = _client_with({
        "similarartists": {
            "artist": [
                {"name": "B", "match": "0.5"},
                {"name": "A", "match": "0.9"},
            ]
        }
    })
    result = get_similar_artists(client, "Test")
    assert result[0]["name"] == "A"


def test_get_similar_artists_returns_empty_on_not_found():
    from lastfm.client import LastFMNotFound
    client = MagicMock()
    client.call.side_effect = LastFMNotFound(6, "Artist not found")
    result = get_similar_artists(client, "Unknown")
    assert result == []


def test_get_similar_artists_returns_empty_on_timeout():
    from lastfm.client import LastFMTimeout
    client = MagicMock()
    client.call.side_effect = LastFMTimeout("timeout")
    result = get_similar_artists(client, "Unknown")
    assert result == []


def test_get_similar_artists_handles_single_dict():
    client = _client_with({
        "similarartists": {
            "artist": {"name": "Autechre", "match": "0.7"}
        }
    })
    result = get_similar_artists(client, "Aphex Twin")
    assert len(result) == 1
    assert result[0]["name"] == "Autechre"


# ── get_artist_top_tracks ──────────────────────────────────────────────────────

def test_get_artist_top_tracks_returns_title_and_artist():
    client = _client_with({
        "toptracks": {
            "track": [
                {"name": "Windowlicker", "artist": {"name": "Aphex Twin"}},
                {"name": "Come to Daddy", "artist": {"name": "Aphex Twin"}},
            ]
        }
    })
    result = get_artist_top_tracks(client, "Aphex Twin", limit=5)
    assert result[0] == {"title": "Windowlicker", "artist": "Aphex Twin"}
    assert result[1] == {"title": "Come to Daddy", "artist": "Aphex Twin"}


def test_get_artist_top_tracks_returns_empty_on_error():
    client = MagicMock()
    client.call.side_effect = Exception("boom")
    result = get_artist_top_tracks(client, "Unknown")
    assert result == []


# ── score_candidates ───────────────────────────────────────────────────────────

def test_score_candidates_with_genre_profile_uses_dot_product():
    similar_artists = [
        {"name": "Artist A", "match": 0.9},
        {"name": "Artist B", "match": 0.8},
    ]
    genre_profile = {"electronic": 0.6, "ambient": 0.4}
    candidate_tags = {
        "artist a": [
            {"name": "electronic", "weight": 100},
            {"name": "ambient", "weight": 80},
        ],
        "artist b": [
            {"name": "rock", "weight": 100},
        ],
    }
    result = score_candidates(similar_artists, genre_profile, candidate_tags)
    # Artist A has high genre overlap, Artist B has none
    assert result[0]["name"] == "Artist A"
    assert result[0]["score"] > result[1]["score"]


def test_score_candidates_empty_profile_falls_back_to_pure_match():
    similar_artists = [
        {"name": "Artist A", "match": 0.9},
        {"name": "Artist B", "match": 0.5},
    ]
    result = score_candidates(similar_artists, {}, {})
    # With empty profile, score == match
    assert result[0]["name"] == "Artist A"
    assert abs(result[0]["score"] - 0.9) < 1e-9
    assert abs(result[1]["score"] - 0.5) < 1e-9


def test_score_candidates_returns_sorted_by_score_desc():
    similar_artists = [
        {"name": "Low", "match": 0.3},
        {"name": "High", "match": 0.95},
    ]
    result = score_candidates(similar_artists, {}, {})
    assert result[0]["name"] == "High"


def test_score_candidates_returns_empty_for_empty_input():
    result = score_candidates([], {"electronic": 1.0}, {})
    assert result == []


def test_score_candidates_candidate_with_no_tags_scores_zero_when_profile_set():
    similar_artists = [{"name": "Mystery", "match": 0.8}]
    genre_profile = {"electronic": 1.0}
    candidate_tags = {}  # no tags for Mystery
    result = score_candidates(similar_artists, genre_profile, candidate_tags)
    assert result[0]["score"] == 0.0
