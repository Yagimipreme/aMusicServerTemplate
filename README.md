# aMusicServer

A self-hosted music server that saves songs from **YouTube and SoundCloud** straight to your own machine. Click a button while you're on a song's page — the track lands in your library a few seconds later, tagged and ready to play.

Built around [Navidrome](https://www.navidrome.org/), so any Subsonic-compatible player (Symfonium, DSub, Substreamer, etc.) picks up new tracks automatically.

No cloud account. No subscription. Everything stays on your disk.

---

## Quick start (Windows)

1. **Download the installer** from the latest release:
   - [github.com/Yagimipreme/aMusicServerTemplate/releases](https://github.com/Yagimipreme/aMusicServerTemplate/releases)
   - or [codeberg.org/Lycka/musicServerTemplate/releases](https://codeberg.org/Lycka/musicServerTemplate/releases)

   Grab `Setup_aMusicServer_v1.0.0.exe` (or newer).

2. **Run the installer.** Per-user install — no admin password, no UAC. Tick **"Start with Windows"** during setup to launch it automatically.

3. **Look for the tray icon** (bottom-right, near the clock). That's the server running. Right-click for options.

4. **Install the browser extension** — see the next section.

The installer handles updates: when a new release appears on either forge, the tray icon will offer to install it.

---

## Install the browser extension

### Firefox

1. Type `about:debugging` → **This Firefox** → **Load Temporary Add-on…**
2. Browse to the `sWebExt` folder and pick any file inside (e.g. `manifest.json`).

Stays loaded until Firefox closes. For a permanent install, sign it with a Firefox developer account or use Developer Edition / Nightly.

### Chrome / Edge / Brave

1. Go to `chrome://extensions` (or `edge://extensions`, `brave://extensions`).
2. Enable **Developer mode** (top-right toggle).
3. Click **Load unpacked** → pick the `sWebExt` folder.

After the Windows installer: the folder is at `Documents\aMusicServer\sWebExt`.

### Point the extension at your server

Click the extension icon → **Settings / Options**.

- **Same computer:** leave it at `http://localhost:5000`.
- **Another device on your network:** use the server's `.local` name — e.g. `http://homeserver.local:5000`. Click **Test connection** first.

---

## Daily use

### Save a song

1. Open YouTube or SoundCloud, play the song.
2. Click the extension icon → **Send current URL**.
3. A green checkmark flashes — downloading in the background.

You can also paste any link directly into the **Search** screen of the web UI at `http://localhost:5000`.

### Web UI (`http://localhost:5000`)

The web UI has four screens accessible from the bottom nav:

**MIXES** — your mix profiles. Weekly Mix, Daily Mix, and any genre or custom mixes you've created. Tap a mix to expand its editor: set the schedule, blend of new vs. library tracks, seed mode, genre chips, and more. Hit **RUN** to trigger it immediately.

**LIBRARY** — maintenance tools: enrich file tags from Last.fm, repair missing artist info via MusicBrainz, de-duplicate, and manage title-cleanup suffix rules.

**SEARCH** — search SoundCloud and YouTube by text, or paste a direct URL. SoundCloud artist results appear as chips at the top — tap one to browse their tracks. Hit **+** on any track to download it to your library.

**SETUP** — edit all server settings (Navidrome URL/credentials, Last.fm keys, discovery tuning) without touching `config.json` by hand.

### Mix profiles (automatic discovery)

The server builds playlists of artists you don't own yet, based on who's similar to what you already play (via Last.fm similar-artist data). Each profile is a Navidrome playlist that updates on a schedule.

Built-in profiles created on first run:
- **Weekly Mix** — runs once a week, seeds from your listening history.
- **Daily Mix** — runs daily, smaller batch, recent listening window.
- Genre mixes auto-generated from your library's top genres (e.g. "Electronic Mix", "Jazz Mix").

Profiles are configurable in the **MIXES** screen: cadence, count, new/library blend ratio, seed mode (history / genre / manual / playlist), and per-profile quality settings.

Requires: Navidrome credentials in `config.json` (or the Setup screen) + some listening history in Navidrome.

### Follow Artists / NEW RELEASES

The **Follows** screen lets you subscribe to specific artists by MusicBrainz ID. Once followed, the server automatically detects their new releases and downloads them.

**How it works:**

1. Search for an artist by name on the Follows screen — candidates come from MusicBrainz with disambiguation text so you can pick the right one.
2. Click **Follow**. The server immediately runs a one-time backfill: any release from that artist within the last `default_backfill_days` days is queued for download.
3. Nightly (at `run_hour`), the server polls the ListenBrainz fresh-releases feed for the past `lookback_days` days, finds releases from your followed artists, and downloads new tracks.
4. Downloaded tracks are collected into the **NEW RELEASES** playlist (an `.m3u` file in your library, capped at `playlist_cap` entries — oldest fall off as new ones arrive).
5. New releases appear in the in-app feed on the Follows screen. The nav badge shows the unseen count; opening the screen clears it.

**Scope rule:** Singles and EPs → every track is downloaded. Albums → one representative track (the title track if it exists, otherwise the first track).

**Retry:** if a track cannot be resolved on the first attempt, it is retried on the next two nightly runs. After three failures it is marked unavailable.

**`config.json` keys (under `"follow"`):**

| Key | Default | Description |
|---|---|---|
| `enabled` | `true` | Enable/disable the nightly follow check |
| `run_hour` | `4` | Hour (0–23, local time) to run the nightly check |
| `lookback_days` | `7` | How many past days the ListenBrainz feed covers |
| `default_backfill_days` | `30` | How far back to look when an artist is first followed |
| `playlist_name` | `"NEW RELEASES"` | Name of the M3U playlist written to your library |
| `playlist_cap` | `100` | Maximum entries in the playlist (sliding window) |
| `notify.webhook_url` | `""` | Optional webhook URL — receives JSON `{count, tracks, message}` |
| `notify.ntfy_topic` | `""` | Optional [ntfy](https://ntfy.sh/) topic for push notifications |

All keys can also be edited live from the Follows screen's settings panel without touching `config.json`.

### Add songs to a playlist

The browser extension can track a list of playlist names. Pick one before sending a song and the track is appended to that playlist automatically. Navidrome picks it up on its next scan.

Click **Pull playlists from Navidrome** in the extension popup to import your existing playlist names.

---

## Common problems

**Extension says "Connection failed"** — server isn't running, or the URL in Options is wrong. On Options, click **Test connection** to diagnose.

**Songs don't appear in Navidrome / Symfonium** — Navidrome rescans after each download, but pull-to-refresh in your player app forces an immediate sync.

**SoundCloud songs fail** — usually a brief network blip. Try again in a few minutes. The server refreshes its SoundCloud token automatically via a headless browser; if that fails it falls back to yt-dlp.

**"Pull playlists from Navidrome" doesn't work** — check `navidrome_user` and `navidrome_pass` in config (or Setup screen) — they must match your Navidrome login.

**Windows installer: "Chrome was not detected"** — harmless warning. Chrome makes the SoundCloud token refresh slightly more reliable but everything works without it.

---

## Run it yourself (Linux / Mac)

```bash
git clone https://github.com/Yagimipreme/aMusicServerTemplate.git
cd aMusicServerTemplate
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cp config.example.json config.json
$EDITOR config.json   # set song_dir, navidrome_url/user/pass, lastfm_api_key

.venv/bin/python sWebExt/py_server/server.py
```

You also need `ffmpeg` system-wide (`apt install ffmpeg`, `brew install ffmpeg`, etc.).

**AppArmor note (recent Debian/Ubuntu):** if logs show `Permission denied for libglib-2.0.so.0`, the SoundCloud token refresh is blocked by the AppArmor unprivileged namespace restriction. Fix:

```bash
sudo sh -c 'echo "kernel.apparmor_restrict_unprivileged_userns = 0" > /etc/sysctl.d/60-apparmor-namespace.conf'
sudo sysctl --system
```

Downloads still work without this — the server falls back to yt-dlp for SoundCloud.

### Optional: local audio analysis (Insights)

The **Insights → audio features** view (BPM / key / mood) is sourced from
[AcousticBrainz](https://acousticbrainz.org/) by recording MBID. For tracks
AcousticBrainz has no data on, the server can fall back to analysing your local
audio files with [librosa](https://librosa.org/). This is **optional** and off by
default — without it, those tracks simply show no features.

To enable it:

```bash
.venv/bin/pip install -r requirements-insights.txt
```

Then in `config.json`:

```jsonc
"insights": {
  "enable_local_analysis": true   // default false
}
```

Local analysis also needs `song_dir` set so the server can locate your files.
The librosa stack (numpy, scipy, numba, soundfile) is heavy and is **not** bundled
in the Windows `.exe`. If you turn the flag on without installing it, the feature
sync reports `local_analysis: "unavailable…"` in its status rather than failing.

---

## Technical rundown

### Architecture

```
┌─────────────────┐   POST {url}       ┌──────────────────────────┐
│  Browser ext.   │ ─────────────────► │  Flask server (port 5000) │
│  (popup +       │                    │  sWebExt/py_server/       │
│   options)      │ ◄── HTTP 200 ───── │  server.py                │
└─────────────────┘                    └────────────┬─────────────┘
                                                    │
         ┌──────────────────────────────────────────┤
         │                                          │
         ▼                                          ▼
┌──────────────────────┐               ┌────────────────────────┐
│ scripts/sTownload/   │               │ Discovery engine        │
│   script_web.py      │               │ discover/engine.py      │
│ (YouTube via yt-dlp) │               │ (run_profile → Last.fm  │
│ + library/tagger.py  │               │  seeds → yt-dlp acquire │
│   (title cleanup,    │               │  → Navidrome playlist)  │
│    WOAS tag)         │               └────────────┬───────────┘
└──────────┬───────────┘                            │
           │                                        │
           ▼                                        ▼
     ┌──────────────┐  startScan.view   ┌─────────────────────┐
     │   song_dir   │ ────────────────► │     Navidrome       │
     └──────────────┘                   └──────────┬──────────┘
                                                   │ Subsonic API
                                                   ▼
                                         ┌──────────────────────┐
                                         │  Symfonium / any     │
                                         │  Subsonic client     │
                                         └──────────────────────┘
```

### Tech stack

- **HTTP server:** [Flask](https://flask.palletsprojects.com/) on port 5000. Threaded mode for concurrent downloads. Templates in `web/templates/`; static assets in `web/static/`.
- **Web UI:** vanilla JS SPA (`web/static/app.js`, ~1400 lines) with a hash router. No framework. `createElement`/`textContent` only — no `innerHTML` with data. Four screens: Mixes, Library, Search, Setup.
- **YouTube:** [yt-dlp](https://github.com/yt-dlp/yt-dlp) with `bestaudio/best` + `FFmpegExtractAudio` → MP3 at 192 kbps. Thumbnail embedded as ID3v2 APIC. Post-processing: title cleanup via `library/tagger.py` (configurable suffix-strip rules), WOAS source URL tag, then Navidrome `startScan.view`.
- **SoundCloud:** custom pipeline via `soundcloud/` — SC api-v2 `resolve` → HLS transcoding URL → ffmpeg → MP3. Token (`client_id`) refreshed by a background thread using headless Chromium + Selenium; falls back to yt-dlp if unavailable.
- **Discovery:** `discover/engine.py` → `run_profile(deps, cfg, profile)`. Seeds from Last.fm `user.getTopArtists` / `tag.getTopArtists` (genre mode) → similar-artist expansion → YouTube resolve → yt-dlp download → Navidrome playlist write. State (dedupe history, next_runs, last_runs) persisted in `discover_state.json`.
- **Mix profiles:** schema in `discover/profiles.py`. Each profile: `id`, `name`, `schedule` (cadence/day/hour), `count`, `new_ratio` (fraction of new vs. library tracks), `seeds.mode` (history/genre/manual/playlist), `quality` gates. Built-ins (weekly, daily) auto-created on first run; genre profiles auto-generated from library genres.
- **Settings API:** `GET/POST /settings` with a 18-entry schema (`SETTINGS_SCHEMA`) covering Discovery, Sources, Maintenance, Server, and Credentials groups. Write-only for secret fields; returns only changed fields on POST.
- **Library tools:** `POST /library/enrich` (Last.fm genre tagging), `POST /library/repair` (MusicBrainz artist fix), `POST /library/dedup/run`, `GET/POST /library/suffixes` (title-cleanup rules). All long-running ops expose a `/status` polling endpoint.
- **Browser extension:** Manifest V3, vanilla JS. `chrome.storage.local` for playlists, `chrome.storage.sync` for LAN URL. Firefox and Chromium-based via the same manifest. Host permissions cover localhost, `*.local`, and common RFC1918 ranges.
- **Windows packaging:** PyInstaller onedir + Inno Setup 6. pystray tray app wraps the server. Per-user install in `%LOCALAPPDATA%\Programs\aMusicServer`. Auto-update polls both GitHub and Codeberg APIs.

### File layout

```
aMusicServerTemplate/
├── config.example.json             ← copy → config.json + fill in credentials
├── requirements.txt
├── discover/                       ← discovery engine
│   ├── engine.py                   ← run_profile(): blend math, acquire, playlist write
│   ├── profiles.py                 ← validate_profile(), migrate_config(), suggest_genre_profiles()
│   ├── library_pick.py             ← select_library_tracks() — genre/history modes
│   ├── seeds.py                    ← genre_seed_artists() via Last.fm tag.getTopArtists
│   ├── subsonic.py                 ← Subsonic API wrapper (get_genres, get_songs_by_genre, …)
│   ├── state.py                    ← DiscoverState (dedupe TTL, suggested dict, next_runs)
│   └── assemble.py                 ← write_weekly_mix(), read_playlist_basenames()
├── lastfm/                         ← Last.fm client + tag/similar artist helpers
├── soundcloud/                     ← SC client, search, mirror (likes/resolve), discovery
├── library/                        ← tagger.py (title cleanup, WOAS tag), enrich, repair, dedup
├── sWebExt/                        ← browser extension + Flask server
│   ├── manifest.json
│   ├── popup/                      ← popup.html + popup.js
│   ├── options.html + options.js
│   └── py_server/server.py         ← Flask app (all routes, scheduler threads)
├── scripts/
│   ├── sTownload/
│   │   ├── script_web.py           ← per-URL YouTube → MP3 (yt-dlp + post-processing)
│   │   └── app.py                  ← bulk Spotify-CSV → yt-dlp search → MP3
│   └── Sc2Sp_src/
│       └── script_web.py           ← per-URL SoundCloud → MP3 (custom pipeline)
├── web/
│   ├── templates/app.html          ← SIGNAL app shell (4 screens, bottom nav)
│   ├── static/app.js               ← SPA router + all screen logic (~1400 lines)
│   ├── static/app.css              ← SIGNAL design tokens + all component styles
│   └── static/fonts/               ← self-hosted woff2s (Unbounded, Archivo, JetBrains Mono)
├── tests/                          ← pytest suite (~390 tests)
└── windows/                        ← Windows packaging only
    ├── version.py
    ├── tray_app.py
    ├── updater.py
    ├── musicserver.spec
    ├── installer.iss
    └── build.bat
```

### API surface

| Route | Method | Purpose |
|---|---|---|
| `/` | GET | Serve SIGNAL web UI |
| `/` | POST | Browser extension download dispatch |
| `/mixes` | GET | List profiles + next_runs + last_runs |
| `/mixes` | POST | Create/update profile |
| `/mixes/<id>` | DELETE | Delete profile |
| `/mixes/<id>/run` | POST | Run profile now |
| `/mixes/suggest` | POST | Auto-generate genre profiles |
| `/settings` | GET/POST | Schema-driven config read/write |
| `/acquire` | POST | Download single track URL (YT/SC) |
| `/yt/search` | GET | YouTube flat search via yt-dlp |
| `/sc/search/tracks` | GET | SoundCloud track search |
| `/sc/search/users` | GET | SoundCloud artist/user search |
| `/sc/resolve` | GET | Resolve SC URL → track list |
| `/library/enrich` | POST | Tag files with Last.fm genre data |
| `/library/enrich/status` | GET | Enrich progress |
| `/library/repair` | POST | Fix missing artist tags via MusicBrainz |
| `/library/repair/status` | GET | Repair progress |
| `/library/dedup/run` | POST | Find and remove duplicate files |
| `/library/suffixes` | GET/POST | Title-cleanup suffix rules |
| `/playlists` | GET | Proxy Navidrome playlist list |

### Building from source (Windows installer)

```bat
cd windows
build.bat
```

Requires `py`, `pyinstaller`, `pystray`, `pillow` + project `requirements.txt`, and Inno Setup 6 at its default path. Output: `Output\Setup_aMusicServer_vX.Y.Z.exe`.

**Cutting a release:**

1. Bump `windows/version.py`
2. Commit, tag (`v1.0.1`), push to both remotes
3. GitHub Actions (`.github/workflows/build-windows.yml`) builds on a Windows runner and attaches the `.exe` to both the GitHub and Codeberg releases automatically.

One-time Codeberg setup: generate a token at `codeberg.org/user/settings/applications` (repo write scope), add it as the `CODEBERG_CI` secret in GitHub → Settings → Secrets. Without it the build still publishes to GitHub; the Codeberg step warns and exits cleanly.
