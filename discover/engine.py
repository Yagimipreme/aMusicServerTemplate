import logging

from discover.seeds import collect_seeds
from discover.expand import expand_similar
from discover.resolve import resolve_tracks
from discover.dedupe import filter_fresh, track_key
from discover.acquire import acquire
from discover.assemble import write_weekly_mix

logger = logging.getLogger(__name__)


def run_weekly(deps, count=30, seed_limit=20, per_seed=20, per_artist=1,
               playlist_name="Weekly Mix"):
    """Run the full pipeline once and (re)build the Weekly Mix playlist.

    deps must provide: subsonic, search_fn, download_fn, state, song_dir.
    Returns {"acquired": int, "m3u": path|None}.
    """
    seeds = collect_seeds(deps.subsonic, limit=seed_limit)
    logger.info("discover: %d seeds", len(seeds))

    artists = expand_similar(deps.subsonic, seeds, per_seed=per_seed)
    logger.info("discover: %d not-owned similar artists", len(artists))

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
        acquired_paths.extend(paths)
        deps.state.add(track_key(c["artist"], c["title"]))

    m3u = None
    if acquired_paths:
        m3u = write_weekly_mix(deps.song_dir, acquired_paths, name=playlist_name)
        deps.state.save()
        try:
            deps.subsonic.start_scan()
        except Exception:
            logger.exception("discover: scan trigger failed")

    return {"acquired": len(acquired_paths), "m3u": m3u}
