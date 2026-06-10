"""SoundCloud API v2 client.

Injects client_id on every request. On 401, fetches a new client_id via
Selenium (from scripts/Sc2Sp_src/script_web.py) and retries once.
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


class SCClient:
    """Thin wrapper around the SoundCloud API v2."""

    def __init__(self, client_id: str, config_path: str):
        self.client_id = client_id
        self._config_path = config_path
        self._session = requests.Session()
        self._session.headers["User-Agent"] = (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120 Safari/537.36"
        )

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

    def get(self, path: str, params: dict = None) -> dict:
        """GET request with client_id injection. Retries once on 401."""
        if params is None:
            params = {}
        params["client_id"] = self.client_id
        url = f"{_BASE}{path}"

        resp = self._session.get(url, params=params, timeout=20)
        if resp.status_code == 401:
            logger.info("[SC] 401 received — refreshing client_id via Selenium")
            new_cid = fetch_client_id_via_selenium(self._config_path)
            if new_cid:
                self.client_id = new_cid
                self._persist_client_id(new_cid)
                params["client_id"] = new_cid
                resp = self._session.get(url, params=params, timeout=20)

        resp.raise_for_status()
        return resp.json()
