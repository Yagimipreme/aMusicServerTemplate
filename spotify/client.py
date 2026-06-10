"""Spotify internal API client — TOTP-based anonymous token, three-layer refresh cascade.

BEST-EFFORT: Depends on Spotify's undocumented internal API. This module is
clearly labelled; at personal/self-hosted scale enforcement has not occurred.
On any unhandled failure, returns empty results rather than crashing the server.
"""
import logging
import time

import requests

from spotify import cipher, hashes

logger = logging.getLogger(__name__)

_GRAPHQL_URL = "https://api-partner.spotify.com/pathfinder/v1/query"
_SERVER_TIME_URL = "https://open.spotify.com/api/server-time"
_TOKEN_URL = "https://open.spotify.com/api/token"
_TOKEN_SAFETY_MARGIN = 60  # re-mint 60s before actual expiry
_TIMEOUT = 15

_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"


class SpotifyClient:
    """Anonymous Spotify internal API client with auto-refreshing token + hashes."""

    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": _UA, "Accept": "application/json"})
        self._token = None
        self._token_expiry = 0.0
        # Eagerly fetch cipher on construction — raises CipherFetchError if both sources fail
        cipher.get_cipher()

    def _mint_token(self):
        """Fetch a fresh anonymous bearer token using TOTP."""
        ts_resp = self._session.get(_SERVER_TIME_URL, timeout=_TIMEOUT)
        ts_resp.raise_for_status()
        server_time = ts_resp.json()["serverTime"]

        cipher_bytes, version = cipher.get_cipher()
        totp, ver = cipher.compute_totp(cipher_bytes, server_time, version)

        tok_resp = self._session.get(
            _TOKEN_URL,
            params={"reason": "init", "productType": "web-player",
                    "totp": totp, "totpVer": ver, "ts": server_time},
            timeout=_TIMEOUT,
        )
        tok_resp.raise_for_status()
        data = tok_resp.json()
        self._token = data["accessToken"]
        expiry_ms = data.get("accessTokenExpirationTimestampMs", 0)
        self._token_expiry = expiry_ms / 1000.0 - _TOKEN_SAFETY_MARGIN
        logger.debug("[SPOTIFY] Token minted, expires ~%ds from now",
                     int(self._token_expiry - time.time()))

    def _ensure_token(self):
        if not self._token or time.time() >= self._token_expiry:
            self._mint_token()

    def graphql(self, operation_name: str, variables: dict) -> dict:
        """Execute a Spotify GraphQL persisted query with the three-layer refresh cascade."""
        self._ensure_token()

        def _do_call():
            op_hash = hashes.get_hash(operation_name)
            payload = {
                "operationName": operation_name,
                "variables": variables,
                "extensions": {"persistedQuery": {"version": 1, "sha256Hash": op_hash}},
            }
            return self._session.post(
                _GRAPHQL_URL,
                json=payload,
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=_TIMEOUT,
            )

        resp = _do_call()

        # Layer 1: 401 -> re-mint token -> retry once
        if resp.status_code == 401:
            logger.info("[SPOTIFY] 401 — re-minting token")
            self._mint_token()
            resp = _do_call()

        # Layer 2: PersistedQueryNotFound -> refresh hashes + re-mint -> retry once
        if resp.status_code == 200:
            data = resp.json()
            errors = data.get("errors") or []
            if any("PersistedQueryNotFound" in (e.get("message") or "") for e in errors):
                logger.info("[SPOTIFY] PersistedQueryNotFound — refreshing hashes + token")
                hashes.refresh()
                self._mint_token()
                resp = _do_call()

        # Layer 3: 429 -> sleep 30s -> retry once
        if resp.status_code == 429:
            logger.warning("[SPOTIFY] 429 rate limit — backing off 30s")
            time.sleep(30)
            resp = _do_call()

        return resp.json()


def _url_to_uri(url_or_uri: str) -> str:
    """Convert open.spotify.com URL to spotify:type:id URI, or pass through."""
    if url_or_uri.startswith("spotify:"):
        return url_or_uri
    import re
    m = re.search(r"open\.spotify\.com/(artist|playlist|track|album)/([A-Za-z0-9]+)", url_or_uri)
    if m:
        return f"spotify:{m.group(1)}:{m.group(2)}"
    return url_or_uri
