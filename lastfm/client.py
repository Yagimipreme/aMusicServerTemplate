"""Thin wrapper around the Last.fm REST API.

- All calls use format=json, api_key=<key>
- Enforces 1 req/s rate limit (token bucket, in-process, shared across all instances)
- Returns parsed JSON on success
- Raises typed exceptions on known error codes 6, 10, 11, 29
- Network timeouts raise LastFMTimeout
"""

import logging
import threading
import time

import requests

logger = logging.getLogger(__name__)

_BASE_URL = "https://ws.audioscrobbler.com/2.0/"
_TIMEOUT = 10  # seconds for HTTP calls

# ── Shared token bucket (1 req/s) ─────────────────────────────────────────────

_bucket_lock = threading.Lock()
_bucket_tokens: float = 1.0
_bucket_last: float = time.monotonic()
_BUCKET_RATE = 1.0  # tokens per second


def _acquire_token() -> None:
    """Block until a rate-limit token is available."""
    global _bucket_tokens, _bucket_last
    with _bucket_lock:
        now = time.monotonic()
        elapsed = now - _bucket_last
        _bucket_last = now
        _bucket_tokens = min(1.0, _bucket_tokens + elapsed * _BUCKET_RATE)
        if _bucket_tokens >= 1.0:
            _bucket_tokens -= 1.0
            return
        # Need to wait
        wait = (1.0 - _bucket_tokens) / _BUCKET_RATE
        _bucket_tokens = 0.0

    # Sleep outside the lock so other threads can re-enter
    time.sleep(wait)


# ── Typed exceptions ───────────────────────────────────────────────────────────

class LastFMError(Exception):
    """Base Last.fm API error."""
    def __init__(self, code: int, message: str):
        super().__init__(f"Last.fm error {code}: {message}")
        self.code = code
        self.message = message


class LastFMNotFound(LastFMError):
    """Error code 6 — artist or track not found."""


class LastFMInvalidKey(LastFMError):
    """Error code 10 — invalid API key."""


class LastFMServiceOffline(LastFMError):
    """Error code 11 — service temporarily offline."""


class LastFMRateLimitExceeded(LastFMError):
    """Error code 29 — rate limit exceeded."""


class LastFMTimeout(Exception):
    """Network timeout reaching Last.fm."""


_ERROR_MAP = {
    6: LastFMNotFound,
    10: LastFMInvalidKey,
    11: LastFMServiceOffline,
    29: LastFMRateLimitExceeded,
}


# ── Client ─────────────────────────────────────────────────────────────────────

class LastFMClient:
    """Authenticated Last.fm API client.

    Pass *session* (a requests.Session) for testability; if omitted, a fresh
    session is created per instance.
    """

    def __init__(self, api_key: str, session=None):
        self.api_key = api_key
        self._session = session or requests.Session()

    def _raw_call(self, params: dict) -> dict:
        """Make one rate-limited HTTP call and return parsed JSON."""
        _acquire_token()
        full_params = {"format": "json", "api_key": self.api_key}
        full_params.update(params)
        try:
            resp = self._session.get(_BASE_URL, params=full_params, timeout=_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.Timeout as exc:
            raise LastFMTimeout("Request timed out") from exc
        except requests.exceptions.RequestException as exc:
            raise LastFMTimeout(f"Network error: {exc}") from exc

    def call(self, method: str, **params) -> dict:
        """Call a Last.fm method with retry logic for codes 11 and 29.

        Raises typed LastFMError subclasses for known error codes.
        Raises LastFMTimeout on network failure.
        """
        all_params = {"method": method, **params}
        for attempt in range(2):
            data = self._raw_call(all_params)
            err = data.get("error")
            if err is None:
                return data

            msg = data.get("message", "")
            code = int(err)

            if code == 11:
                if attempt == 0:
                    logger.warning("Last.fm service offline (code 11), retrying in 5s")
                    time.sleep(5)
                    continue
                raise LastFMServiceOffline(code, msg)

            if code == 29:
                if attempt == 0:
                    logger.warning("Last.fm rate limit exceeded (code 29), backing off 10s")
                    time.sleep(10)
                    continue
                raise LastFMRateLimitExceeded(code, msg)

            exc_cls = _ERROR_MAP.get(code, LastFMError)
            raise exc_cls(code, msg)

        # Should not be reached, but satisfy type checker
        raise LastFMError(-1, "Unexpected retry exhaustion")
