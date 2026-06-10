"""SoundCloud discovery lens — feeds the Weekly Mix engine."""
from soundcloud.mirror import _track_from_raw, _user_from_raw


def get_followings(client, user_id, limit: int = 100) -> list:
    data = client.get(f"/users/{user_id}/followings", params={"limit": limit})
    return [_user_from_raw(u) for u in (data.get("collection") or [])]


def get_reposts(client, user_id, limit: int = 100) -> list:
    data = client.get(f"/users/{user_id}/reposts", params={"limit": limit})
    return [_track_from_raw(t) for t in (data.get("collection") or [])]


def get_related(client, track_id, limit: int = 20) -> list:
    data = client.get(f"/tracks/{track_id}/related", params={"limit": limit})
    return [_track_from_raw(t) for t in (data.get("collection") or [])]


def to_candidates(tracks: list) -> list:
    """Map SC track dicts to the Candidate format expected by the discover engine."""
    return [
        {
            "artist": t["artist"],
            "title": t["title"],
            "source": "soundcloud",
            "preview_ref": t.get("stream_url", ""),
            "download_ref": t.get("permalink_url", ""),
            "why": "soundcloud_discovery",
        }
        for t in tracks
    ]
