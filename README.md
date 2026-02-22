# aMusicServerTemplate

Local music download server + browser extension.

- **YouTube / SoundCloud** — download a track by clicking the extension while on the page
- **Spotify playlists** — bulk-download playlists by putting their IDs in `config.json`

---

## How it works

```
Browser extension  →  POST {url}  →  server.py (:5000)
                                          ├─ youtube.com   →  scripts/sTownload/script_web.py
                                          └─ soundcloud.com →  scripts/Sc2Sp_src/script_web.py

start.sh also launches:
    scripts/sTownload/app.py  →  bulk-downloads Spotify playlists from config.json
```

---

## Prerequisites

- Python 3.10+
- ffmpeg (`pacman -S ffmpeg` / `apt install ffmpeg`)
- Chrome or Chromium (used by server to auto-fetch SoundCloud tokens)
- Firefox or Chrome browser (for the extension)

---

## Setup

### 1. Create the virtual environment

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

### 2. Edit `config.json`

```json
{
    "song_dir": "/home/you/Music",
    "sp_playlist_ids": [],
    "sc_username": "",
    "sp_username": "",
    "sc_topsong": "",
    "path": "",
    "is_timed": false
}
```

| Field | Required | Description |
|---|---|---|
| `song_dir` | yes | Folder where all downloaded songs are saved. Defaults to `~/Music` if empty. |
| `sp_playlist_ids` | no | List of Spotify playlist IDs for bulk download. Leave empty `[]` to skip. |
| `sc_username` | no | Your SoundCloud profile URL (reserved for future use). |
| `sp_username` | no | Your Spotify username (reserved for future use). |
| `ffmpeg_location` | no | Full path to the ffmpeg binary if it is not on your PATH. |

> `sc_client_id` is fetched and written automatically by the server — do not set it manually.

#### How to get a Spotify playlist ID

Open the playlist on Spotify, copy the share link:
`https://open.spotify.com/playlist/28a3p0Qb08VewDYPbTC8SF`
                                              ↑ this part is the ID

### 3. Install the browser extension

1. Open your browser's extension manager
   - Firefox: `about:debugging` → "This Firefox" → "Load Temporary Add-on"
   - Chrome: `chrome://extensions` → "Load unpacked"
2. Select the `sWebExt/` folder
3. Click the extension icon → open **Settings / Options**
4. Enter the server address:
   - Same machine: `http://localhost:5000`
   - Other machine on LAN: `http://192.168.x.x:5000`

---

## Starting

```bash
./start.sh
```

This does two things at once:

| Process | What it does |
|---|---|
| `server.py` | Starts the HTTP server on port 5000. Stays running. Handles all extension requests. |
| `app.py` | Runs the Spotify batch downloader. Downloads all playlists in `sp_playlist_ids`, then exits. Only does work if that list is non-empty. |

The per-request scripts (`script_web.py`) are launched automatically by the server when a URL arrives — you do not start them manually.

---

## Usage

### Download a YouTube or SoundCloud track

1. Make sure `./start.sh` is running
2. Open any YouTube or SoundCloud track page in your browser
3. Click the extension icon → click **Send current URL**
4. The track downloads to `song_dir` in the background

You can also paste any URL manually into the extension popup.

### Bulk-download Spotify playlists

1. Add playlist IDs to `sp_playlist_ids` in `config.json`
2. Run `./start.sh` — `app.py` will start downloading immediately
3. Songs are saved to `song_dir`

To re-run the bulk download without restarting the server:
```bash
./venv/bin/python scripts/sTownload/app.py
```

---

## Troubleshooting

**`No module named ...`** — venv not activated or dependencies not installed. Re-run:
```bash
python3 -m venv venv --clear && venv/bin/pip install -r requirements.txt
```

**Server not reachable from extension** — check that `start.sh` is running and the IP in extension options matches the machine running the server.

**SoundCloud downloads fail with 401** — the server auto-refreshes the SoundCloud token every hour via Chrome/Chromium headless. Make sure Chrome or Chromium is installed.

**ffmpeg not found** — install it (`pacman -S ffmpeg`) or set `ffmpeg_location` in `config.json` to the full binary path.
