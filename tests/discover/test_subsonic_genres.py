"""Tests for Subsonic.get_genres() and Subsonic.get_songs_by_genre()."""
from discover.subsonic import Subsonic


def make_client(responses):
    """responses: dict mapping URL substring -> parsed json dict."""
    def fake_fetch(url):
        for needle, payload in responses.items():
            if needle in url:
                return payload
        raise AssertionError(f"unexpected url: {url}")
    return Subsonic("http://nd:4533", "user", "pw", fetch_json=fake_fetch)


# ── get_genres ────────────────────────────────────────────────────────────────

def test_get_genres_parses_list():
    payload = {"subsonic-response": {"status": "ok", "genres": {"genre": [
        {"value": "Techno", "songCount": 50, "albumCount": 5},
        {"value": "Ambient", "songCount": 20, "albumCount": 2},
    ]}}}
    c = make_client({"getGenres": payload})
    genres = c.get_genres()
    assert len(genres) == 2
    assert genres[0]["name"] == "Techno"
    assert genres[0]["songCount"] == 50
    assert genres[1]["name"] == "Ambient"


def test_get_genres_sorted_by_song_count_desc():
    payload = {"subsonic-response": {"status": "ok", "genres": {"genre": [
        {"value": "Jazz", "songCount": 5},
        {"value": "Electronic", "songCount": 100},
        {"value": "Classical", "songCount": 30},
    ]}}}
    c = make_client({"getGenres": payload})
    genres = c.get_genres()
    counts = [g["songCount"] for g in genres]
    assert counts == sorted(counts, reverse=True)


def test_get_genres_handles_name_field():
    """Some servers use 'name' instead of 'value'."""
    payload = {"subsonic-response": {"status": "ok", "genres": {"genre": [
        {"name": "Drum and Bass", "songCount": 10},
    ]}}}
    c = make_client({"getGenres": payload})
    genres = c.get_genres()
    assert len(genres) == 1
    assert genres[0]["name"] == "Drum and Bass"


def test_get_genres_handles_single_genre_as_dict():
    """When only one genre, Navidrome may return a dict instead of list."""
    payload = {"subsonic-response": {"status": "ok", "genres": {"genre": {
        "value": "House", "songCount": 40
    }}}}
    c = make_client({"getGenres": payload})
    genres = c.get_genres()
    assert len(genres) == 1
    assert genres[0]["name"] == "House"


def test_get_genres_tolerates_missing_key():
    """If genres key is missing entirely, return empty list."""
    payload = {"subsonic-response": {"status": "ok"}}
    c = make_client({"getGenres": payload})
    genres = c.get_genres()
    assert genres == []


def test_get_genres_skips_empty_names():
    payload = {"subsonic-response": {"status": "ok", "genres": {"genre": [
        {"value": "", "songCount": 10},
        {"value": "Techno", "songCount": 5},
    ]}}}
    c = make_client({"getGenres": payload})
    genres = c.get_genres()
    assert all(g["name"] for g in genres)
    assert len(genres) == 1


# ── get_songs_by_genre ────────────────────────────────────────────────────────

def test_get_songs_by_genre_parses_list():
    payload = {"subsonic-response": {"status": "ok", "songsByGenre": {"song": [
        {"id": "s1", "artist": "Burial", "title": "Archangel",
         "path": "/music/burial/archangel.mp3", "played": "2024-01-15T12:00:00"},
        {"id": "s2", "artist": "Actress", "title": "N.E.W.",
         "path": "/music/actress/new.mp3"},
    ]}}}
    c = make_client({"getSongsByGenre": payload})
    songs = c.get_songs_by_genre("dubstep", count=10)
    assert len(songs) == 2
    assert songs[0]["id"] == "s1"
    assert songs[0]["artist"] == "Burial"
    assert songs[0]["title"] == "Archangel"
    assert songs[0]["path"] == "/music/burial/archangel.mp3"
    assert songs[0]["played"] == "2024-01-15T12:00:00"
    # played may be absent → None
    assert songs[1]["played"] is None


def test_get_songs_by_genre_played_absent_is_none():
    payload = {"subsonic-response": {"status": "ok", "songsByGenre": {"song": [
        {"id": "s1", "artist": "Burial", "title": "Archangel", "path": "/music/x.mp3"},
    ]}}}
    c = make_client({"getSongsByGenre": payload})
    songs = c.get_songs_by_genre("dubstep")
    assert songs[0]["played"] is None


def test_get_songs_by_genre_single_song_as_dict():
    """Single song may be returned as dict rather than list."""
    payload = {"subsonic-response": {"status": "ok", "songsByGenre": {"song": {
        "id": "s1", "artist": "Burial", "title": "Archangel",
        "path": "/music/x.mp3",
    }}}}
    c = make_client({"getSongsByGenre": payload})
    songs = c.get_songs_by_genre("dubstep")
    assert len(songs) == 1


def test_get_songs_by_genre_missing_key_returns_empty():
    payload = {"subsonic-response": {"status": "ok"}}
    c = make_client({"getSongsByGenre": payload})
    songs = c.get_songs_by_genre("unknown_genre")
    assert songs == []
