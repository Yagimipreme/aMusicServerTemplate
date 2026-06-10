"""Tests for soundcloud/mirror.py."""
from unittest.mock import MagicMock, patch


def _make_sc_client():
    client = MagicMock()
    client.client_id = "testcid"
    return client


def test_resolve_returns_user_entity():
    sc = _make_sc_client()
    sc.get.return_value = {
        "kind": "user",
        "id": 42,
        "username": "burial",
        "full_name": "Burial",
        "avatar_url": "https://img/a.jpg",
        "track_count": 10,
        "followers_count": 500,
    }
    from soundcloud.mirror import resolve
    entity = resolve(sc, "https://soundcloud.com/burial")
    sc.get.assert_called_once_with("/resolve", params={"url": "https://soundcloud.com/burial"})
    assert entity["kind"] == "user"
    assert entity["id"] == 42


def test_get_profile_user_fetches_tracks_and_sets():
    sc = _make_sc_client()
    user_data = {
        "kind": "user", "id": 42, "username": "burial",
        "full_name": "Burial", "avatar_url": "", "track_count": 1, "followers_count": 0,
    }
    track_data = {"collection": [
        {"id": 1, "title": "Archangel", "user": {"username": "burial"},
         "media": {"transcodings": [{"url": "https://stream/1", "format": {"protocol": "hls"}}]},
         "permalink_url": "https://soundcloud.com/burial/archangel",
         "artwork_url": "https://img/art.jpg", "duration": 240000}
    ]}
    sets_data = {"collection": []}

    def fake_get(path, params=None):
        if path == "/resolve":
            return user_data
        if "/tracks" in path:
            return track_data
        if "/playlists" in path:
            return sets_data
        return {}

    sc.get.side_effect = fake_get
    from soundcloud.mirror import get_profile
    result = get_profile(sc, "https://soundcloud.com/burial")
    assert result["user"]["id"] == 42
    assert len(result["tracks"]) == 1
    assert result["tracks"][0]["title"] == "Archangel"
    assert result["tracks"][0]["source"] == "sc"


def test_get_profile_single_track():
    sc = _make_sc_client()
    track_raw = {
        "kind": "track", "id": 7, "title": "Windowlicker",
        "user": {"username": "aphex twin"},
        "media": {"transcodings": [{"url": "https://stream/7", "format": {"protocol": "hls"}}]},
        "permalink_url": "https://soundcloud.com/aphex/w",
        "artwork_url": "", "duration": 360000,
    }
    sc.get.return_value = track_raw
    from soundcloud.mirror import get_profile
    result = get_profile(sc, "https://soundcloud.com/aphex/w")
    assert len(result["tracks"]) == 1
    assert result["tracks"][0]["id"] == 7
    assert result["sets"] == []
