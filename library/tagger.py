import json
import logging
import os
import re

import eyed3

logger = logging.getLogger(__name__)

_BUILTIN_SUFFIXES = [
    "Official Music Video", "Official Video", "Official Audio",
    "Official Lyric Video", "Lyric Video", "Lyrics Video",
    "Music Video", "Audio Only", "Official",
    "HD", "HQ", "4K", "1080p", "720p",
    "Full Song", "Full Audio", "Remastered",
    "Visualizer", "Official Visualizer",
]


def _load_extra_suffixes(path):
    if not path or not os.path.exists(path):
        return []
    result = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    result.append(line)
    except Exception:
        logger.warning("tagger: could not read extra suffixes from %s", path)
    return result


def _build_pattern(suffixes):
    escaped = [re.escape(s) for s in sorted(suffixes, key=len, reverse=True)]
    alt = "|".join(escaped)
    return re.compile(
        r'\s*(?:'
        r'\((?:' + alt + r')\)'
        r'|\[(?:' + alt + r')\]'
        r'|(?<!\w)(?:' + alt + r')'
        r')\s*$',
        re.IGNORECASE,
    )


def clean_title(title, extra_suffixes_file=None):
    """Strip known noise suffixes from a song title string."""
    extra = _load_extra_suffixes(extra_suffixes_file)
    pattern = _build_pattern(_BUILTIN_SUFFIXES + extra)
    prev = None
    while prev != title:
        prev = title
        title = pattern.sub("", title)
    return re.sub(r'[\s\-,]+$', '', title).strip()


def apply_to_file(path, extra_suffixes_file=None):
    """Read ID3 title from path, strip noise, write back if changed. Returns True if changed."""
    try:
        audio = eyed3.load(path)
        if audio is None or audio.tag is None:
            return False
        original = audio.tag.title or ""
        cleaned = clean_title(original, extra_suffixes_file)
        if not cleaned or cleaned == original:
            return False
        audio.tag.title = cleaned
        audio.tag.save()
        logger.info("tagger: %r → %r", original, cleaned)
        return True
    except Exception:
        logger.exception("tagger: failed for %s", path)
        return False


def apply_from_config(path, config_path):
    """Apply title cleanup using settings from config_path. Best-effort, never raises."""
    try:
        with open(config_path, encoding="utf-8") as f:
            cfg = json.load(f)
        tc = cfg.get("title_cleanup") or {}
        if not tc.get("enabled", True):
            return False
        suffix_file = tc.get("extra_suffixes_file", "title_suffixes.txt")
        suffix_path = (
            os.path.join(os.path.dirname(config_path), suffix_file)
            if suffix_file else None
        )
        return apply_to_file(path, suffix_path)
    except Exception:
        logger.exception("tagger: apply_from_config failed for %s", path)
        return False
