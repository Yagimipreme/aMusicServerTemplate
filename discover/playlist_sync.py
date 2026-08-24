"""Merge-aware Navidrome playlist sync.

Replaces the m3u overwrite in discover/engine.py and follow/runner.py. The
engine owns a subset of a playlist's tracks (recorded in a per-playlist
ledger); everything else in the playlist belongs to the user and is never
touched. User deletions of engine tracks stick — the suggested-TTL dedupe in
discover/state.py keeps them from being immediately rediscovered.
"""
import logging
import os
import re
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


def _m3u_path(song_dir: str, name: str) -> str:
    safe = re.sub(r'[\\/:*?"<>|]', "_", name)
    return os.path.join(song_dir, safe + ".m3u")


def migrate_from_m3u(subsonic, name, song_dir, tag_reader=None, wait_fn=None):
    """One-time migration of an m3u-backed playlist to an API playlist.

    Deletes the imported playlist via the API, renames the .m3u to .m3u.bak
    (never deletes it), rescans so Navidrome forgets it, then recreates the
    playlist through the API seeded with the old contents.
    """
    wait_fn = wait_fn or wait_for_scan
    result = {"migrated": False, "playlist_id": "", "owned": [], "backup": ""}

    m3u = _m3u_path(song_dir, name)
    if not os.path.exists(m3u):
        return result

    from discover.assemble import read_playlist_basenames
    basenames = read_playlist_basenames(song_dir, name)

    try:
        old_id = subsonic.find_playlist_id(name)
    except Exception:
        logger.exception("playlist_sync: could not look up %r before migration", name)
        old_id = None
    if old_id:
        try:
            subsonic.delete_playlist(old_id)
        except Exception:
            logger.exception("playlist_sync: could not delete old playlist %s", old_id)

    backup = m3u + ".bak"
    try:
        os.replace(m3u, backup)
        result["backup"] = backup
    except Exception:
        logger.exception("playlist_sync: could not rename %s — aborting migration", m3u)
        return result

    wait_fn(subsonic)

    ids, unresolved = resolve_paths(subsonic,
                                    [os.path.join(song_dir, b) for b in basenames],
                                    tag_reader=tag_reader)
    if unresolved:
        logger.warning("playlist_sync: %d of %d migrated tracks unresolved for %r",
                       len(unresolved), len(basenames), name)
    try:
        new_id = subsonic.create_playlist(name, ids)
    except Exception:
        logger.exception("playlist_sync: could not create migrated playlist %r", name)
        return result

    result.update({"migrated": True, "playlist_id": new_id or "", "owned": ids})
    logger.info("playlist_sync: migrated %r from m3u — %d tracks seeded, backup at %s",
                name, len(ids), backup)
    return result


def _blank_ledger() -> dict:
    return {"playlist_id": "", "owned": [], "pending": [], "migrated": False}


def sync_playlist(subsonic, name, new_paths, ledger, cap,
                  song_dir=None, tag_reader=None, wait_fn=None, known_ids=None):
    """Merge this run's new tracks into the named Navidrome playlist.

    Never touches user-added tracks, never re-adds tracks the user deleted, and
    caps only the engine-owned share. Mutates `ledger` in place; the caller
    persists it.

    known_ids -- song ids that are already resolved Navidrome ids (e.g.
    library-blend picks from select_library_tracks, which already carry a
    real song id) and should be merged in directly without going through
    resolve_paths' path->id lookup.
    """
    wait_fn = wait_fn or wait_for_scan
    for k, v in _blank_ledger().items():
        ledger.setdefault(k, v)

    try:
        # 1. one-time migration off the m3u writer
        if not ledger.get("migrated") and song_dir:
            mig = migrate_from_m3u(subsonic, name, song_dir,
                                   tag_reader=tag_reader, wait_fn=wait_fn)
            if mig["migrated"]:
                ledger["playlist_id"] = mig["playlist_id"]
                ledger["owned"] = list(mig["owned"])
            ledger["migrated"] = True   # attempted once, never retried

        # 2. make sure this run's downloads are indexed
        wait_fn(subsonic)

        # 3. resolve pending retries first, then this run's new files
        candidates = list(ledger.get("pending") or []) + list(new_paths or [])
        new_ids, unresolved = resolve_paths(subsonic, candidates, tag_reader=tag_reader)
        ledger["pending"] = unresolved

        # merge in ids that arrived already resolved — no point re-resolving
        # a library pick's own Navidrome song id via a title/artist search.
        for kid in (known_ids or []):
            if kid and kid not in new_ids:
                new_ids.append(kid)

        # 4. locate (or create) the playlist
        pid = ledger.get("playlist_id") or subsonic.find_playlist_id(name)
        if not pid:
            pid = subsonic.create_playlist(name, [])
        ledger["playlist_id"] = pid
        current_ids = subsonic.get_playlist_song_ids(pid) if pid else []

        # 5. merge and write back
        merged = merge_playlist(current_ids, ledger.get("owned") or [], new_ids, cap)
        subsonic.replace_playlist(pid, merged["final"])
        ledger["owned"] = merged["owned"]

        logger.info("playlist_sync: %r — %d user, %d engine (+%d new, -%d evicted, %d pending)",
                    name, len(merged["user"]), len(merged["owned"]),
                    len(new_ids), len(merged["evicted"]), len(unresolved))
        return {"status": "ok", "playlist_id": pid,
                "added": len(new_ids), "evicted": len(merged["evicted"]),
                "pending": len(unresolved), "migrated": ledger["migrated"],
                "final_count": len(merged["final"])}
    except Exception as e:
        logger.exception("playlist_sync: sync failed for %r", name)
        return {"status": "error", "error": str(e), "playlist_id": ledger.get("playlist_id", ""),
                "added": 0, "evicted": 0, "pending": len(ledger.get("pending") or []),
                "migrated": ledger.get("migrated", False), "final_count": 0}
