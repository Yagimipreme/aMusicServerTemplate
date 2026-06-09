import logging

logger = logging.getLogger(__name__)


def _is_not_owned(artist: dict) -> bool:
    # Navidrome returns id == "-1" (or -1) for similar artists not in the library.
    return str(artist.get("id")) == "-1"


def expand_similar(subsonic, seeds, per_seed: int = 20):
    """Seeds -> scored not-owned similar artists (score = how many seeds suggested them)."""
    seed_names = {s["name"].casefold() for s in seeds}
    scores: dict[str, int] = {}
    canonical: dict[str, str] = {}  # casefold -> first-seen display name
    for seed in seeds:
        try:
            sims = subsonic.get_artist_info2(seed["id"], count=per_seed)
        except Exception:
            logger.warning("expand: artist_info2 failed for %s — skipping", seed.get("name"))
            continue
        for sim in sims:
            name = sim.get("name")
            if not name or not _is_not_owned(sim):
                continue
            if name.casefold() in seed_names:
                continue
            key = name.casefold()
            if key not in canonical:
                canonical[key] = name
            scores[key] = scores.get(key, 0) + 1
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"name": canonical[k], "score": s} for k, s in ranked]
