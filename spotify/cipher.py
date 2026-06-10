"""Spotify TOTP cipher — fetches cipher bytes from JS bundle or fallback repo.

BEST-EFFORT: This depends on Spotify's undocumented internal JS. The cipher
rotates with every JS deploy. On complete failure, raises CipherFetchError
so the server can disable Spotify routes gracefully rather than crashing.
"""
import hashlib
import hmac
import logging
import re
import struct

import requests

logger = logging.getLogger(__name__)

_CIPHER_CACHE = None  # (cipher_bytes, version)
# Module-level alias used by tests
_cipher_cache = None

_SPOTIFY_HOME = "https://open.spotify.com"
_FALLBACK_URL = "https://raw.githubusercontent.com/xyloflake/spot-secrets-go/main/secrets/secretDict.json"
_TIMEOUT = 15

_SESS = requests.Session()
_SESS.headers["User-Agent"] = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120 Safari/537.36"
)


class CipherFetchError(Exception):
    """Raised when both primary and fallback cipher sources fail."""


def _fetch_from_bundle():
    """Fetch Spotify homepage -> find JS bundle -> extract cipher + version."""
    resp = _SESS.get(_SPOTIFY_HOME, timeout=_TIMEOUT)
    resp.raise_for_status()
    html = resp.text

    bundle_match = re.search(r'src="(/cdn/build/web-player/web-player\.[^"]+\.js)"', html)
    if not bundle_match:
        raise CipherFetchError("JS bundle URL not found in Spotify HTML")

    bundle_url = f"{_SPOTIFY_HOME}{bundle_match.group(1)}"
    js_resp = _SESS.get(bundle_url, timeout=_TIMEOUT)
    js_resp.raise_for_status()
    js = js_resp.text

    # Find cipher array: e.g. [81,53,45,41,...] near "totpVer" or similar
    arr_match = re.search(r'\[(\d+(?:,\d+){10,})\]', js)
    if not arr_match:
        raise CipherFetchError("Cipher array not found in JS bundle")
    cipher_ints = [int(x) for x in arr_match.group(1).split(",")]

    # Find version
    ver_match = re.search(r'totpVer["\s:]+(\d+)', js)
    version = int(ver_match.group(1)) if ver_match else 61

    return bytes(cipher_ints), version


def _fetch_from_fallback():
    """Fetch from community-maintained fallback repo."""
    resp = _SESS.get(_FALLBACK_URL, timeout=_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    cipher_ints = data["secret"]
    version = data.get("version", 61)
    return bytes(cipher_ints), version


def get_cipher():
    """Return (cipher_bytes, version). Uses in-memory cache; call refresh() to re-fetch."""
    global _CIPHER_CACHE, _cipher_cache
    if _CIPHER_CACHE is not None:
        return _CIPHER_CACHE
    if _cipher_cache is not None:
        return _cipher_cache
    return refresh()


def refresh():
    """Re-fetch cipher from primary then fallback. Raises CipherFetchError if both fail."""
    global _CIPHER_CACHE, _cipher_cache
    try:
        result = _fetch_from_bundle()
        _CIPHER_CACHE = result
        _cipher_cache = result
        logger.info("[SPOTIFY] Cipher loaded from JS bundle, version=%d", result[1])
        return result
    except Exception as e:
        logger.warning("[SPOTIFY] Primary cipher fetch failed: %s — trying fallback", e)

    try:
        result = _fetch_from_fallback()
        _CIPHER_CACHE = result
        _cipher_cache = result
        logger.info("[SPOTIFY] Cipher loaded from fallback, version=%d", result[1])
        return result
    except Exception as e:
        logger.error("[SPOTIFY] Fallback cipher fetch also failed: %s", e)
        raise CipherFetchError(f"Both cipher sources failed: {e}") from e


def compute_totp(cipher_bytes: bytes, server_time: int, version: int = 61):
    """Compute RFC 6238 6-digit TOTP using Spotify's XOR-transformed cipher.

    Steps:
    1. XOR each byte with (index % 33 + 9)
    2. Join transformed bytes as decimal string
    3. UTF-8-encode -> hex-encode -> use raw hex bytes as HMAC-SHA1 key
    4. counter = floor(server_time / 30)
    5. Standard RFC 6238 truncation -> 6-digit code
    """
    transformed = [b ^ (i % 33 + 9) for i, b in enumerate(cipher_bytes)]
    key_str = "".join(str(b) for b in transformed)
    key_bytes = key_str.encode("utf-8").hex().encode("utf-8")

    counter = server_time // 30
    counter_bytes = struct.pack(">Q", counter)

    digest = hmac.new(key_bytes, counter_bytes, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    truncated = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    otp = truncated % 1_000_000

    return str(otp).zfill(6), version
