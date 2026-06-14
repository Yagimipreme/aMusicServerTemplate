"""Fetch front cover art from the Cover Art Archive."""
import logging

import requests

logger = logging.getLogger(__name__)

_BASE = "https://coverartarchive.org"
_TIMEOUT = 10
_USER_AGENT = "aMusicServer/1.0 (https://github.com/Yagimipreme/aMusicServer)"


def fetch_front(release_mbid, size="500", session=None):
    """Return (image_bytes, mime_type) for a release front cover, or None.

    size is the Cover Art Archive thumbnail suffix ("250", "500", "1200").
    Returns None for an empty mbid, a 404 (no art), or any network error.
    """
    if not release_mbid:
        return None
    sess = session or requests
    url = f"{_BASE}/release/{release_mbid}/front-{size}"
    try:
        resp = sess.get(url, headers={"User-Agent": _USER_AGENT},
                        timeout=_TIMEOUT, allow_redirects=True)
        if resp.status_code != 200:
            return None
        mime = resp.headers.get("Content-Type", "image/jpeg")
        return resp.content, mime
    except requests.exceptions.RequestException:
        logger.warning("coverart: fetch failed for %s", release_mbid, exc_info=True)
        return None
