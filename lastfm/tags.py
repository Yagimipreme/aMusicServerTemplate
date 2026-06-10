"""Last.fm tag functions: fetch track/artist tags, build genre profiles."""

import logging

logger = logging.getLogger(__name__)

# Tags that carry no genre information — filtered out before genre tagging.
NOISE_TAGS = {
    # engagement
    "seen live", "favorites", "favourite", "love", "loved", "awesome", "good", "cool",
    # demographic
    "male vocalists", "female vocalists", "singer-songwriter",
    # nationality (filtered by default)
    "american", "british", "german", "swedish", "french", "canadian", "australian",
    # era
    "00s", "90s", "80s", "70s", "60s",
    # misc
    "all", "music", "albums i own",
}

_MIN_WEIGHT = 10  # Last.fm 0–100 scale
_TOP_N = 3


def _clean_tags(raw_tags) -> list[dict]:
    """Normalize raw tag list/dict from API response into [{name, weight}, ...]."""
    if isinstance(raw_tags, dict):
        raw_tags = [raw_tags]
    if not raw_tags:
        return []

    result = []
    for t in raw_tags:
        name = (t.get("name") or "").strip().lower()
        try:
            weight = int(t.get("count", t.get("weight", 0)) or 0)
        except (TypeError, ValueError):
            weight = 0
        if not name:
            continue
        if name in NOISE_TAGS:
            continue
        if weight < _MIN_WEIGHT:
            continue
        result.append({"name": name, "weight": weight})

    result.sort(key=lambda x: -x["weight"])
    return result[:_TOP_N]


def get_track_tags(client, artist: str, title: str) -> list[dict]:
    """Return top genre tags (weight ≥ 10, not in NOISE_TAGS, top 3) for a track.

    Returns [] on error (error 6 = not found logged at DEBUG; others at WARNING).
    Each entry: {"name": str, "weight": int}
    """
    from lastfm.client import LastFMNotFound
    try:
        data = client.call("track.getTopTags", artist=artist, track=title)
    except LastFMNotFound:
        logger.debug("lastfm.tags: track not found — %s / %s", artist, title)
        return []
    except Exception:
        logger.warning("lastfm.tags: get_track_tags failed for %s / %s", artist, title, exc_info=True)
        return []

    raw = (data.get("toptags", {}) or {}).get("tag", []) or []
    return _clean_tags(raw)


def get_artist_tags(client, artist: str) -> list[dict]:
    """Return top genre tags for an artist.

    Returns [] on error.
    Each entry: {"name": str, "weight": int}
    """
    from lastfm.client import LastFMNotFound
    try:
        data = client.call("artist.getTopTags", artist=artist)
    except LastFMNotFound:
        logger.debug("lastfm.tags: artist not found — %s", artist)
        return []
    except Exception:
        logger.warning("lastfm.tags: get_artist_tags failed for %s", artist, exc_info=True)
        return []

    raw = (data.get("toptags", {}) or {}).get("tag", []) or []
    return _clean_tags(raw)


def build_genre_profile(artist_tag_sets: list[list[dict]]) -> dict[str, float]:
    """Build a normalized genre fingerprint from a collection of artist tag sets.

    Algorithm:
    1. Normalize each artist's tag set to their own max weight (each artist contributes equally).
    2. Sum across all artists.
    3. Re-normalize total to sum to 1.0.

    Returns {} when all input tag sets are empty.
    """
    accumulated: dict[str, float] = {}

    for tags in artist_tag_sets:
        if not tags:
            continue
        max_weight = max(t["weight"] for t in tags)
        if max_weight <= 0:
            continue
        for t in tags:
            normalized = t["weight"] / max_weight
            accumulated[t["name"]] = accumulated.get(t["name"], 0.0) + normalized

    total = sum(accumulated.values())
    if total <= 0:
        return {}

    return {tag: score / total for tag, score in sorted(
        accumulated.items(), key=lambda kv: -kv[1]
    )}
