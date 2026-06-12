import logging
import math

logger = logging.getLogger(__name__)


def _is_not_owned(artist: dict) -> bool:
    return str(artist.get("id")) == "-1"


def get_sc_followings(soundcloud_client, user_id):
    """Import and call soundcloud.discovery.get_followings. Returns [] on failure."""
    try:
        from soundcloud.discovery import get_followings
        users = get_followings(soundcloud_client, user_id)
        return [{"name": u["username"], "id": "-1"} for u in users]
    except Exception:
        logger.warning("expand: SC get_followings failed", exc_info=True)
        return []


def get_sc_related(soundcloud_client, track_id):
    """Import and call soundcloud.discovery.get_related. Returns [] on failure."""
    try:
        from soundcloud.discovery import get_related
        tracks = get_related(soundcloud_client, track_id)
        return [{"name": t["artist"], "id": "-1"} for t in tracks]
    except Exception:
        logger.warning("expand: SC get_related failed", exc_info=True)
        return []


def expand_similar(subsonic, seeds, per_seed: int = 20, lastfm_client=None,
                   soundcloud_client=None):
    """Seeds -> scored not-owned similar artists.

    When lastfm_client is provided, uses Last.fm artist.getSimilar (limit=100)
    instead of Navidrome getArtistInfo2.

    When soundcloud_client is provided, merges SC get_followings (for first seed's
    SC user id) and get_related results alongside Last.fm / Navidrome candidates.
    """
    seed_names = {s["name"].casefold() for s in seeds}
    # When using Last.fm, Navidrome's id==-1 check isn't available — build owned set once.
    owned_names = subsonic.get_all_artist_names() if lastfm_client is not None else set()
    scores: dict = {}
    canonical: dict = {}

    for seed in seeds:
        try:
            if lastfm_client is not None:
                sims = _expand_via_lastfm(lastfm_client, seed["name"])
            else:
                sid = seed.get("id")
                if not sid:
                    sid = subsonic.find_artist_id(seed["name"])
                if not sid:
                    logger.warning("expand: no Navidrome id for %s — skipping", seed.get("name"))
                    continue
                sims = subsonic.get_artist_info2(sid, count=per_seed)
        except Exception:
            logger.warning("expand: failed for %s — skipping", seed.get("name"))
            continue

        for sim in sims:
            name = sim.get("name")
            if not name:
                continue
            if lastfm_client is not None:
                cf = name.casefold()
                if cf in seed_names or cf in owned_names:
                    continue
            else:
                if not _is_not_owned(sim):
                    continue
                if name.casefold() in seed_names:
                    continue

            key = name.casefold()
            if key not in canonical:
                canonical[key] = name
            # Last.fm path: accumulate match scores for richer ranking.
            # Navidrome path: match is absent, treat each co-occurrence as 1.
            match_val = sim.get("match", 1.0)
            seed_weight = seed.get("weight", 1.0)
            scores[key] = scores.get(key, 0.0) + match_val * seed_weight

    # SC lens: merge followings and related
    if soundcloud_client is not None:
        # Use the first seed's SC user id (if configured)
        sc_user_id = getattr(soundcloud_client, "sc_user_id", None)
        if sc_user_id:
            sc_followings = get_sc_followings(soundcloud_client, sc_user_id)
            for sim in sc_followings:
                name = sim.get("name")
                if name and name.casefold() not in seed_names:
                    key = name.casefold()
                    if key not in canonical:
                        canonical[key] = name
                    scores[key] = scores.get(key, 0) + 1

        # Related tracks from all seeds that have a track_id
        for seed in seeds:
            track_id = seed.get("track_id")
            if track_id:
                sc_related = get_sc_related(soundcloud_client, track_id)
                for sim in sc_related:
                    name = sim.get("name")
                    if name and name.casefold() not in seed_names:
                        key = name.casefold()
                        if key not in canonical:
                            canonical[key] = name
                        scores[key] = scores.get(key, 0) + 1

    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"name": canonical[k], "score": s} for k, s in ranked]


def _expand_via_lastfm(lastfm_client, artist_name: str) -> list:
    try:
        import sys
        import os
        _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if _root not in sys.path:
            sys.path.insert(0, _root)
        from lastfm.similar import get_similar_artists
        sims = get_similar_artists(lastfm_client, artist_name, limit=100)
        return [{"name": s["name"], "id": "-1", "match": s["match"]} for s in sims]
    except Exception:
        logger.warning("expand: Last.fm similar_artists failed for %s", artist_name, exc_info=True)
        return []


def enrich_artist_info(lastfm_client, artists, min_listeners: int = 5000):
    """Fetch artist.getInfo per artist; filter by listener floor; rescale score.

    Adds: listeners (int), top_track (str|None) to each artist dict.
    Returns filtered list — artists below min_listeners are dropped.
    Artists where the API call fails are KEPT (don't silently empty the list).
    """
    from lastfm.similar import get_artist_top_tracks

    enriched = []
    for a in artists:
        keep = True
        try:
            info = lastfm_client.call("artist.getInfo", artist=a["name"])
            listeners = int(
                (info.get("artist") or {})
                .get("stats", {})
                .get("listeners", 0)
            )
            a["listeners"] = listeners
            if listeners < min_listeners:
                keep = False
        except Exception:
            logger.warning("enrich_artist_info: getInfo failed for %s — keeping",
                           a["name"], exc_info=True)
            a["listeners"] = 0  # unknown; keep to avoid empty list on API failure

        if not keep:
            continue

        # Rescale score: similarity × log10(listeners) — keeps similarity primary
        if a.get("listeners", 0) > 0:
            a["score"] = a.get("score", 1.0) * math.log10(max(a["listeners"], 10))

        # Top track — for targeted YouTube search; None falls back to "official audio"
        try:
            tracks = get_artist_top_tracks(lastfm_client, a["name"], limit=1)
            a["top_track"] = tracks[0]["title"] if tracks else None
        except Exception:
            a["top_track"] = None

        enriched.append(a)

    return enriched

