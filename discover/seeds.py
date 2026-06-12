import logging
import os

logger = logging.getLogger(__name__)


def collect_seeds(subsonic, limit: int = 20, lastfm_client=None,
                  lastfm_username: str = "", lastfm_period: str = "1month",
                  lastfm_periods=None, seed_playlist: str = ""):
    """Ranked owned artists to seed discovery (most-played first).

    When lastfm_client and lastfm_username are provided, blends Navidrome
    play counts with Last.fm scrobble history:
      - Artists in both sources get a boosted rank (appear in both → front)
      - Artists appearing in multiple Last.fm periods get further priority
      - Last.fm-only artists fill remaining slots up to `limit`
    If the Last.fm call fails, falls back silently to Navidrome-only.

    lastfm_periods: list of period strings e.g. ["7day", "overall"]. When
    provided, overrides lastfm_period and fetches each period separately,
    boosting artists that appear across multiple periods.

    seed_playlist: when set, use songs from this named Navidrome playlist as
    the play-count source instead of getAlbumList2 (album-level) data.
    """
    if seed_playlist:
        artists = subsonic.get_playlist_artists(seed_playlist)
    else:
        artists = subsonic.get_frequent_artists(size=max(limit, 50))
    # Normalize play_count to [0, 1] weights; missing/zero play counts get 0.0
    _max_pc = max((a.get("play_count", 0) for a in artists), default=1) or 1
    for a in artists:
        a["weight"] = (a.get("play_count", 0) or 0) / _max_pc
    nav_artists = artists[:limit]

    if not lastfm_client or not lastfm_username:
        return nav_artists

    periods = lastfm_periods if lastfm_periods else [lastfm_period]

    try:
        import sys
        import os
        _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if _root not in sys.path:
            sys.path.insert(0, _root)
        from lastfm.seeds import get_top_artists

        # Fetch each period; track how many periods each artist appears in
        period_counts: dict = {}   # casefold_name -> count of periods
        canonical: dict = {}       # casefold_name -> display name
        for period in periods:
            names = get_top_artists(lastfm_client, lastfm_username,
                                    period=period, limit=limit)
            for name in names:
                cf = name.casefold()
                period_counts[cf] = period_counts.get(cf, 0) + 1
                if cf not in canonical:
                    canonical[cf] = name

        if not period_counts:
            return nav_artists

        # Ranked by period_count desc (most cross-period artists first)
        lfm_ranked = sorted(period_counts.keys(), key=lambda cf: -period_counts[cf])
        lfm_names = [canonical[cf] for cf in lfm_ranked]
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
            # weight=1.0: Last.fm-only artists have no local play data but are strong
            # discovery signals — treat them equal to the most-played owned artist.
            merged.append({"id": None, "name": name, "play_count": 0, "weight": 1.0})
            seen.add(cf)

    return merged[:limit]


def get_bootstrap_seeds(cfg, subsonic, lastfm_client=None):
    """Collect cold-start seeds from manual config, Spotify CSVs, and library frequency."""
    # Bootstrap seeds omit weight/play_count — the bootstrap path is cold-start
    # and has no play history. expand_similar defaults weight to 1.0 for all seeds.
    seeds = []

    # 1. Manual seeds (highest priority)
    manual = (cfg.get("discover") or {}).get("manual_seeds", [])
    seeds.extend(manual)

    # 2. Artists from Spotify CSVs
    csv_dir = cfg.get("spotify_playlists_dir", "")
    if csv_dir:
        seeds.extend(get_csv_artists(csv_dir, limit=30))

    # 3. Library frequency from Navidrome
    try:
        seeds.extend(get_library_artist_frequency(subsonic, limit=20))
    except Exception:
        logger.warning("discover.seeds: could not fetch library artist frequency", exc_info=True)

    # Dedup preserving insertion order; normalise to {"name": ..., "id": ...} dicts
    seen, result = set(), []
    for s in seeds:
        if isinstance(s, dict):
            name = s.get("name", "")
            sid = s.get("id")
        else:
            name = s
            sid = None
        cf = name.casefold()
        if name and cf not in seen:
            seen.add(cf)
            result.append({"name": name, "id": sid})

    return result[:20]


def get_csv_artists(csv_dir: str, limit: int = 30) -> list:
    """Return up to `limit` unique artist names from all *.csv files in csv_dir."""
    import glob, csv as csv_mod
    artists = []
    seen = set()
    for path in sorted(glob.glob(os.path.join(csv_dir, "*.csv"))):
        try:
            with open(path, encoding="utf-8", newline="") as f:
                reader = csv_mod.DictReader(f)
                for row in reader:
                    # Exportify CSVs have "Artist Name" or "Artist Name(s)"
                    artist = (row.get("Artist Name(s)") or row.get("Artist Name") or
                              row.get("artist") or "").strip()
                    # May be comma-separated list; take first
                    if "," in artist:
                        artist = artist.split(",")[0].strip()
                    if artist and artist.casefold() not in seen:
                        seen.add(artist.casefold())
                        artists.append(artist)
                        if len(artists) >= limit:
                            return artists
        except Exception:
            logger.debug("get_csv_artists: could not read %s", path, exc_info=True)
    return artists


def _is_junk_artist_name(name: str) -> bool:
    """True for names that are clearly not real artists (untagged, channel-style)."""
    if not name or len(name) < 2:
        return True
    # "[Unknown Artist]", "[unknown]", etc.
    if name.startswith("[") and name.endswith("]"):
        return True
    return False


def get_library_artist_frequency(subsonic, limit: int = 20) -> list:
    """Return up to `limit` artist dicts (name, id) sorted by play count in the Navidrome library."""
    try:
        artists = subsonic.get_frequent_artists(size=200)
        # get_frequent_artists already returns them sorted by play count
        return [a for a in artists if a.get("name") and not _is_junk_artist_name(a["name"])][:limit]
    except Exception:
        logger.warning("discover.seeds: get_library_artist_frequency failed", exc_info=True)
        return []
