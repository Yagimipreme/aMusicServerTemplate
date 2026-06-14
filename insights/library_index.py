"""Index the local library's (artist, track) keys into library_tracks.

Stored normalized (strip().lower()) so the analytics JOIN can match
Last.fm scrobble names via SQL lower(trim(...)). Spelling differences
between ID3 tags and Last.fm names are an accepted limitation.
"""

import logging

logger = logging.getLogger(__name__)


def normalize(s) -> str:
    return (s or "").strip().lower()


def index_library(conn, song_dir, scan=None) -> int:
    """Rebuild library_tracks from a fresh scan of song_dir.

    Clears the table then repopulates, so tracks removed from disk drop out.
    `scan` is injectable for tests; defaults to library.scanner.scan.
    Returns the number of distinct library tracks indexed.
    """
    if scan is None:
        from library.scanner import scan as _scan
        scan = _scan
    try:
        records = scan(song_dir)
    except Exception:
        logger.warning("library_index: scan failed for %s", song_dir, exc_info=True)
        return 0

    rows = []
    for rec in records:
        a = normalize(rec.get("artist"))
        t = normalize(rec.get("title"))
        if a and t:
            rows.append((a, t))

    conn.execute("DELETE FROM library_tracks")
    conn.executemany(
        "INSERT OR IGNORE INTO library_tracks (artist, track) VALUES (?, ?)", rows)
    conn.commit()
    return conn.execute("SELECT COUNT(*) FROM library_tracks").fetchone()[0]
