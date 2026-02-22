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
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)

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

                        try:
                            if 'soundcloud' in url_arg:
                                sys.argv = [path, '-one', url_arg]
                            else:
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
            self._set_headers(200)
            self.wfile.write(json.dumps({"status": "ok", "pid": os.getpid(), "cwd": os.getcwd()}).encode())
        except Exception:
            logger.exception('GET failed')
            self._set_headers(500)
            self.wfile.write(json.dumps({"status": "error"}).encode())


# ── SoundCloud client_id background refresher ──────────────────────────────────

def _refresh_sc_client_id_loop(period_seconds=3600):
    """Periodically fetch and persist a fresh SoundCloud client_id via Selenium.
    Best-effort: failures are logged and ignored.
    """
    script_fp = os.path.join(_PROJECT_ROOT, 'scripts/Sc2Sp_src/script_web.py')

    while True:
        try:
            if os.path.exists(script_fp):
                spec   = importlib.util.spec_from_file_location('sc2_web_helper', script_fp)
                module = importlib.util.module_from_spec(spec)
                try:
                    spec.loader.exec_module(module)
                except Exception:
                    logger.exception('Failed importing sc2 helper module')
                    module = None

                if module and hasattr(module, 'fetch_client_id_via_selenium'):
                    try:
                        cid = module.fetch_client_id_via_selenium()
                        if cid:
                            try:
                                cfg = {}
                                if os.path.exists(_CONFIG_PATH):
                                    with open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
                                        cfg = json.load(f)
                                cfg['sc_client_id']    = cid
                                cfg['sc_client_id_ts'] = int(time.time())
                                with open(_CONFIG_PATH, 'w', encoding='utf-8') as f:
                                    json.dump(cfg, f, ensure_ascii=False, indent=2)
                                logger.info('Persisted sc_client_id from background refresher')
                            except Exception:
                                logger.exception('Failed to persist sc_client_id')
                    except Exception:
                        logger.exception('fetch_client_id_via_selenium failed')
            else:
                logger.debug('sc2 helper not found for client_id refresh: %s', script_fp)
        except Exception:
            logger.exception('Unhandled error in sc_client_id refresher')

        time.sleep(period_seconds)


# ── Entry point ────────────────────────────────────────────────────────────────

def start_background_server(port=5000):
    logger.info('Server starting on port %d (pid=%d, root=%s)', port, os.getpid(), _PROJECT_ROOT)

    t_ref = threading.Thread(target=_refresh_sc_client_id_loop, args=(3600,), daemon=True)
    t_ref.start()
    logger.info('Started background sc_client_id refresher thread')

    httpd = HTTPServer(('0.0.0.0', port), SimpleHandler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info('Server stopped by KeyboardInterrupt')
    except Exception:
        logger.exception('Server encountered an error')


if __name__ == "__main__":
    start_background_server(port=5000)
