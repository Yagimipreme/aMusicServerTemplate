import logging
import os

import eyed3

logger = logging.getLogger(__name__)


def _normalize(s):
    return (s or "").strip().casefold()


def _make_record(path, artist, title, has_tags):
    key = f"{_normalize(artist)}|{_normalize(title)}"
    if not key.replace("|", "").strip():
        key = _normalize(os.path.splitext(os.path.basename(path))[0])
    return {"path": path, "key": key, "artist": artist, "title": title, "has_tags": has_tags}


def scan(song_dir):
    """Walk song_dir recursively for *.mp3, read ID3 tags, return list of record dicts."""
    results = []
    for root, _, files in os.walk(song_dir):
        for fname in sorted(files):
            if not fname.lower().endswith(".mp3"):
                continue
            path = os.path.join(root, fname)
            try:
                audio = eyed3.load(path)
                if audio is not None and audio.tag is not None:
                    artist = (audio.tag.artist or "").strip()
                    title = (audio.tag.title or "").strip()
                    has_tags = bool(artist and title)
                else:
                    artist, title, has_tags = "", "", False
            except Exception:
                logger.warning("scanner: could not read tags from %s", path)
                artist, title, has_tags = "", "", False
            results.append(_make_record(path, artist, title, has_tags))
    return results
