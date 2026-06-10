"""Spotify persisted query hashes — extracted from the JS bundle on demand.

Hashes rotate with every Spotify JS deploy. On PersistedQueryNotFound,
call refresh() which re-fetches the current bundle.
"""
import logging
import re

import requests

logger = logging.getLogger(__name__)

_hash_cache: dict = {}

_SPOTIFY_HOME = "https://open.spotify.com"
_TIMEOUT = 15
_SESS = requests.Session()
_SESS.headers["User-Agent"] = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120 Safari/537.36"
)

# Pattern: new tM.l("queryArtistOverview","query","<sha256>",null)
_HASH_RE = re.compile(
    r'new\s+\w+\.l\("(\w+)","query","([a-f0-9]{64})"',
)


def _fetch_bundle() -> str:
    """Fetch Spotify homepage and then the JS bundle. Returns bundle JS text."""
    resp = _SESS.get(_SPOTIFY_HOME, timeout=_TIMEOUT)
    resp.raise_for_status()
    html = resp.text
    bundle_match = re.search(r'src="(/cdn/build/web-player/web-player\.[^"]+\.js)"', html)
    if not bundle_match:
        raise RuntimeError("JS bundle URL not found in Spotify HTML")
    bundle_url = f"{_SPOTIFY_HOME}{bundle_match.group(1)}"
    js_resp = _SESS.get(bundle_url, timeout=_TIMEOUT)
    js_resp.raise_for_status()
    return js_resp.text


def refresh():
    """Re-fetch JS bundle and rebuild hash cache."""
    global _hash_cache
    try:
        js = _fetch_bundle()
        new_cache = {}
        for op_name, sha in _HASH_RE.findall(js):
            new_cache[op_name] = sha
        if new_cache:
            _hash_cache = new_cache
            logger.info("[SPOTIFY] Loaded %d operation hashes", len(new_cache))
        else:
            logger.warning("[SPOTIFY] No hashes found in bundle")
    except Exception as e:
        logger.warning("[SPOTIFY] Hash refresh failed: %s", e)


def get_hash(operation_name: str) -> str:
    """Return SHA256 hash for the given operation name. Calls refresh() if cache empty."""
    if not _hash_cache:
        refresh()
    return _hash_cache[operation_name]  # raises KeyError if not found after refresh
