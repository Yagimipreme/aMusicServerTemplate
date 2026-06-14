"""ListenBrainz fresh-releases client (global new-release feed, no API key)."""
import logging

import requests

logger = logging.getLogger(__name__)

_URL = "https://api.listenbrainz.org/1/explore/fresh-releases/"
_TIMEOUT = 10
_USER_AGENT = "aMusicServer/1.0 (https://github.com/Yagimipreme/aMusicServer)"


class ListenBrainzClient:
    def __init__(self, session=None):
        self._session = session or requests.Session()

    def fresh_releases(self, pivot_date: str, days: int = 7,
                       past: bool = True, future: bool = False) -> list:
        params = {
            "release_date": pivot_date,
            "days": days,
            "past": "true" if past else "false",
            "future": "true" if future else "false",
        }
        try:
            resp = self._session.get(
                _URL, params=params,
                headers={"User-Agent": _USER_AGENT}, timeout=_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.RequestException as exc:
            logger.warning("ListenBrainz fetch failed: %s", exc)
            return []
        releases = ((data or {}).get("payload") or {}).get("releases") or []
        out = []
        for r in releases:
            out.append({
                "artist_mbids": r.get("artist_mbids", []) or [],
                "release_date": r.get("release_date", "") or "",
                "release_group_mbid": r.get("release_group_mbid", "") or "",
                "release_name": r.get("release_name", "") or "",
                "primary_type": r.get("release_group_primary_type", "") or "",
                "artist_name": r.get("artist_credit_name", "") or "",
            })
        return out
