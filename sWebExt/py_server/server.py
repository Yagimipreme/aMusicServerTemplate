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

# ── Enrich state ──────────────────────────────────────────────────────────────

_enrich_running = threading.Lock()
_enrich_last_result: dict = {"status": "idle"}

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

    state_path = os.path.join(_PROJECT_ROOT, "discover_state.json")
    return SimpleNamespace(
        subsonic=Subsonic(host, user, pw),
        search_fn=make_search_fn(),
        download_fn=make_download_fn(lambda url: dl_mod.download_url(url, song_dir)),
        state=load_state(state_path),
        song_dir=song_dir,
    )


def _run_discover_once():
    try:
        deps = _build_discover_deps()
        if deps is None:
            return {"status": "disabled", "reason": "navidrome creds missing"}
        from discover.engine import run_weekly
        cfg = {}
        try:
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            pass
        disc = cfg.get("discover") or {}
        count = disc.get("weekly_count", 30)
        playlist_name = disc.get("playlist_name", "Weekly Mix")
        result = run_weekly(deps, count=count, playlist_name=playlist_name)
        logger.info("[DISCOVER] run complete: %s", result)
        return {"status": "ok", **result}
    except Exception as e:
        logger.exception("[DISCOVER] run failed")
        return {"status": "error", "error": str(e)}


def _discover_weekly_loop(period_seconds=7 * 24 * 3600):
    while True:
        logger.info("[DISCOVER] weekly cycle starting")
        _run_discover_once()
        time.sleep(period_seconds)


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


def _refresh_sc_client_id_loop(period_seconds=3600):
    script_fp = os.path.join(_PROJECT_ROOT, "scripts/Sc2Sp_src/script_web.py")
    cycle = 0
    while True:
        cycle += 1
        try:
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
                        if cid:
                            cfg = {}
                            if os.path.exists(_CONFIG_PATH):
                                with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                                    cfg = json.load(f)
                            cfg["sc_client_id"] = cid
                            cfg["sc_client_id_ts"] = int(time.time())
                            with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
                                json.dump(cfg, f, ensure_ascii=False, indent=2)
                            logger.info("[SC-REFRESH] Persisted sc_client_id")
                    except Exception:
                        logger.exception("[SC-REFRESH] fetch_client_id_via_selenium raised")
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


@app.route("/discover/run", methods=["POST"])
def discover_run():
    result = _run_discover_once()
    code = 200 if result.get("status") in ("ok", "disabled") else 500
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
        logger.exception("[SC] resolve failed")
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/sc/search/users", methods=["GET"])
def sc_search_users():
    q = request.args.get("q", "")
    if not q:
        return jsonify({"status": "error", "error": "q required"}), 400
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
            import subprocess
            query = url if url else f"ytsearch:{artist} {title}"
            try:
                result = subprocess.run(
                    [".venv/bin/yt-dlp", "--get-url", "-f", "bestaudio/best", "--no-playlist", query],
                    capture_output=True, text=True, timeout=15,
                    cwd=_PROJECT_ROOT,
                )
                stream = result.stdout.strip().splitlines()[0] if result.stdout.strip() else None
                if stream:
                    return jsonify({"status": "ok", "stream_url": stream})
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

    t_disc = threading.Thread(target=_discover_weekly_loop, daemon=True)
    t_disc.start()

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
