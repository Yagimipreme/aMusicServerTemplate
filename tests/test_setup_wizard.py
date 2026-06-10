"""Tests for setup.py standalone functions."""
import json, os, sys, types, unittest
from unittest.mock import patch, MagicMock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import importlib.util
spec = importlib.util.spec_from_file_location("setup", os.path.join(_ROOT, "setup.py"))
setup_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup_mod)


class TestNavidromeRescan(unittest.TestCase):
    @patch("urllib.request.urlopen")
    def test_polls_until_done(self, mock_open):
        scanning_resp = json.dumps({"subsonic-response": {"scanStatus": {"scanning": True, "count": 50}}}).encode()
        done_resp = json.dumps({"subsonic-response": {"scanStatus": {"scanning": False, "count": 100}}}).encode()
        ctx1 = MagicMock(); ctx1.__enter__ = lambda s: s; ctx1.__exit__ = MagicMock(return_value=False); ctx1.read.return_value = scanning_resp
        ctx2 = MagicMock(); ctx2.__enter__ = lambda s: s; ctx2.__exit__ = MagicMock(return_value=False); ctx2.read.return_value = done_resp
        mock_open.side_effect = [MagicMock(), ctx1, ctx2]
        with patch("time.sleep"):
            result = setup_mod.trigger_navidrome_rescan("http://localhost:4533", "admin", "pass", poll_interval=0)
        self.assertEqual(result, 100)


class TestSetupAutostart(unittest.TestCase):
    def test_linux_writes_unit_file(self):
        import tempfile, platform
        with tempfile.TemporaryDirectory() as tmp:
            unit_path = os.path.join(tmp, "amusicserver.service")
            with patch("platform.system", return_value="Linux"), \
                 patch("os.path.expanduser", return_value=tmp), \
                 patch("os.makedirs"), \
                 patch("subprocess.run", side_effect=Exception("no systemctl")):
                # monkeypatch open to write to tmp
                real_open = open
                def _fake_open(path, *a, **kw):
                    if "amusicserver.service" in str(path):
                        return real_open(unit_path, *a, **kw)
                    return real_open(path, *a, **kw)
                with patch("builtins.open", side_effect=_fake_open):
                    setup_mod._setup_autostart()
            # Just verify it didn't raise


class TestGetCsvArtists(unittest.TestCase):
    def test_reads_exportify_csv(self):
        import tempfile, csv
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = os.path.join(tmp, "Chill.csv")
            with open(csv_path, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=["Track Name", "Artist Name(s)"])
                w.writeheader()
                w.writerow({"Track Name": "Windowlicker", "Artist Name(s)": "Aphex Twin"})
                w.writerow({"Track Name": "Archangel", "Artist Name(s)": "Burial"})
            from discover.seeds import get_csv_artists
            artists = get_csv_artists(tmp)
        self.assertIn("Aphex Twin", artists)
        self.assertIn("Burial", artists)

    def test_deduplicates(self):
        import tempfile, csv
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("A.csv", "B.csv"):
                with open(os.path.join(tmp, name), "w", newline="") as f:
                    w = csv.DictWriter(f, fieldnames=["Track Name", "Artist Name"])
                    w.writeheader()
                    w.writerow({"Track Name": "Song", "Artist Name": "Burial"})
            from discover.seeds import get_csv_artists
            artists = get_csv_artists(tmp)
        self.assertEqual(artists.count("Burial"), 1)
