from types import SimpleNamespace
from discover.seeds import collect_seeds, filter_artists_by_genre


def _client_with_weighted_tags(tag_map):
    """tag_map: {artist: [(tag, count), ...]} -> fake lastfm client."""
    def call(method, **kwargs):
        if method == "artist.getTopTags":
            name = kwargs.get("artist")
            pairs = tag_map.get(name, [])
            return {"toptags": {"tag": [{"name": t, "count": c} for t, c in pairs]}}
        return {}
    return SimpleNamespace(call=call)


def test_gate_keeps_artist_with_low_ranked_genre_tag():
    # "phonk" is 4th AND below weight 10 — old top-3/weight-10 view dropped it.
    client = _client_with_weighted_tags({
        "Underground": [("memphis", 50), ("trap", 40), ("lo-fi", 20), ("phonk", 8)]
    })
    artists = [{"id": "-1", "name": "Underground"}]
    assert len(filter_artists_by_genre(client, artists, ["phonk"])) == 1


def _client_with_tags(tag_map):
    """tag_map: {artist_name: [tag_str, ...]} -> fake lastfm client."""
    def call(method, **kwargs):
        if method == "artist.getTopTags":
            name = kwargs.get("artist")
            tags = tag_map.get(name, [])
            return {"toptags": {"tag": [{"name": t, "count": 100} for t in tags]}}
        return {}
    return SimpleNamespace(call=call)


def test_gate_keeps_exact_genre_match():
    client = _client_with_tags({"V21": ["phonk", "electronic"]})
    artists = [{"id": "-1", "name": "V21"}]
    assert filter_artists_by_genre(client, artists, ["phonk"]) == artists


def test_gate_keeps_genre_variant_substring():
    # "phonk" token is a substring of "drift phonk"
    client = _client_with_tags({"OBLXKQ": ["drift phonk", "memphis"]})
    artists = [{"id": "-1", "name": "OBLXKQ"}]
    assert len(filter_artists_by_genre(client, artists, ["phonk"])) == 1


def test_gate_drops_rap_only_artist():
    client = _client_with_tags({"SomeRapper": ["rap", "hip-hop", "trap"]})
    artists = [{"id": "-1", "name": "SomeRapper"}]
    assert filter_artists_by_genre(client, artists, ["phonk"]) == []


def test_gate_is_case_insensitive_and_dedupes_genres():
    client = _client_with_tags({"V21": ["Phonk"]})  # get_artist_tags lowercases tags
    artists = [{"id": "-1", "name": "V21"}]
    # genres list has mixed case duplicates
    assert len(filter_artists_by_genre(client, artists, ["Phonk", "phonk"])) == 1


def test_gate_drops_artist_when_tag_fetch_fails():
    def call(method, **kwargs):
        raise RuntimeError("lastfm down")
    client = SimpleNamespace(call=call)
    artists = [{"id": "-1", "name": "Whoever"}]
    assert filter_artists_by_genre(client, artists, ["phonk"]) == []


def test_gate_passthrough_when_no_genres():
    client = _client_with_tags({})
    artists = [{"id": "-1", "name": "A"}, {"id": "-1", "name": "B"}]
    assert filter_artists_by_genre(client, artists, []) == artists


def test_gate_passthrough_when_no_client():
    artists = [{"id": "-1", "name": "A"}]
    assert filter_artists_by_genre(None, artists, ["phonk"]) == artists


class FakeSubsonic:
    def __init__(self, artists):
        self._artists = artists
        self.last_size = None

    def get_frequent_artists(self, size=50):
        self.last_size = size
        return self._artists


def test_collect_seeds_returns_artists_capped_to_limit():
    fake = FakeSubsonic([
        {"id": "a1", "name": "BoC"},
        {"id": "a2", "name": "Aphex"},
        {"id": "a3", "name": "Plaid"},
    ])
    seeds = collect_seeds(fake, limit=2)
    assert seeds == [
        {"id": "a1", "name": "BoC", "weight": 0.0},
        {"id": "a2", "name": "Aphex", "weight": 0.0},
    ]


def test_collect_seeds_requests_at_least_limit_from_subsonic():
    fake = FakeSubsonic([])
    collect_seeds(fake, limit=10)
    assert fake.last_size >= 10


def test_collect_seeds_attaches_weight_from_play_count():
    """Seeds must carry a normalized weight derived from playCount."""
    import pytest
    artists = [
        {"id": "1", "name": "Burial", "play_count": 100},
        {"id": "2", "name": "Actress", "play_count": 50},
        {"id": "3", "name": "Shackleton", "play_count": 25},
    ]

    class FakeSub:
        def get_frequent_artists(self, size):
            return artists
        def get_all_artist_names(self):
            return set()

    from discover.seeds import collect_seeds
    seeds = collect_seeds(FakeSub(), limit=10)

    burial = next(s for s in seeds if s["name"] == "Burial")
    actress = next(s for s in seeds if s["name"] == "Actress")

    assert burial["weight"] == pytest.approx(1.0)
    assert actress["weight"] == pytest.approx(0.5)


def test_collect_seeds_uses_playlist_when_seed_playlist_set():
    """When seed_playlist is set, get_playlist_artists is called instead of get_frequent_artists."""
    import pytest
    playlist_songs = [
        {"id": "a1", "name": "Kobosil", "play_count": 12},
        {"id": "a2", "name": "Vatican Shadow", "play_count": 8},
    ]

    class FakeSubWithPlaylist:
        def get_frequent_artists(self, size=50):
            raise AssertionError("should not be called when seed_playlist is set")

        def get_playlist_artists(self, playlist_name):
            assert playlist_name == "Most Played"
            return playlist_songs

    seeds = collect_seeds(FakeSubWithPlaylist(), limit=10, seed_playlist="Most Played")
    assert len(seeds) == 2
    assert seeds[0]["name"] == "Kobosil"
    assert seeds[0]["weight"] == pytest.approx(1.0)
    assert seeds[1]["weight"] == pytest.approx(8 / 12)
