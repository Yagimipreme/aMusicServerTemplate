"""Flask-based HTTP server — replaces stdlib HTTPServer.

All existing routes are preserved 1:1. New routes added per spec.
"""
import json
import logging
import os
import re
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

# ── Repair state ──────────────────────────────────────────────────────────────

_repair_running = threading.Lock()
_repair_last_result: dict = {"status": "idle"}

# ── Discover run state ────────────────────────────────────────────────────────

_discover_running = threading.Lock()

# ── Dedup state ───────────────────────────────────────────────────────────────

_dedup_running = threading.Lock()


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


def _run_discover_once():
    try:
        deps = _build_discover_deps()
        if deps is None:
            return {"status": "disabled", "reason": "navidrome creds missing"}
        from discover.engine import run_mix
        cfg = {}
        try:
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            pass
        result = run_mix(deps, cfg)
        logger.info("[DISCOVER] run complete: %s", result)
        return {"status": "ok", **result}
    except Exception as e:
        logger.exception("[DISCOVER] run failed")
        return {"status": "error", "error": str(e)}


def _run_discover_daily_once():
    try:
        deps = _build_discover_deps()
        if deps is None:
            return {"status": "disabled", "reason": "navidrome creds missing"}
        from discover.engine import run_daily
        cfg = {}
        try:
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            pass
        result = run_daily(deps, cfg)
        logger.info("[DISCOVER-DAILY] run complete: %s", result)
        return {"status": "ok", **result}
    except Exception as e:
        logger.exception("[DISCOVER-DAILY] run failed")
        return {"status": "error", "error": str(e)}


def _seconds_until_next_run(schedule: str, run_day: str, run_hour: int) -> float:
    import datetime as _dt
    now = _dt.datetime.now()
    if schedule == "daily":
        candidate = now.replace(hour=run_hour, minute=0, second=0, microsecond=0)
        if candidate <= now:
            candidate += _dt.timedelta(days=1)
        return (candidate - now).total_seconds()
    else:  # weekly
        day_map = {"sun": 6, "mon": 0, "tue": 1, "wed": 2,
                   "thu": 3, "fri": 4, "sat": 5}
        target_wd = day_map.get(run_day.lower()[:3], 6)
        days_ahead = (target_wd - now.weekday()) % 7
        candidate = (now + _dt.timedelta(days=days_ahead)).replace(
            hour=run_hour, minute=0, second=0, microsecond=0)
        if candidate <= now:
            candidate += _dt.timedelta(weeks=1)
        return (candidate - now).total_seconds()


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
        return result
    except Exception as e:
        logger.exception("[MIXES] %s run failed", profile.get("id"))
        return {"status": "error", "error": str(e)}
    finally:
        _discover_running.release()


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

    if not has_last_run:
        try:
            cfg_initial = _get_config()
            host_i = cfg_initial.get("navidrome_url", "")
            user_i = cfg_initial.get("navidrome_user", "")
            pw_i = cfg_initial.get("navidrome_pass", "")
            if host_i and user_i and pw_i:
                from discover.subsonic import Subsonic as _Sub
                sub_i = _Sub(host_i, user_i, pw_i)
                artists_i = sub_i.get_frequent_artists(size=1)
                if artists_i:
                    logger.info("[MIXES] No last_run found and library has songs — running initial weekly mix")
                    mixes_init = _load_mixes()
                    weekly = next((m for m in mixes_init if m.get("id") == "weekly" and m.get("enabled")), None)
                    if weekly:
                        _run_profile_once(weekly)
        except Exception:
            logger.warning("[MIXES] initial-run check failed", exc_info=True)

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
            now = datetime.datetime.now()
            for m in [m for m in _load_mixes() if m.get("enabled")]:
                if _profile_next_run(m, now - datetime.timedelta(minutes=1)) <= now:
                    _run_profile_once(m)         # sequential; failures logged inside
        except Exception:
            logger.exception("[MIXES] scheduler iteration failed; retrying in 3600s")
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
        api_key = cfg.get("lastfm_api_key", "")
        if not api_key:
            result = {"status": "disabled", "reason": "lastfm_api_key not configured"}
            _enrich_last_result = result
            return result
        song_dir = cfg.get("song_dir", "")
        if not song_dir:
            result = {"status": "disabled", "reason": "song_dir not set"}
            _enrich_last_result = result
            return result
        enrich_cfg = cfg.get("enrich") or {}
        only_missing = enrich_cfg.get("only_missing_genre", True)
        from lastfm.client import LastFMClient
        from library.enrich import run as enrich_run
        lfm = LastFMClient(api_key)
        result = enrich_run(song_dir, lfm, only_missing_genre=only_missing, limit=limit)
        result["status"] = "ok"
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


def _get_hostname() -> str:
    return _get_config().get("hostname", "amusicserver.local")


# ── Existing routes (migrated) ────────────────────────────────────────────────

@app.route("/", methods=["GET"])
def root_get():
    return jsonify({"status": "ok", "pid": os.getpid(), "cwd": os.getcwd()})


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
    # Discovery group
    {"path": "discover.schedule",            "type": "str",       "label": "Weekly schedule",          "group": "Discovery"},
    {"path": "discover.run_day",             "type": "str",       "label": "Run day (weekly)",          "group": "Discovery"},
    {"path": "discover.run_hour",            "type": "int",       "label": "Run hour (0–23)",           "group": "Discovery", "min": 0, "max": 23},
    {"path": "discover.weekly_count",        "type": "int",       "label": "Weekly mix size",           "group": "Discovery", "min": 1, "max": 200},
    {"path": "discover.playlist_cap",        "type": "int",       "label": "Playlist size cap",         "group": "Discovery", "min": 1, "max": 1000},
    {"path": "discover.suggested_ttl_days",  "type": "int",       "label": "Suggestion memory (days)", "group": "Discovery", "min": 1, "max": 365},
    {"path": "discover.min_artist_listeners","type": "int",       "label": "Min artist listeners",      "group": "Discovery", "min": 0, "max": 10000000},
    {"path": "discover.candidate_oversample","type": "int",       "label": "Candidate oversample",      "group": "Discovery", "min": 1, "max": 20},
    {"path": "discover.daily.enabled",       "type": "bool",      "label": "Daily mix enabled",         "group": "Discovery"},
    {"path": "discover.daily.count",         "type": "int",       "label": "Daily mix size",            "group": "Discovery", "min": 1, "max": 50},
    {"path": "discover.daily.run_hour",      "type": "int",       "label": "Daily run hour (0–23)",     "group": "Discovery", "min": 0, "max": 23},
    {"path": "discover.daily.window_days",   "type": "int",       "label": "Daily window (days)",       "group": "Discovery", "min": 1, "max": 30},
    {"path": "discover.daily.playlist_name", "type": "str",       "label": "Daily playlist name",       "group": "Discovery"},
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
    if not _discover_running.acquire(blocking=False):
        return jsonify({"status": "busy", "reason": "another discover run in progress"}), 409
    try:
        result = _run_discover_once()
    finally:
        _discover_running.release()
    code = 200 if result.get("status") in ("ok", "disabled") else 500
    return jsonify(result), code


@app.route("/discover/run_daily", methods=["POST"])
def discover_run_daily():
    if not _discover_running.acquire(blocking=False):
        return jsonify({"status": "busy", "reason": "another discover run in progress"}), 409
    try:
        result = _run_discover_daily_once()
    finally:
        _discover_running.release()
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
    body = request.get_json(force=True, silent=True) or {}
    limit = body.get("limit", None)
    t = threading.Thread(target=_run_enrich_once, kwargs={"limit": limit}, daemon=True)
    t.start()
    return jsonify({"status": "started"})


@app.route("/library/enrich/status", methods=["GET"])
def enrich_status():
    return jsonify(_enrich_last_result)


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


# ── Explore UI ────────────────────────────────────────────────────────────────

@app.route("/explore", methods=["GET"])
def explore():
    hostname = _get_hostname()
    return render_template("explore.html", hostname=hostname, port=5000)


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
