"""Merge-aware Navidrome playlist sync.

Replaces the m3u overwrite in discover/engine.py and follow/runner.py. The
engine owns a subset of a playlist's tracks (recorded in a per-playlist
ledger); everything else in the playlist belongs to the user and is never
touched. User deletions of engine tracks stick — the suggested-TTL dedupe in
discover/state.py keeps them from being immediately rediscovered.
"""
import logging
import os
import time

logger = logging.getLogger(__name__)

_SCAN_FALLBACK_SLEEP = 15.0


def merge_playlist(current_ids, owned_ids, new_ids, cap):
    """Pure merge. Returns {"final", "owned", "evicted", "user"}.

    current_ids -- song ids currently in the Navidrome playlist, in order
    owned_ids   -- song ids the engine believes it owns, in discovery order
    new_ids     -- song ids resolved from this run's downloads
    cap         -- max engine-owned tracks (0/None = unbounded); user tracks
                   are never counted against it and never evicted
    """
    current = list(current_ids or [])
    owned = list(owned_ids or [])
    incoming = list(new_ids or [])

    owned_set = set(owned)
    current_set = set(current)

    user = [i for i in current if i not in owned_set]
    user_set = set(user)

    # User deletions are respected: an owned id absent from the playlist is dropped.
    engine = [i for i in owned if i in current_set]
    engine_set = set(engine)

    for sid in incoming:
        if not sid or sid in engine_set or sid in user_set:
            continue
        engine.append(sid)
        engine_set.add(sid)

    evicted = []
    if cap and len(engine) > cap:
        cut = len(engine) - cap
        evicted = engine[:cut]
        engine = engine[cut:]

    return {"final": user + engine, "owned": engine,
            "evicted": evicted, "user": user}


def _norm(s) -> str:
    return (s or "").strip().casefold()


def _read_tags(path):
    """(artist, title) from ID3, best effort. Mirrors library/scanner.py."""
    try:
        import eyed3
        af = eyed3.load(path)
        if af is None or af.tag is None:
            return ("", "")
        return (af.tag.artist or "", af.tag.title or "")
    except Exception:
        return ("", "")


def wait_for_scan(subsonic, timeout=120, poll=3.0, sleep_fn=None,
                  clock=None) -> bool:
    """Trigger a library scan and wait (bounded) for it to settle.

    Returns True when the scan reported idle inside the timeout. A client with
    no get_scan_status falls back to one fixed sleep and returns False.
    """
    sleep_fn = sleep_fn or time.sleep
    clock = clock or time.monotonic
    try:
        subsonic.start_scan()
    except Exception:
        logger.exception("playlist_sync: start_scan failed")

    status_fn = getattr(subsonic, "get_scan_status", None)
    if status_fn is None:
        sleep_fn(_SCAN_FALLBACK_SLEEP)
        return False

    deadline = clock() + timeout
    while clock() < deadline:
        try:
            st = status_fn() or {}
        except Exception:
            logger.exception("playlist_sync: get_scan_status failed")
            sleep_fn(_SCAN_FALLBACK_SLEEP)
            return False
        if not st.get("scanning"):
            return True
        sleep_fn(poll)
    logger.warning("playlist_sync: scan did not settle within %ss", timeout)
    return False


def resolve_paths(subsonic, paths, tag_reader=None):
    """Resolve downloaded file paths to Navidrome song ids.

    Returns (ids, unresolved_paths). A hit counts only when title AND artist
    both match; when the file has no usable tags we fall back to matching the
    basename against the hit's reported path.
    """
    tag_reader = tag_reader or _read_tags
    ids, unresolved, seen = [], [], set()

    for path in (paths or []):
        artist, title = tag_reader(path)
        base = os.path.basename(path)
        query = f"{artist} {title}".strip() or os.path.splitext(base)[0]
        try:
            hits = subsonic.search_songs(query, count=5) or []
        except Exception:
            logger.exception("playlist_sync: search failed for %s", path)
            unresolved.append(path)
            continue

        match = None
        if artist and title:
            for h in hits:
                if _norm(h.get("title")) == _norm(title) and _norm(h.get("artist")) == _norm(artist):
                    match = h
                    break
        if match is None:
            for h in hits:
                if base and os.path.basename(h.get("path") or "") == base:
                    match = h
                    break

        sid = (match or {}).get("id")
        if not sid:
            unresolved.append(path)
            continue
        if sid not in seen:
            seen.add(sid)
            ids.append(sid)

    return ids, unresolved
