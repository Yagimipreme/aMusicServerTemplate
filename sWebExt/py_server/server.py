from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import os
import re
import sys
import logging
import threading
import runpy
import importlib.util
import time
import shutil

# ── Paths ──────────────────────────────────────────────────────────────────────

_SERVER_DIR   = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SERVER_DIR, "../../"))
_CONFIG_PATH  = os.path.join(_PROJECT_ROOT, "config.json")
_LOG_DIR      = os.path.join(_PROJECT_ROOT, "logs")

os.makedirs(_LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(_LOG_DIR, 'server.log')

# ── Logging (file + console) ───────────────────────────────────────────────────

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format='%(asctime)s %(levelname)s: %(message)s',
)
logger = logging.getLogger(__name__)
logger.addHandler(logging.StreamHandler())


# ── Discover engine wiring ─────────────────────────────────────────────────────

def _build_discover_deps():
    """Assemble engine dependencies from config + existing downloader. Returns deps or None."""
    from types import SimpleNamespace

    if _PROJECT_ROOT not in sys.path:  # make `discover` importable
        sys.path.insert(0, _PROJECT_ROOT)
    from discover.config import load_config
    from discover.subsonic import Subsonic
    from discover.state import load_state
    from discover.ytdlp_adapter import make_search_fn, make_download_fn

    cfg = load_config(_CONFIG_PATH)
    host = cfg.get("navidrome_url", "")
    user = cfg.get("navidrome_user", "")
    pw = cfg.get("navidrome_pass", "")
    if not host or not user or not pw:
        logger.warning("[DISCOVER] navidrome creds missing — engine disabled")
        return None

    # Reuse the existing YouTube downloader's download_url(url, out_dir) + song_dir.
    # download_url returns (playlist_title, [mp3_paths]); acquire() handles that tuple.
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
    """Build deps and run one weekly pass. Best-effort; logs and swallows errors."""
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


# ── HTTP handler ───────────────────────────────────────────────────────────────

class SimpleHandler(BaseHTTPRequestHandler):
    def _set_headers(self, status_code=200):
        self.send_response(status_code)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Content-Type', 'application/json')
        self.end_headers()

    def do_OPTIONS(self):
        self._set_headers(204)

    def do_POST(self):
        content_length = int(self.headers.get('Content-Length') or 0)
        post_data = self.rfile.read(content_length)

        if self.path.rstrip('/') == '/discover/run':
            result = _run_discover_once()
            code = 200 if result.get("status") in ("ok", "disabled") else 500
            self._set_headers(code)
            self.wfile.write(json.dumps(result).encode())
            return

        try:
            data = json.loads(post_data.decode('utf-8'))
            incoming_url = data.get('url', '')
            m3u_file = data.get('m3u', 'default_playlist')
            logger.info('Received POST from %s: url=%s m3u=%s', self.client_address, incoming_url, m3u_file)

            script_to_run = None

            if re.search(r"https?://(www\.)?youtube\.", incoming_url):
                candidates = [
                    "scripts/sTownload/script_web.py",
                    "scripts/sTownload/app.py",
                ]
            elif re.search(r"https?://(www\.)?soundcloud\.", incoming_url):
                candidates = [
                    "scripts/Sc2Sp_src/script_web.py",
                ]
            else:
                candidates = []

            for cand in candidates:
                cand_path = os.path.join(_PROJECT_ROOT, cand)
                if os.path.exists(cand_path):
                    script_to_run = cand_path
                    logger.info('Selected script: %s', script_to_run)
                    break

            if script_to_run:
                def _run_script(path, url_arg, m3u_arg):
                    try:
                        script_dir = os.path.dirname(path)
                        old_argv = sys.argv[:]
                        old_cwd  = os.getcwd()

                        if shutil.which('ffmpeg') is None:
                            logger.warning('ffmpeg not found on PATH')

                        # Ensure the script's directory is on sys.path so
                        # relative package imports (e.g. "import Sc2Sp.script2")
                        # work when the file is loaded via runpy.
                        if script_dir not in sys.path:
                            sys.path.insert(0, script_dir)

                        try:
                            sys.argv = [path, url_arg, m3u_arg]
                            os.chdir(script_dir)
                            logger.info('Executing script in-thread: %s', path)
                            runpy.run_path(path, run_name='__main__')
                            logger.info('Script finished: %s', path)
                        finally:
                            sys.argv = old_argv
                            try:
                                os.chdir(old_cwd)
                            except Exception:
                                pass
                    except Exception:
                        logger.exception('Unhandled exception while running script: %s', path)

                try:
                    t = threading.Thread(target=_run_script, args=(script_to_run, incoming_url, m3u_file), daemon=True)
                    t.start()
                    response_msg = {"status": "started", "script": script_to_run}
                    code = 200
                except Exception:
                    logger.exception('Failed to start in-thread script')
                    response_msg = {"status": "error", "message": "could not start script thread"}
                    code = 500
            else:
                logger.warning('No matching script found for URL: %s', incoming_url)
                response_msg = {"status": "ignored", "reason": "no matching script found or URL not supported"}
                code = 404

        except Exception as e:
            response_msg = {"status": "error", "message": str(e)}
            code = 400

        self._set_headers(code)
        self.wfile.write(json.dumps(response_msg).encode())

    def do_GET(self):
        try:
            # Route on path so we can serve the extension's "pull playlists"
            # button without exposing Navidrome creds to the browser side.
            if self.path.rstrip('/') == '/playlists':
                return self._get_playlists()
            self._set_headers(200)
            self.wfile.write(json.dumps({"status": "ok", "pid": os.getpid(), "cwd": os.getcwd()}).encode())
        except Exception:
            logger.exception('GET failed')
            self._set_headers(500)
            self.wfile.write(json.dumps({"status": "error"}).encode())

    def _get_playlists(self):
        """Proxy Navidrome's getPlaylists.view → minimal JSON for the extension."""
        import urllib.parse as _up
        import urllib.request as _ur

        try:
            cfg = {}
            if os.path.exists(_CONFIG_PATH):
                with open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
        except Exception:
            logger.exception('GET /playlists: could not read config')
            cfg = {}

        host = cfg.get('navidrome_url', 'http://localhost:4533')
        nuser = cfg.get('navidrome_user', '')
        npw   = cfg.get('navidrome_pass', '')
        if not nuser or not npw:
            self._set_headers(503)
            self.wfile.write(json.dumps({
                "status": "error",
                "error": "navidrome credentials not configured in config.json",
            }).encode())
            return

        params = _up.urlencode({
            'u': nuser, 'p': npw, 'v': '1.16.1', 'c': 'amusicserver-ext', 'f': 'json'
        })
        url = f"{host}/rest/getPlaylists.view?{params}"
        try:
            with _ur.urlopen(url, timeout=8) as resp:
                data = json.loads(resp.read().decode('utf-8'))
        except Exception as e:
            logger.exception('GET /playlists: upstream call failed')
            self._set_headers(502)
            self.wfile.write(json.dumps({"status": "error", "error": str(e)}).encode())
            return

        sr = data.get('subsonic-response', {})
        if sr.get('status') != 'ok':
            err = sr.get('error', {}).get('message', 'unknown navidrome error')
            self._set_headers(502)
            self.wfile.write(json.dumps({"status": "error", "error": err}).encode())
            return

        raw = sr.get('playlists', {}).get('playlist', []) or []
        playlists = [
            {"name": p.get("name", ""), "songCount": p.get("songCount", 0)}
            for p in raw if p.get("name")
        ]
        self._set_headers(200)
        self.wfile.write(json.dumps({"status": "ok", "playlists": playlists}).encode())


# ── SoundCloud client_id background refresher ──────────────────────────────────

def _refresh_sc_client_id_loop(period_seconds=3600):
    """Periodically fetch and persist a fresh SoundCloud client_id via Selenium.
    Best-effort: failures are logged and ignored.
    """
    script_fp = os.path.join(_PROJECT_ROOT, 'scripts/Sc2Sp_src/script_web.py')
    cycle = 0

    while True:
        cycle += 1
        logger.info('[SC-REFRESH] Cycle %d starting (interval=%ds)', cycle, period_seconds)

        try:
            if not os.path.exists(script_fp):
                logger.warning('[SC-REFRESH] sc2 helper not found at: %s', script_fp)
            else:
                logger.debug('[SC-REFRESH] Loading sc2 helper: %s', script_fp)
                spec   = importlib.util.spec_from_file_location('sc2_web_helper', script_fp)
                module = importlib.util.module_from_spec(spec)
                try:
                    spec.loader.exec_module(module)
                    logger.debug('[SC-REFRESH] sc2 helper loaded OK')
                except Exception:
                    logger.exception('[SC-REFRESH] Failed to load sc2 helper module')
                    module = None

                if module is None:
                    logger.warning('[SC-REFRESH] Skipping cycle %d — module load failed', cycle)
                elif not hasattr(module, 'fetch_client_id_via_selenium'):
                    logger.warning('[SC-REFRESH] fetch_client_id_via_selenium not found in module')
                else:
                    logger.info('[SC-REFRESH] Launching headless Chrome to fetch client_id …')
                    try:
                        cid = module.fetch_client_id_via_selenium()
                        if cid:
                            logger.info('[SC-REFRESH] Got client_id: %s…%s', cid[:4], cid[-4:])
                            try:
                                cfg = {}
                                if os.path.exists(_CONFIG_PATH):
                                    with open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
                                        cfg = json.load(f)
                                cfg['sc_client_id']    = cid
                                cfg['sc_client_id_ts'] = int(time.time())
                                with open(_CONFIG_PATH, 'w', encoding='utf-8') as f:
                                    json.dump(cfg, f, ensure_ascii=False, indent=2)
                                logger.info('[SC-REFRESH] Persisted sc_client_id to config')
                            except Exception:
                                logger.exception('[SC-REFRESH] Failed to persist sc_client_id')
                        else:
                            logger.warning('[SC-REFRESH] fetch_client_id_via_selenium returned None — token unchanged')
                    except Exception:
                        logger.exception('[SC-REFRESH] fetch_client_id_via_selenium raised an exception')
        except Exception:
            logger.exception('[SC-REFRESH] Unhandled error in cycle %d', cycle)

        logger.info('[SC-REFRESH] Cycle %d done — sleeping %ds', cycle, period_seconds)
        time.sleep(period_seconds)


# ── Entry point ────────────────────────────────────────────────────────────────

def start_background_server(port=5000):
    logger.info('Server starting on port %d (pid=%d, root=%s)', port, os.getpid(), _PROJECT_ROOT)

    t_ref = threading.Thread(target=_refresh_sc_client_id_loop, args=(3600,), daemon=True)
    t_ref.start()
    logger.info('Started background sc_client_id refresher thread')

    t_disc = threading.Thread(target=_discover_weekly_loop, daemon=True)
    t_disc.start()
    logger.info('Started background discover weekly thread')

    httpd = HTTPServer(('0.0.0.0', port), SimpleHandler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info('Server stopped by KeyboardInterrupt')
    except Exception:
        logger.exception('Server encountered an error')


if __name__ == "__main__":
    start_background_server(port=5000)
