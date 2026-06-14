"""Artist genre cache for listening insights.

Reuses lastfm/tags.py (noise-filtered, weight-ranked top tags). Each artist
is fetched once; an artist with no usable tags is cached with an empty tag
list + NULL primary_genre so we never re-query it (negative cache).
"""

import json
import logging
import time

logger = logging.getLogger(__name__)


def primary_genre_for(tags: list[dict]) -> str | None:
    """Highest-weighted tag name, or None for an empty tag set.

    lastfm.tags.get_artist_tags already returns tags sorted by descending
    weight, but we do not rely on order here — we pick the max explicitly.
    """
    if not tags:
        return None
    return max(tags, key=lambda t: t.get("weight", 0)).get("name")


def cached_artists(conn) -> set:
    """Artist names already present in the artist_tags cache."""
    rows = conn.execute("SELECT artist FROM artist_tags").fetchall()
    return {r[0] for r in rows}


def ensure_artist_tags(client, conn, artists) -> int:
    """Fetch + cache genre tags for any of `artists` not already cached.

    Returns the number of artists newly written. Network failures for a
    single artist are swallowed by lastfm.tags (returns []), so that artist
    is cached as "no genre" and not retried.
    """
    from lastfm.tags import get_artist_tags

    have = cached_artists(conn)
    written = 0
    for artist in artists:
        if not artist or artist in have:
            continue
        tags = get_artist_tags(client, artist)
        conn.execute(
            "INSERT OR IGNORE INTO artist_tags "
            "(artist, tags_json, primary_genre, fetched_at) VALUES (?, ?, ?, ?)",
            (artist, json.dumps(tags), primary_genre_for(tags), int(time.time())),
        )
        have.add(artist)
        written += 1
    if written:
        conn.commit()
    return written
