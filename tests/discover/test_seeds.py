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
    assert seeds == [{"id": "a1", "name": "BoC"}, {"id": "a2", "name": "Aphex"}]


def test_collect_seeds_requests_at_least_limit_from_subsonic():
    fake = FakeSubsonic([])
    collect_seeds(fake, limit=10)
    assert fake.last_size >= 10
