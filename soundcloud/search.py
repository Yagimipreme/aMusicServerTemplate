"""SoundCloud search — users and tracks."""
from soundcloud.mirror import _track_from_raw, _user_from_raw


def search_users(client, query: str, limit: int = 10) -> list:
    data = client.get("/search/users", params={"q": query, "limit": limit})
    return [_user_from_raw(u) for u in (data.get("collection") or [])]


def search_tracks(client, query: str, limit: int = 20) -> list:
    data = client.get("/search/tracks", params={"q": query, "limit": limit})
    return [_track_from_raw(t) for t in (data.get("collection") or [])]
