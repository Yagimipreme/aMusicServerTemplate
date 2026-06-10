"""Tests for spotify/hashes.py — JS bundle regex extraction."""
from unittest.mock import MagicMock, patch
import pytest


_FAKE_JS = '''
(function(){
    new tM.l("queryArtistOverview","query","abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",null)
    new tM.l("fetchPlaylist","query","fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210",null)
})();
'''


def test_get_hash_extracts_from_bundle():
    import spotify.hashes as h
    h._hash_cache = {}

    with patch("spotify.hashes._fetch_bundle", return_value=_FAKE_JS):
        h.refresh()

    assert h.get_hash("queryArtistOverview") == "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"
    assert h.get_hash("fetchPlaylist") == "fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210"


def test_get_hash_raises_keyerror_for_unknown_op():
    import spotify.hashes as h
    h._hash_cache = {"queryArtistOverview": "abc123" * 10 + "ab"}
    with pytest.raises(KeyError):
        h.get_hash("unknownOperation")


def test_refresh_clears_and_refills_cache():
    import spotify.hashes as h
    h._hash_cache = {"stale_op": "deadbeef" * 8}
    with patch("spotify.hashes._fetch_bundle", return_value=_FAKE_JS):
        h.refresh()
    assert "stale_op" not in h._hash_cache
    assert "queryArtistOverview" in h._hash_cache
