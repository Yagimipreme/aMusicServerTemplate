# aMusicServerTemplate — Project Handoff

**Last updated:** 2026-06-10  
**Server:** `/home/taichi/repos/musicServer/aMusicServerTemplate`  
**Run:** `.venv/bin/python sWebExt/py_server/server.py --host 0.0.0.0 --port 5000`  
**LAN access:** `http://amusicserver.local:5000` (mDNS) or `http://192.168.178.140:5000`

---

## What This Project Is

A self-hosted, single-machine music pipeline that:

1. **Downloads** music from SoundCloud, Spotify (via CSV/Exportify), YouTube (yt-dlp)
2. **Organises** files to a local NAS (`/mnt/nas1/media/music`) and tags them with ID3 metadata
3. **Serves** a Navidrome-compatible library over Subsonic API
4. **Discovers** new music automatically — weekly Last.fm-based mixes when warm, Bootstrap Mix on cold start
5. **Explores** via mobile-first web UI at `/explore` — unified search bar, SC/Spotify/YouTube handling, preview + share
6. **Shares** tracks between devices via signed share URLs (`/share/import?v=1&d=<b64>`)

The target end state is: run `python setup.py` once, get Navidrome playing your migrated library and auto-discovering new music weekly with zero ongoing manual effort.

---

## Stack

| Layer | Tech |
|---|---|
| Web server | Flask + flask-cors, served on port 5000 |
| mDNS | zeroconf (`amusicserver.local`) |
| Download engine | yt-dlp (YT, fallback for SC), Sc2Sp pipeline (SC native HLS) |
| Metadata | eyeD3 (ID3 tags), WOAS frame for source URL |
| Music server | Navidrome (external, `localhost:4533`) via Subsonic API |
| Discovery | Last.fm API + Subsonic → yt-dlp download chain |
| SC auth | JS bundle scrape → `client_id` in `config.json`, refreshed hourly |
| Tests | pytest, 144 passing (library tests need eyed3 in venv) |
| Venv | `.venv/` — has flask, yt-dlp, requests, selenium, spotipy, eyed3 |

---

## Module Map

```
sWebExt/py_server/server.py   ← Flask app — all routes, background threads
discover/
  engine.py      ← run_mix(), lastfm_is_ready(), run_weekly()
  seeds.py       ← get_bootstrap_seeds(), get_csv_artists(), get_library_artist_frequency()
  acquire.py     ← download pipeline
  assemble.py    ← playlist assembly
  subsonic.py    ← Navidrome/Subsonic client
lastfm/
  client.py      ← Last.fm API client
  seeds.py       ← top artists/tracks extraction
soundcloud/
  client.py      ← SC API client (uses config sc_client_id)
  mirror.py      ← get_profile(), resolve(), _track_from_raw()
  search.py      ← search_users(), search_tracks()
spotify/
  client.py      ← Spotify internal API (no OAuth — scrapes web player tokens)
share/
  codec.py       ← encode_track(), encode_playlist(), decode()
library/
  scanner.py     ← walk song_dir, read ID3 tags
  dedupe.py      ← duplicate scanner
  tagger.py      ← write_source_url() (WOAS frame)
  enrich.py      ← Last.fm genre enrichment
scripts/
  sTownload/script_web.py    ← download_url() — yt-dlp wrapper
  Sc2Sp_src/script_web.py   ← download_single_soundcloud(), fetch_client_id_via_selenium()
setup.py                      ← 14-step interactive setup wizard
web/
  templates/explore.html
  static/explore.js
  static/explore.css
```

---

## Key Config (`config.json`) — Current State

```json
{
  "song_dir": "/mnt/nas1/media/music",
  "navidrome_url": "http://localhost:4533",
  "navidrome_user": "taichi",
  "navidrome_pass": "[SET]",
  "sc_username": "https://soundcloud.com/user352647366/likes",
  "sp_playlist_ids": ["6Nn6uwPMgNQCM3P2bAMIFX", "..."],
  "sc_client_id": "[SET — auto-refreshed hourly via JS scrape]",
  "hostname": "amusicserver.local"
}
```

**Missing from live config** (present in `config.example.json`, needed for full feature set):
- `discover` block (schedule, bootstrap seeds, lastfm_readiness thresholds)
- `lastfm_api_key`, `lastfm_api_secret`, `lastfm_username`
- `enrich`, `dedup`, `title_cleanup` blocks

Run `python setup.py --reconfigure` to fill these in interactively.

---

## Server Routes (Full API Surface)

| Method | Route | What it does |
|---|---|---|
| GET | `/` | Legacy download form (POST) |
| GET | `/explore` | Mobile web UI |
| GET | `/sc/resolve` | Resolve SC URL → track/user/set |
| GET | `/sc/search/users` | Search SC users |
| GET | `/sc/search/tracks` | Search SC tracks |
| GET | `/sc/preview` | Stream URL for SC track |
| GET | `/preview` | yt-dlp `--dump-json` (YT/SC preview) |
| POST | `/spotify/artist` | Spotify artist discography |
| POST | `/spotify/playlist` | Spotify playlist tracks |
| GET | `/spotify/search` | Spotify artist search |
| GET | `/playlists` | List Navidrome playlists |
| POST | `/discover/run` | Trigger weekly mix manually |
| POST | `/discover/playlist_mix` | Mix from specific playlist |
| POST | `/library/dedup/run` | Scan for duplicates |
| POST | `/library/dedup/report` | Dedup report |
| POST | `/library/enrich` | Last.fm genre enrichment |
| GET | `/library/enrich/status` | Enrichment job status |
| POST | `/import/tracks` | Download track(s) by URL/search |
| GET | `/import/status` | Poll import job progress |
| GET | `/share/link` | Generate single-track share URL |
| GET | `/share/code` | Generate playlist share code |
| POST | `/share/parse` | Decode share URL or PLAYLIST: block |
| GET | `/share/import` | Receive share link → redirect to `/explore#import:` |

---

## Background Threads (server startup)

1. **`_refresh_sc_client_id_loop`** — every 3600s; tries Selenium first, falls back to JS bundle scrape of soundcloud.com
2. **`_discover_weekly_loop`** — runs `run_mix()` on schedule (daily/weekly); first run is immediate if no `last_run` in `discover_state.json`
3. **`_dedup_scheduled_loop`** — only if `dedup.enabled` in config

---

## Web UI (`/explore`)

Single page, two tabs:

**Explore tab:**
- Single search input — routes by URL content:
  - `soundcloud.com` → `handleScUrl()` → `/sc/resolve`
  - `spotify.com/artist/` → `/spotify/artist`
  - `spotify.com/playlist/` → `/spotify/playlist`
  - `spotify.com/track/` → `/spotify/search` fallback
  - `youtube.com` / `youtu.be` → yt-dlp preview + clean params
  - Plain text → parallel SC + Spotify search
- Track list: checkbox · cover · title · artist · badge (SC/SP/YT) · status · preview ▶ · share ⬆
- Save bar (bottom, fixed): selected count · playlist name input · Save Selected · Save All · Share (playlist)
- Player bar (fixed above save bar)

**Import Share tab:**
- Paste textarea for share URL or `PLAYLIST:` text block
- Auto-triggered when visiting `/share/import?...` (redirects to `#import:` fragment)
- Parsed tracks drop into the shared track list → Save as normal

**Share URLs generated:**
- Single track: `http://amusicserver.local:5000/share/import?v=1&d=<base64url(json)>`
- Playlist: pipe-delimited `PLAYLIST:<name>\n<artist>|<title>|<url>` block
- Codec uses `config.json` hostname — always generates `amusicserver.local` URLs even when accessed via IP

---

## SC Client ID Refresh Chain

1. `_refresh_sc_client_id_loop()` runs at startup
2. Tries `scripts/Sc2Sp_src/script_web.py::fetch_client_id_via_selenium()` (headless Chrome)
3. If that fails (Chrome unavailable, as on this server): `_fetch_sc_client_id_via_scrape()` — fetches soundcloud.com, finds SC CDN JS bundles, regex-searches for `client_id=<32chars>`
4. Writes fresh `sc_client_id` + `sc_client_id_ts` to `config.json`
5. SC routes return `{"status":"connecting","retry_after":15}` if `_sc_client_ready` is False — JS auto-retries

---

## Discovery Engine

```
run_mix(deps, cfg)
  → lastfm_is_ready()?  YES → run_weekly()   (Last.fm top artists → similar → download)
                         NO  → Bootstrap Mix  (manual_seeds + Exportify CSV + library frequency)
```

`lastfm_is_ready()` gate: `>= 100` scrobbles AND `>= 15` unique artists. Caches positive result in `discover_state.json["lastfm_ready"]`.

Bootstrap Mix: `get_bootstrap_seeds()` → deduped list of ≤20 artists → same acquire/assemble pipeline as weekly mix → Navidrome playlist created.

---

## Known Issues / Active Limitations

| Issue | Status |
|---|---|
| `config.json` missing `discover` / `lastfm_*` keys | Config needs `--reconfigure` run |
| Selenium headless Chrome fails on this server | Worked around: JS bundle scrape fallback |
| `somerust` venv used historically — no flask/yt-dlp there | Fixed: server must use `.venv/bin/python` |
| SC 401 on stale client_id | Fixed: returns `connecting` + auto-retry; JS bundle scrape refreshes hourly |
| Library tests excluded from CI run | eyed3 not installed in `somerust` venv; works fine in `.venv` |
| `sp_username` empty in config | Spotify search still works via internal token; playlist imports need playlist IDs |

---

## Roadmap

### DONE ✓

- [x] Flask server with all download + discovery routes
- [x] SoundCloud client + mirror (resolve, search, profile, likes)
- [x] Spotify internal client (search, artist discography, playlist)
- [x] yt-dlp download pipeline with title cleanup
- [x] Last.fm client (top artists, similar artists, weekly chart)
- [x] Weekly Mix engine (Last.fm → discover → download → Navidrome playlist)
- [x] Bootstrap Mix (cold start — manual seeds + CSV + library frequency)
- [x] Configurable scheduler (daily/weekly, day, hour)
- [x] Library scanner, deduper, ID3 tagger
- [x] Last.fm genre enrichment
- [x] Share codec (single track + playlist, base64url encode/decode)
- [x] Share import flow (`/share/import` redirect + JS auto-parse + download)
- [x] Mobile-first Explore UI (unified search bar, SC/SP/YT, badges, preview, share)
- [x] Clipboard fallback for HTTP LAN (no HTTPS required)
- [x] SC client_id auto-refresh via JS bundle scrape (Selenium fallback)
- [x] Setup wizard (14 steps: SC likes, Exportify CSV, Last.fm, autostart, schedule)
- [x] mDNS registration (`amusicserver.local`)
- [x] Navidrome playlist creation after import + rescan trigger
- [x] 144 tests passing

### IN PROGRESS / NEXT

- [ ] **Run `setup.py --reconfigure`** to fill in `lastfm_*` and `discover` config keys — blocks weekly mix
- [ ] **Test full download round-trip** from Import Share tab → Save All → file appears in Navidrome
- [ ] **Test discover/run** once Last.fm keys are configured
- [ ] **Server autostart** — add systemd unit or Windows `.bat` startup (spec written, not yet installed)

### NICE TO HAVE (not specced yet)

- [ ] **README rewrite** — project feature-complete enough to document properly
- [ ] **HTTPS / self-signed cert** — enables `navigator.clipboard` natively on LAN without execCommand fallback
- [ ] **SC Likes auto-import** — `get_user_likes()` is implemented in `soundcloud/mirror.py`; needs a trigger (cron or UI button)
- [ ] **Playlist share via QR code** — phone sees QR on desktop → taps → auto-imports
- [ ] **Web player improvement** — currently a raw `<audio>` element; could stream from Navidrome
- [ ] **Download queue UI** — show all in-progress / completed imports, not just the current job
- [ ] **Multi-user / auth** — currently single-user, no access control

---

## How to Resume

```bash
cd /home/taichi/repos/musicServer/aMusicServerTemplate

# Start server
.venv/bin/python sWebExt/py_server/server.py --host 0.0.0.0 --port 5000 > /tmp/aMusicServer.log 2>&1 &

# Check it's alive
curl http://localhost:5000/explore -I

# Run tests
.venv/bin/python -m pytest tests/ -q --ignore=tests/library

# Reconfigure (add lastfm keys, discover block, etc.)
.venv/bin/python setup.py --reconfigure

# Fill in Last.fm keys → trigger first weekly mix
curl -X POST http://localhost:5000/discover/run
```

---

## Spec Documents

All design decisions are in `docs/superpowers/specs/`:

| Spec | Topic |
|---|---|
| `2026-06-09-discover-addon-design.md` | Discover engine architecture |
| `2026-06-10-lastfm-integration-design.md` | Last.fm weekly mix, enrichment |
| `2026-06-10-library-management-setup-wizard-design.md` | Library scanner, dedup, tagger, setup.py |
| `2026-06-10-sc-spotify-ui-share-design.md` | SC/Spotify clients, Explore UI, share codec |
| `2026-06-10-setup-wizard-enhancements-design.md` | Bootstrap Mix, configurable schedule, autostart, argparse CLI |
