# aMusicServer

A tiny local music server that saves songs from **YouTube, SoundCloud, and Spotify playlists** straight to your own machine, in a folder you choose. Click a button while you're on a song's webpage — the track lands in your music library a few seconds later.

It plays nicely with [Navidrome](https://www.navidrome.org/) (a free home music server), which means apps like Symfonium, DSub, Substreamer, or any Subsonic-compatible player on your phone or computer can pick up the new songs automatically.

There's no cloud account, no subscription, nothing leaves your network. The songs live on your disk.

---

## Quick start (Windows)

1. **Download the installer** from the latest release:
   - [github.com/Yagimipreme/aMusicServerTemplate/releases](https://github.com/Yagimipreme/aMusicServerTemplate/releases)
   - or [codeberg.org/Lycka/musicServerTemplate/releases](https://codeberg.org/Lycka/musicServerTemplate/releases)

   Grab the file named `Setup_aMusicServer_v1.0.0.exe` (or newer).

2. **Run the installer.** It installs into your user folder — no admin password needed, no UAC prompt. Tick **"Start with Windows"** during setup if you want it to launch automatically when you sign in.

3. **Look for the tray icon** (bottom-right of the screen, near the clock). That's the server running quietly in the background. Right-click it for options.

4. **Install the browser extension** — see the next section.

That's it. The installer also takes care of updates: when a new release comes out on either Github or Codeberg, the tray icon will offer to install it for you.

---

## Install the browser extension

The extension is what turns "I'm listening to a song in my browser" into "save this song". It only sends URLs to your local server — no data goes anywhere else.

### Firefox

1. Type `about:debugging` into your address bar and press Enter.
2. Click **This Firefox** on the left, then **Load Temporary Add-on…**.
3. Browse to the folder named `sWebExt` and pick any file inside it (e.g. `manifest.json`).

Firefox will load the extension. It stays loaded until you close Firefox. To make it permanent, sign it with a Firefox developer account or use the Developer Edition / Nightly browser.

### Chrome / Edge / Brave

1. Type `chrome://extensions` (or `edge://extensions`, `brave://extensions`) into your address bar.
2. Turn on **Developer mode** (toggle in the top-right).
3. Click **Load unpacked** and pick the `sWebExt` folder.

Where to find the `sWebExt` folder after the Windows installer ran:
- `Documents\aMusicServer\sWebExt`

If you're on Linux/Mac and ran it manually, it's the `sWebExt` folder next to this README.

### Point the extension at your server

Click the extension icon → **Settings / Options**.

- **Same computer:** leave the field at `http://localhost:5000` and click **Save**.
- **Different computer on your home network:** use your server computer's `.local` name — e.g. `http://homeserver.local:5000` (whatever you named the machine). Click **Test connection** first to make sure it can talk to the server, then **Save**.

The `.local` name is preferred over a numeric IP because it doesn't change when your router hands out new addresses.

---

## Daily use

### Save one song

1. Open YouTube or SoundCloud and play the song you want.
2. Click the extension icon.
3. Click **Send current URL**.
4. A green checkmark flashes — the track is downloading in the background.

You can also paste any link into the text field at the top of the popup if you'd rather not navigate to the page.

### Add songs to a playlist

The extension can keep a list of playlist names. When you pick one before sending a song, the new track gets added to that playlist automatically. Navidrome notices and updates the matching playlist; your phone app picks it up on its next sync.

**Easiest way to get started:**
1. Open the extension popup.
2. Click **Pull playlists from Navidrome** — it imports the names of all your existing playlists.
3. Pick one with the radio button before sending a song.

You can also type a new name into the "Name of Playlist" field and click **Add** to make a new one on the fly.

If you remove a playlist in Navidrome and want to clear it from the extension list too, click the small **X** next to its name.

### Save a whole Spotify playlist

Spotify itself doesn't let outside programs download songs anymore, so this works in two steps:

1. **Export the playlist to a file** using [exportify.app](https://exportify.app):
   - Open the site in your browser
   - Click "Log in with Spotify" — only your own browser sees your account
   - Pick the playlist and click "Export"
   - Save the `.csv` file it gives you

2. **Drop the file into your `playlists` folder:**
   - Windows: `Documents\aMusicServer\playlists\`
   - Linux/Mac: `playlists/` next to this README

The next time the server runs its bulk-download (it does this automatically when you start it, or you can re-run `start.sh` / restart the tray app), it goes through every CSV in that folder and downloads each track from YouTube. Songs come out tagged with the right title, artist, and album.

You only need to re-export when you change the playlist on Spotify.

---

## Common problems

**Extension says "Connection failed"** — the server isn't running, or the URL in the extension's Options is wrong. Click the tray icon → make sure it says "Running"; on the Options page, click **Test connection** to see what's actually responding.

**Songs download but don't show up in Navidrome / Symfonium** — Navidrome rescans regularly, but if you want to see new songs *right now*, refresh the library in your phone app (pull-to-refresh on the library or playlist).

**SoundCloud songs fail to download** — usually a one-off network blip. Try again a few minutes later. If it consistently fails, see the technical section below — the server has a fallback that should kick in automatically.

**"Pull playlists from Navidrome" doesn't work** — check the Navidrome username and password in `config.json` (or `Documents\aMusicServer\config.json` on Windows). They have to match what you log into Navidrome's web interface with.

**The Windows installer says "Chrome was not detected"** — that's fine, it's only a heads-up. Chrome makes SoundCloud's auto-token refresh a bit more reliable, but everything works without it.

---

## Run it yourself on Linux / Mac

If you'd rather build from source than use the Windows installer:

```bash
git clone https://github.com/Yagimipreme/aMusicServerTemplate.git
cd aMusicServerTemplate
python3 -m venv venv
venv/bin/pip install -r requirements.txt

# Copy the example config and edit at least song_dir
cp config.example.json config.json
$EDITOR config.json

./start.sh
```

You also need `ffmpeg` installed system-wide (`apt install ffmpeg`, `pacman -S ffmpeg`, `brew install ffmpeg`, etc.). If `start.sh` is running, the browser extension setup above (Firefox/Chrome instructions) is the same.

**Linux note on SoundCloud token refresh:** on recent Debian/Ubuntu, Chromium's sandbox can be blocked by the AppArmor unprivileged user-namespace restriction. If your logs show `Permission denied for libglib-2.0.so.0`, run once:

```bash
sudo sh -c 'echo "kernel.apparmor_restrict_unprivileged_userns = 0" > /etc/sysctl.d/60-apparmor-namespace.conf'
sudo sysctl --system
```

Downloads still work without this — the server falls back to a different method for SoundCloud — it just makes the token-refresh path more reliable.

---

## For interested people (technical rundown)

This is a small Python project that wires together a few moving parts to give you a one-click "save this song" workflow on a self-hosted music library. The summary for anyone who likes to read the source.

### Architecture

```
┌─────────────┐    POST {url, m3u}   ┌────────────────────┐
│   Browser   │ ───────────────────► │     server.py      │  :5000
│   ext.      │                      │   (HTTPServer,     │
│ (popup +    │ ◄─── HTTP 200 ────── │    stdlib only)    │
│  options)   │                      └──────────┬─────────┘
└─────────────┘                                 │
       ▲                                        │ runpy.run_path
       │ GET /playlists                         ▼
       │       ┌──────────────────────┐  ┌──────────────────────┐
       │       │ scripts/sTownload/   │  │ scripts/Sc2Sp_src/   │
       └──────►│   script_web.py      │  │   script_web.py      │
   (proxied to │ (YouTube via yt-dlp) │  │ (SoundCloud HLS via  │
    Navidrome) └──────┬───────────────┘  │  custom resolver +   │
                      │                  │  ffmpeg, yt-dlp      │
                      │                  │  fallback)           │
                      ▼                  └──────┬───────────────┘
                ┌────────────────────┐          │
                │  ffmpeg + eyed3    │ ◄────────┘
                │  (mp3 + ID3 tags + │
                │   embedded cover)  │
                └──────────┬─────────┘
                           │ writes .mp3 + appends to .m3u
                           ▼
                    ┌──────────────┐  startScan.view   ┌─────────────┐
                    │   song_dir   │ ────────────────► │  Navidrome  │
                    └──────────────┘                   └──────┬──────┘
                                                              │ Subsonic API
                                                              ▼
                                                    ┌──────────────────┐
                                                    │  Symfonium / any │
                                                    │  Subsonic client │
                                                    └──────────────────┘
```

### Tech stack

- **HTTP server:** `http.server` from the Python stdlib. No framework. Per-request scripts are dispatched in a new thread via `runpy.run_path` so each download reads fresh source — changes to the per-URL scripts take effect on the next request without restarting `server.py`. (`server.py` itself is the only long-lived process and needs an actual restart for changes.)
- **YouTube:** [yt-dlp](https://github.com/yt-dlp/yt-dlp) with `bestaudio/best` + `FFmpegExtractAudio` postprocessor → MP3 at 320 kbps. `FFmpegThumbnailsConvertor` + `EmbedThumbnail` + `FFmpegMetadata` embed the cover art as an ID3v2 APIC frame so files travel cleanly.
- **SoundCloud:** custom pipeline using their internal `api-v2.soundcloud.com/resolve` → picks an HLS transcoding → hands the playback URL to `ffmpeg` → MP3 with embedded cover. The `client_id` rotates frequently; a background thread in `server.py` refreshes it via headless Chromium + Selenium (CDP performance logs to sniff the param from a real api-v2 request). If the token is dead **and** Selenium can't refresh it (e.g. the AppArmor issue mentioned above, or no Chromium installed), the request falls back to plain yt-dlp on the SoundCloud URL — slightly slower, no cover-art parity, but always works.
- **Spotify:** the free Web API developer tier dropped support for `/playlists/{id}/items` in 2024 (now requires a Premium account on the registered app). Third-party scrapers (spotisaver, spotdl/spotapi) work intermittently. The reliable path is therefore Spotify → Exportify (browser-only, uses your own OAuth token) → CSV → `scripts/sTownload/app.py` reads the CSV, builds yt-dlp search queries from `title + artist`, downloads, tags with `eyed3`, appends to the playlist M3U.
- **M3U:** plain `#EXTM3U`-headed file in `song_dir`, one filename per line. Append-only with dedup so re-sending the same URL doesn't double-add. Strips a trailing `.m3u` from the playlist name so callers can pass either `"MyHits"` or `"MyHits.m3u"`.
- **Navidrome integration:** after each download (single or bulk), the server hits `/rest/startScan.view` with the configured credentials so the library + playlists re-index immediately. The extension's "Pull playlists" button goes through a `GET /playlists` proxy endpoint so Navidrome credentials never touch the browser.
- **Browser extension:** Manifest V3, vanilla JS, no build step. `chrome.storage.local` for the playlist list, `chrome.storage.sync` for the LAN URL. Both Firefox (`browser_specific_settings.gecko`) and Chromium-based browsers are supported by the same manifest. Host permissions cover `localhost`, `*.local`, and common RFC1918 ranges (192.168.x.x, 10.x.x.x, 172.16.x.x) without prompting; anything else triggers a one-time grant via `chrome.permissions.request` on Save.
- **Windows packaging:** [PyInstaller](https://pyinstaller.org/) onedir bundle + [Inno Setup 6](https://jrsoftware.org/isinfo.php). A [pystray](https://github.com/moses-palmer/pystray) tray app wraps the server: spawns `server.py` as a subprocess, exposes "Open music folder", "Open extension folder", "Check for updates…", and a "Start with Windows" toggle that writes to `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`. Install location is `%LOCALAPPDATA%\Programs\aMusicServer` (per-user, no UAC required, stable AppId for in-place upgrades).
- **Auto-update:** `windows/updater.py` polls `codeberg.org/api/v1/repos/.../releases/latest` and `api.github.com/repos/.../releases/latest`, picks the newer `tag_name` (semver compare), downloads the `Setup_aMusicServer_vX.Y.Z.exe` asset, and launches it detached. The Inno installer's stable AppId handles upgrade-in-place. Silent check on launch (delayed 30s); explicit check via the tray menu.

### File layout

```
aMusicServerTemplate/
├── README.md                       ← this file
├── config.example.json             ← copy → config.json + edit
├── start.sh                        ← Linux/Mac launcher (server.py + bulk app.py)
├── requirements.txt                ← Python deps (yt-dlp, selenium, eyed3, ...)
├── playlists/                      ← drop Exportify CSVs here
├── sWebExt/                        ← browser extension (Manifest V3)
│   ├── manifest.json
│   ├── popup/                      ← popup.html + popup.js (the main UI)
│   ├── options.html + options.js   ← LAN URL settings + Test connection
│   └── py_server/server.py         ← stdlib HTTP server (port 5000)
├── scripts/
│   ├── sTownload/
│   │   ├── script_web.py           ← per-URL YouTube → mp3 (yt-dlp)
│   │   └── app.py                  ← bulk Spotify-CSV → yt-dlp search → mp3
│   └── Sc2Sp_src/
│       ├── script_web.py           ← per-URL SoundCloud → mp3 (custom + fallback)
│       └── Sc2Sp/script2.py        ← SC resolve/transcoding/ffmpeg pipeline
└── windows/                        ← Windows packaging only
    ├── version.py                  ← single source of truth for releases
    ├── tray_app.py                 ← pystray entry point
    ├── updater.py                  ← codeberg + github release poller
    ├── musicserver.spec            ← PyInstaller spec
    ├── installer.iss               ← Inno Setup 6 script
    └── build.bat                   ← PyInstaller → ISCC, one command
```

### Why these choices

- **Stdlib HTTP server over FastAPI/Flask:** This handles maybe ten requests an hour. The startup overhead and dependency footprint of a real framework aren't worth it for a one-process side project, and a frozen single .exe stays small (~150 MB instead of ~400 MB) when we don't drag in uvicorn/starlette/pydantic.
- **`runpy` for per-request scripts:** Lets each download script be edited and re-deployed without restarting the long-running server. Bypasses Python's import cache.
- **Exportify over Spotify Web API:** Spotify's free dev tier silently broke `/playlists/{id}/items` in 2024 (now needs Premium on the app owner). Exportify uses the user's own OAuth scope, so it just works, and the CSV format becomes a stable interchange that survives Spotify's API moods.
- **Two-forge releases (codeberg + github):** redundancy. If either forge has downtime when a user opens the app, the updater queries both and picks whichever responded with a newer tag.
- **Per-user Windows install:** modern Windows convention (Discord, VSCode, Chrome do the same). Skips UAC, runs as the user, can write to its own files for auto-update.

### Building from source

Linux/Mac: see "Run it yourself" above.

Windows installer:
```bat
cd windows
build.bat
```
…which expects `py`, `pyinstaller`, `pystray`, `pillow` (and the project's `requirements.txt`) installed via `pip`, plus Inno Setup 6 in its default install location. Outputs `Output\Setup_aMusicServer_vX.Y.Z.exe`.

Cutting a release (automated, no Windows machine required):

1. Bump `windows/version.py`
2. Commit, tag (`v1.0.1`), push to both remotes
3. The GitHub Actions workflow in `.github/workflows/build-windows.yml` fires on the tag push: spins up a Windows runner, runs `build.bat`, attaches the resulting `Setup_aMusicServer_vX.Y.Z.exe` to both the GitHub release (created automatically) and the Codeberg release (created if it doesn't exist).
4. Installed users see the update prompt on next launch.

One-time setup for the Codeberg upload half:

1. Generate a Codeberg API token at <https://codeberg.org/user/settings/applications> — give it `repository: write` scope.
2. On GitHub, open the repo → Settings → Secrets and variables → Actions → New repository secret. Name it `CODEBERG_CI`, paste the value.

If you forget to set `CODEBERG_CI`, the workflow still builds and uploads to GitHub — the Codeberg step prints a warning and exits cleanly, so the build doesn't fail outright. You can upload to Codeberg by hand by downloading the built `.exe` from the GitHub release and uploading it on `codeberg.org/Lycka/musicServerTemplate/releases`.
