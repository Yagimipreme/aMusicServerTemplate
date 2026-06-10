"""Library tag enrichment: backfill missing ID3 genre tags from Last.fm."""

import logging

import eyed3
import eyed3.id3

from library.scanner import scan

logger = logging.getLogger(__name__)


def run(song_dir: str, lastfm_client, only_missing_genre: bool = True,
        limit: int | None = None) -> dict:
    """Walk song_dir and write Last.fm genre tags to MP3 files.

    Parameters
    ----------
    song_dir           : directory to scan (recursive)
    lastfm_client      : LastFMClient instance
    only_missing_genre : if True, skip files that already have a genre tag
    limit              : max number of files to process (None = all)

    Returns
    -------
    {"processed": N, "tagged": N, "skipped": N, "errors": N}
    """
    from lastfm.tags import get_track_tags, get_artist_tags

    records = scan(song_dir)
    if limit is not None:
        records = records[:limit]

    processed = 0
    tagged = 0
    skipped = 0
    errors = 0

    for rec in records:
        path = rec["path"]
        artist = rec.get("artist", "")
        title = rec.get("title", "")

        # Check existing genre if only_missing_genre
        if only_missing_genre:
            try:
                audio = eyed3.load(path)
                if audio is not None and audio.tag is not None:
                    genre = audio.tag.genre
                    if genre is not None and (genre.name or genre.id is not None):
                        skipped += 1
                        continue
            except Exception:
                logger.warning("enrich: could not read genre from %s", path)
                errors += 1
                continue

        processed += 1

        if not artist or not title:
            logger.debug("enrich: no artist/title tags on %s — skipping", path)
            skipped += 1
            processed -= 1
            continue

        # Try track-level first, then artist fallback
        tags = get_track_tags(lastfm_client, artist, title)
        if not tags:
            tags = get_artist_tags(lastfm_client, artist)

        if not tags:
            logger.debug("enrich: no usable tags found for %s / %s", artist, title)
            skipped += 1
            processed -= 1
            continue

        # Write genre tag
        genre_str = ", ".join(t["name"] for t in tags)
        try:
            audio = eyed3.load(path)
            if audio is None:
                logger.warning("enrich: eyed3 could not load %s", path)
                errors += 1
                processed -= 1
                continue
            if audio.tag is None:
                audio.initTag()
            audio.tag.genre = eyed3.id3.Genre(name=genre_str)
            audio.tag.save()
            logger.info("enrich: tagged %s → %r", path, genre_str)
            tagged += 1
        except Exception:
            logger.exception("enrich: failed to write tag for %s", path)
            errors += 1
            processed -= 1

    return {
        "processed": processed,
        "tagged": tagged,
        "skipped": skipped,
        "errors": errors,
    }
