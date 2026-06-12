import json
import logging
import os
import datetime

from discover.seeds import collect_seeds, genre_seed_artists
from discover.expand import expand_similar, enrich_artist_info
from discover.resolve import resolve_tracks
from discover.dedupe import filter_fresh, track_key
from discover.acquire import acquire
from discover.assemble import write_weekly_mix, read_playlist_basenames

logger = logging.getLogger(__name__)

_STATE_PATH_DEFAULT = os.path.join(os.path.dirname(__file__), "..", "discover_state.json")


def run_weekly(deps, count=30, seed_limit=20, per_seed=20, per_artist=1,
               playlist_name="Weekly Mix", lastfm_client=None,
               lastfm_username="", lastfm_period="1month", lastfm_periods=None,
               playlist_cap=100, min_artist_listeners=5000,
               candidate_oversample=3, seed_playlist=""):
    """Run the full pipeline once and (re)build the Weekly Mix playlist.

    deps must provide: subsonic, search_fn, download_fn, state, song_dir.
    Returns {"acquired": int, "m3u": path|None}.
    """
    seeds = collect_seeds(deps.subsonic, limit=seed_limit,
                          lastfm_client=lastfm_client,
                          lastfm_username=lastfm_username,
                          lastfm_period=lastfm_period,
                          lastfm_periods=lastfm_periods,
                          seed_playlist=seed_playlist)
    logger.info("discover: %d seeds", len(seeds))

    artists = expand_similar(deps.subsonic, seeds, per_seed=per_seed,
                             lastfm_client=lastfm_client)
    logger.info("discover: %d not-owned similar artists", len(artists))

    if lastfm_client is not None:
        k = seed_limit * candidate_oversample
        artists = sorted(artists, key=lambda a: -a.get("score", 0))[:k]
        logger.info("discover: trimmed to top %d candidates before enrichment", len(artists))
        artists = enrich_artist_info(lastfm_client, artists,
                                     min_listeners=min_artist_listeners)
        logger.info("discover: %d candidates after listener floor", len(artists))

    candidates = resolve_tracks(deps.search_fn, artists, per_artist=per_artist)
    fresh = filter_fresh(deps.subsonic.song_exists, deps.state, candidates)
    logger.info("discover: %d fresh candidates", len(fresh))

    acquired_paths = []
    for c in fresh:
        if len(acquired_paths) >= count:
            break
        paths = acquire(deps.download_fn, c)
        if not paths:
            continue
        remaining = count - len(acquired_paths)
        acquired_paths.extend(paths[:remaining])
        deps.state.add(track_key(c["artist"], c["title"]))

    m3u = None
    if acquired_paths:
        m3u = write_weekly_mix(deps.song_dir, acquired_paths, name=playlist_name,
                               cap=playlist_cap)
        try:
            deps.subsonic.start_scan()
        except Exception:
            logger.exception("discover: scan trigger failed")

    deps.state.save(stamp_last_run=True)
    return {"acquired": len(acquired_paths), "m3u": m3u}


def lastfm_is_ready(client, username, cfg):
    """Return True if the user has enough Last.fm history for Weekly Mix."""
    # Check cached result first
    state_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "discover_state.json"))
    try:
        with open(state_path, encoding="utf-8") as f:
            state = json.load(f)
        if state.get("lastfm_ready"):
            return True
    except Exception:
        pass

    if not client or not username:
        return False

    threshold = (cfg.get("discover") or {}).get("lastfm_readiness", {})
    min_scrobbles = threshold.get("min_scrobbles", 100)
    min_artists = threshold.get("min_unique_artists", 15)

    try:
        # user.getRecentTracks with limit=1 gives total scrobble count in @attr.total
        recent = client.call("user.getRecentTracks", user=username, limit=1)
        total = int((recent.get("recenttracks", {}).get("@attr") or {}).get("total", 0))
        if total < min_scrobbles:
            return False
    except Exception:
        logger.warning("discover.engine: lastfm_is_ready scrobble check failed", exc_info=True)
        return False

    try:
        # user.getTopArtists to count unique artists (may 500 for very new accounts)
        top = client.call("user.getTopArtists", user=username, period="overall", limit=500)
        artists = top.get("topartists", {}).get("artist", [])
        if not isinstance(artists, list):
            artists = [artists] if artists else []
        if len(artists) < min_artists:
            return False
    except Exception:
        # Last.fm returns 500 for accounts with very few plays — skip artist-count gate
        logger.info("discover.engine: user.getTopArtists unavailable, skipping artist-count check")

    # Cache the positive result
    try:
        try:
            with open(state_path, encoding="utf-8") as f:
                state = json.load(f)
        except Exception:
            state = {}
        state["lastfm_ready"] = True
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception:
        pass
    return True


def run_mix(deps, cfg):
    """Route to bootstrap or Last.fm Weekly Mix based on readiness gate."""
    disc = cfg.get("discover") or {}
    lastfm_client = getattr(deps, "lastfm_client", None)
    lastfm_username = cfg.get("lastfm_username", "")

    if lastfm_client and lastfm_is_ready(lastfm_client, lastfm_username, cfg):
        playlist_name = disc.get("playlist_name", "Weekly Mix")
        count = disc.get("weekly_count", 30)
        seed_limit = disc.get("seed_artist_count", 20)
        lastfm_period = disc.get("lastfm_period", "1month")
        lastfm_periods = disc.get("lastfm_periods") or None
        playlist_cap = int(disc.get("playlist_cap", 100))
        min_artist_listeners = int(disc.get("min_artist_listeners", 5000))
        candidate_oversample = int(disc.get("candidate_oversample", 3))
        seed_playlist = disc.get("seed_playlist", "")
        return run_weekly(deps, count=count, seed_limit=seed_limit,
                         playlist_name=playlist_name,
                         lastfm_client=lastfm_client,
                         lastfm_username=lastfm_username,
                         lastfm_period=lastfm_period,
                         lastfm_periods=lastfm_periods,
                         playlist_cap=playlist_cap,
                         min_artist_listeners=min_artist_listeners,
                         candidate_oversample=candidate_oversample,
                         seed_playlist=seed_playlist)
    else:
        # Bootstrap path
        playlist_name = disc.get("bootstrap_playlist_name", "Starter Mix")
        count = disc.get("weekly_count", 30)
        from discover.seeds import get_bootstrap_seeds
        seeds = get_bootstrap_seeds(cfg, deps.subsonic, lastfm_client)
        logger.info("discover: bootstrap seeds: %d", len(seeds))
        if not seeds:
            logger.warning("discover: no bootstrap seeds available")
            deps.state.save(stamp_last_run=True)
            return {"acquired": 0, "m3u": None}
        # reuse the existing expansion/acquire pipeline
        artists = expand_similar(deps.subsonic, seeds, per_seed=20,
                                 lastfm_client=lastfm_client)
        if lastfm_client is not None:
            artists = sorted(artists, key=lambda a: -a.get("score", 0))[:60]
            artists = enrich_artist_info(lastfm_client, artists, min_listeners=5000)
            logger.info("discover: bootstrap — %d candidates after listener floor", len(artists))
        candidates = resolve_tracks(deps.search_fn, artists, per_artist=1)
        fresh = filter_fresh(deps.subsonic.song_exists, deps.state, candidates)
        acquired_paths = []
        for c in fresh:
            if len(acquired_paths) >= count:
                break
            paths = acquire(deps.download_fn, c)
            if not paths:
                continue
            remaining = count - len(acquired_paths)
            acquired_paths.extend(paths[:remaining])
            deps.state.add(track_key(c["artist"], c["title"]))
        m3u = None
        if acquired_paths:
            m3u = write_weekly_mix(deps.song_dir, acquired_paths, name=playlist_name,
                                   cap=int(disc.get("playlist_cap", 100)))
            try:
                deps.subsonic.start_scan()
            except Exception:
                logger.exception("discover: scan trigger failed")
        deps.state.save(stamp_last_run=True)
        return {"acquired": len(acquired_paths), "m3u": m3u}


def _existing_playlist_basenames(song_dir: str, name: str) -> set:
    """Return set of basenames already in the named playlist's m3u."""
    return set(read_playlist_basenames(song_dir, name))


def run_profile(deps, cfg, profile):
    """Run one mix profile: blend of newly-acquired + owned tracks. Spec §Engine."""
    disc = cfg.get("discover") or {}
    quality = {**disc, **(profile.get("quality") or {})}
    count = max(1, int(profile["count"]))
    cap = max(count, int(profile["cap"]))
    new_ratio = float(profile.get("new_ratio", 1.0))
    new_count = round(count * new_ratio)
    lib_count = count - new_count
    seeds_cfg = profile.get("seeds") or {}
    mode = seeds_cfg.get("mode", "history")
    lastfm_client = getattr(deps, "lastfm_client", None)
    lastfm_username = cfg.get("lastfm_username", "")

    acquired_paths = []
    if new_count > 0:
        seeds = []
        if mode == "history":
            if not lastfm_client or not lastfm_is_ready(lastfm_client, lastfm_username, cfg):
                if lib_count == 0:
                    return {"profile": profile["id"], "status": "skipped",
                            "reason": "lastfm not ready"}
                new_count, lib_count = 0, count
            else:
                seeds = collect_seeds(deps.subsonic, limit=int(quality.get("seed_artist_count", 20)),
                                      lastfm_client=lastfm_client, lastfm_username=lastfm_username,
                                      lastfm_period=quality.get("lastfm_period", "1month"),
                                      lastfm_periods=quality.get("lastfm_periods") or None,
                                      seed_playlist=seeds_cfg.get("playlist", ""))
        elif mode == "genre":
            seeds = genre_seed_artists(lastfm_client, seeds_cfg.get("genres") or []) if lastfm_client else []
        elif mode == "manual":
            seeds = [{"id": "-1", "name": a} for a in seeds_cfg.get("artists") or []]
        elif mode == "playlist":
            seeds = collect_seeds(deps.subsonic, limit=int(quality.get("seed_artist_count", 20)),
                                  lastfm_client=lastfm_client, lastfm_username=lastfm_username,
                                  seed_playlist=seeds_cfg.get("playlist", ""))
        if new_count > 0 and seeds:
            artists = expand_similar(deps.subsonic, seeds, per_seed=20, lastfm_client=lastfm_client)
            if lastfm_client is not None:
                artists = sorted(artists, key=lambda a: -a.get("score", 0))[:60]
                artists = enrich_artist_info(lastfm_client, artists,
                                             min_listeners=int(quality.get("min_artist_listeners", 5000)))
            candidates = resolve_tracks(deps.search_fn, artists, per_artist=1)
            fresh = filter_fresh(deps.subsonic.song_exists, deps.state, candidates)
            for c in fresh:
                if len(acquired_paths) >= new_count:
                    break
                paths = acquire(deps.download_fn, c)
                if not paths:
                    continue
                acquired_paths.extend(paths[: new_count - len(acquired_paths)])
                deps.state.add(track_key(c["artist"], c["title"]))

    # library share + backfill of any new-share shortfall
    lib_paths = []
    lib_needed = count - len(acquired_paths) if lib_count > 0 or len(acquired_paths) < new_count else 0
    lib_needed = min(lib_needed, count - len(acquired_paths))
    if lib_needed > 0:
        existing = _existing_playlist_basenames(deps.song_dir, profile["name"])
        from discover.library_pick import select_library_tracks
        picks = select_library_tracks(deps.subsonic, profile, existing, lib_needed)
        lib_paths = [s["path"] for s in picks]

    m3u = None
    if acquired_paths or lib_paths:
        m3u = write_weekly_mix(deps.song_dir, acquired_paths + lib_paths,
                               name=profile["name"], cap=cap)
        try:
            deps.subsonic.start_scan()
        except Exception:
            logger.exception("discover: scan trigger failed")
    deps.state.save(stamp_last_run=False)
    return {"profile": profile["id"], "acquired": len(acquired_paths),
            "library_added": len(lib_paths), "m3u": m3u}


def run_daily(deps, cfg):
    """Run the Daily Mix pipeline: a small daily discovery drop.

    Delegates to run_profile() using the daily profile built from config.
    Returns {"status": "skipped", "reason": ...} if Last.fm is not ready.
    Returns {"acquired": int, "m3u": str|None} on success.
    Calls state.save(stamp_last_run=False) so the weekly last_run is not clobbered.
    """
    disc = cfg.get("discover") or {}
    daily = disc.get("daily") or {}
    count = max(1, int(daily.get("count", 7)))
    window_days = max(1, int(daily.get("window_days", 7)))
    playlist_name = daily.get("playlist_name", "Daily Mix")
    cap = count * window_days

    profile = {
        "id": "daily",
        "name": playlist_name,
        "enabled": bool(daily.get("enabled", True)),
        "auto_generated": False,
        "schedule": {"cadence": "daily", "run_day": "",
                     "run_hour": int(daily.get("run_hour", 7))},
        "count": count,
        "cap": cap,
        "new_ratio": 1.0,
        "seeds": {"mode": "history", "genres": [], "artists": [], "playlist": ""},
        "quality": {},
    }
    result = run_profile(deps, cfg, profile)
    # Normalise: run_profile returns {"profile": ..., "acquired": ..., "m3u": ...}
    # run_daily callers expect {"acquired": ..., "m3u": ...} or {"status": "skipped", ...}
    if result.get("status") == "skipped":
        return result
    return {"acquired": result.get("acquired", 0), "m3u": result.get("m3u"),
            "library_added": result.get("library_added", 0)}


def run_daily_from_config(project_root: str):
    """Build a Deps object from config.json at project_root and call run_daily().
    Used by CLI / manual trigger."""
    import sys
    import importlib.util
    sys.path.insert(0, project_root)
    from discover.config import load_config
    from discover.subsonic import Subsonic
    from discover.state import DiscoverState
    from discover.ytdlp_adapter import make_search_fn, make_download_fn
    cfg = load_config(os.path.join(project_root, "config.json"))
    disc = cfg.get("discover") or {}
    if not disc.get("enabled", True):
        return {"acquired": 0, "m3u": None, "reason": "discover disabled"}
    daily = disc.get("daily") or {}
    if not daily.get("enabled", False):
        return {"acquired": 0, "m3u": None, "reason": "daily disabled"}
    host = cfg.get("navidrome_url", "")
    user = cfg.get("navidrome_user", "")
    pw = cfg.get("navidrome_pass", "")
    if not host:
        return {"acquired": 0, "m3u": None, "reason": "navidrome_url not set"}
    subsonic = Subsonic(host, user, pw)

    dl_path = os.path.join(project_root, "scripts/sTownload/script_web.py")
    spec = importlib.util.spec_from_file_location("sTownload_web", dl_path)
    dl_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dl_mod)
    song_dir = dl_mod.get_config_song_dir()

    state_path = os.path.join(project_root, "discover_state.json")
    from discover.state import load_state
    ttl_days = int((cfg.get("discover") or {}).get("suggested_ttl_days", 90))
    state = load_state(state_path, ttl_days=ttl_days)

    lastfm_client = None
    lfm_key = cfg.get("lastfm_api_key", "")
    if lfm_key:
        try:
            from lastfm.client import LastFMClient
            lastfm_client = LastFMClient(lfm_key)
        except Exception:
            pass

    _oversample = int(disc.get("yt_oversample", 5))
    _extra_junk = frozenset(disc.get("junk_keywords", []))

    from types import SimpleNamespace
    deps = SimpleNamespace(
        subsonic=subsonic,
        state=state,
        song_dir=song_dir,
        search_fn=make_search_fn(oversample=_oversample, extra_junk_keywords=_extra_junk),
        download_fn=make_download_fn(lambda url: dl_mod.download_url(url, song_dir)),
        lastfm_client=lastfm_client,
    )

    return run_daily(deps, cfg)


def run_mix_from_config(project_root: str):
    """Build a Deps object from config.json at project_root and call run_mix().
    Used by --generate-mix CLI and the Starter Mix background trigger."""
    import sys
    import importlib.util
    sys.path.insert(0, project_root)
    from discover.config import load_config
    from discover.subsonic import Subsonic
    from discover.state import DiscoverState
    from discover.ytdlp_adapter import make_search_fn, make_download_fn
    cfg = load_config(os.path.join(project_root, "config.json"))
    disc = cfg.get("discover") or {}
    if not disc.get("enabled", True):
        return {"acquired": 0, "m3u": None, "reason": "discover disabled"}
    host = cfg.get("navidrome_url", "")
    user = cfg.get("navidrome_user", "")
    pw = cfg.get("navidrome_pass", "")
    if not host:
        return {"acquired": 0, "m3u": None, "reason": "navidrome_url not set"}
    subsonic = Subsonic(host, user, pw)

    dl_path = os.path.join(project_root, "scripts/sTownload/script_web.py")
    spec = importlib.util.spec_from_file_location("sTownload_web", dl_path)
    dl_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dl_mod)
    song_dir = dl_mod.get_config_song_dir()

    state = DiscoverState(os.path.join(project_root, "discover_state.json"))

    lastfm_client = None
    lfm_key = cfg.get("lastfm_api_key", "")
    if lfm_key:
        try:
            from lastfm.client import LastFMClient
            lastfm_client = LastFMClient(lfm_key)
        except Exception:
            pass

    _oversample = int(disc.get("yt_oversample", 5))
    _extra_junk = frozenset(disc.get("junk_keywords", []))

    class Deps:
        pass
    deps = Deps()
    deps.subsonic = subsonic
    deps.state = state
    deps.song_dir = song_dir
    deps.search_fn = make_search_fn(oversample=_oversample, extra_junk_keywords=_extra_junk)
    deps.download_fn = make_download_fn(lambda url: dl_mod.download_url(url, song_dir))
    deps.lastfm_client = lastfm_client

    return run_mix(deps, cfg)
