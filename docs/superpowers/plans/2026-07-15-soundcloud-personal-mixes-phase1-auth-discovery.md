# SoundCloud Personal Mixes — Phase 1 (Auth + Discovery Spike) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `soundcloud/client.py` the ability to authenticate as the logged-in SC user (headless login, OAuth token capture, refresh-on-401), and ship a discovery-spike utility that dumps the real `api-v2` network traffic behind SoundCloud's personal mix pages, so the exact endpoint/JSON shape needed for Phase 2 (fetch → download → m3u) can be determined from real data instead of guesses.

**Architecture:** This plan implements Phase 1 only, per `docs/superpowers/specs/2026-07-15-soundcloud-personal-mixes-design.md`. Phase 2 (the actual mix-fetch/download/m3u/scheduler/webUI work) depends on the real endpoint shape this plan's final task uncovers by running against a live account — it cannot be planned concretely yet and will get its own plan once that data exists. This plan's last task is a manual, non-automated step: run the discovery CLI against a real SoundCloud account and inspect the dump.

**Tech Stack:** Python 3.13, Selenium 4.29 (already a dependency), Flask (existing `sWebExt/py_server/server.py`), pytest with `unittest.mock`.

## Global Constraints

- All new tests mock Selenium and network calls entirely — no live browser/login in CI (per spec's Testing section).
- Follow existing file/module conventions: deferred imports inside functions where the existing code already does this (e.g. `soundcloud.auth` imported lazily inside `soundcloud/client.py`, matching how `fetch_client_id_via_selenium` is structured today).
- Credentials (`sc_password`) use the existing `"secret"` settings-schema type, exactly like `navidrome_pass`.

---

## Task 1: SC login credential config field

**Files:**
- Modify: `config.example.json`
- Modify: `sWebExt/py_server/server.py` (SETTINGS_SCHEMA, ~line 1188-1216)
- Test: `tests/server/test_routes.py`

**Interfaces:**
- Produces: `sc_password` config key, exposed in `SETTINGS_SCHEMA` under group `"SoundCloud Mixes"`, type `"secret"`. Later tasks read this via `cfg.get("sc_password", "")`.

- [ ] **Step 1: Write the failing test**

Add to `tests/server/test_routes.py` (near `test_settings_schema_no_dead_discover_scheduler_rows`):

```python
def test_settings_schema_has_sc_password_secret_field(client, tmp_path):
    """sc_password must be exposed as a secret field under the SoundCloud Mixes group."""
    import json as _json
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(_json.dumps({}))
    with patch("sWebExt.py_server.server._CONFIG_PATH", str(cfg_file)):
        resp = client.get("/settings")
    data = _json.loads(resp.data)
    entries = {e["path"]: e for e in data["schema"]}
    assert "sc_password" in entries
    assert entries["sc_password"]["type"] == "secret"
    assert entries["sc_password"]["group"] == "SoundCloud Mixes"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/server/test_routes.py::test_settings_schema_has_sc_password_secret_field -v`
Expected: FAIL — `sc_password` not in entries (KeyError or assertion failure)

- [ ] **Step 3: Add the field to config.example.json**

In `config.example.json`, change:
```json
  "sc_username": "",
```
to:
```json
  "sc_username": "",
  "sc_password": "",
```

- [ ] **Step 4: Add the field to SETTINGS_SCHEMA**

In `sWebExt/py_server/server.py`, immediately after the `# Sources group` block (after the `spotify_playlists_dir` row, before `# Maintenance group`), add:

```python
    # SoundCloud Mixes group
    {"path": "sc_password",                  "type": "secret",    "label": "SoundCloud password",       "group": "SoundCloud Mixes"},
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/server/test_routes.py::test_settings_schema_has_sc_password_secret_field -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add config.example.json sWebExt/py_server/server.py tests/server/test_routes.py
git commit -m "feat(soundcloud): add sc_password config field for personal-mix login"
```

---

## Task 2: SCClient OAuth token support

**Files:**
- Modify: `soundcloud/client.py`
- Modify: `sWebExt/py_server/server.py` (`_get_sc_client`, ~line 1965-1976)
- Test: `tests/soundcloud/test_client.py`
- Test: `tests/server/test_routes.py`

**Interfaces:**
- Consumes: `soundcloud.auth.login_and_capture_token(username, password) -> str | None` (built in Task 3 — not called by any test in this task except via a patched reference, so Task 2 can be implemented and tested before Task 3 exists).
- Produces: `SCClient(client_id, config_path, oauth_token=None)` — when `oauth_token` is set, every request carries an `Authorization` header, and a 401 triggers re-login instead of client_id refresh. `SCClient.get()` signature unchanged.

- [ ] **Step 1: Write the failing header-injection test**

Add to `tests/soundcloud/test_client.py`:

```python
def test_oauth_token_sets_authorization_header():
    from soundcloud.client import SCClient
    with patch("soundcloud.client.requests.Session") as mock_sess_cls:
        mock_sess = MagicMock()
        mock_sess.headers = {}
        mock_sess_cls.return_value = mock_sess
        SCClient("cid", "/tmp/cfg.json", oauth_token="OAuth abc123")
    assert mock_sess.headers["Authorization"] == "OAuth abc123"


def test_no_oauth_token_leaves_authorization_header_unset():
    from soundcloud.client import SCClient
    with patch("soundcloud.client.requests.Session") as mock_sess_cls:
        mock_sess = MagicMock()
        mock_sess.headers = {}
        mock_sess_cls.return_value = mock_sess
        SCClient("cid", "/tmp/cfg.json")
    assert "Authorization" not in mock_sess.headers
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/soundcloud/test_client.py -k oauth_token_sets_authorization_header -v`
Expected: FAIL — `SCClient.__init__()` has no `oauth_token` parameter (`TypeError`)

- [ ] **Step 3: Write the failing 401-refresh test**

Add to `tests/soundcloud/test_client.py`:

```python
def test_get_401_with_oauth_token_refreshes_via_login_and_retries():
    import json, os, tempfile
    from soundcloud.client import SCClient

    cfg = {"sc_username": "user1", "sc_password": "pw1", "sc_oauth_token": "OAuth old"}
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(cfg, f)
        cfg_path = f.name

    try:
        resp_401 = MagicMock()
        resp_401.status_code = 401
        resp_401.raise_for_status.side_effect = Exception("401")

        resp_ok = MagicMock()
        resp_ok.status_code = 200
        resp_ok.json.return_value = {"id": 42}
        resp_ok.raise_for_status.return_value = None

        with patch("soundcloud.client.requests.Session") as mock_sess_cls:
            mock_sess = MagicMock()
            mock_sess.headers = {}
            mock_sess.get.side_effect = [resp_401, resp_ok]
            mock_sess_cls.return_value = mock_sess

            with patch("soundcloud.client._refresh_oauth_token", return_value="OAuth new") as mock_refresh:
                c = SCClient("cid", cfg_path, oauth_token="OAuth old")
                result = c.get("/me")

        assert result == {"id": 42}
        assert mock_sess.headers["Authorization"] == "OAuth new"
        mock_refresh.assert_called_once_with(cfg_path)
        with open(cfg_path) as f2:
            persisted = json.load(f2)
        assert persisted["sc_oauth_token"] == "OAuth new"
    finally:
        os.unlink(cfg_path)
```

- [ ] **Step 4: Run test to verify it fails**

Run: `.venv/bin/pytest tests/soundcloud/test_client.py::test_get_401_with_oauth_token_refreshes_via_login_and_retries -v`
Expected: FAIL — `soundcloud.client._refresh_oauth_token` does not exist (`AttributeError` from patch target)

- [ ] **Step 5: Write the failing `_get_sc_client` wiring test**

Add to `tests/server/test_routes.py`:

```python
def test_sc_client_factory_passes_oauth_token(tmp_path):
    import json as _json, os
    cfg = {"sc_client_id": "cid1", "sc_oauth_token": "OAuth tok1"}
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(_json.dumps(cfg))
    with patch("sWebExt.py_server.server._CONFIG_PATH", str(cfg_file)):
        with patch("soundcloud.client.SCClient") as mock_cls:
            from sWebExt.py_server import server as srv
            srv._get_sc_client()
    mock_cls.assert_called_once_with("cid1", str(cfg_file), oauth_token="OAuth tok1")
```

- [ ] **Step 6: Run test to verify it fails**

Run: `.venv/bin/pytest tests/server/test_routes.py::test_sc_client_factory_passes_oauth_token -v`
Expected: FAIL — `_get_sc_client` calls `SCClient(cid, _CONFIG_PATH)` without `oauth_token`, so `assert_called_once_with` mismatches

- [ ] **Step 7: Implement `soundcloud/client.py` changes**

Replace the whole `SCClient` class and add a module-level helper. Full new file content:

```python
"""SoundCloud API v2 client.

Injects client_id on every request. On 401, fetches a new client_id via
Selenium (from scripts/Sc2Sp_src/script_web.py) and retries once. When an
oauth_token is configured (personal-account calls), 401 instead triggers a
headless re-login via soundcloud.auth and retries with the new token.
"""
import importlib.util
import json
import logging
import os
import sys

import requests

logger = logging.getLogger(__name__)

_BASE = "https://api-v2.soundcloud.com"
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def fetch_client_id_via_selenium(config_path: str) -> str:
    """Load and call fetch_client_id_via_selenium from scripts/Sc2Sp_src/script_web.py."""
    script_fp = os.path.join(_PROJECT_ROOT, "scripts/Sc2Sp_src/script_web.py")
    if not os.path.exists(script_fp):
        logger.warning("[SC] sc2 helper not found: %s", script_fp)
        return None
    try:
        spec = importlib.util.spec_from_file_location("sc2_web_helper", script_fp)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.fetch_client_id_via_selenium()
    except Exception:
        logger.exception("[SC] fetch_client_id_via_selenium failed")
        return None


def _refresh_oauth_token(config_path: str) -> str:
    """Read sc_username/sc_password from config and re-login via soundcloud.auth."""
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        logger.exception("[SC] Failed to read config for oauth refresh")
        return None
    username = cfg.get("sc_username", "")
    password = cfg.get("sc_password", "")
    if not username or not password:
        logger.warning("[SC] sc_username/sc_password not set — cannot refresh oauth token")
        return None
    from soundcloud.auth import login_and_capture_token
    return login_and_capture_token(username, password)


class SCClient:
    """Thin wrapper around the SoundCloud API v2."""

    def __init__(self, client_id: str, config_path: str, oauth_token: str = None):
        self.client_id = client_id
        self.oauth_token = oauth_token
        self._config_path = config_path
        self._session = requests.Session()
        self._session.headers["User-Agent"] = (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120 Safari/537.36"
        )
        if oauth_token:
            self._session.headers["Authorization"] = oauth_token

    def _persist_client_id(self, new_id: str):
        try:
            cfg = {}
            if os.path.exists(self._config_path):
                with open(self._config_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            cfg["sc_client_id"] = new_id
            with open(self._config_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception:
            logger.exception("[SC] Failed to persist client_id")

    def _persist_oauth_token(self, new_token: str):
        try:
            cfg = {}
            if os.path.exists(self._config_path):
                with open(self._config_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            cfg["sc_oauth_token"] = new_token
            with open(self._config_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception:
            logger.exception("[SC] Failed to persist oauth_token")

    def get(self, path: str, params: dict = None) -> dict:
        """GET request with client_id/oauth_token injection. Retries once on 401."""
        if params is None:
            params = {}
        params["client_id"] = self.client_id
        url = f"{_BASE}{path}"

        resp = self._session.get(url, params=params, timeout=20)
        if resp.status_code == 401:
            if self.oauth_token:
                logger.info("[SC] 401 received — re-authenticating via headless login")
                new_token = _refresh_oauth_token(self._config_path)
                if new_token:
                    self.oauth_token = new_token
                    self._session.headers["Authorization"] = new_token
                    self._persist_oauth_token(new_token)
                    resp = self._session.get(url, params=params, timeout=20)
            else:
                logger.info("[SC] 401 received — refreshing client_id via Selenium")
                new_cid = fetch_client_id_via_selenium(self._config_path)
                if new_cid:
                    self.client_id = new_cid
                    self._persist_client_id(new_cid)
                    params["client_id"] = new_cid
                    resp = self._session.get(url, params=params, timeout=20)

        resp.raise_for_status()
        return resp.json()
```

- [ ] **Step 8: Implement `_get_sc_client` wiring in `server.py`**

In `sWebExt/py_server/server.py`, replace:

```python
def _get_sc_client():
    """Return SCClient if sc_client_id is configured, else None."""
    try:
        from soundcloud.client import SCClient
        cfg = _get_config()
        cid = cfg.get("sc_client_id", "")
        if not cid:
            return None
        return SCClient(cid, _CONFIG_PATH)
    except Exception:
        logger.warning("[SC] Could not build SCClient")
        return None
```

with:

```python
def _get_sc_client():
    """Return SCClient if sc_client_id is configured, else None."""
    try:
        from soundcloud.client import SCClient
        cfg = _get_config()
        cid = cfg.get("sc_client_id", "")
        if not cid:
            return None
        return SCClient(cid, _CONFIG_PATH, oauth_token=cfg.get("sc_oauth_token", ""))
    except Exception:
        logger.warning("[SC] Could not build SCClient")
        return None
```

- [ ] **Step 9: Run all three new tests to verify they pass**

Run: `.venv/bin/pytest tests/soundcloud/test_client.py tests/server/test_routes.py::test_sc_client_factory_passes_oauth_token -v`
Expected: all PASS

- [ ] **Step 10: Run the full test suite to check for regressions**

Run: `.venv/bin/pytest tests/ -q`
Expected: all tests pass (no count regression from before this task)

- [ ] **Step 11: Commit**

```bash
git add soundcloud/client.py sWebExt/py_server/server.py tests/soundcloud/test_client.py tests/server/test_routes.py
git commit -m "feat(soundcloud): SCClient OAuth token support with headless-login refresh on 401"
```

---

## Task 3: Headless login + OAuth token capture

**Files:**
- Create: `soundcloud/auth.py`
- Test: `tests/soundcloud/test_auth.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (this task makes `soundcloud.auth._refresh_oauth_token`'s target, `login_and_capture_token`, real).
- Produces:
  - `login_and_capture_token(username: str, password: str) -> str | None`
  - `_make_driver() -> WebDriver | None` (internal, patched by later tests/tasks)
  - `_perform_login(driver, username: str, password: str) -> bool` (internal)
  - `_extract_oauth_token(entries: list) -> str | None` (internal, pure function — used again in Task 4)
  - `_DEBUG_HTML_PATH` module constant (path to `logs/sc_login_debug.html`)

- [ ] **Step 1: Create the test file and write the failing pure-function tests**

Create `tests/soundcloud/test_auth.py`:

```python
"""Tests for soundcloud/auth.py — headless login + OAuth token capture."""
import json
from unittest.mock import MagicMock, patch


def test_extract_oauth_token_finds_authorization_header():
    from soundcloud.auth import _extract_oauth_token
    entries = [
        {"message": json.dumps({"message": {
            "method": "Network.requestWillBeSent",
            "params": {"request": {"url": "https://api-v2.soundcloud.com/me",
                                    "headers": {"Authorization": "OAuth abc123"}}}
        }})},
    ]
    assert _extract_oauth_token(entries) == "OAuth abc123"


def test_extract_oauth_token_returns_none_when_absent():
    from soundcloud.auth import _extract_oauth_token
    entries = [
        {"message": json.dumps({"message": {
            "method": "Network.requestWillBeSent",
            "params": {"request": {"url": "https://api-v2.soundcloud.com/me", "headers": {}}}
        }})},
    ]
    assert _extract_oauth_token(entries) is None


def test_extract_oauth_token_ignores_non_api_requests():
    from soundcloud.auth import _extract_oauth_token
    entries = [
        {"message": json.dumps({"message": {
            "method": "Network.requestWillBeSent",
            "params": {"request": {"url": "https://soundcloud.com/static/app.js",
                                    "headers": {"Authorization": "OAuth should-be-ignored"}}}
        }})},
    ]
    assert _extract_oauth_token(entries) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/soundcloud/test_auth.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'soundcloud.auth'`

- [ ] **Step 3: Write the failing `_perform_login` tests**

Add to `tests/soundcloud/test_auth.py`:

```python
def test_perform_login_success():
    from soundcloud.auth import _perform_login
    mock_driver = MagicMock()
    mock_pass_field = MagicMock()
    mock_driver.find_element.return_value = mock_pass_field
    mock_user_field = MagicMock()
    with patch("soundcloud.auth.WebDriverWait") as mock_wait_cls:
        mock_wait_cls.return_value.until.return_value = mock_user_field
        result = _perform_login(mock_driver, "user1", "pw1")
    assert result is True
    mock_user_field.send_keys.assert_called_once_with("user1")
    mock_pass_field.send_keys.assert_called_once_with("pw1")
    mock_pass_field.submit.assert_called_once()
    mock_driver.get.assert_called_once_with("https://soundcloud.com/signin")


def test_perform_login_failure_dumps_debug_html(tmp_path):
    import soundcloud.auth as auth_mod
    mock_driver = MagicMock()
    mock_driver.page_source = "<html>broken</html>"
    debug_path = tmp_path / "sc_login_debug.html"
    with patch("soundcloud.auth.WebDriverWait", side_effect=Exception("timeout")), \
         patch.object(auth_mod, "_DEBUG_HTML_PATH", str(debug_path)):
        result = auth_mod._perform_login(mock_driver, "user1", "pw1")
    assert result is False
    assert debug_path.exists()
    assert "broken" in debug_path.read_text()
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/soundcloud/test_auth.py -k perform_login -v`
Expected: FAIL — module doesn't exist yet

- [ ] **Step 5: Write the failing `login_and_capture_token` tests**

Add to `tests/soundcloud/test_auth.py`:

```python
def test_login_and_capture_token_returns_token_on_success():
    from soundcloud.auth import login_and_capture_token
    mock_driver = MagicMock()
    mock_driver.get_log.return_value = [
        {"message": json.dumps({"message": {
            "method": "Network.requestWillBeSent",
            "params": {"request": {"url": "https://api-v2.soundcloud.com/me",
                                    "headers": {"Authorization": "OAuth xyz789"}}}
        }})},
    ]
    with patch("soundcloud.auth._make_driver", return_value=mock_driver), \
         patch("soundcloud.auth._perform_login", return_value=True):
        token = login_and_capture_token("user1", "pw1")
    assert token == "OAuth xyz789"
    mock_driver.quit.assert_called_once()


def test_login_and_capture_token_returns_none_when_driver_unavailable():
    from soundcloud.auth import login_and_capture_token
    with patch("soundcloud.auth._make_driver", return_value=None):
        token = login_and_capture_token("user1", "pw1")
    assert token is None


def test_login_and_capture_token_returns_none_when_login_fails():
    from soundcloud.auth import login_and_capture_token
    mock_driver = MagicMock()
    with patch("soundcloud.auth._make_driver", return_value=mock_driver), \
         patch("soundcloud.auth._perform_login", return_value=False):
        token = login_and_capture_token("user1", "pw1")
    assert token is None
    mock_driver.quit.assert_called_once()
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/soundcloud/test_auth.py -k login_and_capture_token -v`
Expected: FAIL — module doesn't exist yet

- [ ] **Step 7: Implement `soundcloud/auth.py`**

Create `soundcloud/auth.py`:

```python
"""Headless SoundCloud login + OAuth token capture.

Reuses the same headless-Chrome / CDP-network-sniffing technique as
scripts/Sc2Sp_src/script_web.py's fetch_client_id_via_selenium — no visible
browser, no manual interaction at run time.
"""
import json
import logging
import os
import shutil
import time

logger = logging.getLogger(__name__)

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service as ChromeService
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
except ImportError:
    webdriver = None
    ChromeService = None
    ChromeOptions = None
    By = None
    WebDriverWait = None
    EC = None

try:
    from webdriver_manager.chrome import ChromeDriverManager
except ImportError:
    ChromeDriverManager = None

_SIGNIN_URL = "https://soundcloud.com/signin"
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_DEBUG_HTML_PATH = os.path.join(_PROJECT_ROOT, "logs", "sc_login_debug.html")


def _make_driver():
    """Start headless Chrome with network logging enabled. Returns driver or None."""
    if webdriver is None:
        logger.warning("[SC-AUTH] Selenium not available")
        return None

    options = ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-dev-shm-usage")
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})

    system_cd = shutil.which("chromedriver")
    if system_cd:
        service = ChromeService(system_cd)
    elif ChromeDriverManager is not None:
        service = ChromeService(ChromeDriverManager().install())
    else:
        logger.error("[SC-AUTH] No chromedriver available")
        return None

    chromium_bin = (shutil.which("chromium") or shutil.which("chromium-browser")
                     or shutil.which("google-chrome"))
    if chromium_bin:
        options.binary_location = chromium_bin

    try:
        driver = webdriver.Chrome(service=service, options=options)
    except Exception:
        logger.exception("[SC-AUTH] Failed to start ChromeDriver")
        return None

    driver.execute_cdp_cmd("Network.enable", {})
    return driver


def _dump_debug_html(driver):
    """Best-effort dump of page source for troubleshooting selector drift."""
    try:
        os.makedirs(os.path.dirname(_DEBUG_HTML_PATH), exist_ok=True)
        with open(_DEBUG_HTML_PATH, "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        logger.warning("[SC-AUTH] Dumped page source to %s for troubleshooting", _DEBUG_HTML_PATH)
    except Exception:
        logger.exception("[SC-AUTH] Failed to dump debug HTML")


def _perform_login(driver, username: str, password: str) -> bool:
    """Fill and submit the SC sign-in form. Returns True if the form was submitted.

    Selectors target the direct /signin route (name="username"/"password" fields)
    rather than the header login modal, since a direct URL navigation is more
    reliable headlessly. If SC changes this form, _dump_debug_html captures the
    page source so selectors can be corrected from real data.
    """
    driver.get(_SIGNIN_URL)
    try:
        user_field = WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.NAME, "username")))
        pass_field = driver.find_element(By.NAME, "password")
        user_field.send_keys(username)
        pass_field.send_keys(password)
        pass_field.submit()
        time.sleep(3)
        return True
    except Exception:
        logger.exception("[SC-AUTH] Login form interaction failed")
        _dump_debug_html(driver)
        return False


def _extract_oauth_token(entries: list) -> str:
    """Scan CDP performance-log entries for an Authorization header on api-v2 calls."""
    for entry in entries:
        msg = json.loads(entry.get("message", "{}")).get("message", {})
        if msg.get("method") != "Network.requestWillBeSent":
            continue
        req = msg.get("params", {}).get("request", {})
        url = req.get("url", "")
        if "api-v2.soundcloud.com" not in url:
            continue
        headers = req.get("headers", {}) or {}
        auth = headers.get("Authorization") or headers.get("authorization")
        if auth:
            return auth
    return None


def login_and_capture_token(username: str, password: str) -> str:
    """Headless-login to SoundCloud and return the OAuth token sniffed from
    subsequent api-v2 network traffic, or None on any failure."""
    driver = _make_driver()
    if driver is None:
        return None
    try:
        if not _perform_login(driver, username, password):
            return None
        driver.get("https://soundcloud.com/")
        time.sleep(3)
        try:
            entries = driver.get_log("performance")
        except Exception:
            logger.exception("[SC-AUTH] Failed to read performance logs")
            return None
        token = _extract_oauth_token(entries)
        if token is None:
            logger.warning("[SC-AUTH] Logged in but no Authorization header found in traffic")
        return token
    finally:
        try:
            driver.quit()
        except Exception:
            pass
```

- [ ] **Step 8: Run all Task 3 tests to verify they pass**

Run: `.venv/bin/pytest tests/soundcloud/test_auth.py -v`
Expected: all PASS (8 tests)

- [ ] **Step 9: Run the full test suite to check for regressions**

Run: `.venv/bin/pytest tests/ -q`
Expected: all tests pass

- [ ] **Step 10: Commit**

```bash
git add soundcloud/auth.py tests/soundcloud/test_auth.py
git commit -m "feat(soundcloud): headless login + OAuth token capture"
```

---

## Task 4: Personal-mix endpoint discovery spike

**Files:**
- Modify: `soundcloud/auth.py`
- Test: `tests/soundcloud/test_auth.py`

**Interfaces:**
- Consumes: `_make_driver()`, `_perform_login()` from Task 3.
- Produces: `discover_personal_mix_endpoints(username: str, password: str, dump_path: str, visit_urls: list = None) -> str` — always writes valid JSON to `dump_path` (even `{"entries": []}` on failure) and returns `dump_path`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/soundcloud/test_auth.py`:

```python
def test_discover_personal_mix_endpoints_writes_dump(tmp_path):
    from soundcloud.auth import discover_personal_mix_endpoints
    dump_path = str(tmp_path / "dump.json")
    mock_driver = MagicMock()
    mock_driver.get_log.return_value = [
        {"message": json.dumps({"message": {
            "method": "Network.requestWillBeSent",
            "params": {"requestId": "1",
                       "request": {"url": "https://api-v2.soundcloud.com/stream"}}
        }})},
    ]
    mock_driver.execute_cdp_cmd.return_value = {"body": '{"collection": []}'}
    with patch("soundcloud.auth._make_driver", return_value=mock_driver), \
         patch("soundcloud.auth._perform_login", return_value=True):
        result_path = discover_personal_mix_endpoints(
            "user1", "pw1", dump_path, visit_urls=["https://soundcloud.com/"])
    assert result_path == dump_path
    with open(dump_path) as f:
        dumped = json.load(f)
    assert dumped["entries"][0]["url"] == "https://api-v2.soundcloud.com/stream"
    assert "collection" in dumped["entries"][0]["body_sample"]


def test_discover_personal_mix_endpoints_dedupes_urls_across_pages(tmp_path):
    from soundcloud.auth import discover_personal_mix_endpoints
    dump_path = str(tmp_path / "dump.json")
    mock_driver = MagicMock()
    mock_driver.get_log.return_value = [
        {"message": json.dumps({"message": {
            "method": "Network.requestWillBeSent",
            "params": {"requestId": "1",
                       "request": {"url": "https://api-v2.soundcloud.com/stream"}}
        }})},
    ]
    mock_driver.execute_cdp_cmd.return_value = {"body": "{}"}
    with patch("soundcloud.auth._make_driver", return_value=mock_driver), \
         patch("soundcloud.auth._perform_login", return_value=True):
        discover_personal_mix_endpoints(
            "user1", "pw1", dump_path,
            visit_urls=["https://soundcloud.com/", "https://soundcloud.com/discover"])
    with open(dump_path) as f:
        dumped = json.load(f)
    assert len(dumped["entries"]) == 1


def test_discover_personal_mix_endpoints_writes_empty_dump_on_driver_failure(tmp_path):
    from soundcloud.auth import discover_personal_mix_endpoints
    dump_path = str(tmp_path / "dump.json")
    with patch("soundcloud.auth._make_driver", return_value=None):
        result_path = discover_personal_mix_endpoints("user1", "pw1", dump_path)
    with open(result_path) as f:
        dumped = json.load(f)
    assert dumped == {"entries": []}


def test_discover_personal_mix_endpoints_writes_empty_dump_on_login_failure(tmp_path):
    from soundcloud.auth import discover_personal_mix_endpoints
    dump_path = str(tmp_path / "dump.json")
    mock_driver = MagicMock()
    with patch("soundcloud.auth._make_driver", return_value=mock_driver), \
         patch("soundcloud.auth._perform_login", return_value=False):
        result_path = discover_personal_mix_endpoints("user1", "pw1", dump_path)
    with open(result_path) as f:
        dumped = json.load(f)
    assert dumped == {"entries": []}
    mock_driver.quit.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/soundcloud/test_auth.py -k discover_personal_mix_endpoints -v`
Expected: FAIL — `ImportError: cannot import name 'discover_personal_mix_endpoints'`

- [ ] **Step 3: Implement `discover_personal_mix_endpoints`**

Append to `soundcloud/auth.py`:

```python
def _write_dump(dump_path: str, result: dict) -> None:
    os.makedirs(os.path.dirname(dump_path), exist_ok=True)
    with open(dump_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)


def discover_personal_mix_endpoints(username: str, password: str, dump_path: str,
                                     visit_urls: list = None) -> str:
    """Log in headlessly, visit pages likely to surface personal mixes, and dump
    every distinct api-v2 request URL + a truncated response body to dump_path.

    Returns dump_path. The file is always written, even {"entries": []} on
    login/driver failure, so callers never have to special-case a missing file.
    """
    visit_urls = visit_urls or [
        "https://soundcloud.com/",
        "https://soundcloud.com/discover",
        "https://soundcloud.com/you/library/playlists",
    ]
    result = {"entries": []}
    driver = _make_driver()
    if driver is None:
        _write_dump(dump_path, result)
        return dump_path
    try:
        if not _perform_login(driver, username, password):
            _write_dump(dump_path, result)
            return dump_path

        seen_urls = set()
        for page in visit_urls:
            try:
                driver.get(page)
                time.sleep(3)
            except Exception:
                logger.exception("[SC-AUTH] Failed to visit %s", page)
                continue

            try:
                entries = driver.get_log("performance")
            except Exception:
                logger.exception("[SC-AUTH] Failed to read performance logs for %s", page)
                continue

            for entry in entries:
                msg = json.loads(entry.get("message", "{}")).get("message", {})
                if msg.get("method") != "Network.requestWillBeSent":
                    continue
                params = msg.get("params", {})
                url = params.get("request", {}).get("url", "")
                request_id = params.get("requestId")
                if "api-v2.soundcloud.com" not in url or url in seen_urls:
                    continue
                seen_urls.add(url)

                body = ""
                try:
                    body_resp = driver.execute_cdp_cmd(
                        "Network.getResponseBody", {"requestId": request_id})
                    body = (body_resp or {}).get("body", "")[:2000]
                except Exception:
                    pass  # response body may already be gone (redirects/cache) — non-fatal

                result["entries"].append({"page": page, "url": url, "body_sample": body})

        _write_dump(dump_path, result)
        return dump_path
    finally:
        try:
            driver.quit()
        except Exception:
            pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/soundcloud/test_auth.py -v`
Expected: all PASS (12 tests)

- [ ] **Step 5: Run the full test suite to check for regressions**

Run: `.venv/bin/pytest tests/ -q`
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add soundcloud/auth.py tests/soundcloud/test_auth.py
git commit -m "feat(soundcloud): personal-mix endpoint discovery spike"
```

---

## Task 5: CLI entry point + live discovery run

**Files:**
- Modify: `soundcloud/auth.py`

**Interfaces:**
- Consumes: `discover_personal_mix_endpoints()` from Task 4, `cfg["sc_username"]`/`cfg["sc_password"]` from Task 1.
- Produces: nothing consumed by later code — this is the manual research step whose output (a real `logs/sc_mix_discovery.json`) drives Phase 2's design and plan.

- [ ] **Step 1: Add the CLI entry point**

Append to `soundcloud/auth.py`:

```python
if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)
    _config_path = os.path.join(_PROJECT_ROOT, "config.json")
    with open(_config_path, "r", encoding="utf-8") as _f:
        _cfg = json.load(_f)
    _username = _cfg.get("sc_username", "")
    _password = _cfg.get("sc_password", "")
    if not _username or not _password:
        print("sc_username/sc_password not set in config.json — aborting")
        sys.exit(1)
    _dump_path = os.path.join(_PROJECT_ROOT, "logs", "sc_mix_discovery.json")
    _result_path = discover_personal_mix_endpoints(_username, _password, _dump_path)
    print(f"Discovery dump written to {_result_path}")
```

- [ ] **Step 2: Smoke-test the module still imports cleanly**

Run: `.venv/bin/python -c "import soundcloud.auth"`
Expected: no output, exit code 0 (confirms no syntax errors in the new block)

- [ ] **Step 3: Run the full test suite one more time**

Run: `.venv/bin/pytest tests/ -q`
Expected: all tests pass

- [ ] **Step 4: Commit**

```bash
git add soundcloud/auth.py
git commit -m "feat(soundcloud): CLI entry point for personal-mix discovery spike"
```

- [ ] **Step 5: Set `sc_password` in your real config**

In `config.json` (not `config.example.json`), add your real SoundCloud account password next to the existing `sc_username`:

```json
  "sc_password": "<your real SoundCloud password>",
```

- [ ] **Step 6: Run the discovery spike against your real account**

Run: `.venv/bin/python -m soundcloud.auth`

This launches headless Chrome, logs into soundcloud.com with your credentials, visits the stream/discover/library-playlists pages, and writes `logs/sc_mix_discovery.json`.

- **If login fails:** check `logs/sc_login_debug.html` (dumped page source) to see what the sign-in page actually looked like — the form selectors in `_perform_login` (`By.NAME, "username"` / `By.NAME, "password"`) may need adjusting if SoundCloud's `/signin` page has changed.
- **If login succeeds but the dump's `entries` list looks like generic feed/stream calls rather than a distinct "weekly mix" or "daily mix" playlist:** note which URLs appeared and share the dump — Phase 2 may need `visit_urls` expanded to more specific pages (e.g. a direct link to a "SoundCloud Weekly" playlist if one is visible in your account under Library).

- [ ] **Step 7: Share the dump for Phase 2 planning**

Once `logs/sc_mix_discovery.json` has real entries, share it (or its contents) so Phase 2 (fetch → download → m3u → scheduler → webUI, per the design spec's Phase 2 sections) can be brainstormed and planned against the real endpoint shape instead of assumptions.
