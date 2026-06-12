"""Tests for discover/library_pick.py — library-share track selection."""
import os
from types import SimpleNamespace
import pytest
from discover.library_pick import select_library_tracks


def make_song(id, artist, title, path, played=None):
    return {"id": id, "artist": artist, "title": title, "path": path, "played": played}


def make_genre_subsonic(songs_by_genre: dict):
    """Subsonic fake with get_songs_by_genre."""
    def get_songs_by_genre(genre, count=200):
        return songs_by_genre.get(genre, [])
    return SimpleNamespace(get_songs_by_genre=get_songs_by_genre)


def make_profile(mode="genre", genres=None, artists=None, playlist=""):
    return {
        "id": "test",
        "name": "Test Mix",
        "seeds": {
            "mode": mode,
            "genres": genres or [],
            "artists": artists or [],
            "playlist": playlist,
        }
    }


# ── genre mode ────────────────────────────────────────────────────────────────

def test_genre_mode_pulls_songs_by_genre():
    songs = [make_song("s1", "Burial", "Archangel", "/music/archangel.mp3")]
    subsonic = make_genre_subsonic({"dubstep": songs})
    profile = make_profile(mode="genre", genres=["dubstep"])
    result = select_library_tracks(subsonic, profile, exclude_basenames=set(), count=10)
    assert any(s["id"] == "s1" for s in result)


def test_genre_mode_unions_multiple_genres():
    songs_a = [make_song("s1", "Burial", "A", "/music/a.mp3")]
    songs_b = [make_song("s2", "Actress", "B", "/music/b.mp3")]
    subsonic = make_genre_subsonic({"dubstep": songs_a, "ambient": songs_b})
    profile = make_profile(mode="genre", genres=["dubstep", "ambient"])
    result = select_library_tracks(subsonic, profile, exclude_basenames=set(), count=10)
    ids = {s["id"] for s in result}
    assert "s1" in ids
    assert "s2" in ids


def test_genre_mode_dedupes_songs():
    song = make_song("s1", "Burial", "A", "/music/a.mp3")
    # Same song appears in two genres
    subsonic = make_genre_subsonic({"dubstep": [song], "ambient": [song]})
    profile = make_profile(mode="genre", genres=["dubstep", "ambient"])
    result = select_library_tracks(subsonic, profile, exclude_basenames=set(), count=10)
    assert len([s for s in result if s["id"] == "s1"]) == 1


# ── play ordering ─────────────────────────────────────────────────────────────

def test_unplayed_songs_come_before_recently_played():
    never_played = make_song("s1", "Burial", "A", "/music/a.mp3", played=None)
    played_old = make_song("s2", "Actress", "B", "/music/b.mp3", played="2020-01-01T00:00:00")
    played_recent = make_song("s3", "C", "D", "/music/d.mp3", played="2024-01-01T00:00:00")
    subsonic = make_genre_subsonic({"techno": [played_old, played_recent, never_played]})
    profile = make_profile(mode="genre", genres=["techno"])
    result = select_library_tracks(subsonic, profile, exclude_basenames=set(), count=3)
    # First result must be the unplayed song
    assert result[0]["id"] == "s1"
    # played_old should come before played_recent
    assert result[1]["id"] == "s2"
    assert result[2]["id"] == "s3"


def test_unplayed_first_ascending_date():
    """Never-played tracks precede ascending ISO date order."""
    songs = [
        make_song("s3", "X", "C", "/music/c.mp3", played="2024-06-01T00:00:00"),
        make_song("s1", "X", "A", "/music/a.mp3", played=None),
        make_song("s2", "X", "B", "/music/b.mp3", played="2020-01-01T00:00:00"),
    ]
    subsonic = make_genre_subsonic({"techno": songs})
    profile = make_profile(mode="genre", genres=["techno"])
    result = select_library_tracks(subsonic, profile, exclude_basenames=set(), count=3)
    assert result[0]["id"] == "s1"   # unplayed first
    assert result[1]["id"] == "s2"   # older play first


# ── exclusion set ─────────────────────────────────────────────────────────────

def test_basenames_in_exclusion_set_are_skipped():
    songs = [
        make_song("s1", "Burial", "A", "/music/a.mp3"),
        make_song("s2", "Actress", "B", "/music/b.mp3"),
    ]
    subsonic = make_genre_subsonic({"techno": songs})
    profile = make_profile(mode="genre", genres=["techno"])
    result = select_library_tracks(subsonic, profile, exclude_basenames={"a.mp3"}, count=10)
    ids = {s["id"] for s in result}
    assert "s1" not in ids
    assert "s2" in ids


# ── count limit ───────────────────────────────────────────────────────────────

def test_result_length_does_not_exceed_count():
    songs = [make_song(f"s{i}", "X", f"T{i}", f"/music/t{i}.mp3") for i in range(20)]
    subsonic = make_genre_subsonic({"techno": songs})
    profile = make_profile(mode="genre", genres=["techno"])
    result = select_library_tracks(subsonic, profile, exclude_basenames=set(), count=5)
    assert len(result) <= 5


def test_zero_count_returns_empty():
    subsonic = make_genre_subsonic({"techno": [make_song("s1", "X", "T", "/music/t.mp3")]})
    profile = make_profile(mode="genre", genres=["techno"])
    result = select_library_tracks(subsonic, profile, exclude_basenames=set(), count=0)
    assert result == []


# ── non-genre mode uses search_songs ─────────────────────────────────────────

def test_non_genre_mode_uses_search_songs():
    found_songs = [make_song("s1", "Aphex Twin", "Windowlicker", "/music/w.mp3")]

    def search_songs(query, count=20):
        return found_songs

    subsonic = SimpleNamespace(search_songs=search_songs)
    profile = make_profile(mode="manual", artists=["Aphex Twin"])
    result = select_library_tracks(subsonic, profile, exclude_basenames=set(), count=10)
    assert any(s["id"] == "s1" for s in result)


def test_non_genre_mode_no_search_songs_returns_empty():
    """If subsonic has no search_songs, return empty list for non-genre modes."""
    subsonic = SimpleNamespace()  # no search_songs
    profile = make_profile(mode="manual", artists=["Aphex Twin"])
    result = select_library_tracks(subsonic, profile, exclude_basenames=set(), count=10)
    assert result == []


def test_genre_mode_error_contributes_nothing():
    """A failing get_songs_by_genre call for one genre contributes nothing."""
    def get_songs_by_genre(genre, count=200):
        if genre == "badgenre":
            raise RuntimeError("API error")
        return [make_song("s1", "X", "T", "/music/t.mp3")]

    subsonic = SimpleNamespace(get_songs_by_genre=get_songs_by_genre)
    profile = make_profile(mode="genre", genres=["badgenre", "ambient"])
    result = select_library_tracks(subsonic, profile, exclude_basenames=set(), count=10)
    assert any(s["id"] == "s1" for s in result)  # ambient songs included
