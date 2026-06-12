from discover.seeds import collect_seeds


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
