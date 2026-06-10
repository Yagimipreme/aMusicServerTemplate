"""Tests for lastfm/tags.py — noise filtering, weight threshold, top-3, artist fallback."""

from unittest.mock import MagicMock

from lastfm.tags import (
    NOISE_TAGS,
    build_genre_profile,
    get_artist_tags,
    get_track_tags,
)


def _client_with(data):
    client = MagicMock()
    client.call.return_value = data
    return client


def _tags_response(tags):
    """Build a toptags response dict."""
    return {"toptags": {"tag": tags}}


# ── Noise filtering ────────────────────────────────────────────────────────────

def test_get_track_tags_filters_noise_tags():
    client = _client_with(_tags_response([
        {"name": "electronic", "count": "80"},
        {"name": "seen live", "count": "60"},
        {"name": "ambient", "count": "50"},
    ]))
    result = get_track_tags(client, "Aphex Twin", "Windowlicker")
    names = [t["name"] for t in result]
    assert "seen live" not in names
    assert "electronic" in names
    assert "ambient" in names


def test_get_artist_tags_filters_noise_tags():
    client = _client_with(_tags_response([
        {"name": "favorites", "count": "90"},
        {"name": "idm", "count": "75"},
    ]))
    result = get_artist_tags(client, "Aphex Twin")
    names = [t["name"] for t in result]
    assert "favorites" not in names
    assert "idm" in names


# ── Weight threshold ───────────────────────────────────────────────────────────

def test_get_track_tags_drops_tags_below_weight_10():
    client = _client_with(_tags_response([
        {"name": "electronic", "count": "80"},
        {"name": "rare", "count": "5"},   # below threshold
        {"name": "ambient", "count": "9"},  # exactly below
    ]))
    result = get_track_tags(client, "A", "T")
    names = [t["name"] for t in result]
    assert "rare" not in names
    assert "ambient" not in names
    assert "electronic" in names


def test_get_track_tags_keeps_tags_at_exactly_10():
    client = _client_with(_tags_response([
        {"name": "electronic", "count": "10"},
    ]))
    result = get_track_tags(client, "A", "T")
    assert len(result) == 1
    assert result[0]["name"] == "electronic"


# ── Top-3 selection ────────────────────────────────────────────────────────────

def test_get_track_tags_returns_at_most_3():
    client = _client_with(_tags_response([
        {"name": f"genre{i}", "count": str(80 - i * 5)}
        for i in range(8)
    ]))
    result = get_track_tags(client, "A", "T")
    assert len(result) <= 3


def test_get_track_tags_returns_highest_weight_first():
    client = _client_with(_tags_response([
        {"name": "ambient", "count": "50"},
        {"name": "electronic", "count": "80"},
        {"name": "idm", "count": "65"},
    ]))
    result = get_track_tags(client, "A", "T")
    assert result[0]["name"] == "electronic"
    assert result[1]["name"] == "idm"
    assert result[2]["name"] == "ambient"


# ── Artist fallback ────────────────────────────────────────────────────────────

def test_get_track_tags_returns_empty_on_not_found():
    from lastfm.client import LastFMNotFound
    client = MagicMock()
    client.call.side_effect = LastFMNotFound(6, "Track not found")
    result = get_track_tags(client, "Unknown", "NoSong")
    assert result == []


def test_get_artist_tags_returns_empty_on_error():
    from lastfm.client import LastFMTimeout
    client = MagicMock()
    client.call.side_effect = LastFMTimeout("timeout")
    result = get_artist_tags(client, "Unknown")
    assert result == []


# ── build_genre_profile ────────────────────────────────────────────────────────

def test_build_genre_profile_normalizes_to_sum_one():
    tag_sets = [
        [{"name": "electronic", "weight": 80}, {"name": "ambient", "weight": 40}],
        [{"name": "ambient", "weight": 60}, {"name": "idm", "weight": 20}],
    ]
    profile = build_genre_profile(tag_sets)
    total = sum(profile.values())
    assert abs(total - 1.0) < 1e-9


def test_build_genre_profile_gives_shared_tags_higher_weight():
    tag_sets = [
        [{"name": "ambient", "weight": 80}],
        [{"name": "ambient", "weight": 60}, {"name": "drone", "weight": 20}],
    ]
    profile = build_genre_profile(tag_sets)
    assert profile["ambient"] > profile["drone"]


def test_build_genre_profile_returns_empty_for_all_empty_sets():
    result = build_genre_profile([[], [], []])
    assert result == {}


def test_build_genre_profile_handles_single_artist():
    tag_sets = [
        [{"name": "electronic", "weight": 100}, {"name": "techno", "weight": 50}],
    ]
    profile = build_genre_profile(tag_sets)
    total = sum(profile.values())
    assert abs(total - 1.0) < 1e-9
    assert "electronic" in profile
    assert "techno" in profile


def test_build_genre_profile_noise_tags_not_present():
    """build_genre_profile just sums what it's given; noise tag filtering happens upstream."""
    tag_sets = [
        [{"name": "electronic", "weight": 80}],
    ]
    profile = build_genre_profile(tag_sets)
    assert list(profile.keys()) == ["electronic"]
