from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import subprocess
import os
import re

class SimpleHandler(BaseHTTPRequestHandler):
    def _set_headers(self, status_code=200):
        self.send_response(status_code)
        # Erlaubt Anfragen von Browser-Extensions (CORS)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Content-Type', 'application/json')
        self.end_headers()

    def do_OPTIONS(self):
        # Preflight-Request Handling für Firefox/Chrome
        self._set_headers(204)

    def do_POST(self):
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)
        
        try:
            data = json.loads(post_data.decode('utf-8'))
            incoming_url = data.get('url', '')
            m3u_file = data.get('m3u', 'default_playlist') # Fallback Name
            
            script_to_run = None

            # Regex statt startswith, um Subdomains wie music.youtube.com oder de.soundcloud.com zu fangen
            if re.search(r"https?://(www\.)?youtube\.", incoming_url):
                script_to_run = "../sTownload/script_web.py"
            elif re.search(r"https?://(www\.)?soundcloud\.", incoming_url):
                script_to_run = "../Sc2Sp/script_web.py"

            if script_to_run and os.path.exists(script_to_run):
                # Script asynchron starten
                # Wichtig: m3u_file als Argument mitgeben
                subprocess.Popen(["python", script_to_run, incoming_url, m3u_file])
                response_msg = {"status": "started", "script": script_to_run}
                code = 200
            else:
                response_msg = {"status": "ignored", "reason": "no matching script found or file missing"}
                code = 404

        except Exception as e:
            response_msg = {"status": "error", "message": str(e)}
            code = 400

        self._set_headers(code)
        self.wfile.write(json.dumps(response_msg).encode())

print("Server läuft auf Port 5000...")
HTTPServer(('0.0.0.0', 5000), SimpleHandler).serve_forever()