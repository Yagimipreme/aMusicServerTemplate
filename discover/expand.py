import logging

logger = logging.getLogger(__name__)


def _is_not_owned(artist: dict) -> bool:
    # Navidrome returns id == "-1" (or -1) for similar artists not in the library.
    return str(artist.get("id")) == "-1"


def expand_similar(subsonic, seeds, per_seed: int = 20, lastfm_client=None):
    """Seeds -> scored not-owned similar artists (score = how many seeds suggested them).

    When lastfm_client is provided, uses Last.fm artist.getSimilar (limit=100)
    instead of Navidrome getArtistInfo2. Falls back to Navidrome if Last.fm call
    fails or lastfm_client is absent.
    """
    seed_names = {s["name"].casefold() for s in seeds}
    scores: dict[str, int] = {}
    canonical: dict[str, str] = {}  # casefold -> first-seen display name

    for seed in seeds:
        try:
            if lastfm_client is not None:
                sims = _expand_via_lastfm(lastfm_client, seed["name"])
            else:
                sims = subsonic.get_artist_info2(seed["id"], count=per_seed)
        except Exception:
            logger.warning("expand: failed for %s — skipping", seed.get("name"))
            continue

        for sim in sims:
            name = sim.get("name")
            if not name:
                continue
            # For Last.fm results, we have no library-owned check via id;
            # filter out seeds but keep everything else (ownership is checked later).
            if lastfm_client is not None:
                if name.casefold() in seed_names:
                    continue
            else:
                if not _is_not_owned(sim):
                    continue
                if name.casefold() in seed_names:
                    continue

            key = name.casefold()
            if key not in canonical:
                canonical[key] = name
            scores[key] = scores.get(key, 0) + 1

    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"name": canonical[k], "score": s} for k, s in ranked]


def _expand_via_lastfm(lastfm_client, artist_name: str) -> list[dict]:
    """Call lastfm.similar.get_similar_artists and return dicts compatible with expand_similar."""
    try:
        import sys
        import os
        _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if _root not in sys.path:
            sys.path.insert(0, _root)
        from lastfm.similar import get_similar_artists
        sims = get_similar_artists(lastfm_client, artist_name, limit=100)
        # Return in the same dict shape used by Navidrome (name key always present)
        return [{"name": s["name"], "id": "-1"} for s in sims]
    except Exception:
        logger.warning("expand: Last.fm similar_artists failed for %s", artist_name, exc_info=True)
        return []
