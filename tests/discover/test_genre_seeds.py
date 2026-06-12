"""Tests for genre_seed_artists() in discover/seeds.py."""
from types import SimpleNamespace
import pytest
from discover.seeds import genre_seed_artists


def make_lastfm_client(artists_by_tag: dict):
    """Fake Last.fm client whose .call() returns topartists for given tags."""
    def call(method, **kwargs):
        if method == "tag.gettopartists":
            tag = kwargs.get("tag", "")
            names = artists_by_tag.get(tag, [])
            return {"topartists": {"artist": [{"name": n} for n in names]}}
        return {}
    return SimpleNamespace(call=call)


def test_genre_seed_artists_single_tag():
    client = make_lastfm_client({"techno": ["Artist A", "Artist B", "Artist C"]})
    result = genre_seed_artists(client, ["techno"])
    names = [a["name"] for a in result]
    assert "Artist A" in names
    assert "Artist B" in names
    assert "Artist C" in names


def test_genre_seed_artists_all_have_id_minus_one():
    client = make_lastfm_client({"techno": ["Artist A"]})
    result = genre_seed_artists(client, ["techno"])
    assert all(a["id"] == "-1" for a in result)


def test_genre_seed_artists_multi_genre_dedupes():
    """Artists appearing in multiple genre tags are deduped."""
    client = make_lastfm_client({
        "techno": ["Artist A", "Shared"],
        "ambient": ["Shared", "Artist B"],
    })
    result = genre_seed_artists(client, ["techno", "ambient"])
    names = [a["name"] for a in result]
    assert names.count("Shared") == 1
    assert "Artist A" in names
    assert "Artist B" in names


def test_genre_seed_artists_dedupes_case_insensitive():
    """Deduplication is case-insensitive."""
    client = make_lastfm_client({
        "techno": ["Aphex Twin"],
        "ambient": ["aphex twin"],
    })
    result = genre_seed_artists(client, ["techno", "ambient"])
    names = [a["name"].lower() for a in result]
    assert names.count("aphex twin") == 1


def test_genre_seed_artists_preserves_first_seen_order():
    """First-seen name is kept when duplicates across genres."""
    client = make_lastfm_client({
        "techno": ["Burial"],
        "dubstep": ["Burial"],
    })
    result = genre_seed_artists(client, ["techno", "dubstep"])
    # Should be there once
    assert len([a for a in result if a["name"].lower() == "burial"]) == 1


def test_genre_seed_artists_error_raises_nothing():
    """A tag that raises LastFMError contributes nothing and does not raise."""
    def bad_call(method, **kwargs):
        if kwargs.get("tag") == "badtag":
            raise RuntimeError("API error")
        return {"topartists": {"artist": [{"name": "Good Artist"}]}}

    client = SimpleNamespace(call=bad_call)
    result = genre_seed_artists(client, ["badtag", "ambient"])
    names = [a["name"] for a in result]
    assert "Good Artist" in names
    # No exception raised


def test_genre_seed_artists_empty_genres():
    client = make_lastfm_client({})
    result = genre_seed_artists(client, [])
    assert result == []


def test_genre_seed_artists_none_genres():
    client = make_lastfm_client({})
    result = genre_seed_artists(client, None)
    assert result == []


def test_genre_seed_artists_respects_limit_per_genre():
    """limit_per_genre is passed to the API call; fake client respects it."""
    calls = []

    def call(method, **kwargs):
        calls.append(kwargs)
        limit = kwargs.get("limit", 30)
        all_names = [f"Artist {i}" for i in range(50)]
        return {"topartists": {"artist": [{"name": n} for n in all_names[:limit]]}}

    client = SimpleNamespace(call=call)
    result = genre_seed_artists(client, ["techno"], limit_per_genre=5)
    # The limit was passed correctly to the API
    assert calls[0]["limit"] == 5
    # The result contains at most 5 artists (as many as the API was asked to return)
    assert len(result) <= 5


def test_genre_seed_artists_single_artist_dict():
    """If Last.fm returns a single artist as dict instead of list."""
    def call(method, **kwargs):
        return {"topartists": {"artist": {"name": "Solo Artist"}}}
    client = SimpleNamespace(call=call)
    result = genre_seed_artists(client, ["jazz"])
    names = [a["name"] for a in result]
    assert "Solo Artist" in names
