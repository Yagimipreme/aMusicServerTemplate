"""Tests for lastfm/seeds.py — top artists + weekly chart."""

from unittest.mock import MagicMock

from lastfm.seeds import get_top_artists, get_weekly_chart


def _client_with(data):
    client = MagicMock()
    client.call.return_value = data
    return client


# ── get_top_artists ────────────────────────────────────────────────────────────

def test_get_top_artists_returns_name_list():
    client = _client_with({
        "topartists": {
            "artist": [
                {"name": "Aphex Twin", "playcount": "200"},
                {"name": "Boards of Canada", "playcount": "150"},
            ]
        }
    })
    result = get_top_artists(client, "testuser", period="1month", limit=5)
    assert result == ["Aphex Twin", "Boards of Canada"]


def test_get_top_artists_handles_single_result_dict():
    """Last.fm returns a dict (not list) when there is exactly one result."""
    client = _client_with({
        "topartists": {
            "artist": {"name": "Aphex Twin", "playcount": "999"}
        }
    })
    result = get_top_artists(client, "testuser")
    assert result == ["Aphex Twin"]


def test_get_top_artists_returns_empty_on_api_error():
    from lastfm.client import LastFMError
    client = MagicMock()
    client.call.side_effect = LastFMError(6, "not found")
    result = get_top_artists(client, "nobody")
    assert result == []


def test_get_top_artists_returns_empty_on_network_error():
    from lastfm.client import LastFMTimeout
    client = MagicMock()
    client.call.side_effect = LastFMTimeout("timeout")
    result = get_top_artists(client, "nobody")
    assert result == []


def test_get_top_artists_skips_blank_names():
    client = _client_with({
        "topartists": {
            "artist": [
                {"name": "", "playcount": "10"},
                {"name": "Autechre", "playcount": "5"},
            ]
        }
    })
    result = get_top_artists(client, "user")
    assert result == ["Autechre"]


# ── get_weekly_chart ───────────────────────────────────────────────────────────

def test_get_weekly_chart_returns_name_list():
    client = _client_with({
        "weeklyartistchart": {
            "artist": [
                {"name": "Plaid", "playcount": "30"},
                {"name": "Squarepusher", "playcount": "20"},
            ]
        }
    })
    result = get_weekly_chart(client, "testuser")
    assert result == ["Plaid", "Squarepusher"]


def test_get_weekly_chart_returns_empty_on_error():
    client = MagicMock()
    client.call.side_effect = Exception("network down")
    result = get_weekly_chart(client, "user")
    assert result == []


# ── discover/seeds.py blending logic ──────────────────────────────────────────

from discover.seeds import collect_seeds


class FakeSubsonic:
    def __init__(self, artists):
        self._artists = artists

    def get_frequent_artists(self, size=50):
        return self._artists


def test_collect_seeds_navidrome_only_when_no_lastfm():
    sub = FakeSubsonic([
        {"id": "a1", "name": "BoC"},
        {"id": "a2", "name": "Aphex"},
    ])
    result = collect_seeds(sub, limit=2)
    assert [r["name"] for r in result] == ["BoC", "Aphex"]


def test_collect_seeds_blends_lastfm_boosting_overlap():
    sub = FakeSubsonic([
        {"id": "a1", "name": "BoC"},
        {"id": "a2", "name": "Aphex"},
        {"id": "a3", "name": "Plaid"},
    ])
    client = _client_with({
        "topartists": {
            "artist": [
                {"name": "Aphex", "playcount": "100"},
                {"name": "Autechre", "playcount": "80"},
            ]
        }
    })
    # limit=4: Aphex appears in both → boosted first, then BoC + Plaid (nav-only), then Autechre (lfm-only)
    result = collect_seeds(sub, limit=4, lastfm_client=client, lastfm_username="user")
    names = [r["name"] for r in result]
    assert names[0] == "Aphex"   # boosted — in both sources
    assert "BoC" in names
    assert "Plaid" in names
    assert "Autechre" in names


def test_collect_seeds_falls_back_silently_on_lastfm_error():
    sub = FakeSubsonic([{"id": "a1", "name": "BoC"}])
    client = MagicMock()
    client.call.side_effect = Exception("network")
    result = collect_seeds(sub, limit=2, lastfm_client=client, lastfm_username="user")
    # Should fall back to Navidrome-only without raising
    assert len(result) == 1
    assert result[0]["id"] == "a1"
    assert result[0]["name"] == "BoC"


def test_collect_seeds_respects_limit():
    sub = FakeSubsonic([{"id": f"a{i}", "name": f"Artist{i}"} for i in range(10)])
    result = collect_seeds(sub, limit=3)
    assert len(result) == 3
