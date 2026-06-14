"""MusicBrainz JSON API client.

No API key. MusicBrainz requires <=1 req/s and a descriptive User-Agent.
Mirrors lastfm/client.py structure (instance-level rate limiter + typed errors).
"""
import logging
import threading
import time

import requests

logger = logging.getLogger(__name__)

_BASE = "https://musicbrainz.org/ws/2"
_TIMEOUT = 10
_USER_AGENT = "aMusicServer/1.0 (https://github.com/Yagimipreme/aMusicServer)"


class MBError(Exception):
    pass


class MBTimeout(MBError):
    pass


class MusicBrainzClient:
    def __init__(self, session=None, min_interval: float = 1.0):
        self._session = session or requests.Session()
        self._min_interval = min_interval
        self._last = 0.0
        self._lock = threading.Lock()

    def _throttle(self):
        if self._min_interval <= 0:
            return
        with self._lock:
            wait = self._min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()

    def _get(self, path: str, params: dict) -> dict:
        self._throttle()
        full = {"fmt": "json", **params}
        try:
            resp = self._session.get(
                f"{_BASE}/{path}", params=full,
                headers={"User-Agent": _USER_AGENT}, timeout=_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.Timeout as exc:
            raise MBTimeout("MusicBrainz timed out") from exc
        except requests.exceptions.RequestException as exc:
            raise MBTimeout(f"MusicBrainz network error: {exc}") from exc

    def search_artist(self, name: str, limit: int = 5) -> list:
        data = self._get("artist", {"query": f'artist:"{name}"', "limit": limit})
        out = []
        for a in data.get("artists", []) or []:
            out.append({
                "mbid": a.get("id", ""),
                "name": a.get("name", ""),
                "disambiguation": a.get("disambiguation", "") or "",
                "score": a.get("score", 0),
            })
        return out

    def get_release_groups(self, artist_mbid: str, limit: int = 100) -> list:
        data = self._get("release-group",
                         {"artist": artist_mbid, "limit": limit})
        out = []
        for rg in data.get("release-groups", []) or []:
            out.append({
                "rg_mbid": rg.get("id", ""),
                "title": rg.get("title", ""),
                "first_release_date": rg.get("first-release-date", "") or "",
                "primary_type": rg.get("primary-type", "") or "",
            })
        return out

    def get_release_tracks(self, rg_mbid: str) -> list:
        data = self._get("release",
                         {"release-group": rg_mbid, "inc": "recordings", "limit": 1})
        releases = data.get("releases", []) or []
        if not releases:
            return []
        titles = []
        for medium in releases[0].get("media", []) or []:
            for track in medium.get("tracks", []) or []:
                if track.get("title"):
                    titles.append(track["title"])
        return titles
