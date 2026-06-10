# aMusicServerTemplate — Project Handoff

**Last updated:** 2026-06-10 (session 2)
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
| Tests | pytest, 144 passing |
| Venv | `.venv/` — has flask, yt-dlp, requests, selenium, spotipy, eyed3 |

---

## Module Map

```
sWebExt/py_server/server.py   ← Flask app — all routes, background threads
discover/
  engine.py      ← run_mix(), lastfm_is_ready(), run_weekly()
  seeds.py       ← get_bootstrap_seeds(), get_csv_artists(), get_library_artist_frequency()
  expand.py      ← expand_similar() — Navidrome or Last.fm similar-artist lookup
  acquire.py     ← download pipeline
  assemble.py    ← playlist assembly (writes .m3u)
  state.py       ← DiscoverState — suggested set + last_run timestamp
  dedupe.py      ← filter_fresh()
  resolve.py     ← resolve_tracks() — yt-dlp search per artist
  subsonic.py    ← Navidrome/Subsonic client (incl. find_artist_id)
  ytdlp_adapter.py ← make_search_fn(), make_download_fn()
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
  "sc_client_id": "[SET — auto-refreshed hourly via JS scrape]",
  "hostname": "amusicserver.local",
  "discover": {
    "schedule": "daily",
    "run_day": "sunday",
    "run_hour": 22,
    "manual_seeds": ["Burial", "Aphex Twin", "Boards of Canada", "Andy Stott", "The Haxan Cloak"]
  }
}
```

**Still missing:**
- `lastfm_api_key`, `lastfm_api_secret`, `lastfm_username` — blocks weekly mix; bootstrap runs instead
- `spotify_playlists_dir` — CSV seeds path for bootstrap (optional)

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
| POST | `/discover/run` | Trigger mix manually (blocking — runs full pipeline) |
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

## Discovery Engine — Current State

```
run_mix(deps, cfg)
  → lastfm_is_ready()?  YES → run_weekly()   (Last.fm top artists → similar → download)
                         NO  → Bootstrap Mix  (manual_seeds + Exportify CSV + library frequency)
```

### Bootstrap pipeline (active — no Last.fm keys yet)

```
get_bootstrap_seeds(cfg, subsonic) → [{"name": artist, "id": navidrome_id_or_None}]
  sources (priority order):
    1. manual_seeds from config (id=None — looked up via find_artist_id)
    2. Exportify CSV artists (id=None)
    3. library frequency from Navidrome getAlbumList2 (id included)
  → capped at 20, deduped, junk names filtered ([Unknown Artist] etc.)

expand_similar(subsonic, seeds)
  → per seed: subsonic.get_artist_info2(id) if id else find_artist_id(name) first
  → returns not-owned similar artists, scored by frequency across seeds

resolve_tracks(search_fn, artists)
  → ytsearch1:{artist} music   ← " music" appended to bias toward music results
  → duration > 900s dropped at search time

filter_fresh(song_exists, state, candidates)
  → drops tracks already in Navidrome (song_exists) or already suggested (state)

acquire(download_fn, candidate)
  → download_url(url, song_dir) with temp staging in /tmp/ytdlp_dl
  → moves finished mp3 to NAS only after ffmpeg conversion completes

state.save(stamp_last_run=True)  ← called unconditionally after every run
```

### Current library seed pool (top artists from Navidrome)

Mix of real artists and YouTube channel names. The good seeds:
- `Vatican Shadow`, `Pessimist`, `Kobosil` — industrial/techno/DnB
- `Chikoi The Maid`, `Medoi The Maid` — maid-core/anime electronic
- `XXXTENTACION` — trap
- Manual: `Burial`, `Aphex Twin`, `Boards of Canada`, `Andy Stott`, `The Haxan Cloak` — dark UK electronic

Garbage seeds still in the pool (not filtered): `BOONDAWG`, `f1rstpers0n`, `Nintendo DE`, `futureengineers`, `OOooHACKERooOO`, `RELEVANT DNB`, `UKF Dubstep`. These produce off-genre expansions but the `" music"` search qualifier limits the damage.

---

## Discover State — Current

```json
{
  "last_run": "2026-06-10T19:20:56",
  "suggested": []   ← race condition from concurrent runs; harmless since
}                      filter_fresh uses song_exists (Navidrome) as primary dedup
```

**Next scheduled run:** daily at 22:00. Will run bootstrap mix again (no Last.fm).

### Playlists written to NAS

| File | Tracks | Notes |
|---|---|---|
| `Weekly Mix.m3u` | 27 | Written by `run_weekly` (pre-fix run); maid-core heavy |
| `Starter Mix.m3u` | 1 | Written by bootstrap run (post-fix); only 1 acquired before race |

---

## Known Issues / Open Work

### Priority: mix coherence

The bootstrap seeds span too many genres (trap + maid-core + industrial + DnB + YouTube channels). Two paths to fix:

**Option A — Last.fm keys (recommended, best long-term)**
- Run `python setup.py --reconfigure`, complete step 6 (Last.fm API key + secret + username)
- Once `lastfm_is_ready()` gate passes (≥100 scrobbles, ≥15 unique artists), run_mix routes to `run_weekly` automatically
- Weekly mix seeds from your actual scrobble history → naturally coherent

**Option B — focused manual_seeds (quick)**
- Edit `config.json` → `discover.manual_seeds`
- Replace current 5-seed list with 10–15 artists all from one mood/genre
- Consider adding `"skip_library_seeds": true` flag (not yet implemented) to prevent library diversity from diluting manual seeds
- Current 5 manual seeds compete with 15 library seeds — library wins by count

### `skip_library_seeds` flag (not implemented)

When `manual_seeds` is non-empty, the user might want library expansion disabled entirely so the mix stays coherent with their curated seeds. Currently `get_bootstrap_seeds` always merges manual + CSV + library (capped at 20 total). The implementation would be a one-liner in `get_bootstrap_seeds`:

```python
if manual and cfg.get("discover", {}).get("skip_library_seeds"):
    return [{"name": n, "id": None} for n in manual[:20]]
```

### `POST /discover/run` is blocking

The HTTP endpoint runs the full pipeline synchronously. A full run takes 5–30 minutes (resolve = yt-dlp searches, acquire = downloads). Any HTTP client will timeout. Should be made async: start in background thread, return `{"status": "started"}` immediately, poll via `/discover/status`.

### Garbage library seeds

Artists like `BOONDAWG`, `Nintendo DE`, `futureengineers` are YouTube channels that produce off-genre expansions. Options:
- `skip_library_seeds` flag (see above)
- More aggressive `_is_junk_artist_name` filter (currently only catches `[Unknown Artist]` pattern)
- Let the issue self-correct: once Last.fm is configured, library seeds are replaced by scrobble history

### Race condition in concurrent discover runs

If two runs execute simultaneously (background thread + manual `/discover/run`), both load state at the start and the second run's `state.save()` overwrites the first. The `suggested` set ends up empty. This is **harmless** because `filter_fresh` uses `song_exists` (Navidrome lookup) as primary dedup, so tracks won't be re-downloaded. But the `suggested` list no longer grows, losing its fast-path optimization.

Fix: mutex/lock around `_run_discover_once()`, or make the route async so it doesn't race with the background thread.

---

## What Was Fixed This Session (commits 4000ce7, 59a8492)

### Bug 1 — NAS rename failure (`scripts/sTownload/script_web.py`)
yt-dlp staged `.part` files directly on the CIFS/NFS NAS mount. Concurrent fragment writes caused the rename from `.part` to final format to fail with `ENOENT`. Fix: `"paths": {"home": out_dir, "temp": "/tmp/ytdlp_dl"}` — fragments are written to local disk, finished mp3 moved to NAS only after ffmpeg conversion.

### Bug 2 — `_run_discover_once()` called wrong function (`sWebExt/py_server/server.py`)
Was calling `run_weekly()` directly, bypassing the `run_mix()` router entirely. Bootstrap path was never used. Fix: call `run_mix(deps, cfg)`. Also wired `lastfm_client` into deps so Last.fm activates automatically once keys are in config.

### Bug 3 — spin loop (`discover/state.py`, `discover/engine.py`, `sWebExt/py_server/server.py`)
`discover_state.json` was only written when tracks were acquired. Zero-acquire runs never wrote `last_run`, so the background loop re-ran every ~7 seconds. Fix: `state.save(stamp_last_run=True)` called unconditionally after every run; `while True` loop wrapped in `try/except` to survive transient errors.

### Additional fixes found during debugging
- `get_bootstrap_seeds` returned plain strings; `expand_similar` expected `{"name", "id"}` dicts — normalised throughout
- `expand_similar` had no fallback for seeds with `id=None` — `subsonic.find_artist_id(name)` added to resolve names to Navidrome IDs via `search3.view`
- `get_library_artist_frequency` stripped IDs from Navidrome dicts — now returns full objects
- Search quality: all artist searches append `" music"` to bias YouTube results away from tech/cooking channels; candidates > 900s dropped at search time
- Seed filter: `[Unknown Artist]` and bracket-pattern names skipped

---

## How to Resume

```bash
cd /home/taichi/repos/musicServer/aMusicServerTemplate

# Start server (if not running)
.venv/bin/python sWebExt/py_server/server.py --host 0.0.0.0 --port 5000 > /tmp/aMusicServer.log 2>&1 &

# Check it's alive
curl http://localhost:5000/explore -I

# Run tests
.venv/bin/python -m pytest tests/ -q --ignore=tests/library

# Trigger manual discover run (warning: blocks until complete, may take 10–30 min)
curl -X POST http://localhost:5000/discover/run

# Watch discover log
tail -f logs/server.log | grep -E "(DISCOVER|discover:|seeds|candidates|fresh|acquired|ERROR)"

# Add Last.fm keys (enables weekly mix)
.venv/bin/python setup.py --reconfigure
```

---

## Recommended Next Steps

1. **Add Last.fm keys** — biggest unlock; enables personalised weekly mix, genre enrichment, proper coherence
   - `python setup.py --reconfigure` → step 6
   - Keys free at last.fm/api/account/create

2. **Decide on mix coherence approach** — manual_seeds only, or wait for Last.fm
   - If manual: update `config.json` → `discover.manual_seeds` with 10–15 focused artists
   - Consider implementing `skip_library_seeds` flag (one-liner, described above)

3. **Make `/discover/run` async** — currently blocks HTTP for 10–30 min; any curl/browser will timeout
   - Start run in background thread, return `{"status": "started", "run_id": ...}` immediately
   - Add `/discover/status` polling endpoint

4. **Server autostart** — systemd unit spec written, not yet installed
   - `python setup.py` → last step

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
