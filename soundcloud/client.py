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
