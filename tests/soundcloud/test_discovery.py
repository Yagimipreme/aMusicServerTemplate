"""Tests for soundcloud/discovery.py."""
from unittest.mock import MagicMock


def _make_sc_client():
    client = MagicMock()
    client.client_id = "testcid"
    return client


def _track_raw(id_, title, user="burial"):
    return {
        "id": id_, "title": title,
        "user": {"username": user},
        "media": {"transcodings": [{"url": f"https://s/{id_}", "format": {"protocol": "hls"}}]},
        "permalink_url": f"https://soundcloud.com/{user}/{id_}",
        "artwork_url": "", "duration": 120000,
    }


def test_get_related_returns_tracks():
    sc = _make_sc_client()
    sc.get.return_value = {"collection": [_track_raw(1, "Track A"), _track_raw(2, "Track B")]}
    from soundcloud.discovery import get_related
    tracks = get_related(sc, track_id=99, limit=20)
    sc.get.assert_called_once_with("/tracks/99/related", params={"limit": 20})
    assert len(tracks) == 2
    assert tracks[0]["title"] == "Track A"


def test_to_candidates_mapping():
    tracks = [
        {"id": 1, "title": "Archangel", "artist": "Burial",
         "stream_url": "https://s/1", "permalink_url": "https://soundcloud.com/burial/arch",
         "artwork_url": "", "duration_ms": 240000, "source": "sc"},
    ]
    from soundcloud.discovery import to_candidates
    candidates = to_candidates(tracks)
    assert len(candidates) == 1
    c = candidates[0]
    assert c["artist"] == "Burial"
    assert c["title"] == "Archangel"
    assert c["source"] == "soundcloud"
    assert c["download_ref"] == "https://soundcloud.com/burial/arch"
    assert c["preview_ref"] == "https://s/1"


def test_get_followings_returns_users():
    sc = _make_sc_client()
    sc.get.return_value = {"collection": [
        {"id": 10, "username": "shackleton", "full_name": "Shackleton",
         "avatar_url": "", "track_count": 5, "followers_count": 100}
    ]}
    from soundcloud.discovery import get_followings
    users = get_followings(sc, user_id=42, limit=100)
    sc.get.assert_called_once_with("/users/42/followings", params={"limit": 100})
    assert users[0]["username"] == "shackleton"
