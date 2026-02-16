from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import subprocess
import os
import re
import sys
import logging
import threading
import runpy
import importlib.util
import time


def resource_path(relative_path):
    """Return path to resource, works for dev and PyInstaller bundle."""
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)


# Logging (file + console)
log_dir = os.path.join(os.getenv('APPDATA') or os.getcwd(), 'MusicServerTemp')
os.makedirs(log_dir, exist_ok=True)
LOG_FILE = os.path.join(log_dir, 'server.log')
logging.basicConfig(filename=LOG_FILE, level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)
logger.addHandler(logging.StreamHandler())

class SimpleHandler(BaseHTTPRequestHandler):
    def _set_headers(self, status_code=200):
        self.send_response(status_code)
        # (CORS)
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
            m3u_file = data.get('m3u', 'default_playlist') # Fallback Name
            logger.info('Received POST from %s: url=%s m3u=%s', self.client_address, incoming_url, m3u_file)

            script_to_run = None

            # Regex to get subdomains and map to candidate script locations
            candidates = []
            if re.search(r"https?://(www\.)?youtube\.", incoming_url):
                candidates = [
                    "scripts/sTownload/script_web.py",
                    "scripts/sTownload/app.py",
                    "../sTownload/script_web.py",
                ]
            elif re.search(r"https?://(www\.)?soundcloud\.", incoming_url):
                candidates = [
                    "scripts/Sc2Sp_src/script_web.py",
                    "scripts/Sc2Sp_src/Sc2Sp/script.py",
                    "../Sc2Sp/script_web.py",
                    "../Sc2Sp/script.py",
                ]

            # Resolve the first existing candidate using resource_path
            logger.debug('Candidates: %s', candidates)
            for cand in candidates:
                cand_path = resource_path(cand)
                logger.debug('Checking candidate: %s -> %s', cand, cand_path)
                if os.path.exists(cand_path):
                    script_to_run = cand_path
                    logger.info('Selected script: %s', script_to_run)
                    break

            if script_to_run:
                # Run the selected script in a background thread using runpy.
                # This avoids launching the bundled EXE again (which would re-run the launcher).
                def _run_script(path, url_arg, m3u_arg):
                    try:
                        script_dir = os.path.dirname(path) or os.getcwd()
                        # prepare argv for the executed script
                        ffmpeg_dir = getattr(sys, '_MEIPASS', os.path.abspath("."))
                        old_path = os.environ.get("PATH", "")
                        if ffmpeg_dir not in old_path:
                            os.environ["PATH"] = ffmpeg_dir + os.pathsep + old_path
                            logger.info('Added ffmpeg directory to PATH: %s', ffmpeg_dir)
                        old_argv = sys.argv[:]
                        old_cwd = os.getcwd()
                        added_to_path = False
                        try:
                            if sc_flag:
                                sys.argv = [path, "-one", url_arg]
                            sys.argv = [path, url_arg, m3u_arg]
                            if script_dir not in sys.path:
                                sys.path.insert(0, script_dir)
                                added_to_path = True
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
                            if added_to_path and script_dir in sys.path:
                                try:
                                    sys.path.remove(script_dir)
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
                response_msg = {"status": "ignored", "reason": "no matching script found or file missing"}
                code = 404

        except Exception as e:
            response_msg = {"status": "error", "message": str(e)}
            code = 400

        self._set_headers(code)
        self.wfile.write(json.dumps(response_msg).encode())

    def do_GET(self):
        # Simple health/status endpoint
        try:
            info = {
                "status": "ok",
                "pid": os.getpid(),
                "cwd": os.getcwd()
            }
            self._set_headers(200)
            self.wfile.write(json.dumps(info).encode())
        except Exception:
            logger.exception('GET /status failed')
            self._set_headers(500)
            self.wfile.write(json.dumps({"status": "error"}).encode())

def start_background_server(port=5000):
    logger.info('Web-Extension Server starting on port %d (pid=%s)...', port, os.getpid())

    def _refresh_sc_client_id_loop(period_seconds=3600):
        """Periodically attempt to fetch/persist SoundCloud client_id using the bundled helper.
        This is best-effort: failures are logged and ignored so the server keeps running.
        """
        cfg_path = os.path.join(os.getenv('APPDATA') or os.getcwd(), 'MusicServerTemp', 'config.json')
        script_fp = resource_path('scripts/Sc2Sp_src/script_web.py')
        while True:
            try:
                if os.path.exists(script_fp):
                    spec = importlib.util.spec_from_file_location('sc2_web_helper', script_fp)
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
                                # persist into config.json alongside a timestamp
                                try:
                                    if os.path.exists(cfg_path):
                                        with open(cfg_path, 'r', encoding='utf-8') as f:
                                            cfg = json.load(f)
                                    else:
                                        cfg = {}
                                    cfg['sc_client_id'] = cid
                                    cfg['sc_client_id_ts'] = int(time.time())
                                    with open(cfg_path, 'w', encoding='utf-8') as f:
                                        json.dump(cfg, f, ensure_ascii=False, indent=2)
                                    logger.info('Persisted sc_client_id from background refresher')
                                except Exception:
                                    logger.exception('Failed to persist sc_client_id')
                        except Exception:
                            logger.exception('fetch_client_id_via_selenium failed')
                else:
                    logger.debug('sc2 helper script not found for client_id refresh: %s', script_fp)
            except Exception:
                logger.exception('Unhandled error in sc_client_id refresher')
            # Sleep then retry
            time.sleep(period_seconds)

    # start background refresher (best-effort)
    try:
        t_ref = threading.Thread(target=_refresh_sc_client_id_loop, args=(3600,), daemon=True)
        t_ref.start()
        logger.info('Started background sc_client_id refresher thread')
    except Exception:
        logger.exception('Failed to start sc_client_id refresher thread')
    httpd = HTTPServer(('0.0.0.0', port), SimpleHandler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info('Server stopped by KeyboardInterrupt')
    except Exception:
        logger.exception('Server encountered an error')