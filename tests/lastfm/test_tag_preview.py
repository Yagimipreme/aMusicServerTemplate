"""Tests for lastfm/tag_preview.py."""
from unittest.mock import MagicMock

import pytest


def _client(responses: dict, errors: dict | None = None):
    """Mock client: responses/errors keyed by API method name."""
    client = MagicMock()

    def call(method, **params):
        if errors and method in errors:
            raise errors[method]
        return responses.get(method, {})

    client.call.side_effect = call
    return client


def test_preview_happy_path():
    from lastfm.tag_preview import get_tag_preview
    client = _client({
        "tag.getInfo": {"tag": {"name": "dub techno", "total": 51234, "reach": 9876}},
        "tag.getTopArtists": {"topartists": {"artist": [
            {"name": "Deepchord"}, {"name": "Basic Channel"}]}},
        "tag.getSimilar": {"similartags": {"tag": [{"name": "dub"}, {"name": "minimal techno"}]}},
    })
    p = get_tag_preview(client, "  dub techno ")
    assert p == {
        "tag": "dub techno",
        "taggings": 51234,
        "reach": 9876,
        "top_artists": ["Deepchord", "Basic Channel"],
        "similar": ["dub", "minimal techno"],
    }


def test_preview_unknown_tag_returns_zeros():
    from lastfm.tag_preview import get_tag_preview
    client = _client({
        "tag.getInfo": {"tag": {"name": "dub tecno", "total": 0, "reach": 0}},
        "tag.getTopArtists": {"topartists": {"artist": []}},
        "tag.getSimilar": {"similartags": {"tag": []}},
    })
    p = get_tag_preview(client, "dub tecno")
    assert p["taggings"] == 0
    assert p["top_artists"] == []
    assert p["similar"] == []


def test_preview_single_artist_dict_normalized():
    from lastfm.tag_preview import get_tag_preview
    client = _client({
        "tag.getInfo": {"tag": {"total": 5, "reach": 3}},
        "tag.getTopArtists": {"topartists": {"artist": {"name": "Only One"}}},
        "tag.getSimilar": {"similartags": {}},
    })
    p = get_tag_preview(client, "obscure")
    assert p["top_artists"] == ["Only One"]


def test_preview_partial_failure_degrades():
    from lastfm.tag_preview import get_tag_preview
    client = _client(
        {"tag.getInfo": {"tag": {"total": 100, "reach": 50}},
         "tag.getTopArtists": {"topartists": {"artist": [{"name": "A"}]}}},
        errors={"tag.getSimilar": RuntimeError("boom")},
    )
    p = get_tag_preview(client, "dub techno")
    assert p["taggings"] == 100
    assert p["top_artists"] == ["A"]
    assert p["similar"] == []


def test_preview_total_failure_raises():
    from lastfm.tag_preview import get_tag_preview
    err = RuntimeError("lastfm down")
    client = _client({}, errors={
        "tag.getInfo": err, "tag.getTopArtists": err, "tag.getSimilar": err})
    with pytest.raises(RuntimeError):
        get_tag_preview(client, "dub techno")
