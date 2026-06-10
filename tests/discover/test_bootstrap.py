"""Tests for bootstrap seeds and lastfm_is_ready gate."""
import os, sys, json, unittest
from unittest.mock import MagicMock, patch

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)

from discover.seeds import get_bootstrap_seeds, get_csv_artists, get_library_artist_frequency
from discover.engine import lastfm_is_ready


class TestGetBootstrapSeeds(unittest.TestCase):
    def _make_subsonic(self, artists):
        sub = MagicMock()
        sub.get_frequent_artists.return_value = [{"name": a, "id": f"id_{a}"} for a in artists]
        return sub

    def _names(self, seeds):
        return [s["name"] for s in seeds]

    def test_manual_seeds_first(self):
        cfg = {"discover": {"manual_seeds": ["Aphex Twin", "Burial"]}}
        sub = self._make_subsonic(["Boards of Canada"])
        seeds = get_bootstrap_seeds(cfg, sub)
        self.assertEqual(self._names(seeds)[0], "Aphex Twin")
        self.assertEqual(self._names(seeds)[1], "Burial")

    def test_deduplicates(self):
        cfg = {"discover": {"manual_seeds": ["Burial"]}}
        sub = self._make_subsonic(["Burial", "Aphex Twin"])
        seeds = get_bootstrap_seeds(cfg, sub)
        self.assertEqual(self._names(seeds).count("Burial"), 1)

    def test_caps_at_20(self):
        cfg = {"discover": {"manual_seeds": [f"Artist {i}" for i in range(30)]}}
        sub = self._make_subsonic([])
        seeds = get_bootstrap_seeds(cfg, sub)
        self.assertLessEqual(len(seeds), 20)


class TestLastfmIsReady(unittest.TestCase):
    def _make_client(self, total_scrobbles, num_artists):
        client = MagicMock()
        client.call.side_effect = lambda method, **kwargs: {
            "user.getRecentTracks": {"recenttracks": {"@attr": {"total": str(total_scrobbles)}}},
            "user.getTopArtists": {"topartists": {"artist": [{"name": f"A{i}"} for i in range(num_artists)]}},
        }[method]
        return client

    def test_ready_when_thresholds_met(self):
        client = self._make_client(200, 20)
        cfg = {"discover": {"lastfm_readiness": {"min_scrobbles": 100, "min_unique_artists": 15}}}
        with patch("builtins.open", side_effect=FileNotFoundError), \
             patch("json.load", side_effect=FileNotFoundError), \
             patch("json.dump"):
            result = lastfm_is_ready(client, "testuser", cfg)
        self.assertTrue(result)

    def test_not_ready_below_scrobbles(self):
        client = self._make_client(50, 20)
        cfg = {"discover": {"lastfm_readiness": {"min_scrobbles": 100, "min_unique_artists": 15}}}
        with patch("builtins.open", side_effect=FileNotFoundError):
            result = lastfm_is_ready(client, "testuser", cfg)
        self.assertFalse(result)

    def test_not_ready_below_artists(self):
        client = self._make_client(200, 5)
        cfg = {"discover": {"lastfm_readiness": {"min_scrobbles": 100, "min_unique_artists": 15}}}
        with patch("builtins.open", side_effect=FileNotFoundError):
            result = lastfm_is_ready(client, "testuser", cfg)
        self.assertFalse(result)

    def test_returns_false_on_error(self):
        client = MagicMock()
        client.call.side_effect = Exception("network error")
        cfg = {}
        with patch("builtins.open", side_effect=FileNotFoundError):
            result = lastfm_is_ready(client, "testuser", cfg)
        self.assertFalse(result)
