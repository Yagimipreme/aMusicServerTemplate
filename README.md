# aMusicServerTemplate

Local music download server + browser extension.

- **YouTube / SoundCloud** — download a track by clicking the extension while on the page
- **Spotify playlists** — bulk-download playlists by exporting them to CSV (via [exportify.app](https://exportify.app)) and dropping the files into a `playlists/` folder

---

## How it works

```
Browser extension  →  POST {url}  →  server.py (:5000)
                                          ├─ youtube.com   →  scripts/sTownload/script_web.py
                                          └─ soundcloud.com →  scripts/Sc2Sp_src/script_web.py

start.sh also launches:
    scripts/sTownload/app.py  →  bulk-downloads every CSV in playlists/
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
    "playlists_dir": "",
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
| `playlists_dir` | no | Folder containing Spotify-exported `.csv` files for bulk download. Defaults to `./playlists/` next to this README. |
| `sc_username` | no | Your SoundCloud profile URL (reserved for future use). |
| `sp_username` | no | Your Spotify username (reserved for future use). |
| `ffmpeg_location` | no | Full path to the ffmpeg binary if it is not on your PATH. |

> `sc_client_id` is fetched and written automatically by the server — do not set it manually.

#### How to bulk-download Spotify playlists

The Spotify Web API no longer permits playlist track access on the free
developer tier, so this project drives bulk downloads from CSV exports
instead. The process is once-per-playlist:

1. Go to <https://exportify.app> (open-source, runs entirely in your browser).
2. Log in with your Spotify account — the page only uses your OAuth token to
   read your own playlist data; nothing is uploaded anywhere.
3. Export each playlist you want as a CSV.
4. Drop the CSV files into the `playlists/` folder at the root of this repo
   (create it if it doesn't exist).

`app.py` will pick up every `.csv` file in that folder on the next run and
download every track via yt-dlp search. The CSV filename becomes the M3U
playlist name in your music library.

Re-export and overwrite the CSV when you change a playlist on Spotify.

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

**SoundCloud downloads fail with 401** — the server auto-refreshes the SoundCloud token every hour via Chrome/Chromium headless. Make sure Chrome or Chromium is installed. If you also see `Could not get session` / `Permission denied` for `libglib-2.0.so.0` in the logs on recent Debian/Ubuntu, AppArmor's unprivileged user-namespace restriction is blocking Chromium's sandbox even with `--no-sandbox`. Disable it persistently with:

```bash
sudo sh -c 'echo "kernel.apparmor_restrict_unprivileged_userns = 0" > /etc/sysctl.d/60-apparmor-namespace.conf'
sudo sysctl --system
```

Then verify Chromium runs with `chromium --headless=new --no-sandbox --dump-dom https://example.com | head`. (If the SC token can't be refreshed for any reason, downloads still work — the per-URL SC handler now falls back to yt-dlp automatically.)

**ffmpeg not found** — install it (`pacman -S ffmpeg`) or set `ffmpeg_location` in `config.json` to the full binary path.

---

## Windows installer

The `windows/` folder contains everything needed to produce a single-file Windows installer (`Setup_aMusicServer_vX.Y.Z.exe`) that:

- Installs to `%LOCALAPPDATA%\Programs\aMusicServer` (per-user, no UAC prompt)
- Starts a system-tray app on launch that runs the HTTP server in the background
- Exposes the browser extension at `Documents\aMusicServer\sWebExt` for "Load Unpacked"
- Optionally registers a "Start with Windows" entry under `HKCU\...\Run`
- Auto-updates by polling Codeberg + GitHub releases on every launch and via a "Check for updates…" item in the tray menu — newer release found → installer is downloaded and run

### Building the installer (Windows machine)

One-time setup:

```bat
py -m pip install -r requirements.txt pyinstaller pystray pillow
```

Then install **Inno Setup 6** from <https://jrsoftware.org/isinfo.php>.

Build:

```bat
cd windows
build.bat
```

Output: `windows\Output\Setup_aMusicServer_vX.Y.Z.exe`. Upload that file as the release asset under the **exact filename** `Setup_aMusicServer_vX.Y.Z.exe` on both <https://codeberg.org/Lycka/musicServerTemplate/releases> and <https://github.com/Yagimipreme/aMusicServerTemplate/releases>. Use a tag like `v1.0.0`. The updater picks the newer of the two and downloads its asset.

### Cutting a new release

1. Bump `windows/version.py` (e.g. `__version__ = "1.0.1"`)
2. Commit, tag `v1.0.1`, push to both remotes
3. `cd windows && build.bat`
4. Create matching releases on both forges, upload `Setup_aMusicServer_v1.0.1.exe`
5. Users on v1.0.0 see the update prompt on next launch (or via the tray menu)

### Chrome prerequisite (optional)

The installer detects whether Google Chrome is installed and warns if it isn't. Chrome is only needed for **automatic** SoundCloud token refresh — SoundCloud downloads still work without Chrome via the built-in yt-dlp fallback. Install Chrome from <https://www.google.com/chrome/> at any time.
