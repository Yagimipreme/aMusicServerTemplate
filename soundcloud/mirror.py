"""SoundCloud profile/track/set mirror."""
import logging

logger = logging.getLogger(__name__)

_HLS_PROTOCOL = "hls"


def _first_hls_url(transcodings: list) -> str:
    """Pick the first HLS transcoding URL."""
    for t in transcodings:
        if t.get("format", {}).get("protocol") == _HLS_PROTOCOL:
            return t.get("url", "")
    return ""


def _track_from_raw(raw: dict) -> dict:
    transcodings = (raw.get("media") or {}).get("transcodings", [])
    return {
        "id": raw.get("id"),
        "title": raw.get("title", ""),
        "artist": (raw.get("user") or {}).get("username", ""),
        "stream_url": _first_hls_url(transcodings),
        "permalink_url": raw.get("permalink_url", ""),
        "artwork_url": raw.get("artwork_url") or "",
        "duration_ms": raw.get("duration", 0),
        "source": "sc",
    }


def _user_from_raw(raw: dict) -> dict:
    return {
        "id": raw.get("id"),
        "username": raw.get("username", ""),
        "full_name": raw.get("full_name", ""),
        "avatar_url": raw.get("avatar_url") or "",
        "track_count": raw.get("track_count", 0),
        "followers_count": raw.get("followers_count", 0),
    }


def resolve(client, url: str) -> dict:
    """GET /resolve -> entity dict with kind, id, name, artwork_url."""
    data = client.get("/resolve", params={"url": url})
    return data


def get_user_tracks(client, user_id, limit: int = 200) -> list:
    data = client.get(f"/users/{user_id}/tracks", params={"limit": limit})
    return [_track_from_raw(t) for t in (data.get("collection") or [])]


def get_user_sets(client, user_id, limit: int = 50) -> list:
    data = client.get(f"/users/{user_id}/playlists", params={"limit": limit})
    sets = []
    for pl in (data.get("collection") or []):
        tracks = [_track_from_raw(t) for t in (pl.get("tracks") or [])]
        sets.append({
            "id": pl.get("id"),
            "title": pl.get("title", ""),
            "track_count": pl.get("track_count", 0),
            "artwork_url": pl.get("artwork_url") or "",
            "tracks": tracks,
        })
    return sets


def get_set_tracks(client, playlist_id) -> list:
    data = client.get(f"/playlists/{playlist_id}")
    return [_track_from_raw(t) for t in (data.get("tracks") or [])]


def get_profile(client, url: str) -> dict:
    """Resolve a URL and return {user, tracks, sets}."""
    entity = resolve(client, url)
    kind = entity.get("kind", "")

    if kind == "user":
        user_id = entity["id"]
        user = _user_from_raw(entity)
        tracks = get_user_tracks(client, user_id)
        sets = get_user_sets(client, user_id)
        return {"user": user, "tracks": tracks, "sets": sets}

    elif kind == "playlist":
        playlist_id = entity["id"]
        tracks = get_set_tracks(client, playlist_id)
        user_raw = entity.get("user") or {}
        user = _user_from_raw({**user_raw, "kind": "user"})
        single_set = {
            "id": entity["id"],
            "title": entity.get("title", ""),
            "track_count": entity.get("track_count", 0),
            "artwork_url": entity.get("artwork_url") or "",
            "tracks": tracks,
        }
        return {"user": user, "tracks": tracks, "sets": [single_set]}

    elif kind == "track":
        track = _track_from_raw(entity)
        user_raw = entity.get("user") or {}
        user = _user_from_raw({**user_raw, "kind": "user"})
        return {"user": user, "tracks": [track], "sets": []}

    else:
        logger.warning("[SC] unknown entity kind: %s", kind)
        return {"user": {}, "tracks": [], "sets": []}
