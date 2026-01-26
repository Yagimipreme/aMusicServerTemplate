from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import subprocess
import os

class SimpleHandler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_POST(self):
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)
        data = json.loads(post_data.decode('utf-8'))
        try :
            m3u_file = data.get('m3u')  
        except Exception as e:
            print("No m3u-playlist name was sent.", e)
        
        incoming_url = data.get('url', '')
        script_to_run = None

        # Logik für die Skriptauswahl
        if incoming_url.startswith("https://youtube.*"):
            script_to_run = "../sTownload/script_web.py"
        elif incoming_url.startswith("https://soundcloud.*"):
            script_to_run = "../Sc2Sp/script_web.py"

        if script_to_run and os.path.exists(script_to_run):
            # Skript asynchron ausführen, damit der Server nicht blockiert
            subprocess.Popen(["python", script_to_run, incoming_url, m3u_file])
            response_msg = {"status": "started", "script": script_to_run}
            code = 200
        else:
            response_msg = {"status": "ignored", "reason": "no matching script found"}
            code = 404

        self.send_response(code)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(response_msg).encode())

HTTPServer(('0.0.0.0', 5000), SimpleHandler).serve_forever()
