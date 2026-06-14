"""Resolve a track to canonical MusicBrainz metadata for tag enrichment."""
import logging

from follow.musicbrainz import MBError

logger = logging.getLogger(__name__)


def _pick_release(releases):
    """Choose the canonical release: prefer official albums, then earliest date."""
    if not releases:
        return None

    def date_key(rel):
        # Empty dates sort last.
        return rel.get("date") or "9999-99-99"

    official_albums = [
        r for r in releases
        if r.get("primary_type") == "Album" and r.get("status") == "Official"
    ]
    pool = official_albums or releases
    return sorted(pool, key=date_key)[0]


def _year_from_date(date_str):
    """Extract a 4-digit year from a MusicBrainz date like '1998-04-20'."""
    if date_str and len(date_str) >= 4 and date_str[:4].isdigit():
        return date_str[:4]
    return ""


def resolve(mb_client, artist, title, min_score):
    """Return canonical metadata for the best-matching recording, or None.

    Returns None if no recording matches or the top match scores below min_score.
    """
    try:
        recordings = mb_client.search_recording(artist, title)
    except MBError:
        logger.warning("mbmeta: search_recording failed for %s / %s",
                       artist, title, exc_info=True)
        return None

    if not recordings:
        return None

    rec = recordings[0]
    try:
        score = int(rec.get("score", 0))
    except (ValueError, TypeError):
        score = 0
    if score < min_score:
        return None

    release = _pick_release(rec.get("releases", [])) or {}
    return {
        "score": score,
        "recording_mbid": rec.get("mbid", ""),
        "artist_mbid": rec.get("artist_mbid", ""),
        "album": release.get("title", ""),
        "album_artist": rec.get("artist_name", ""),
        "year": _year_from_date(release.get("date", "")),
        "release_mbid": release.get("mbid", ""),
        "rg_mbid": release.get("rg_mbid", ""),
    }
