"""Pick owned tracks for a mix profile's library share."""
import os
import random
import logging

logger = logging.getLogger(__name__)


def select_library_tracks(subsonic, profile: dict, exclude_basenames: set,
                          count: int, seed_artists: list | None = None) -> list:
    """Return up to `count` song dicts (with 'path') from the library.

    genre mode: union of get_songs_by_genre per genre.
    other modes: songs by seed artists. seed_artists (if provided) overrides
    profile seeds.artists — used by run_profile to pass the dynamically-computed
    seed list for history/playlist modes (which have no static artists list).
    Prefers never/least-recently played. Excludes basenames already in the
    playlist. Never touches DiscoverState.
    """
    if count <= 0:
        return []
    seeds_cfg = profile.get("seeds") or {}
    pool, seen_ids = [], set()
    if seeds_cfg.get("mode") == "genre":
        for g in seeds_cfg.get("genres") or []:
            try:
                songs = subsonic.get_songs_by_genre(g, count=200)
            except Exception:
                logger.warning("library_pick: getSongsByGenre failed for %r", g, exc_info=True)
                continue
            for s in songs:
                if s.get("id") and s["id"] not in seen_ids:
                    seen_ids.add(s["id"])
                    pool.append(s)
    else:
        # Use injected seed_artists when provided (history/playlist modes pass computed seeds)
        artists_to_search = seed_artists if seed_artists is not None else (seeds_cfg.get("artists") or [])
        search = getattr(subsonic, "search_songs", None)
        if callable(search):
            for a in artists_to_search[:20]:
                try:
                    for s in search(a, count=20):
                        if s.get("id") and s["id"] not in seen_ids:
                            seen_ids.add(s["id"])
                            pool.append(s)
                except Exception:
                    continue
    pool = [s for s in pool
            if os.path.basename(s.get("path") or "") not in exclude_basenames
            and s.get("path")]
    random.shuffle(pool)                                  # tie-break
    pool.sort(key=lambda s: (s.get("played") is not None, s.get("played") or ""))
    return pool[:count]
