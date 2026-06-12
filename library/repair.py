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


def _repair_by_musicbrainz(title: str, min_score: int):
    """Query MusicBrainz recording search; return artist if score is confident.

    Rate-limited to 1 req/s per MusicBrainz ToS. Requires User-Agent header.
    Returns artist string or None.
    """
    import json
    import time
    import urllib.parse
    import urllib.request

    query = urllib.parse.quote(f'recording:"{title}"')
    url = f"https://musicbrainz.org/ws/2/recording/?query={query}&limit=1&fmt=json"
    req = urllib.request.Request(url, headers={"User-Agent": _MB_USER_AGENT})
    try:
        time.sleep(1.0)  # 1 req/s — MusicBrainz ToS requirement
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        recordings = data.get("recordings", [])
        if not recordings:
            return None
        rec = recordings[0]
        if int(rec.get("score", 0)) < min_score:
            return None
        credits = rec.get("artist-credit", [])
        if not credits:
            return None
        return (credits[0].get("artist", {}).get("name") or "").strip() or None
    except Exception:
        logger.warning("repair: MusicBrainz search failed for %r", title, exc_info=True)
        return None


def repair_missing_artists(song_dir: str, lastfm_client=None,
                            min_lastfm_listeners: int = _DEFAULT_MIN_LASTFM_LISTENERS,
                            min_musicbrainz_score: int = _DEFAULT_MIN_MB_SCORE,
                            limit: int = 0) -> dict:
    """Scan song_dir for MP3s missing artist tag; attempt three-stage repair.

    Returns: {processed, repaired_stage1, repaired_stage2, repaired_stage3, skipped, errors}
    """
    from library.scanner import scan

    stats = {
        "processed": 0, "repaired_stage1": 0, "repaired_stage2": 0,
        "repaired_stage3": 0, "skipped": 0, "errors": 0,
    }

    records = scan(song_dir)
    if limit:
        records = records[:limit]

    for rec in records:
        stats["processed"] += 1
        path = rec["path"]
        try:
            af = eyed3.load(path)
            if af is None or af.tag is None:
                stats["skipped"] += 1
                continue

            artist = (af.tag.artist or "").strip()
            if artist:
                stats["skipped"] += 1
                continue

            title = (af.tag.title or "").strip()
            if not title:
                stats["skipped"] += 1
                continue

            # Stage 1: "Artist - Title" in title field
            parsed_artist, clean_title = _repair_by_title_parse(title)
            if parsed_artist:
                af.tag.artist = parsed_artist
                af.tag.title = clean_title
                af.tag.save()
                stats["repaired_stage1"] += 1
                logger.info("repair stage1: %r -> artist=%r", path, parsed_artist)
                continue

            # Stage 2: Last.fm
            if lastfm_client:
                lfm_artist = _repair_by_lastfm(lastfm_client, title,
                                               min_lastfm_listeners)
                if lfm_artist:
                    af.tag.artist = lfm_artist
                    af.tag.save()
                    stats["repaired_stage2"] += 1
                    logger.info("repair stage2: %r -> artist=%r", path, lfm_artist)
                    continue

            # Stage 3: MusicBrainz
            mb_artist = _repair_by_musicbrainz(title, min_musicbrainz_score)
            if mb_artist:
                af.tag.artist = mb_artist
                af.tag.save()
                stats["repaired_stage3"] += 1
                logger.info("repair stage3: %r -> artist=%r", path, mb_artist)
                continue

            stats["skipped"] += 1
            logger.debug("repair: no match for %r (title=%r)", path, title)

        except Exception:
            logger.exception("repair: error processing %r", path)
            stats["errors"] += 1

    return stats
