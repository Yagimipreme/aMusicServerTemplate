from discover.expand import expand_similar


class FakeSubsonic:
    def __init__(self, mapping):
        self._mapping = mapping  # artist_id -> [similar artist dicts]

    def get_artist_info2(self, artist_id, count=20):
        return self._mapping.get(artist_id, [])


def test_expand_keeps_only_not_owned_and_scores_by_overlap():
    fake = FakeSubsonic({
        "a1": [{"id": "-1", "name": "Zmajor"}, {"id": "b1", "name": "Owned"}],
        "a2": [{"id": "-1", "name": "Zmajor"}, {"id": "-1", "name": "Rushex"}],
    })
    seeds = [{"id": "a1", "name": "BoC"}, {"id": "a2", "name": "Aphex"}]
    result = expand_similar(fake, seeds, per_seed=20)
    # Owned (id != -1) dropped; Zmajor seen twice -> top score.
    names = [r["name"] for r in result]
    assert "Owned" not in names
    assert result[0] == {"name": "Zmajor", "score": 2}
    assert {"name": "Rushex", "score": 1} in result


def test_expand_excludes_artists_already_seeds():
    fake = FakeSubsonic({"a1": [{"id": "-1", "name": "Aphex"}]})
    seeds = [{"id": "a1", "name": "BoC"}, {"id": "a2", "name": "Aphex"}]
    result = expand_similar(fake, seeds, per_seed=20)
    assert all(r["name"] != "Aphex" for r in result)
