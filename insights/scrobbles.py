"""Last.fm scrobble-history ingestion into the insights SQLite store."""

import logging

logger = logging.getLogger(__name__)


def _clean(v: "str | None") -> "str | None":
    return (v or "").strip() or None


def parse_recent_tracks(data: dict) -> list[dict]:
    """Parse one user.getRecentTracks JSON page into scrobble rows.

    Skips the "now playing" row (it has no timestamp). Blank optional
    string fields are normalised to None.
    """
    root = data.get("recenttracks", {}) or {}
    raw = root.get("track", []) or []
    if isinstance(raw, dict):  # single-result API quirk
        raw = [raw]

    rows = []
    for t in raw:
        attr = t.get("@attr") or {}
        if attr.get("nowplaying") == "true":
            continue
        date = t.get("date") or {}
        uts = date.get("uts")
        if not uts:
            continue
        artist = t.get("artist") or {}
        album = t.get("album") or {}

        artist_name = (artist.get("#text") or artist.get("name") or "").strip()
        if not artist_name:
            logger.warning("parse_recent_tracks: skipping scrobble with empty artist at ts=%s", uts)
            continue

        rows.append({
            "ts": int(uts),
            "artist": artist_name,
            "track": (t.get("name") or "").strip(),
            "album": _clean(album.get("#text")),
            "artist_mbid": _clean(artist.get("mbid")),
            "recording_mbid": _clean(t.get("mbid")),
        })
    return rows


def total_pages(data: dict) -> int:
    """Read totalPages from a getRecentTracks page (defaults to 1)."""
    root = data.get("recenttracks", {}) or {}
    attr = root.get("@attr", {}) or {}
    try:
        return int(attr.get("totalPages", 1))
    except (TypeError, ValueError):
        return 1
