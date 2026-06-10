"""Tests for soundcloud/search.py."""
from unittest.mock import MagicMock


def _make_sc_client():
    client = MagicMock()
    client.client_id = "testcid"
    return client


def test_search_users_returns_list():
    sc = _make_sc_client()
    sc.get.return_value = {"collection": [
        {"id": 1, "username": "burial", "full_name": "Burial",
         "avatar_url": "https://img/a.jpg", "track_count": 10, "followers_count": 500}
    ]}
    from soundcloud.search import search_users
    users = search_users(sc, "burial", limit=10)
    sc.get.assert_called_once_with("/search/users", params={"q": "burial", "limit": 10})
    assert len(users) == 1
    assert users[0]["username"] == "burial"
    assert users[0]["id"] == 1


def test_search_tracks_returns_list():
    sc = _make_sc_client()
    sc.get.return_value = {"collection": [
        {"id": 5, "title": "Archangel",
         "user": {"username": "burial"},
         "media": {"transcodings": [{"url": "https://s/5", "format": {"protocol": "hls"}}]},
         "permalink_url": "https://soundcloud.com/burial/arch",
         "artwork_url": "https://img/5.jpg", "duration": 240000}
    ]}
    from soundcloud.search import search_tracks
    tracks = search_tracks(sc, "archangel", limit=20)
    sc.get.assert_called_once_with("/search/tracks", params={"q": "archangel", "limit": 20})
    assert len(tracks) == 1
    assert tracks[0]["title"] == "Archangel"
    assert tracks[0]["source"] == "sc"


def test_search_users_empty_result():
    sc = _make_sc_client()
    sc.get.return_value = {"collection": []}
    from soundcloud.search import search_users
    assert search_users(sc, "nobody") == []
