"""The Selenium client_id scrape must not leak Chromium profile dirs.

Chromedriver's default profile lives in /tmp/org.chromium.Chromium.scoped_dir.*
and is only removed on a clean driver.quit(). A killed process (daemon thread at
interpreter exit, SIGKILL) leaks it. We own the profile dir instead: removed in
`finally`, and stale ones from killed runs are swept on the next scrape.
"""
import importlib.util
import os
import time
from unittest.mock import MagicMock

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SCRIPT = os.path.join(_ROOT, "scripts", "Sc2Sp_src", "script_web.py")


@pytest.fixture
def script_web(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("sc2_web_profile_test", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "_SELENIUM_PROFILE_ROOT", str(tmp_path / "profiles"))
    monkeypatch.setattr(mod.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(mod, "ChromeService", MagicMock())
    return mod


def _fake_webdriver(seen_args):
    driver = MagicMock()
    driver.get_log.return_value = []

    def chrome(service, options):
        seen_args.extend(options.arguments)
        return driver

    wd = MagicMock()
    wd.Chrome.side_effect = chrome
    return wd, driver


def test_uses_owned_profile_and_removes_it(script_web, monkeypatch):
    seen = []
    wd, driver = _fake_webdriver(seen)
    monkeypatch.setattr(script_web, "webdriver", wd)

    script_web.fetch_client_id_via_selenium()

    udd = [a.split("=", 1)[1] for a in seen if a.startswith("--user-data-dir=")]
    assert len(udd) == 1
    assert udd[0].startswith(script_web._SELENIUM_PROFILE_ROOT)
    assert not os.path.exists(udd[0])
    driver.quit.assert_called_once()


def test_profile_removed_when_driver_fails_to_start(script_web, monkeypatch):
    wd = MagicMock()
    wd.Chrome.side_effect = RuntimeError("boom")
    monkeypatch.setattr(script_web, "webdriver", wd)

    assert script_web.fetch_client_id_via_selenium() is None
    root = script_web._SELENIUM_PROFILE_ROOT
    assert not os.path.exists(root) or os.listdir(root) == []


def test_sweeps_stale_profiles_from_killed_runs(script_web, monkeypatch):
    root = script_web._SELENIUM_PROFILE_ROOT
    os.makedirs(root)
    stale = os.path.join(root, "profile-stale")
    fresh = os.path.join(root, "profile-fresh")
    os.makedirs(stale)
    os.makedirs(fresh)
    old = time.time() - 2 * 3600
    os.utime(stale, (old, old))

    seen = []
    wd, _ = _fake_webdriver(seen)
    monkeypatch.setattr(script_web, "webdriver", wd)
    script_web.fetch_client_id_via_selenium()

    assert not os.path.exists(stale)
    assert os.path.exists(fresh)
