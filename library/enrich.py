"""Library tag enrichment: backfill ID3 metadata from Last.fm + MusicBrainz."""

import logging

import eyed3
import eyed3.core
import eyed3.id3
import eyed3.id3.frames

from library.scanner import scan

logger = logging.getLogger(__name__)

_ALL_FIELDS = ("genre", "year", "album", "album_artist", "mbids", "cover_art")

_MB_TXXX_ARTIST = "MusicBrainz Artist Id"
_MB_TXXX_ALBUM = "MusicBrainz Album Id"
_MB_TXXX_RG = "MusicBrainz Release Group Id"
_MB_UFID_OWNER = "http://musicbrainz.org"


def _default_fields():
    return {f: {"enabled": True, "only_missing": True} for f in _ALL_FIELDS}


def _has_mbids(tag):
    """True if any MusicBrainz ID frame is already present."""
    try:
        if tag.user_text_frames.get(_MB_TXXX_ARTIST):
            return True
        if tag.unique_file_ids.get(_MB_UFID_OWNER):
            return True
    except Exception:
        pass
    return False


def _write_mbids(tag, meta):
    """Write available MusicBrainz ID frames. Returns True if any were written."""
    wrote = False
    if meta.get("artist_mbid"):
        tag.user_text_frames.set(meta["artist_mbid"], _MB_TXXX_ARTIST)
        wrote = True
    if meta.get("release_mbid"):
        tag.user_text_frames.set(meta["release_mbid"], _MB_TXXX_ALBUM)
        wrote = True
    if meta.get("rg_mbid"):
        tag.user_text_frames.set(meta["rg_mbid"], _MB_TXXX_RG)
        wrote = True
    if meta.get("recording_mbid"):
        tag.unique_file_ids.set(meta["recording_mbid"].encode("utf-8"),
                                _MB_UFID_OWNER)
        wrote = True
    return wrote


def _needed(fields, name, is_empty):
    cfg = fields.get(name, {})
    if not cfg.get("enabled"):
        return False
    if cfg.get("only_missing", True) and not is_empty:
        return False
    return True


def run(song_dir, lastfm_client=None, mb_client=None, fields=None,
        min_musicbrainz_score=90, cover_art_size="500", limit=None,
        progress=None):
    """Walk song_dir and enrich ID3 tags from Last.fm (genre) + MusicBrainz.

    fields   : per-field config {name: {"enabled": bool, "only_missing": bool}}.
               Defaults to all six fields enabled + only_missing.
    progress : optional callback progress(done, total) called once before the
               loop with (0, total) and after each file with (i, total).

    Returns {processed, files_total, enriched, per_field, skipped, errors}.
    """
    from lastfm.tags import get_track_tags, get_artist_tags
    from library import mbmeta, coverart

    if fields is None:
        fields = _default_fields()

    records = scan(song_dir)
    if limit is not None:
        records = records[:limit]

    total = len(records)
    per_field = {f: 0 for f in _ALL_FIELDS}
    processed = 0
    enriched = 0
    skipped = 0
    errors = 0

    if progress:
        progress(0, total)

    for i, rec in enumerate(records, start=1):
        path = rec["path"]
        artist = rec.get("artist", "")
        title = rec.get("title", "")

        try:
            audio = eyed3.load(path)
        except Exception:
            logger.warning("enrich: could not load %s", path)
            errors += 1
            if progress:
                progress(i, total)
            continue
        if audio is None:
            logger.warning("enrich: eyed3 could not load %s", path)
            errors += 1
            if progress:
                progress(i, total)
            continue
        if audio.tag is None:
            audio.initTag()
        tag = audio.tag
        processed += 1

        genre_empty = not (tag.genre and (tag.genre.name or tag.genre.id is not None))
        year_empty = tag.getBestDate() is None
        album_empty = not (tag.album or "").strip()
        album_artist_empty = not (tag.album_artist or "").strip()
        mbids_empty = not _has_mbids(tag)
        cover_empty = len(list(tag.images)) == 0

        need_genre = _needed(fields, "genre", genre_empty)
        need_year = _needed(fields, "year", year_empty)
        need_album = _needed(fields, "album", album_empty)
        need_album_artist = _needed(fields, "album_artist", album_artist_empty)
        need_mbids = _needed(fields, "mbids", mbids_empty)
        need_cover = _needed(fields, "cover_art", cover_empty)

        wrote = False

        # Genre via Last.fm
        if need_genre and lastfm_client and artist and title:
            tags = get_track_tags(lastfm_client, artist, title)
            if not tags:
                tags = get_artist_tags(lastfm_client, artist)
            if tags:
                tag.genre = eyed3.id3.Genre(name=", ".join(t["name"] for t in tags))
                per_field["genre"] += 1
                wrote = True

        # MusicBrainz-backed fields (one resolve per file)
        need_mb = (need_year or need_album or need_album_artist
                   or need_mbids or need_cover)
        if need_mb and mb_client and artist and title:
            meta = mbmeta.resolve(mb_client, artist, title, min_musicbrainz_score)
            if meta:
                if need_year and meta["year"]:
                    tag.recording_date = eyed3.core.Date(int(meta["year"]))
                    per_field["year"] += 1
                    wrote = True
                if need_album and meta["album"]:
                    tag.album = meta["album"]
                    per_field["album"] += 1
                    wrote = True
                if need_album_artist and meta["album_artist"]:
                    tag.album_artist = meta["album_artist"]
                    per_field["album_artist"] += 1
                    wrote = True
                if need_mbids and _write_mbids(tag, meta):
                    per_field["mbids"] += 1
                    wrote = True
                if need_cover and meta["release_mbid"]:
                    art = coverart.fetch_front(meta["release_mbid"], cover_art_size)
                    if art:
                        img_bytes, mime = art
                        tag.images.set(
                            eyed3.id3.frames.ImageFrame.FRONT_COVER, img_bytes, mime)
                        per_field["cover_art"] += 1
                        wrote = True

        if wrote:
            try:
                tag.save()
                enriched += 1
            except Exception:
                logger.exception("enrich: failed to save %s", path)
                errors += 1
        else:
            skipped += 1

        if progress:
            progress(i, total)

    return {
        "processed": processed,
        "files_total": total,
        "enriched": enriched,
        "per_field": per_field,
        "skipped": skipped,
        "errors": errors,
    }
