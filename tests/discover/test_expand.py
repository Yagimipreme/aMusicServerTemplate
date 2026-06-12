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


def test_expand_similar_uses_seed_weight():
    """A similar artist linked to a high-weight seed should score higher."""
    import discover.expand as expand_mod

    class FakeSub:
        def get_frequent_artists(self, size):
            return []
        def get_all_artist_names(self):
            return set()

    seeds = [
        {"id": "-1", "name": "HeavySeed", "weight": 1.0},
        {"id": "-1", "name": "LightSeed", "weight": 0.1},
    ]

    original = expand_mod._expand_via_lastfm

    def fake_expand(client, artist_name):
        if artist_name == "HeavySeed":
            return [{"name": "TargetArtist", "id": "-1", "match": 0.9}]
        return [{"name": "WeakTarget", "id": "-1", "match": 0.9}]

    expand_mod._expand_via_lastfm = fake_expand
    try:
        result = expand_mod.expand_similar(FakeSub(), seeds, lastfm_client=object())
    finally:
        expand_mod._expand_via_lastfm = original

    scores = {a["name"]: a["score"] for a in result}
    # TargetArtist: 0.9×1.0 = 0.9; WeakTarget: 0.9×0.1 = 0.09
    assert scores["TargetArtist"] > scores["WeakTarget"]


def test_enrich_artist_info_drops_below_listener_floor():
    from discover.expand import enrich_artist_info

    class FakeLFM:
        def call(self, method, **kwargs):
            name = kwargs.get("artist", "")
            listeners = "200000" if name == "Burial" else "100"
            return {"artist": {"stats": {"listeners": listeners}}}

    artists = [
        {"name": "Burial", "score": 0.9},
        {"name": "TinyArtist", "score": 0.8},
    ]

    result = enrich_artist_info(FakeLFM(), artists, min_listeners=5000)
    names = [a["name"] for a in result]
    assert "Burial" in names
    assert "TinyArtist" not in names


def test_enrich_artist_info_keeps_artist_on_api_error():
    from discover.expand import enrich_artist_info

    class BrokenLFM:
        def call(self, method, **kwargs):
            raise RuntimeError("network error")

    artists = [{"name": "SomeArtist", "score": 0.5}]
    result = enrich_artist_info(BrokenLFM(), artists, min_listeners=5000)
    assert len(result) == 1


def test_enrich_artist_info_rescores_by_listeners():
    from discover.expand import enrich_artist_info
    import math

    class FakeLFM:
        def call(self, method, **kwargs):
            name = kwargs.get("artist", "")
            if method == "artist.getInfo":
                listeners = "1000000" if name == "BigArtist" else "10000"
                return {"artist": {"stats": {"listeners": listeners}}}
            # artist.getTopTracks — return empty
            return {"toptracks": {"track": []}}

    artists = [
        {"name": "BigArtist", "score": 0.5},
        {"name": "SmallArtist", "score": 0.5},
    ]
    result = enrich_artist_info(FakeLFM(), artists, min_listeners=5000)
    scores = {a["name"]: a["score"] for a in result}
    assert scores["BigArtist"] > scores["SmallArtist"]
