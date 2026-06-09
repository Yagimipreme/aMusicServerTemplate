import json
import urllib.parse
import urllib.request

_API_VERSION = "1.16.1"
_CLIENT = "amusicserver-discover"


def _default_fetch_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


class Subsonic:
    """Minimal Navidrome/Subsonic client. HTTP is injected for testability."""

    def __init__(self, host, user, password, fetch_json=None):
        self.host = host.rstrip("/")
        self.user = user
        self.password = password
        self._fetch_json = fetch_json or _default_fetch_json

    def _url(self, view: str, **params) -> str:
        base = {
            "u": self.user, "p": self.password,
            "v": _API_VERSION, "c": _CLIENT, "f": "json",
        }
        base.update({k: v for k, v in params.items() if v is not None})
        return f"{self.host}/rest/{view}?{urllib.parse.urlencode(base)}"

    def _call(self, view: str, **params) -> dict:
        data = self._fetch_json(self._url(view, **params))
        return data.get("subsonic-response", {}) or {}

    def get_frequent_artists(self, size: int = 50):
        """Most-played albums -> ordered, de-duplicated artist list."""
        sr = self._call("getAlbumList2.view", type="frequent", size=size)
        albums = (sr.get("albumList2", {}) or {}).get("album", []) or []
        out, seen = [], set()
        for alb in albums:
            aid = alb.get("artistId")
            name = alb.get("artist")
            if not name or aid in seen:
                continue
            seen.add(aid)
            out.append({"id": aid, "name": name})
        return out

    def get_artist_info2(self, artist_id: str, count: int = 20):
        """Similar artists (includes not-owned, id == '-1')."""
        sr = self._call("getArtistInfo2.view", id=artist_id,
                        count=count, includeNotPresent="true")
        sim = (sr.get("artistInfo2", {}) or {}).get("similarArtist", []) or []
        return [{"id": s.get("id"), "name": s.get("name")} for s in sim if s.get("name")]

    def song_exists(self, artist: str, title: str) -> bool:
        sr = self._call("search3.view", query=f"{artist} {title}",
                        songCount=1, albumCount=0, artistCount=0)
        songs = (sr.get("searchResult3", {}) or {}).get("song", []) or []
        return len(songs) > 0

    def start_scan(self) -> bool:
        sr = self._call("startScan.view")
        return sr.get("status") == "ok"
