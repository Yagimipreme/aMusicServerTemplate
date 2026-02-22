#!/usr/bin/env bash
# start.sh – Launch the MusicServerTemp stack
#   1. HTTP server (port 5000) – handles per-request SC/YT downloads
#   2. Spotify batch downloader – processes sp_playlist_ids from config.json

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PYTHON="${PYTHON:-python3}"

# Activate venv if a Linux-compatible one exists
if [ -f "$SCRIPT_DIR/venv/bin/activate" ]; then
    source "$SCRIPT_DIR/venv/bin/activate"
    echo "[start] venv activated"
fi

PIDS=()

cleanup() {
    echo ""
    echo "[start] Stopping all processes…"
    for pid in "${PIDS[@]}"; do
        kill "$pid" 2>/dev/null || true
    done
    exit 0
}
trap cleanup INT TERM

# 1. HTTP server – stays running, auto-dispatches script_web.py per URL received
echo "[start] Starting sWebExt/py_server/server.py …"
"$PYTHON" sWebExt/py_server/server.py &
PIDS+=($!)
echo "[start]   server PID=${PIDS[-1]}"

# 2. Spotify batch downloader – reads sp_playlist_ids from config.json, exits when done
echo "[start] Starting scripts/sTownload/app.py …"
"$PYTHON" scripts/sTownload/app.py &
PIDS+=($!)
echo "[start]   sTownload PID=${PIDS[-1]}"

echo ""
echo "[start] Running. Press Ctrl-C to stop."
echo "[start] (sTownload/script_web.py and Sc2Sp_src/script_web.py are"
echo "[start]  launched automatically by the server per incoming URL.)"
echo ""
wait
