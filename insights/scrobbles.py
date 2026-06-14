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

        track_name = (t.get("name") or "").strip()
        if not track_name:
            logger.warning("parse_recent_tracks: skipping scrobble with empty track at ts=%s", uts)
            continue

        rows.append({
            "ts": int(uts),
            "artist": artist_name,
            "track": track_name,
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


def insert_scrobbles(conn, rows: list[dict]) -> int:
    """INSERT OR IGNORE rows; return the number actually inserted."""
    if not rows:
        return 0
    before = conn.total_changes
    conn.executemany(
        "INSERT OR IGNORE INTO scrobbles "
        "(ts, artist, track, album, artist_mbid, recording_mbid) "
        "VALUES (:ts, :artist, :track, :album, :artist_mbid, :recording_mbid)",
        rows,
    )
    conn.commit()
    return conn.total_changes - before


def sync_scrobbles(client, username: str, conn, *, page_limit: int = 200,
                   max_pages: int | None = None) -> dict:
    """Incrementally pull scrobbles into the store, newest pages first.

    Resumes from sync_state['last_ts'] using the API's `from` filter, and
    relies on INSERT OR IGNORE to dedup the boundary play. Returns
    {"inserted", "pages", "last_ts"}.

    If client.call raises mid-sync, rows committed so far are kept but
    last_ts is only written after a clean run, so the next sync re-fetches
    already-stored rows (INSERT OR IGNORE deduplicates them). The client's
    built-in rate limiter throttles large backfills.
    """
    from insights import db

    last_ts = int(db.get_state(conn, "last_ts", "0") or 0)
    inserted = 0
    page = 1
    while True:
        params = {"user": username, "limit": page_limit, "page": page}
        if last_ts:
            params["from"] = last_ts
        data = client.call("user.getRecentTracks", **params)
        rows = parse_recent_tracks(data)
        inserted += insert_scrobbles(conn, rows)
        pages = total_pages(data)
        if page >= pages or (max_pages and page >= max_pages) or not rows:
            break
        page += 1

    newest = conn.execute("SELECT MAX(ts) FROM scrobbles").fetchone()[0]
    if newest is not None:
        db.set_state(conn, "last_ts", str(newest))
    return {"inserted": inserted, "pages": page, "last_ts": newest}
