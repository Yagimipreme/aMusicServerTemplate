import logging

logger = logging.getLogger(__name__)


def track_key(artist: str, title: str) -> str:
    return f"{artist.strip().casefold()}|{title.strip().casefold()}"


def filter_fresh(is_owned, state, candidates):
    """Drop candidates already suggested (state), already owned, or duplicated in-batch."""
    fresh, seen = [], set()
    for c in candidates:
        key = track_key(c["artist"], c["title"])
        if key in seen or state.has(key):
            continue
        try:
            if is_owned(c["artist"], c["title"]):
                continue
        except Exception:
            logger.warning("dedupe: ownership check failed for %s / %s — skipping",
                           c["artist"], c["title"])
            continue
        seen.add(key)
        fresh.append(c)
    return fresh
