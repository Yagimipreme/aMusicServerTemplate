"""Flask-based HTTP server — replaces stdlib HTTPServer.

All existing routes are preserved 1:1. New routes added per spec.
"""
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import datetime
import importlib.util
import runpy
import shutil
import uuid

from flask import Flask, jsonify, redirect, render_template, request
from flask_cors import CORS

# ── Paths ──────────────────────────────────────────────────────────────────────

_SERVER_DIR   = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SERVER_DIR, "../../"))
_CONFIG_PATH  = os.path.join(_PROJECT_ROOT, "config.json")
_LOG_DIR      = os.path.join(_PROJECT_ROOT, "logs")
_TEMPLATE_DIR = os.path.join(_PROJECT_ROOT, "web", "templates")
_STATIC_DIR   = os.path.join(_PROJECT_ROOT, "web", "static")

# ── Follow feature paths + defaults ──────────────────────────────────────────

_FOLLOWS_PATH = os.path.join(_PROJECT_ROOT, "follows.json")
_FOLLOW_STATE_PATH = os.path.join(_PROJECT_ROOT, "follow_state.json")

_FOLLOW_DEFAULTS = {
    "enabled": True,
    "run_hour": 4,
    "lookback_days": 7,
    "default_backfill_days": 30,
    "playlist_name": "NEW RELEASES",
    "playlist_cap": 100,
    "notify": {"webhook_url": "", "ntfy_topic": ""},
}

# Resolve yt-dlp once at startup: prefer the venv's copy so it's always found
# even when the server is started from a shell without the venv activated.
_YT_DLP = shutil.which("yt-dlp") or os.path.join(os.path.dirname(sys.executable), "yt-dlp")

os.makedirs(_LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(_LOG_DIR, "server.log")

# ── Logging ────────────────────────────────────────────────────────────────────

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)
logger.addHandler(logging.StreamHandler())

# ── Flask app ──────────────────────────────────────────────────────────────────

app = Flask(
    __name__,
    template_folder=_TEMPLATE_DIR,
    static_folder=_STATIC_DIR,
    static_url_path="/static",
)
CORS(app)

if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# ── Import/download job registry ───────────────────────────────────────────────

_import_jobs: dict = {}  # job_id -> {total, done, errors, tracks: [{title, status}]}

# ── SC client readiness flag ──────────────────────────────────────────────────

_sc_client_ready = False

# ── Enrich state ──────────────────────────────────────────────────────────────

_enrich_running = threading.Lock()
_enrich_last_result: dict = {"status": "idle"}

_ENRICH_ALL_FIELDS = ("genre", "year", "album", "album_artist", "mbids", "cover_art")


def _enrich_fields(enrich_cfg):
    """Return the per-field config dict, mapping legacy only_missing_genre."""
    fields = enrich_cfg.get("fields")
    if fields:
        return fields
    only_missing_genre = enrich_cfg.get("only_missing_genre", True)
    built = {f: {"enabled": True, "only_missing": True}
             for f in _ENRICH_ALL_FIELDS}
    built["genre"]["only_missing"] = bool(only_missing_genre)
    return built


# ── Repair state ──────────────────────────────────────────────────────────────

_repair_running = threading.Lock()
_repair_last_result: dict = {"status": "idle"}

# ── Insights sync state ────────────────────────────────────────────────────────

_insights_running = threading.Lock()
_insights_last_result: dict = {"status": "idle"}

# ── Insights feature-sync state ────────────────────────────────────────────────

_insights_features_running = threading.Lock()
_insights_features_last_result: dict = {"status": "idle"}

# ── Discover run state ────────────────────────────────────────────────────────

_discover_running = threading.Lock()

# ── Follow run state ──────────────────────────────────────────────────────────

_follow_running = threading.Lock()

# ── Dedup state ───────────────────────────────────────────────────────────────

_dedup_running = threading.Lock()

# ── Acquire state ─────────────────────────────────────────────────────────────

_acquire_inflight, _acquire_lock = set(), threading.Lock()
_ACQUIRE_HOSTS = {"youtube.com", "www.youtube.com", "youtu.be", "music.youtube.com",
                  "soundcloud.com", "on.soundcloud.com", "m.soundcloud.com"}

# ── Config read-modify-write lock ─────────────────────────────────────────────
# RLock so nested callers (e.g. _load_mixes inside mixes_post) can re-enter.

_config_lock = threading.RLock()


# ── Business logic (unchanged from stdlib version) ────────────────────────────

def _build_discover_deps():
    from types import SimpleNamespace
    from discover.config import load_config
    from discover.subsonic import Subsonic
    from discover.state import load_state
    from discover.ytdlp_adapter import make_search_fn, make_download_fn

    cfg = load_config(_CONFIG_PATH)
    host = cfg.get("navidrome_url", "")
    user = cfg.get("navidrome_user", "")
    pw   = cfg.get("navidrome_pass", "")
    if not host or not user or not pw:
        logger.warning("[DISCOVER] navidrome creds missing — engine disabled")
        return None

    dl_path = os.path.join(_PROJECT_ROOT, "scripts/sTownload/script_web.py")
    spec = importlib.util.spec_from_file_location("sTownload_web", dl_path)
    dl_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dl_mod)
    song_dir = dl_mod.get_config_song_dir()

    lastfm_client = None
    lfm_key = cfg.get("lastfm_api_key", "")
    if lfm_key:
        try:
            from lastfm.client import LastFMClient
            lastfm_client = LastFMClient(lfm_key)
        except Exception:
            logger.warning("[DISCOVER] Last.fm client init failed", exc_info=True)

    state_path = os.path.join(_PROJECT_ROOT, "discover_state.json")
    ttl_days = int((cfg.get("discover") or {}).get("suggested_ttl_days", 90))
    disc_cfg = cfg.get("discover") or {}
    _oversample = int(disc_cfg.get("yt_oversample", 5))
    _extra_junk = frozenset(disc_cfg.get("junk_keywords", []))
    return SimpleNamespace(
        subsonic=Subsonic(host, user, pw),
        search_fn=make_search_fn(oversample=_oversample, extra_junk_keywords=_extra_junk),
        download_fn=make_download_fn(lambda url: dl_mod.download_url(url, song_dir)),
        state=load_state(state_path, ttl_days=ttl_days),
        song_dir=song_dir,
        lastfm_client=lastfm_client,
    )


def _build_follow_clients():
    """Return (mb_client, lb_client)."""
    from follow.musicbrainz import MusicBrainzClient
    from follow.listenbrainz import ListenBrainzClient
    return MusicBrainzClient(), ListenBrainzClient()


def _run_follow_once() -> dict:
    if not _follow_running.acquire(blocking=False):
        return {"status": "busy", "reason": "another follow run in progress"}
    try:
        from follow import store, fstate, runner
        deps = _build_discover_deps()
        if deps is None:
            return {"status": "disabled", "reason": "navidrome creds missing"}
        mb, lb = _build_follow_clients()
        follows = store.list_follows(_FOLLOWS_PATH)
        state = fstate.load(_FOLLOW_STATE_PATH)
        fc = _follow_cfg()
        result = runner.run_once(
            mb_client=mb, lb_client=lb, follows=follows, state=state,
            search_fn=deps.search_fn, download_fn=deps.download_fn,
            song_dir=deps.song_dir, cfg=fc)
        logger.info("[FOLLOW] run complete: %s", result)
        return {"status": "ok", **result}
    finally:
        _follow_running.release()


def _run_discover_once():
    """Legacy wrapper: runs the 'weekly' profile via _run_profile_once."""
    try:
        mixes = _load_mixes()
        profile = next((m for m in mixes if m.get("id") == "weekly"), None)
        if profile is None:
            # Fall back to run_mix for bootstrap path (no weekly profile in config)
            deps = _build_discover_deps()
            if deps is None:
                return {"status": "disabled", "reason": "navidrome creds missing"}
            from discover.engine import run_mix
            cfg = _get_config()
            result = run_mix(deps, cfg)
            logger.info("[DISCOVER] run complete: %s", result)
            return {"status": "ok", **result}
        result = _run_profile_once(profile)
        if result.get("status") == "skipped":
            # Last.fm not ready: fall back to run_mix bootstrap (Starter Mix), preserving old behavior
            logger.info("[DISCOVER] weekly profile skipped (lastfm not ready) — falling back to run_mix bootstrap")
            deps = _build_discover_deps()
            if deps is None:
                return {"status": "disabled", "reason": "navidrome creds missing"}
            from discover.engine import run_mix
            cfg = _get_config()
            mix_result = run_mix(deps, cfg)
            logger.info("[DISCOVER] bootstrap run complete: %s", mix_result)
            return {"status": "ok", **mix_result}
        if result.get("status") not in ("busy", "disabled", "error"):
            return {"status": "ok", **result}
        return result
    except Exception as e:
        logger.exception("[DISCOVER] run failed")
        return {"status": "error", "error": str(e)}


def _run_discover_daily_once():
    """Legacy wrapper: runs the 'daily' profile via _run_profile_once."""
    try:
        mixes = _load_mixes()
        profile = next((m for m in mixes if m.get("id") == "daily"), None)
        if profile is None:
            return {"status": "disabled", "reason": "daily profile not found"}
        result = _run_profile_once(profile)
        if result.get("status") not in ("busy", "disabled", "error"):
            return {"status": "ok", **result}
        return result
    except Exception as e:
        logger.exception("[DISCOVER-DAILY] run failed")
        return {"status": "error", "error": str(e)}


def _profile_next_run(profile: dict, now: datetime.datetime) -> datetime.datetime:
    """Compute the next scheduled run datetime for a profile.

    daily: today at run_hour (or tomorrow if already past).
    weekly: next run_day at run_hour (or next week if same day past hour).
    run_hour is clamped to [0, 23].
    """
    sched = profile.get("schedule") or {}
    cadence = sched.get("cadence", "daily")
    run_hour = max(0, min(23, int(sched.get("run_hour", 0))))

    if cadence == "daily":
        candidate = now.replace(hour=run_hour, minute=0, second=0, microsecond=0)
        if candidate <= now:
            candidate += datetime.timedelta(days=1)
        return candidate
    else:  # weekly
        day_map = {
            "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
            "friday": 4, "saturday": 5, "sunday": 6,
        }
        run_day = str(sched.get("run_day", "sunday")).lower()
        target_wd = day_map.get(run_day, 6)
        days_ahead = (target_wd - now.weekday()) % 7
        candidate = (now + datetime.timedelta(days=days_ahead)).replace(
            hour=run_hour, minute=0, second=0, microsecond=0)
        if candidate <= now:
            candidate += datetime.timedelta(weeks=1)
        return candidate


def _atomic_write_config(cfg: dict) -> None:
    """Write config atomically using .tmp + os.replace."""
    tmp_path = _CONFIG_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    os.replace(tmp_path, _CONFIG_PATH)


def _load_mixes() -> list:
    """Load and migrate mixes list from config, persisting migration on first load."""
    with _config_lock:
        cfg = _get_config()
        from discover.profiles import migrate_config
        mixes = migrate_config(cfg)
        if "mixes" not in cfg:                       # persist migration once
            cfg["mixes"] = mixes
            _atomic_write_config(cfg)
    return mixes


def _persist_next_runs(next_runs: dict) -> None:
    """Persist next_runs dict into discover_state.json, preserving other keys."""
    state_path = os.path.join(_PROJECT_ROOT, "discover_state.json")
    state = {}
    try:
        with open(state_path, encoding="utf-8") as f:
            state = json.load(f)
    except Exception:
        pass
    state["next_runs"] = next_runs
    tmp = state_path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, state_path)
    except Exception:
        logger.warning("[MIXES] Could not persist next_runs", exc_info=True)


_mix_wake = threading.Event()


def _profiles_due_now(profiles: list, next_runs: dict, now: datetime.datetime) -> list:
    """Return profiles whose precomputed next_run is <= wall-clock now.

    Uses the injected `now` timestamp so callers can re-read the clock per
    scheduler iteration and avoid stale-`now` issues (Issue 7).
    """
    due = []
    for p in profiles:
        nr = next_runs.get(p["id"])
        if nr is not None and nr <= now:
            due.append(p)
    return due


def _record_last_run(profile_id: str) -> None:
    state_path = os.path.join(_PROJECT_ROOT, "discover_state.json")
    try:
        try:
            with open(state_path, encoding="utf-8") as f:
                state = json.load(f)
        except Exception:
            state = {}
        state.setdefault("last_runs", {})[profile_id] = datetime.datetime.now().isoformat()
        tmp = state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, state_path)
    except Exception:
        logger.warning("[MIXES] could not record last_run for %s", profile_id, exc_info=True)


def _run_profile_once(profile) -> dict:
    if not _discover_running.acquire(blocking=False):
        return {"status": "busy", "reason": "another discover run in progress"}
    try:
        deps = _build_discover_deps()
        if deps is None:
            return {"status": "disabled", "reason": "navidrome creds missing"}
        from discover.engine import run_profile
        result = run_profile(deps, _get_config(), profile)
        logger.info("[MIXES] %s run complete: %s", profile["id"], result)
        _record_last_run(profile["id"])
        return result
    except Exception as e:
        logger.exception("[MIXES] %s run failed", profile.get("id"))
        return {"status": "error", "error": str(e)}
    finally:
        _discover_running.release()


def _bootstrap_genre_profiles(subsonic) -> int:
    """Auto-generate genre profiles at startup when none exist yet.

    Spec §Auto-generation trigger: once at server start when no auto_generated
    profiles exist AND the library has genres. Returns count of profiles created.
    """
    try:
        genres = subsonic.get_genres()
        if not genres:
            return 0
        with _config_lock:
            mixes = _load_mixes()
            if any(m.get("auto_generated") for m in mixes):
                return 0  # already bootstrapped
            from discover.profiles import suggest_genre_profiles
            new_profiles = suggest_genre_profiles(subsonic, mixes)
            if not new_profiles:
                return 0
            existing_ids = {m["id"] for m in mixes}
            added = [p for p in new_profiles if p["id"] not in existing_ids]
            if not added:
                return 0
            mixes.extend(added)
            cfg = _get_config()
            cfg["mixes"] = mixes
            _atomic_write_config(cfg)
        logger.info("[MIXES] bootstrapped %d genre profile(s): %s",
                    len(added), [p["id"] for p in added])
        return len(added)
    except Exception:
        logger.warning("[MIXES] genre bootstrap failed", exc_info=True)
        return 0


def _mix_scheduler_loop():
    """Unified profile scheduler loop — replaces _discover_weekly_loop + _discover_daily_loop."""
    # Initial run: if discover_state.json has no last_run and library has songs, run weekly
    state_path = os.path.join(_PROJECT_ROOT, "discover_state.json")
    try:
        with open(state_path, encoding="utf-8") as f:
            _state = json.load(f)
        has_last_run = "last_run" in _state
    except Exception:
        has_last_run = False

    # Startup: genre bootstrap (always) + initial weekly run (when no last_run)
    try:
        cfg_initial = _get_config()
        host_i = cfg_initial.get("navidrome_url", "")
        user_i = cfg_initial.get("navidrome_user", "")
        pw_i   = cfg_initial.get("navidrome_pass", "")
        if host_i and user_i and pw_i:
            from discover.subsonic import Subsonic as _Sub
            sub_i = _Sub(host_i, user_i, pw_i)
            # Genre bootstrap: create auto profiles if none exist yet
            _bootstrap_genre_profiles(sub_i)
            # Initial weekly run on fresh install
            if not has_last_run:
                artists_i = sub_i.get_frequent_artists(size=1)
                if artists_i:
                    logger.info("[MIXES] No last_run found and library has songs — running initial weekly mix")
                    mixes_init = _load_mixes()
                    weekly = next((m for m in mixes_init if m.get("id") == "weekly" and m.get("enabled")), None)
                    if weekly:
                        _run_profile_once(weekly)
    except Exception:
        logger.warning("[MIXES] startup check failed", exc_info=True)

    while True:
        try:
            mixes = [m for m in _load_mixes() if m.get("enabled")]
            now = datetime.datetime.now()
            next_runs = {m["id"]: _profile_next_run(m, now) for m in mixes}
            _persist_next_runs({k: v.isoformat() for k, v in next_runs.items()})
            if not next_runs:
                _mix_wake.wait(3600)
                _mix_wake.clear()
                continue
            soonest = min(next_runs.values())
            _mix_wake.wait(max(1.0, (soonest - now).total_seconds()))
            _mix_wake.clear()
            # Re-read clock and mixes after wake (config may have changed; time moved on)
            now = datetime.datetime.now()
            current_mixes = [m for m in _load_mixes() if m.get("enabled")]
            due = _profiles_due_now(current_mixes, next_runs, now)
            for m in due:
                result = _run_profile_once(m)   # sequential; failures logged inside
                if result.get("status") == "busy":
                    # Another run just finished; retry this profile on next loop pass
                    logger.info("[MIXES] %s was busy, will retry next cycle", m["id"])
        except Exception:
            logger.exception("[MIXES] scheduler iteration failed; retrying in 3600s")
            time.sleep(3600)


# ── Follow scheduler ──────────────────────────────────────────────────────────

_follow_wake = threading.Event()


def _follow_next_run(now: datetime.datetime, run_hour: int) -> datetime.datetime:
    run_hour = max(0, min(23, int(run_hour)))
    candidate = now.replace(hour=run_hour, minute=0, second=0, microsecond=0)
    if candidate <= now:
        candidate += datetime.timedelta(days=1)
    return candidate


def _follow_scheduler_loop():
    while True:
        try:
            fc = _follow_cfg()
            now = datetime.datetime.now()
            if not fc.get("enabled", True):
                _follow_wake.wait(3600)
                _follow_wake.clear()
                continue
            nxt = _follow_next_run(now, fc.get("run_hour", 4))
            # persist next_run for UI
            try:
                from follow import fstate
                st = fstate.load(_FOLLOW_STATE_PATH)
                st.set_runs(next_run=nxt.isoformat())
                st.save()
            except Exception:
                logger.warning("[FOLLOW] could not persist next_run", exc_info=True)
            _follow_wake.wait(max(1.0, (nxt - now).total_seconds()))
            _follow_wake.clear()
            now = datetime.datetime.now()
            if _follow_cfg().get("enabled", True) and now >= nxt:
                _run_follow_once()
        except Exception:
            logger.exception("[FOLLOW] scheduler iteration failed; retry in 3600s")
            time.sleep(3600)


def _run_playlist_mix(playlist_id: str, count: int) -> dict:
    try:
        from discover.config import load_config
        from discover.subsonic import Subsonic
        from lastfm.tags import get_artist_tags, build_genre_profile
        from lastfm.similar import get_similar_artists, get_artist_top_tracks, score_candidates

        cfg = load_config(_CONFIG_PATH)
        host = cfg.get("navidrome_url", "")
        user = cfg.get("navidrome_user", "")
        pw   = cfg.get("navidrome_pass", "")
        if not host or not user or not pw:
            return {"status": "disabled", "reason": "navidrome creds missing"}

        api_key = cfg.get("lastfm_api_key", "")
        if not api_key:
            return {"status": "disabled", "reason": "lastfm_api_key not configured"}

        from lastfm.client import LastFMClient
        lfm = LastFMClient(api_key)
        subsonic = Subsonic(host, user, pw)

        disc = cfg.get("discover") or {}
        seed_count = disc.get("playlist_seed_artist_count", 10)

        pl = subsonic.get_playlist(playlist_id)
        if not pl:
            return {"status": "error", "error": f"playlist {playlist_id!r} not found"}
        source_name = pl.get("name", playlist_id)
        tracks = pl.get("entry", []) or []

        freq: dict = {}
        canonical: dict = {}
        for t in tracks:
            a = (t.get("artist") or "").strip()
            if not a:
                continue
            cf = a.casefold()
            freq[cf] = freq.get(cf, 0) + 1
            if cf not in canonical:
                canonical[cf] = a

        ranked_artists = sorted(freq.items(), key=lambda kv: -kv[1])
        if seed_count and seed_count > 0:
            ranked_artists = ranked_artists[:seed_count]
        seed_artists = [canonical[cf] for cf, _ in ranked_artists]

        if not seed_artists:
            return {"status": "error", "error": "playlist has no tracks with artist tags"}

        artist_tag_sets = [get_artist_tags(lfm, a) for a in seed_artists]
        genre_profile = build_genre_profile(artist_tag_sets)

        all_similar: dict = {}
        canonical_sim: dict = {}
        seed_set = {a.casefold() for a in seed_artists}
        for a in seed_artists:
            sims = get_similar_artists(lfm, a, limit=50)
            for s in sims:
                cf = s["name"].casefold()
                if cf in seed_set:
                    continue
                if cf not in all_similar or s["match"] > all_similar[cf]:
                    all_similar[cf] = s["match"]
                    canonical_sim[cf] = s["name"]

        if not all_similar:
            return {"status": "error", "error": "no similar artists found via Last.fm"}

        similar_list = [{"name": canonical_sim[cf], "match": m} for cf, m in all_similar.items()]
        candidate_tags: dict = {}
        for sim in similar_list:
            cf = sim["name"].casefold()
            candidate_tags[cf] = get_artist_tags(lfm, sim["name"])

        scored = score_candidates(similar_list, genre_profile, candidate_tags)
        top_candidates = [s for s in scored if s["score"] > 0 or not genre_profile]
        if not top_candidates:
            top_candidates = scored

        acquired_ids = []
        for candidate in top_candidates:
            if len(acquired_ids) >= count:
                break
            top_tracks = get_artist_top_tracks(lfm, candidate["name"], limit=5)
            for track in top_tracks:
                if len(acquired_ids) >= count:
                    break
                hits = subsonic.search_songs(f"{track['artist']} {track['title']}", count=1)
                if hits:
                    sid = hits[0].get("id")
                    if sid and sid not in acquired_ids:
                        acquired_ids.append(sid)

        if not acquired_ids:
            return {"status": "error", "error": "no matching tracks found in library"}

        mix_name = f"Mix: {source_name}"
        subsonic.create_or_update_playlist(mix_name, acquired_ids)
        try:
            subsonic.start_scan()
        except Exception:
            pass

        return {"status": "ok", "playlist_name": mix_name, "acquired": len(acquired_ids),
                "skipped": count - len(acquired_ids)}
    except Exception as e:
        logger.exception("[PLAYLIST_MIX] failed")
        return {"status": "error", "error": str(e)}


def _run_enrich_once(limit=None) -> dict:
    global _enrich_last_result
    if not _enrich_running.acquire(blocking=False):
        return {"status": "skipped", "reason": "already running"}
    try:
        from discover.config import load_config
        cfg = load_config(_CONFIG_PATH)
        enrich_cfg = cfg.get("enrich") or {}
        if not enrich_cfg.get("enabled", False):
            result = {"status": "disabled", "reason": "enrich disabled in config"}
            _enrich_last_result = result
            return result
        song_dir = cfg.get("song_dir", "")
        if not song_dir:
            result = {"status": "disabled", "reason": "song_dir not set"}
            _enrich_last_result = result
            return result

        fields = _enrich_fields(enrich_cfg)
        min_score = int(enrich_cfg.get("min_musicbrainz_score", 90))
        cover_size = str(enrich_cfg.get("cover_art_size", "500"))

        lfm = None
        api_key = cfg.get("lastfm_api_key", "")
        if api_key:
            from lastfm.client import LastFMClient
            lfm = LastFMClient(api_key)
        from follow.musicbrainz import MusicBrainzClient
        mbc = MusicBrainzClient()

        from library.enrich import run as enrich_run

        def _progress(done, total):
            global _enrich_last_result
            _enrich_last_result = {"status": "running",
                                   "files_done": done, "files_total": total}

        result = enrich_run(song_dir, lastfm_client=lfm, mb_client=mbc,
                            fields=fields, min_musicbrainz_score=min_score,
                            cover_art_size=cover_size, limit=limit,
                            progress=_progress)
        result["status"] = "ok"
        result["files_done"] = result.get("files_total", 0)
        logger.info("[ENRICH] complete: %s", result)
        _enrich_last_result = result
        return result
    except Exception as e:
        logger.exception("[ENRICH] failed")
        result = {"status": "error", "error": str(e)}
        _enrich_last_result = result
        return result
    finally:
        _enrich_running.release()


def _insights_db_path() -> str:
    cfg = _get_config()
    insights_cfg = cfg.get("insights") or {}
    return insights_cfg.get("db_path") or os.path.join(_PROJECT_ROOT, "insights.db")


def _run_insights_sync_once(max_pages=None) -> dict:
    """Run one on-demand scrobble sync.

    Phase 1 wires on-demand sync only; the scheduled/on-start triggers
    described in the design spec are deferred to a later phase.
    """
    global _insights_last_result
    if not _insights_running.acquire(blocking=False):
        return {"status": "skipped", "reason": "already running"}
    try:
        from discover.config import load_config
        cfg = load_config(_CONFIG_PATH)
        api_key = cfg.get("lastfm_api_key", "")
        username = cfg.get("lastfm_username", "")
        if not api_key or not username:
            result = {"status": "disabled",
                      "reason": "lastfm_api_key/lastfm_username not configured"}
            _insights_last_result = result
            return result

        from lastfm.client import LastFMClient
        from insights import db as insights_db
        from insights.scrobbles import sync_scrobbles

        lfm = LastFMClient(api_key)
        conn = insights_db.connect(_insights_db_path())
        try:
            synced = sync_scrobbles(lfm, username, conn, max_pages=max_pages)
            from insights.genres import ensure_artist_tags
            artists = [r[0] for r in conn.execute(
                "SELECT DISTINCT artist FROM scrobbles").fetchall()]
            # First sync only: genres are fetched for every artist at ~1 req/s.
            # Large libraries can take several minutes; cached artists are skipped after.
            logger.info("[INSIGHTS] tagging %d distinct artists (cached ones skipped)",
                        len(artists))
            tagged = ensure_artist_tags(lfm, conn, artists)
            synced["artists_tagged"] = tagged
            from insights.library_index import index_library
            song_dir = cfg.get("song_dir", "")
            if song_dir:
                synced["library_tracks"] = index_library(conn, song_dir)
        finally:
            conn.close()
        result = {"status": "ok", **synced}
        logger.info("[INSIGHTS] sync complete: %s", result)
        _insights_last_result = result
        return result
    except Exception as e:
        logger.exception("[INSIGHTS] sync failed")
        result = {"status": "error", "error": str(e)}
        _insights_last_result = result
        return result
    finally:
        _insights_running.release()


def _mb_recording_search(artist, track):
    """Resolve a recording MBID via MusicBrainz (mirrors library/repair.py)."""
    import urllib.parse, urllib.request
    q = urllib.parse.quote(f'recording:"{track}" AND artist:"{artist}"')
    url = f"https://musicbrainz.org/ws/2/recording/?query={q}&limit=1&fmt=json"
    req = urllib.request.Request(url, headers={"User-Agent":
        "aMusicServer/1.0 (insights features)"})
    try:
        time.sleep(1.0)  # MusicBrainz 1 req/s ToS
        with urllib.request.urlopen(req, timeout=10) as r:
            recs = json.loads(r.read()).get("recordings", [])
        if not recs:
            return None
        # Reject low-confidence matches (MusicBrainz score 0-100); a wrong MBID
        # would cache features for the wrong recording and never self-correct.
        if int(recs[0].get("score", 0)) < 80:
            return None
        return recs[0]["id"]
    except Exception:
        logger.warning("[INSIGHTS] MB recording search failed for %s / %s", artist, track)
        return None


def _build_track_path_index(song_dir):
    """Map (artist_lower, title_lower) -> file path using the library scanner."""
    try:
        from library.scanner import scan
        idx = {}
        for rec in scan(song_dir):
            a = (rec.get("artist") or "").lower()
            t = (rec.get("title") or "").lower()
            if a and t:
                idx[(a, t)] = rec["path"]
        return idx
    except Exception:
        logger.warning("[INSIGHTS] could not build track path index", exc_info=True)
        return {}


def _run_insights_features_once(max_tracks=200) -> dict:
    """Compute audio features for tracks lacking them (bounded per run)."""
    global _insights_features_last_result
    if not _insights_features_running.acquire(blocking=False):
        return {"status": "skipped", "reason": "already running"}
    try:
        _insights_features_last_result = {"status": "running"}
        from discover.config import load_config
        from insights import db as insights_db
        from insights.features import ensure_track_features
        from insights.acousticbrainz import fetch_features
        cfg = load_config(_CONFIG_PATH)
        enable_local = bool((cfg.get("insights") or {}).get("enable_local_analysis", False))
        local_analyze = None
        local_status = "disabled"
        if enable_local:
            # librosa is imported lazily inside analyze_file, so probe it here to
            # tell an opted-in-but-not-installed user why local analysis is a no-op.
            try:
                import librosa  # noqa: F401
            except ImportError:
                local_status = ("unavailable — run: "
                                "pip install -r requirements-insights.txt")
            else:
                from insights.localfeatures import analyze_file
                song_dir = cfg.get("song_dir", "")
                index = _build_track_path_index(song_dir) if song_dir else {}

                def local_analyze(artist, track, _idx=index):
                    path = _idx.get((artist.lower(), track.lower()))
                    return analyze_file(path) if path else None

                local_status = "enabled"

        conn = insights_db.connect(_insights_db_path())
        try:
            n = ensure_track_features(
                conn, ab_fetch=fetch_features, mb_search=_mb_recording_search,
                local_analyze=local_analyze, limit=max_tracks)
        finally:
            conn.close()
        result = {"status": "ok", "processed": n, "local_analysis": local_status}
        logger.info("[INSIGHTS] feature sync complete: %s", result)
        _insights_features_last_result = result
        return result
    except Exception as e:
        logger.exception("[INSIGHTS] feature sync failed")
        result = {"status": "error", "error": str(e)}
        _insights_features_last_result = result
        return result
    finally:
        _insights_features_running.release()


def _run_repair_once(limit=None) -> dict:
    global _repair_last_result
    if not _repair_running.acquire(blocking=False):
        return {"status": "skipped", "reason": "already running"}
    try:
        from discover.config import load_config
        cfg = load_config(_CONFIG_PATH)
        song_dir = cfg.get("song_dir", "")
        if not song_dir:
            result = {"status": "disabled", "reason": "song_dir not set"}
            _repair_last_result = result
            return result

        repair_cfg = cfg.get("repair") or {}
        min_lfm = int(repair_cfg.get("min_lastfm_listeners", 10000))
        min_mb = int(repair_cfg.get("min_musicbrainz_score", 90))

        lastfm_client = None
        api_key = cfg.get("lastfm_api_key", "")
        if api_key:
            try:
                from lastfm.client import LastFMClient
                lastfm_client = LastFMClient(api_key)
            except Exception:
                logger.warning("[REPAIR] Last.fm client init failed", exc_info=True)

        from library.repair import repair_missing_artists
        result = repair_missing_artists(
            song_dir,
            lastfm_client=lastfm_client,
            min_lastfm_listeners=min_lfm,
            min_musicbrainz_score=min_mb,
            limit=limit or 0,
        )
        result["status"] = "ok"
        logger.info("[REPAIR] complete: %s", result)
        _repair_last_result = result
        return result
    except Exception as e:
        logger.exception("[REPAIR] failed")
        result = {"status": "error", "error": str(e)}
        _repair_last_result = result
        return result
    finally:
        _repair_running.release()


def _run_dedup_once(force_dry_run=False):
    if not _dedup_running.acquire(blocking=False):
        return {"status": "skipped", "reason": "already running"}
    try:
        from library.dedupe import run as dedup_run
        from discover.config import load_config
        cfg = load_config(_CONFIG_PATH)
        song_dir = cfg.get("song_dir", "")
        if not song_dir:
            return {"status": "disabled", "reason": "song_dir not set"}
        auto_delete = False if force_dry_run else cfg.get("dedup", {}).get("auto_delete", False)
        result = dedup_run(song_dir, auto_delete=auto_delete)
        logger.info("[DEDUP] Scan complete: %s", result)
        return {"status": "ok", **result}
    except Exception as e:
        logger.exception("[DEDUP] Scan failed")
        return {"status": "error", "error": str(e)}
    finally:
        _dedup_running.release()


def _dedup_scheduled_loop():
    from discover.config import load_config
    while True:
        cfg = load_config(_CONFIG_PATH)
        interval_hours = cfg.get("dedup", {}).get("interval_hours", 24)
        time.sleep(interval_hours * 3600)
        logger.info("[DEDUP] Starting scheduled scan")
        _run_dedup_once()


def _fetch_sc_client_id_via_scrape() -> str | None:
    """Scrape a fresh SoundCloud client_id from soundcloud.com JS bundles."""
    import re as _re
    import requests as _req
    import urllib3 as _urllib3
    _urllib3.disable_warnings(_urllib3.exceptions.InsecureRequestWarning)
    kw = {"headers": {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"},
          "timeout": 15, "verify": False}
    try:
        page = _req.get("https://soundcloud.com/", **kw).text
        # Only fetch SC CDN scripts (skip third-party domains)
        scripts = [u for u in _re.findall(r'<script[^>]+src="(https://[^"]+\.js)"', page)
                   if "sndcdn.com" in u or "soundcloud.com" in u]
        _PATS = [r'client_id[:=]["\']([0-9a-zA-Z]{32})["\']',
                 r'client_id=([0-9a-zA-Z]{32})',
                 r'"client_id","([0-9a-zA-Z]{32})"']
        for url in scripts:
            try:
                js = _req.get(url, **{**kw, "timeout": 15}).text
                for pat in _PATS:
                    m = _re.search(pat, js)
                    if m:
                        cid = m.group(1)
                        logger.info("[SC-REFRESH] Got client_id via JS scrape: %s…", cid[:12])
                        return cid
            except Exception:
                continue
    except Exception:
        logger.exception("[SC-REFRESH] JS scrape failed")
    return None


def _persist_sc_client_id(cid: str) -> None:
    cfg = {}
    if os.path.exists(_CONFIG_PATH):
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    cfg["sc_client_id"] = cid
    cfg["sc_client_id_ts"] = int(time.time())
    with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    logger.info("[SC-REFRESH] Persisted sc_client_id")


def _refresh_sc_client_id_loop(period_seconds=3600):
    global _sc_client_ready
    script_fp = os.path.join(_PROJECT_ROOT, "scripts/Sc2Sp_src/script_web.py")
    cycle = 0
    while True:
        cycle += 1
        _sc_client_ready = False
        cid = None
        try:
            # Primary: Selenium scrape via sc2 helper script
            if os.path.exists(script_fp):
                spec   = importlib.util.spec_from_file_location("sc2_web_helper", script_fp)
                module = importlib.util.module_from_spec(spec)
                try:
                    spec.loader.exec_module(module)
                except Exception:
                    logger.exception("[SC-REFRESH] Failed to load sc2 helper module")
                    module = None

                if module and hasattr(module, "fetch_client_id_via_selenium"):
                    try:
                        cid = module.fetch_client_id_via_selenium()
                    except Exception:
                        logger.exception("[SC-REFRESH] fetch_client_id_via_selenium raised")

            # Fallback: scrape client_id directly from soundcloud.com JS bundles
            if not cid:
                logger.info("[SC-REFRESH] Selenium unavailable — trying JS scrape fallback")
                cid = _fetch_sc_client_id_via_scrape()

            if cid:
                _persist_sc_client_id(cid)
                _sc_client_ready = True
            else:
                # Last resort: if config already has a client_id, stay ready
                existing = _get_config().get("sc_client_id")
                if existing:
                    logger.warning("[SC-REFRESH] Could not refresh — using existing config id")
                    _sc_client_ready = True
        except Exception:
            logger.exception("[SC-REFRESH] Unhandled error in cycle %d", cycle)
        time.sleep(period_seconds)


def _get_config() -> dict:
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _follow_cfg() -> dict:
    cfg = _get_config()
    fc = dict(_FOLLOW_DEFAULTS)
    fc.update(cfg.get("follow") or {})
    notify = dict(_FOLLOW_DEFAULTS["notify"])
    notify.update((cfg.get("follow") or {}).get("notify") or {})
    fc["notify"] = notify
    return fc


def _get_hostname() -> str:
    return _get_config().get("hostname", "amusicserver.local")


def _download_url(url: str) -> "str | None":
    """Download url using the sTownload script_web.py download_url function.
    Applies title cleanup, WOAS tag, and triggers Navidrome scan (same pipeline
    as script_web.py main()). Returns the first mp3 path downloaded, or None.
    """
    try:
        cfg = _get_config()
        song_dir = cfg.get("song_dir") or str(os.path.join(os.path.expanduser("~"), "Music"))
        os.makedirs(song_dir, exist_ok=True)
        dl_path = os.path.join(_PROJECT_ROOT, "scripts/sTownload/script_web.py")
        spec = importlib.util.spec_from_file_location("sTownload_web_acquire", dl_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _, paths = mod.download_url(url, song_dir)
        if not paths:
            return None
        if _PROJECT_ROOT not in sys.path:
            sys.path.insert(0, _PROJECT_ROOT)
        for path in paths:
            try:
                from library.tagger import apply_from_config
                apply_from_config(path, _CONFIG_PATH)
            except Exception:
                logger.exception("[ACQUIRE] title cleanup failed for %s", path)
            try:
                from library.tagger import write_source_url
                write_source_url(path, url)
            except Exception:
                logger.exception("[ACQUIRE] WOAS write failed for %s", path)
        mod.trigger_navidrome_scan()
        return paths[0]
    except Exception:
        logger.warning("[ACQUIRE] _download_url failed", exc_info=True)
        return None


# ── Existing routes (migrated) ────────────────────────────────────────────────

@app.route("/", methods=["GET"])
def root_get():
    return render_template("app.html")


@app.route("/", methods=["POST"])
def root_post():
    """Download dispatcher — browser extension sends URL here."""
    try:
        data = request.get_json(force=True, silent=True) or {}
        incoming_url = data.get("url", "")
        m3u_file = data.get("m3u", "default_playlist")
        logger.info("POST /: url=%s m3u=%s", incoming_url, m3u_file)

        if re.search(r"https?://(www\.)?youtube\.", incoming_url):
            candidates = ["scripts/sTownload/script_web.py", "scripts/sTownload/app.py"]
        elif re.search(r"https?://(www\.)?soundcloud\.", incoming_url):
            candidates = ["scripts/Sc2Sp_src/script_web.py"]
        else:
            candidates = []

        script_to_run = None
        for cand in candidates:
            cand_path = os.path.join(_PROJECT_ROOT, cand)
            if os.path.exists(cand_path):
                script_to_run = cand_path
                break

        if not script_to_run:
            return jsonify({"status": "ignored", "reason": "no matching script found or URL not supported"}), 404

        def _run_script(path, url_arg, m3u_arg):
            try:
                script_dir = os.path.dirname(path)
                old_argv = sys.argv[:]
                old_cwd  = os.getcwd()
                if script_dir not in sys.path:
                    sys.path.insert(0, script_dir)
                try:
                    sys.argv = [path, url_arg, m3u_arg]
                    os.chdir(script_dir)
                    runpy.run_path(path, run_name="__main__")
                finally:
                    sys.argv = old_argv
                    try:
                        os.chdir(old_cwd)
                    except Exception:
                        pass
            except Exception:
                logger.exception("Unhandled exception running script: %s", path)

        t = threading.Thread(target=_run_script, args=(script_to_run, incoming_url, m3u_file), daemon=True)
        t.start()
        return jsonify({"status": "started", "script": script_to_run})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400


@app.route("/playlists", methods=["GET"])
def get_playlists():
    """Proxy Navidrome getPlaylists.view → minimal JSON for the extension."""
    import urllib.parse as _up
    import urllib.request as _ur
    try:
        cfg = _get_config()
    except Exception:
        cfg = {}

    host  = cfg.get("navidrome_url", "http://localhost:4533")
    nuser = cfg.get("navidrome_user", "")
    npw   = cfg.get("navidrome_pass", "")
    if not nuser or not npw:
        return jsonify({"status": "error", "error": "navidrome credentials not configured"}), 503

    params = _up.urlencode({"u": nuser, "p": npw, "v": "1.16.1", "c": "amusicserver-ext", "f": "json"})
    url = f"{host}/rest/getPlaylists.view?{params}"
    try:
        with _ur.urlopen(url, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 502

    sr = data.get("subsonic-response", {})
    if sr.get("status") != "ok":
        err = sr.get("error", {}).get("message", "unknown navidrome error")
        return jsonify({"status": "error", "error": err}), 502

    raw = sr.get("playlists", {}).get("playlist", []) or []
    playlists = [{"name": p.get("name", ""), "id": p.get("id", ""), "songCount": p.get("songCount", 0)}
                 for p in raw if p.get("name")]
    return jsonify({"status": "ok", "playlists": playlists})


_DISCOVER_CONFIG_KEYS = {
    "weekly_count", "per_artist", "playlist_cap", "schedule", "run_day", "run_hour",
    "lastfm_period", "lastfm_periods", "suggested_ttl_days",
    "manual_seeds", "playlist_name", "bootstrap_playlist_name",
    "min_artist_listeners", "candidate_oversample", "yt_oversample", "junk_keywords",
    "seed_playlist",
}

SETTINGS_SCHEMA = [
    # Discovery group — per-mix schedule/count/cap fields live in Mixes UI, not here
    {"path": "discover.suggested_ttl_days",  "type": "int",       "label": "Suggestion memory (days)", "group": "Discovery", "min": 1, "max": 365},
    {"path": "discover.min_artist_listeners","type": "int",       "label": "Min artist listeners",      "group": "Discovery", "min": 0, "max": 10000000},
    {"path": "discover.candidate_oversample","type": "int",       "label": "Candidate oversample",      "group": "Discovery", "min": 1, "max": 20},
    # Note: discover.schedule/run_day/run_hour/weekly_count/playlist_cap/daily.* removed
    # — superseded by Mixes UI (mix profiles).
    # Sources group
    {"path": "sc_username",                  "type": "str",       "label": "SoundCloud username URL",   "group": "Sources"},
    {"path": "sp_playlist_ids",              "type": "list[str]", "label": "Spotify playlist IDs",      "group": "Sources"},
    {"path": "sc_topsong",                   "type": "str",       "label": "SoundCloud top song URL",   "group": "Sources"},
    {"path": "spotify_playlists_dir",        "type": "str",       "label": "Spotify playlists dir",     "group": "Sources"},
    # Maintenance group
    {"path": "dedup.enabled",                "type": "bool",      "label": "Dedup enabled",             "group": "Maintenance"},
    {"path": "dedup.interval_hours",         "type": "int",       "label": "Dedup interval (hours)",    "group": "Maintenance", "min": 1, "max": 168},
    {"path": "dedup.auto_delete",            "type": "bool",      "label": "Dedup auto-delete",         "group": "Maintenance"},
    {"path": "title_cleanup.enabled",        "type": "bool",      "label": "Title cleanup enabled",     "group": "Maintenance"},
    {"path": "insights.enable_local_analysis","type": "bool",      "label": "Local audio analysis (librosa)", "group": "Maintenance"},
    # Server group
    {"path": "hostname",                     "type": "str",       "label": "Server hostname",           "group": "Server"},
    {"path": "song_dir",                     "type": "str",       "label": "Song directory",            "group": "Server"},
    {"path": "path",                         "type": "str",       "label": "Library path",              "group": "Server"},
    # Credentials group
    {"path": "navidrome_url",                "type": "str",       "label": "Navidrome URL",             "group": "Credentials"},
    {"path": "navidrome_user",               "type": "str",       "label": "Navidrome user",            "group": "Credentials"},
    {"path": "navidrome_pass",               "type": "secret",    "label": "Navidrome password",        "group": "Credentials"},
    {"path": "lastfm_api_key",               "type": "secret",    "label": "Last.fm API key",           "group": "Credentials"},
    {"path": "lastfm_api_secret",            "type": "secret",    "label": "Last.fm API secret",        "group": "Credentials"},
    {"path": "lastfm_username",              "type": "str",       "label": "Last.fm username",          "group": "Credentials"},
]


@app.route("/discover/config", methods=["GET"])
def discover_config_get():
    cfg = _get_config()
    disc = dict(cfg.get("discover") or {})
    return jsonify(disc)


@app.route("/discover/config", methods=["POST"])
def discover_config_post():
    body = request.get_json(force=True, silent=True) or {}
    updates = {k: v for k, v in body.items() if k in _DISCOVER_CONFIG_KEYS}
    if not updates:
        return jsonify({"status": "error", "error": "no valid keys"}), 400
    try:
        with _config_lock:
            with open(_CONFIG_PATH, encoding="utf-8") as f:
                cfg = json.load(f)
            disc = cfg.setdefault("discover", {})
            disc.update(updates)
            with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)
        return jsonify({"status": "ok", "discover": disc})
    except Exception as e:
        logger.exception("discover_config_post failed")
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/discover/run", methods=["POST"])
def discover_run():
    result = _run_discover_once()
    if result.get("status") == "busy":
        return jsonify(result), 409
    code = 200 if result.get("status") in ("ok", "disabled") else 500
    return jsonify(result), code


@app.route("/discover/run_daily", methods=["POST"])
def discover_run_daily():
    result = _run_discover_daily_once()
    if result.get("status") == "busy":
        return jsonify(result), 409
    code = 200 if result.get("status") in ("ok", "disabled", "skipped") else 500
    return jsonify(result), code


@app.route("/discover/playlist_mix", methods=["POST"])
def discover_playlist_mix():
    body = request.get_json(force=True, silent=True) or {}
    playlist_id = body.get("playlist_id", "")
    if not playlist_id:
        return jsonify({"status": "error", "error": "playlist_id required"}), 400
    cfg_disc = _get_config().get("discover") or {}
    count = body.get("count", cfg_disc.get("playlist_mix_count", 20))
    result = _run_playlist_mix(playlist_id, count)
    return jsonify(result)


@app.route("/library/dedup/run", methods=["POST"])
def dedup_run():
    result = _run_dedup_once(force_dry_run=False)
    code = 200 if result.get("status") in ("ok", "skipped", "disabled") else 500
    return jsonify(result), code


@app.route("/library/dedup/report", methods=["POST"])
def dedup_report():
    result = _run_dedup_once(force_dry_run=True)
    code = 200 if result.get("status") in ("ok", "skipped", "disabled") else 500
    return jsonify(result), code


@app.route("/library/enrich", methods=["POST"])
def library_enrich():
    global _enrich_last_result
    if _enrich_running.locked():
        return jsonify({"status": "skipped", "reason": "already running"})
    body = request.get_json(force=True, silent=True) or {}
    limit = body.get("limit", None)
    _enrich_last_result = {"status": "running", "files_done": 0, "files_total": 0}
    t = threading.Thread(target=_run_enrich_once, kwargs={"limit": limit}, daemon=True)
    t.start()
    return jsonify({"status": "running"})


@app.route("/library/enrich/status", methods=["GET"])
def enrich_status():
    return jsonify(_enrich_last_result)


@app.route("/insights/sync", methods=["POST"])
def insights_sync():
    body = request.get_json(force=True, silent=True) or {}
    max_pages = body.get("max_pages", None)
    t = threading.Thread(target=_run_insights_sync_once,
                         kwargs={"max_pages": max_pages}, daemon=True)
    t.start()
    return jsonify({"status": "started"})


@app.route("/insights/sync/status", methods=["GET"])
def insights_sync_status():
    return jsonify(_insights_last_result)


def _insights_query_args():
    period = request.args.get("period", "all")
    try:
        tz = int(request.args.get("tz", 0))
    except (TypeError, ValueError):
        tz = 0
    return period, tz


@app.route("/insights/overview", methods=["GET"])
def insights_overview():
    from insights import db as insights_db, analytics
    period, tz = _insights_query_args()
    conn = insights_db.connect(_insights_db_path())
    try:
        return jsonify(analytics.overview(conn, period=period, tz_offset_min=tz))
    finally:
        conn.close()


@app.route("/insights/temporal", methods=["GET"])
def insights_temporal():
    from insights import db as insights_db, analytics
    period, tz = _insights_query_args()
    conn = insights_db.connect(_insights_db_path())
    try:
        return jsonify({
            "clock": analytics.listening_clock(conn, period=period, tz_offset_min=tz),
            "heatmap": analytics.hour_day_heatmap(conn, period=period, tz_offset_min=tz),
            "weekday_weekend": analytics.weekday_weekend(conn, period=period, tz_offset_min=tz),
            "over_time": analytics.plays_over_time(conn, period=period, tz_offset_min=tz),
        })
    finally:
        conn.close()


@app.route("/insights/genres", methods=["GET"])
def insights_genres():
    from insights import db as insights_db, analytics
    period, tz = _insights_query_args()
    conn = insights_db.connect(_insights_db_path())
    try:
        return jsonify({
            "top": analytics.top_genres(conn, period=period, tz_offset_min=tz),
            "by_hour": analytics.genre_by_hour(conn, period=period, tz_offset_min=tz),
            "evolution": analytics.genre_evolution(conn, period=period, tz_offset_min=tz),
            "diversity": analytics.genre_diversity(conn, period=period, tz_offset_min=tz),
        })
    finally:
        conn.close()


@app.route("/insights/features/sync", methods=["POST"])
def insights_features_sync():
    body = request.get_json(force=True, silent=True) or {}
    try:
        max_tracks = int(body.get("max_tracks", 200))
    except (TypeError, ValueError):
        max_tracks = 200
    t = threading.Thread(target=_run_insights_features_once,
                         kwargs={"max_tracks": max_tracks}, daemon=True)
    t.start()
    return jsonify({"status": "started"})


@app.route("/insights/features/sync/status", methods=["GET"])
def insights_features_sync_status():
    return jsonify(_insights_features_last_result)


@app.route("/insights/features", methods=["GET"])
def insights_features():
    from insights import db as insights_db, analytics
    period, tz = _insights_query_args()
    conn = insights_db.connect(_insights_db_path())
    try:
        return jsonify({
            "bpm_distribution": analytics.bpm_distribution(conn, period=period, tz_offset_min=tz),
            "bpm_curve": analytics.bpm_curve(conn, period=period, tz_offset_min=tz),
            "key_distribution": analytics.key_distribution(conn, period=period, tz_offset_min=tz),
            "mood_distribution": analytics.mood_distribution(conn, period=period, tz_offset_min=tz),
            "mood_by_time": analytics.mood_by_time(conn, period=period, tz_offset_min=tz),
            "coverage": analytics.feature_coverage(conn, period=period, tz_offset_min=tz),
        })
    finally:
        conn.close()


@app.route("/insights/discovery", methods=["GET"])
def insights_discovery():
    from insights import db as insights_db, analytics
    period, tz = _insights_query_args()
    conn = insights_db.connect(_insights_db_path())
    try:
        return jsonify({
            "overlap": analytics.library_overlap(conn, period=period, tz_offset_min=tz),
            "missing_favorites": analytics.missing_favorites(conn, period=period, tz_offset_min=tz),
            "discovery_rate": analytics.discovery_rate(conn, period=period, tz_offset_min=tz),
            "new_vs_repeat": analytics.new_vs_repeat(conn, period=period, tz_offset_min=tz),
        })
    finally:
        conn.close()


@app.route("/library/repair", methods=["POST"])
def library_repair():
    body = request.get_json(force=True, silent=True) or {}
    limit = body.get("limit", None)
    t = threading.Thread(target=_run_repair_once, kwargs={"limit": limit}, daemon=True)
    t.start()
    return jsonify({"status": "started"})


@app.route("/library/repair/status", methods=["GET"])
def repair_status():
    return jsonify(_repair_last_result)


# ── Settings helpers ─────────────────────────────────────────────────────────

def _settings_get_by_path(cfg: dict, path: str):
    """Read a dot-path value from nested dict, returning None if missing."""
    keys = path.split(".")
    node = cfg
    for k in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(k)
    return node


def _settings_set_by_path(cfg: dict, path: str, value) -> None:
    """Deep-merge a value into cfg at the given dot-path, creating dicts as needed."""
    keys = path.split(".")
    node = cfg
    for k in keys[:-1]:
        if k not in node or not isinstance(node[k], dict):
            node[k] = {}
        node = node[k]
    node[keys[-1]] = value


def _settings_validate_and_coerce(entry: dict, raw_value):
    """Validate and coerce a value per schema entry type. Returns (coerced, error_str|None)."""
    typ = entry["type"]
    if typ == "secret":
        if raw_value == "" or raw_value is None:
            return None, None  # empty secret = unchanged
        if not isinstance(raw_value, str):
            return None, "expected string"
        return raw_value, None
    if typ == "bool":
        if isinstance(raw_value, bool):
            return raw_value, None
        if isinstance(raw_value, str):
            if raw_value.lower() in ("true", "1", "yes"):
                return True, None
            if raw_value.lower() in ("false", "0", "no"):
                return False, None
        return None, f"expected bool"
    if typ == "int":
        # Reject booleans (bool is subclass of int in Python)
        if isinstance(raw_value, bool):
            return None, "expected int"
        try:
            v = int(raw_value)
        except (TypeError, ValueError):
            return None, "expected int"
        mn = entry.get("min")
        mx = entry.get("max")
        if mn is not None and v < mn:
            return None, f"must be >= {mn}"
        if mx is not None and v > mx:
            return None, f"must be <= {mx}"
        return v, None
    if typ == "list[str]":
        if isinstance(raw_value, list):
            return [str(x) for x in raw_value], None
        if isinstance(raw_value, str):
            lines = [l.strip() for l in raw_value.splitlines() if l.strip()]
            return lines, None
        return None, "expected list or newline-separated string"
    # str — only accept actual strings; reject dicts, None, booleans, etc.
    if not isinstance(raw_value, str):
        return None, "expected string"
    return raw_value, None


# ── Settings routes ───────────────────────────────────────────────────────────

@app.route("/settings", methods=["GET"])
def settings_get():
    cfg = _get_config()
    values = {}
    schema_out = []
    for entry in SETTINGS_SCHEMA:
        path = entry["path"]
        raw = _settings_get_by_path(cfg, path)
        e = dict(entry)
        if entry["type"] == "secret":
            values[path] = {"value": "", "set": bool(raw)}
        else:
            values[path] = raw
        schema_out.append(e)
    return jsonify({"schema": schema_out, "values": values})


@app.route("/settings", methods=["POST"])
def settings_post():
    body = request.get_json(force=True, silent=True) or {}
    schema_by_path = {e["path"]: e for e in SETTINGS_SCHEMA}

    # Check for unknown keys
    unknown = [k for k in body if k not in schema_by_path]
    if unknown:
        return jsonify({"status": "error", "error": "unknown paths", "unknown": unknown}), 400

    # Validate all values first
    errors = {}
    coerced = {}
    for path, raw_value in body.items():
        entry = schema_by_path[path]
        value, err = _settings_validate_and_coerce(entry, raw_value)
        if err:
            errors[path] = err
        else:
            coerced[path] = value

    if errors:
        return jsonify({"status": "error", "error": "validation errors", "fields": errors}), 400

    # Load current config, apply valid updates
    with _config_lock:
        try:
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}

        for path, value in coerced.items():
            if value is None:
                continue  # empty secret = unchanged
            _settings_set_by_path(cfg, path, value)

        # Atomic write
        tmp_path = _CONFIG_PATH + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, _CONFIG_PATH)
        except Exception as e:
            logger.exception("settings_post: write failed")
            return jsonify({"status": "error", "error": str(e)}), 500

    # Exclude paths where value is None (empty secrets that were skipped)
    actually_updated = [path for path, value in coerced.items() if value is not None]
    return jsonify({"status": "ok", "updated": actually_updated})


# ── /mixes routes ─────────────────────────────────────────────────────────────

@app.route("/mixes", methods=["GET"])
def mixes_get():
    mixes = _load_mixes()
    state = {}
    try:
        with open(os.path.join(_PROJECT_ROOT, "discover_state.json"), encoding="utf-8") as f:
            state = json.load(f)
    except Exception:
        pass
    return jsonify({"mixes": mixes, "next_runs": state.get("next_runs", {}), "last_runs": state.get("last_runs", {})})


@app.route("/mixes", methods=["POST"])
def mixes_post():
    from discover.profiles import validate_profile
    body = request.get_json(force=True, silent=True) or {}
    with _config_lock:
        cfg = _get_config()
        mixes = _load_mixes()
        idx = next((i for i, m in enumerate(mixes) if m["id"] == body.get("id")), None)
        others = [m for i, m in enumerate(mixes) if i != idx]
        errors = validate_profile(body, existing=others if idx is None else others)
        if errors:
            return jsonify({"status": "error", "errors": errors}), 400
        body["auto_generated"] = False if idx is not None else bool(body.get("auto_generated"))
        if idx is None:
            mixes.append(body)
            code = 201
        else:
            mixes[idx] = body
            code = 200
        cfg["mixes"] = mixes
        _atomic_write_config(cfg)
    _mix_wake.set()
    return jsonify({"status": "ok", "mix": body}), code


@app.route("/mixes/<mix_id>", methods=["DELETE"])
def mixes_delete(mix_id):
    with _config_lock:
        cfg = _get_config()
        mixes = _load_mixes()
        idx = next((i for i, m in enumerate(mixes) if m["id"] == mix_id), None)
        if idx is None:
            return jsonify({"status": "error", "error": f"mix {mix_id!r} not found"}), 404
        removed = mixes.pop(idx)
        cfg["mixes"] = mixes
        _atomic_write_config(cfg)
    _mix_wake.set()
    return jsonify({"status": "ok", "removed": removed["id"]})


@app.route("/mixes/<mix_id>/run", methods=["POST"])
def mixes_run(mix_id):
    mixes = _load_mixes()
    profile = next((m for m in mixes if m["id"] == mix_id), None)
    if profile is None:
        return jsonify({"status": "error", "error": f"mix {mix_id!r} not found"}), 404
    result = _run_profile_once(profile)
    if result.get("status") == "busy":
        return jsonify(result), 409
    if result.get("status") == "error":
        return jsonify(result), 500
    return jsonify(result)


@app.route("/mixes/suggest", methods=["POST"])
def mixes_suggest():
    deps = _build_discover_deps()
    if deps is None:
        return jsonify({"status": "disabled", "reason": "navidrome creds missing"}), 503
    try:
        from discover.profiles import suggest_genre_profiles
        with _config_lock:
            cfg = _get_config()
            mixes = _load_mixes()
            new_profiles = suggest_genre_profiles(deps.subsonic, mixes)
            # Append only profiles not already in mixes (by id)
            existing_ids = {m["id"] for m in mixes}
            added = [p for p in new_profiles if p["id"] not in existing_ids]
            mixes.extend(added)
            cfg["mixes"] = mixes
            _atomic_write_config(cfg)
        _mix_wake.set()
        return jsonify({"status": "ok", "created": added})
    except Exception as e:
        logger.exception("[MIXES] suggest failed")
        return jsonify({"status": "error", "error": str(e)}), 500


# ── Follow routes ─────────────────────────────────────────────────────────────

@app.route("/follow/search", methods=["GET"])
def follow_search():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"results": []})
    mb, _ = _build_follow_clients()
    try:
        results = mb.search_artist(q, limit=8)
    except Exception:
        logger.warning("[FOLLOW] search failed", exc_info=True)
        return jsonify({"results": [], "error": "search_failed"}), 502
    return jsonify({"results": results})


@app.route("/follow", methods=["GET"])
def follow_list():
    from follow import store, fstate
    follows = store.list_follows(_FOLLOWS_PATH)
    summary = fstate.load(_FOLLOW_STATE_PATH).summary()
    return jsonify({"artists": follows, "state": summary})


@app.route("/follow", methods=["POST"])
def follow_add():
    from follow import store
    body = request.get_json(force=True, silent=True) or {}
    mbid = (body.get("mbid") or "").strip()
    name = (body.get("name") or "").strip()
    if not mbid or not name:
        return jsonify({"error": "mbid and name required"}), 400
    store.add_follow(_FOLLOWS_PATH, mbid=mbid, name=name,
                     disambiguation=body.get("disambiguation", ""))
    # kick a background run so backfill happens immediately
    threading.Thread(target=_run_follow_once, daemon=True).start()
    return jsonify({"status": "ok"})


@app.route("/follow/<mbid>", methods=["DELETE"])
def follow_remove(mbid):
    from follow import store
    store.remove_follow(_FOLLOWS_PATH, mbid)
    return jsonify({"status": "ok"})


@app.route("/follow/run", methods=["POST"])
def follow_run():
    result = _run_follow_once()
    return jsonify(result)


@app.route("/follow/feed", methods=["GET"])
def follow_feed():
    from follow import fstate
    st = fstate.load(_FOLLOW_STATE_PATH)
    return jsonify({"feed": list(reversed(st.feed())),
                    "unseen_count": st.summary()["unseen_count"]})


@app.route("/follow/feed/seen", methods=["POST"])
def follow_feed_seen():
    from follow import fstate
    st = fstate.load(_FOLLOW_STATE_PATH)
    st.mark_seen()
    st.save()
    return jsonify({"status": "ok"})


@app.route("/follow/settings", methods=["POST"])
def follow_settings():
    body = request.get_json(force=True, silent=True) or {}
    with _config_lock:
        cfg = _get_config()
        follow = dict(_FOLLOW_DEFAULTS)
        follow.update(cfg.get("follow") or {})
        for key in ("enabled", "run_hour", "lookback_days",
                    "default_backfill_days", "playlist_name", "playlist_cap"):
            if key in body:
                follow[key] = body[key]
        if "notify" in body and isinstance(body["notify"], dict):
            notify = dict(follow.get("notify") or {})
            notify.update(body["notify"])
            follow["notify"] = notify
        cfg["follow"] = follow
        _atomic_write_config(cfg)
    _follow_wake.set()
    return jsonify({"status": "ok", "follow": follow})


# ── YouTube search ────────────────────────────────────────────────────────────

@app.route("/yt/search", methods=["GET"])
def yt_search():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"status": "error", "error": "q required"}), 400
    try:
        limit = max(1, min(25, int(request.args.get("limit", 10))))
    except (TypeError, ValueError):
        limit = 10
    try:
        proc = subprocess.run(
            [_YT_DLP, "--flat-playlist", "-J", f"ytsearch{limit}:{q}"],
            capture_output=True, text=True, timeout=10)
        data = json.loads(proc.stdout or "{}")
        results = [{
            "source": "yt",
            "title": e.get("title") or "",
            "artist": e.get("uploader") or e.get("channel") or "",
            "duration": e.get("duration"),
            "url": e.get("url") or f"https://www.youtube.com/watch?v={e.get('id','')}",
        } for e in (data.get("entries") or []) if e]
        return jsonify({"results": results})
    except Exception as e:
        logger.warning("yt_search failed", exc_info=True)
        return jsonify({"results": [], "error": str(e)})


# ── Acquire route ────────────────────────────────────────────────────────────

@app.route("/acquire", methods=["POST"])
def acquire_url():
    body = request.get_json(force=True, silent=True) or {}
    url = (body.get("url") or "").strip()
    if not url:
        return jsonify({"status": "error", "error": "url required"}), 400
    from urllib.parse import urlparse
    p = urlparse(url)
    if p.scheme not in ("http", "https") or p.hostname not in _ACQUIRE_HOSTS:
        return jsonify({"status": "error", "error": "unsupported url"}), 400
    if re.search(r'youtube\.com/playlist\?', url, re.IGNORECASE):
        return jsonify({"status": "error", "error": "playlist URLs not supported; use a single track URL"}), 400
    with _acquire_lock:
        if url in _acquire_inflight:
            return jsonify({"status": "busy", "reason": "already downloading"}), 409
        _acquire_inflight.add(url)
    try:
        path = _download_url(url)
        return jsonify({"status": "ok" if path else "error", "path": path})
    finally:
        with _acquire_lock:
            _acquire_inflight.discard(url)


# ── Library suffixes ──────────────────────────────────────────────────────────

@app.route("/library/suffixes", methods=["GET"])
def library_suffixes_get():
    cfg = _get_config()
    suffix_file = cfg.get("title_cleanup", {}).get(
        "extra_suffixes_file", "title_suffixes.txt"
    )
    path = os.path.join(_PROJECT_ROOT, suffix_file)
    try:
        with open(path, encoding="utf-8") as f:
            lines = [l.strip() for l in f if l.strip() and not l.strip().startswith('#')]
    except FileNotFoundError:
        lines = []
    return jsonify({"suffixes": lines})


@app.route("/library/suffixes", methods=["POST"])
def library_suffixes_post():
    body = request.get_json(force=True, silent=True) or {}
    suffixes = body.get("suffixes")
    if not isinstance(suffixes, list):
        return jsonify({"status": "error", "error": "suffixes must be a list"}), 400
    cfg = _get_config()
    suffix_file = cfg.get("title_cleanup", {}).get(
        "extra_suffixes_file", "title_suffixes.txt"
    )
    path = os.path.join(_PROJECT_ROOT, suffix_file)
    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            for s in suffixes:
                f.write(str(s) + "\n")
        os.replace(tmp_path, path)
    except Exception as e:
        logger.exception("library_suffixes_post: write failed")
        return jsonify({"status": "error", "error": str(e)}), 500
    return jsonify({"status": "ok", "count": len(suffixes)})


# ── Explore UI ────────────────────────────────────────────────────────────────

# ── SoundCloud routes ─────────────────────────────────────────────────────────

def _get_sc_client():
    """Return SCClient if sc_client_id is configured, else None."""
    try:
        from soundcloud.client import SCClient
        cfg = _get_config()
        cid = cfg.get("sc_client_id", "")
        if not cid:
            return None
        return SCClient(cid, _CONFIG_PATH)
    except Exception:
        logger.warning("[SC] Could not build SCClient")
        return None


@app.route("/sc/resolve", methods=["GET"])
def sc_resolve():
    url = request.args.get("url", "")
    if not url:
        return jsonify({"status": "error", "error": "url required"}), 400
    sc = _get_sc_client()
    if not sc:
        return jsonify({"status": "unavailable", "reason": "sc_client_id not configured"})
    try:
        from soundcloud.mirror import get_profile
        result = get_profile(sc, url)
        return jsonify({"status": "ok", **result})
    except Exception as e:
        # 401 after failed client_id refresh → treat as "still connecting"
        if "401" in str(e):
            return jsonify({"status": "connecting", "reason": "SoundCloud auth refreshing", "retry_after": 15})
        logger.exception("[SC] resolve failed")
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/sc/search/users", methods=["GET"])
def sc_search_users():
    q = request.args.get("q", "")
    if not q:
        return jsonify({"status": "error", "error": "q required"}), 400
    if not _sc_client_ready:
        return jsonify({"status": "connecting", "reason": "SoundCloud client initializing", "retry_after": 30})
    sc = _get_sc_client()
    if not sc:
        return jsonify({"status": "unavailable", "reason": "sc_client_id not configured"})
    try:
        from soundcloud.search import search_users
        users = search_users(sc, q)
        return jsonify({"status": "ok", "users": users})
    except Exception as e:
        logger.exception("[SC] search/users failed")
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/sc/search/tracks", methods=["GET"])
def sc_search_tracks():
    q = request.args.get("q", "")
    if not q:
        return jsonify({"status": "error", "error": "q required"}), 400
    if not _sc_client_ready:
        return jsonify({"status": "connecting", "reason": "SoundCloud client initializing", "retry_after": 30})
    sc = _get_sc_client()
    if not sc:
        return jsonify({"status": "unavailable", "reason": "sc_client_id not configured"})
    try:
        from soundcloud.search import search_tracks
        tracks = search_tracks(sc, q)
        return jsonify({"status": "ok", "tracks": tracks})
    except Exception as e:
        logger.exception("[SC] search/tracks failed")
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/sc/preview", methods=["GET"])
def sc_preview():
    stream_url = request.args.get("stream_url", "")
    if not stream_url:
        return jsonify({"status": "error", "error": "stream_url required"}), 400
    sc = _get_sc_client()
    if not sc:
        return jsonify({"status": "unavailable", "reason": "sc_client_id not configured"})
    composed = f"{stream_url}?client_id={sc.client_id}"
    return jsonify({"status": "ok", "stream_url": composed})


# ── Spotify routes ────────────────────────────────────────────────────────────

def _get_spotify_client():
    """Return SpotifyClient or None if cipher fetch failed."""
    try:
        from spotify.client import SpotifyClient
        return SpotifyClient()
    except Exception as e:
        logger.warning("[SPOTIFY] Could not build SpotifyClient: %s", e)
        return None


@app.route("/spotify/artist", methods=["POST"])
def spotify_artist():
    body = request.get_json(force=True, silent=True) or {}
    uri_or_url = body.get("uri") or body.get("url", "")
    if not uri_or_url:
        return jsonify({"status": "error", "error": "uri or url required"}), 400
    sp = _get_spotify_client()
    if not sp:
        return jsonify({"status": "unavailable", "reason": "cipher fetch failed"})
    try:
        from spotify.queries import get_artist_overview
        result = get_artist_overview(sp, uri_or_url)
        return jsonify({"status": "ok", **result})
    except Exception as e:
        logger.exception("[SPOTIFY] artist failed")
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/spotify/playlist", methods=["POST"])
def spotify_playlist():
    body = request.get_json(force=True, silent=True) or {}
    uri_or_url = body.get("uri") or body.get("url", "")
    limit = int(body.get("limit", 50))
    if not uri_or_url:
        return jsonify({"status": "error", "error": "uri or url required"}), 400
    sp = _get_spotify_client()
    if not sp:
        return jsonify({"status": "unavailable", "reason": "cipher fetch failed"})
    try:
        from spotify.queries import get_playlist
        result = get_playlist(sp, uri_or_url, limit=limit)
        return jsonify({"status": "ok", **result})
    except Exception as e:
        logger.exception("[SPOTIFY] playlist failed")
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/spotify/search", methods=["GET"])
def spotify_search():
    q = request.args.get("q", "")
    if not q:
        return jsonify({"status": "error", "error": "q required"}), 400
    sp = _get_spotify_client()
    if not sp:
        return jsonify({"status": "unavailable", "reason": "cipher fetch failed"})
    try:
        from spotify.queries import search_artists
        artists = search_artists(sp, q)
        return jsonify({"status": "ok", "artists": artists})
    except Exception as e:
        logger.exception("[SPOTIFY] search failed")
        return jsonify({"status": "error", "error": str(e)}), 500


# ── Universal preview ─────────────────────────────────────────────────────────

@app.route("/preview", methods=["GET"])
def preview():
    source = request.args.get("source", "unknown")
    url    = request.args.get("url", "")
    artist = request.args.get("artist", "")
    title  = request.args.get("title", "")

    try:
        if source == "sc" and url:
            sc = _get_sc_client()
            if sc:
                stream = f"{url}?client_id={sc.client_id}"
                return jsonify({"status": "ok", "stream_url": stream})
            return jsonify({"status": "error", "error": "sc not configured"}), 503

        if source in ("yt", "unknown") or url:
            import subprocess, json as _json
            query = url if url else f"ytsearch:{artist} {title}"
            try:
                result = subprocess.run(
                    [".venv/bin/yt-dlp", "--dump-json", "-f", "bestaudio/best",
                     "--no-playlist", query],
                    capture_output=True, text=True, timeout=20,
                    cwd=_PROJECT_ROOT,
                )
                if result.stdout.strip():
                    info = _json.loads(result.stdout.strip().splitlines()[0])
                    stream = info.get("url")
                    return jsonify({
                        "status": "ok",
                        "stream_url": stream,
                        "title": info.get("title", ""),
                        "artist": info.get("uploader") or info.get("channel") or "",
                        "thumbnail": info.get("thumbnail", ""),
                    })
            except Exception:
                pass
            return jsonify({"status": "ok", "stream_url": None})

        return jsonify({"status": "ok", "stream_url": None})
    except Exception as e:
        logger.exception("[PREVIEW] failed")
        return jsonify({"status": "error", "error": str(e)}), 500


# ── Import tracks / status ─────────────────────────────────────────────────────

@app.route("/import/tracks", methods=["POST"])
def import_tracks():
    body = request.get_json(force=True, silent=True) or {}
    tracks = body.get("tracks", [])
    playlist_name = body.get("playlist_name", "Import")
    if not tracks:
        return jsonify({"status": "error", "error": "tracks required"}), 400

    job_id = str(uuid.uuid4())
    job = {
        "total": len(tracks),
        "done": 0,
        "errors": 0,
        "tracks": [{"title": t.get("title", "?"), "artist": t.get("artist", ""), "status": "queued"}
                   for t in tracks],
    }
    _import_jobs[job_id] = job

    def _run_import():
        cfg = _get_config()
        song_dir = cfg.get("song_dir", "") or str(os.path.join(os.path.expanduser("~"), "Music"))
        os.makedirs(song_dir, exist_ok=True)

        dl_path = os.path.join(_PROJECT_ROOT, "scripts/sTownload/script_web.py")
        sc_path = os.path.join(_PROJECT_ROOT, "scripts/Sc2Sp_src/script_web.py")

        for i, track in enumerate(tracks):
            job["tracks"][i]["status"] = "downloading"
            url = track.get("url", "")
            artist = track.get("artist", "")
            title_  = track.get("title", "")
            source = track.get("source", "unknown")
            try:
                if source == "sc" or (url and "soundcloud.com" in url):
                    spec = importlib.util.spec_from_file_location("sc_web", sc_path)
                    mod  = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    mp3 = mod.download_single_soundcloud(url, song_dir)
                elif url:
                    spec = importlib.util.spec_from_file_location("yt_web", dl_path)
                    mod  = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    _, paths = mod.download_url(url, song_dir)
                    mp3 = paths[0] if paths else None
                else:
                    spec = importlib.util.spec_from_file_location("yt_web2", dl_path)
                    mod  = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    _, paths = mod.download_url(f"ytsearch:{artist} {title_}", song_dir)
                    mp3 = paths[0] if paths else None

                if mp3 and os.path.exists(mp3):
                    if url:
                        try:
                            from library.tagger import write_source_url
                            write_source_url(mp3, url)
                        except Exception:
                            pass
                    job["tracks"][i]["status"] = "done"
                    job["done"] += 1
                else:
                    job["tracks"][i]["status"] = "error"
                    job["errors"] += 1
            except Exception:
                logger.exception("[IMPORT] track failed: %s", title_)
                job["tracks"][i]["status"] = "error"
                job["errors"] += 1

        try:
            from discover.config import load_config
            from discover.subsonic import Subsonic
            cfg2 = load_config(_CONFIG_PATH)
            host = cfg2.get("navidrome_url", "")
            user = cfg2.get("navidrome_user", "")
            pw   = cfg2.get("navidrome_pass", "")
            if host and user and pw:
                sub = Subsonic(host, user, pw)
                sub.start_scan()
        except Exception:
            pass

        # Create Navidrome playlist with successfully downloaded tracks
        if playlist_name and playlist_name != "Import":
            try:
                from discover.subsonic import Subsonic
                cfg3 = _get_config()
                sub3 = Subsonic(cfg3.get("navidrome_url",""), cfg3.get("navidrome_user",""), cfg3.get("navidrome_pass",""))
                # search for each downloaded track by title+artist and collect song IDs
                song_ids = []
                for i, track in enumerate(tracks):
                    if job["tracks"][i]["status"] == "done":
                        results = sub3.search_songs(
                            f"{track.get('title', '')} {track.get('artist', '')}".strip(),
                            count=1,
                        )
                        if results:
                            song_ids.append(results[0].get("id"))
                if song_ids:
                    sub3.create_or_update_playlist(playlist_name, song_ids)
            except Exception:
                logger.exception("[IMPORT] playlist creation failed")

        # Write failed tracks log
        failed_tracks = [t for t in job["tracks"] if t["status"] == "error"]
        if failed_tracks and playlist_name:
            import re
            safe = re.sub(r"[^\w\-]", "_", playlist_name)
            logs_dir = os.path.join(_PROJECT_ROOT, "logs")
            os.makedirs(logs_dir, exist_ok=True)
            with open(os.path.join(logs_dir, f"import_{safe}_failed.txt"), "w", encoding="utf-8") as lf:
                for t in failed_tracks:
                    lf.write(f"{t.get('artist','')} — {t.get('title','')}\n")

    t = threading.Thread(target=_run_import, daemon=True)
    t.start()
    return jsonify({"status": "ok", "job_id": job_id, "queued": len(tracks)})


@app.route("/import/status", methods=["GET"])
def import_status():
    job_id = request.args.get("job_id", "")
    job = _import_jobs.get(job_id)
    if not job:
        return jsonify({"status": "error", "error": "job not found"}), 404
    return jsonify({"status": "ok", **job})


# ── Share routes ──────────────────────────────────────────────────────────────

@app.route("/share/link", methods=["GET"])
def share_link():
    artist = request.args.get("artist", "")
    title  = request.args.get("title", "")
    url    = request.args.get("url", "")
    if not artist or not title:
        return jsonify({"status": "error", "error": "artist and title required"}), 400
    try:
        from share.codec import encode_track
        share_url = encode_track(artist, title, url or None)
        return jsonify({"status": "ok", "url": share_url})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/share/code", methods=["GET"])
def share_code():
    playlist_id = request.args.get("playlist_id", "")
    if not playlist_id:
        return jsonify({"status": "error", "error": "playlist_id required"}), 400
    try:
        import urllib.parse as _up
        import urllib.request as _ur
        cfg = _get_config()
        host  = cfg.get("navidrome_url", "http://localhost:4533")
        nuser = cfg.get("navidrome_user", "")
        npw   = cfg.get("navidrome_pass", "")
        params = _up.urlencode({"u": nuser, "p": npw, "v": "1.16.1", "c": "amusicserver", "f": "json",
                                "id": playlist_id})
        url = f"{host}/rest/getPlaylist.view?{params}"
        with _ur.urlopen(url, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        pl = data.get("subsonic-response", {}).get("playlist", {})
        name = pl.get("name", "Playlist")
        tracks_raw = pl.get("entry", []) or []
        tracks = []
        for t in tracks_raw:
            tracks.append({"artist": t.get("artist", ""), "title": t.get("title", ""), "url": ""})
        from share.codec import encode_playlist
        text = encode_playlist(name, tracks)
        return jsonify({"status": "ok", "text": text})
    except Exception as e:
        logger.exception("[SHARE] code generation failed")
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/share/parse", methods=["POST"])
def share_parse():
    body = request.get_json(force=True, silent=True) or {}
    text = body.get("text", "")
    if not text:
        return jsonify({"status": "error", "error": "text required"}), 400
    try:
        from share.codec import decode
        result = decode(text)
        return jsonify({"status": "ok", **result})
    except ValueError as e:
        return jsonify({"status": "error", "error": str(e)}), 400
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/share/import", methods=["GET"])
def share_import():
    """Receive shared link — redirect to explore page with payload in fragment."""
    d = request.args.get("d", "")
    if d:
        return redirect(f"/explore#import:{d}")
    return redirect("/explore")


# ── Startup + zeroconf ────────────────────────────────────────────────────────

def _start_zeroconf(hostname: str, port: int = 5000):
    """Register mDNS service. Logs warning and returns on failure."""
    try:
        import socket
        from zeroconf import Zeroconf, ServiceInfo
        local_ip = socket.gethostbyname(socket.gethostname())
        packed_ip = socket.inet_aton(local_ip)
        info = ServiceInfo(
            "_http._tcp.local.",
            "amusicserver._http._tcp.local.",
            addresses=[packed_ip],
            port=port,
            properties={},
            server=f"{hostname}.",
        )
        zc = Zeroconf()
        zc.register_service(info)
        logger.info("[MDNS] Registered %s → %s:%d", hostname, local_ip, port)
        return zc
    except Exception as e:
        logger.warning("[MDNS] Could not register mDNS service: %s", e)
        return None


def start_background_server(port: int = 5000):
    logger.info("Server starting on port %d (pid=%d, root=%s)", port, os.getpid(), _PROJECT_ROOT)

    t_ref = threading.Thread(target=_refresh_sc_client_id_loop, args=(3600,), daemon=True)
    t_ref.start()

    t_mix = threading.Thread(target=_mix_scheduler_loop, daemon=True)
    t_mix.start()

    t_follow = threading.Thread(target=_follow_scheduler_loop, daemon=True)
    t_follow.start()

    from discover.config import load_config as _load_cfg
    _cfg_at_start = _load_cfg(_CONFIG_PATH)
    if _cfg_at_start.get("dedup", {}).get("enabled"):
        t_dedup = threading.Thread(target=_dedup_scheduled_loop, daemon=True)
        t_dedup.start()

    hostname = _cfg_at_start.get("hostname", "amusicserver.local")
    _start_zeroconf(hostname, port)

    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    start_background_server(port=5000)
