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
        """Most-played albums -> ordered, de-duplicated artist list with summed play counts."""
        sr = self._call("getAlbumList2.view", type="frequent", size=size)
        albums = (sr.get("albumList2", {}) or {}).get("album", []) or []
        counts: dict = {}   # aid -> {"id", "name", "play_count"}
        order: list = []    # first-seen insertion order
        for alb in albums:
            aid = alb.get("artistId")
            name = alb.get("artist")
            if not name or not aid:
                continue
            pc = int(alb.get("playCount") or 0)
            if aid not in counts:
                counts[aid] = {"id": aid, "name": name, "play_count": 0}
                order.append(aid)
            counts[aid]["play_count"] += pc
        return [counts[aid] for aid in order]

    def find_artist_id(self, name: str) -> str | None:
        """Search Navidrome for an artist by name and return their ID, or None."""
        try:
            sr = self._call("search3.view", query=name, songCount=0, albumCount=0, artistCount=5)
            hits = (sr.get("searchResult3", {}) or {}).get("artist", []) or []
            name_cf = name.casefold()
            for hit in hits:
                if (hit.get("name") or "").casefold() == name_cf:
                    return hit.get("id")
            # Accept partial match as fallback (name contains the query as prefix)
            for hit in hits:
                if (hit.get("name") or "").casefold().startswith(name_cf):
                    return hit.get("id")
        except Exception:
            pass
        return None

    def get_artist_info2(self, artist_id: str, count: int = 20):
        """Similar artists (includes not-owned, id == '-1')."""
        sr = self._call("getArtistInfo2.view", id=artist_id,
                        count=count, includeNotPresent="true")
        sim = (sr.get("artistInfo2", {}) or {}).get("similarArtist", []) or []
        return [{"id": s.get("id"), "name": s.get("name")} for s in sim if s.get("name")]

    def get_all_artist_names(self) -> set:
        """Return a casefold set of all artist names in the Navidrome library."""
        try:
            sr = self._call("getArtists.view")
            index = (sr.get("artists", {}) or {}).get("index", []) or []
            if isinstance(index, dict):
                index = [index]
            names = set()
            for bucket in index:
                for artist in (bucket.get("artist", []) or []):
                    name = (artist.get("name") or "").strip()
                    if name:
                        names.add(name.casefold())
            return names
        except Exception:
            return set()

    def song_exists(self, artist: str, title: str) -> bool:
        # search3.view is an approximate full-text match (may have false positives); accepted for Phase 1.
        sr = self._call("search3.view", query=f"{artist} {title}",
                        songCount=1, albumCount=0, artistCount=0)
        songs = (sr.get("searchResult3", {}) or {}).get("song", []) or []
        return len(songs) > 0

    def get_playlist(self, playlist_id: str) -> dict:
        """Return playlist metadata + tracks list from Navidrome."""
        sr = self._call("getPlaylist.view", id=playlist_id)
        return (sr.get("playlist", {}) or {})

    def create_or_update_playlist(self, name: str, song_ids: list) -> str:
        """Create (or overwrite) a playlist with the given name and song IDs.

        Returns the playlist id.
        """
        # Check if a playlist with this name already exists
        plsr = self._call("getPlaylists.view")
        playlists = (plsr.get("playlists", {}) or {}).get("playlist", []) or []
        existing_id = None
        for pl in playlists:
            if (pl.get("name") or "") == name:
                existing_id = pl.get("id")
                break

        if existing_id:
            # Delete existing tracks and re-add
            self._call("updatePlaylist.view", playlistId=existing_id,
                       **{f"songIndexToRemove[{i}]": i
                          for i in range(999)})
            # Simpler: delete and recreate
            self._call("deletePlaylist.view", id=existing_id)

        # Create fresh
        params = {"name": name}
        params.update({f"songId[{i}]": sid for i, sid in enumerate(song_ids)})
        sr = self._call("createPlaylist.view", **params)
        pl = sr.get("playlist", {}) or {}
        return pl.get("id", "")

    def search_songs(self, query: str, count: int = 5) -> list:
        """Search for songs by query string, returning list of song dicts."""
        sr = self._call("search3.view", query=query,
                        songCount=count, albumCount=0, artistCount=0)
        return (sr.get("searchResult3", {}) or {}).get("song", []) or []

    def start_scan(self) -> bool:
        sr = self._call("startScan.view")
        return sr.get("status") == "ok"
