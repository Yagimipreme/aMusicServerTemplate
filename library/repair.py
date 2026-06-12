"""Three-stage missing-artist metadata repair.

Stage 1: Parse "Artist - Title" pattern from the title ID3 field.
Stage 2: Last.fm track.search by title.
Stage 3: MusicBrainz recording search by title.
"""
import re
import logging

import eyed3

logger = logging.getLogger(__name__)

_SEPARATOR_RE = re.compile(r'^(.+?)\s*[-–—]\s*(.+)$')

_DEFAULT_MIN_LASTFM_LISTENERS = 10_000
_DEFAULT_MIN_MB_SCORE = 90

_MB_USER_AGENT = "amusicserver/1.0 (asbalk@gmx.de)"


def _repair_by_title_parse(title: str):
    """Extract artist from title field if it matches 'Artist - Title' pattern.

    Returns (artist, clean_title) on match, (None, None) otherwise.
    """
    m = _SEPARATOR_RE.match(title.strip())
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return None, None


def _repair_by_lastfm(lastfm_client, title: str, min_listeners: int):
    """Search Last.fm by title; return artist name if listener count is confident.

    Returns artist string or None.
    """
    try:
        result = lastfm_client.call("track.search", track=title, limit=1)
        matches = (
            (result.get("results") or {})
            .get("trackmatches", {})
            .get("track", [])
        )
        if isinstance(matches, dict):
            matches = [matches]
        if not matches:
            return None
        match = matches[0]
        try:
            listeners = int(match.get("listeners", 0))
        except (ValueError, TypeError):
            listeners = 0
        if listeners < min_listeners:
            return None
        return (match.get("artist") or "").strip() or None
    except Exception:
        logger.warning("repair: Last.fm track.search failed for %r", title, exc_info=True)
        return None
