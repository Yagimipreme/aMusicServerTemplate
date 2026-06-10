from unittest.mock import MagicMock, patch
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


def test_expand_with_soundcloud_client_merges_sc_candidates():
    """When soundcloud_client is set, SC get_followings results are merged."""
    fake = FakeSubsonic({})
    seeds = [{"id": "a1", "name": "Burial"}]

    sc_client = MagicMock()

    # Patch get_followings and get_related
    with patch("discover.expand.get_sc_followings") as mock_followings, \
         patch("discover.expand.get_sc_related") as mock_related:
        mock_followings.return_value = [{"id": "sc1", "name": "Shackleton"}]
        mock_related.return_value = []
        result = expand_similar(fake, seeds, per_seed=20, soundcloud_client=sc_client)

    names = [r["name"] for r in result]
    assert "Shackleton" in names
