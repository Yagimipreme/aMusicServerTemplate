"""Merge-aware Navidrome playlist sync.

Replaces the m3u overwrite in discover/engine.py and follow/runner.py. The
engine owns a subset of a playlist's tracks (recorded in a per-playlist
ledger); everything else in the playlist belongs to the user and is never
touched. User deletions of engine tracks stick — the suggested-TTL dedupe in
discover/state.py keeps them from being immediately rediscovered.
"""
import logging

logger = logging.getLogger(__name__)


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
