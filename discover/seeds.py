import logging

logger = logging.getLogger(__name__)


def collect_seeds(subsonic, limit: int = 20, lastfm_client=None,
                  lastfm_username: str = "", lastfm_period: str = "1month"):
    """Ranked owned artists to seed discovery (most-played first).

    When lastfm_client and lastfm_username are provided, blends Navidrome
    play counts with Last.fm scrobble history:
      - Artists in both sources get a boosted rank (appear in both → front)
      - Last.fm-only artists fill remaining slots up to `limit`
    If the Last.fm call fails, falls back silently to Navidrome-only.
    """
    artists = subsonic.get_frequent_artists(size=max(limit, 50))
    nav_artists = artists[:limit]

    if not lastfm_client or not lastfm_username:
        return nav_artists

    try:
        import sys
        import os
        _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if _root not in sys.path:
            sys.path.insert(0, _root)
        from lastfm.seeds import get_top_artists
        lfm_names = get_top_artists(
            lastfm_client,
            lastfm_username,
            period=lastfm_period,
            limit=limit,
        )
    except Exception:
        logger.warning("discover.seeds: Last.fm fetch failed — using Navidrome seeds only",
                       exc_info=True)
        return nav_artists

    if not lfm_names:
        return nav_artists

    nav_names_cf = {a["name"].casefold(): a for a in nav_artists}
    lfm_names_cf = [n.casefold() for n in lfm_names]

    # First: artists that appear in both sources (boosted)
    merged = []
    seen = set()
    for a in nav_artists:
        cf = a["name"].casefold()
        if cf in set(lfm_names_cf):
            merged.append(a)
            seen.add(cf)

    # Second: Navidrome-only artists
    for a in nav_artists:
        cf = a["name"].casefold()
        if cf not in seen:
            merged.append(a)
            seen.add(cf)

    # Third: Last.fm-only artists to fill up to limit
    for name in lfm_names:
        if len(merged) >= limit:
            break
        cf = name.casefold()
        if cf not in seen:
            merged.append({"id": None, "name": name})
            seen.add(cf)

    return merged[:limit]
