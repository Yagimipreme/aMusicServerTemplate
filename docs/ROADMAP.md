# aMusicServer — Project Roadmap

> Accurate as of 2026-06-14, branch `bare_bones`.
> Written for session continuity: a new session can read this and start working without exploring the codebase.

---

## Architecture overview

```
Browser extension (Manifest V3, popup + options)
    │ POST {url}
    ▼
Flask server  sWebExt/py_server/server.py  port 5000
    │
    ├── Download: yt-dlp (YouTube) or custom SC pipeline (SoundCloud)
    │   └── Post-process: library/tagger.py → Navidrome startScan.view
    │
    ├── Discovery: discover/engine.py
    │   └── Last.fm seeds → similar-artist expand → yt-dlp acquire → Navidrome playlist write
    │
    └── Library maintenance: library/{enrich,repair,scanner,dedupe,tagger}.py
```

Web UI: vanilla JS SPA, `web/static/app.js` (~1250 lines), hash router, 4 screens.
**Security invariant:** DOM built with `createElement`/`textContent` only — never `innerHTML` with data.

---

## Implemented feature surface

### Web UI (4 screens)

#### MIXES screen
- Lists all mix profiles; accordion expand with inline editor
- Per-mix editor fields: name, blend slider (new/library ratio), schedule (weekly/daily + day + hour), seed mode (history / genre / manual / playlist), genre chips, playlist input, size / cap
- RUN NOW button with status feedback
- DELETE button
- + NEW button creates blank card
- SUGGEST button calls `/mixes/suggest` to auto-generate genre profiles from library
- `isFresh` badge: mix shows "fresh" if it ran within one cadence period (uses `last_runs` from server)
- `suggested` badge for auto-generated-but-not-yet-enabled profiles

#### LIBRARY screen
- **Enrich metadata** — POST `/library/enrich` — tags MP3s with Last.fm genre data; progress bar via 2 s poll of `/library/enrich/status`
- **Repair library** — POST `/library/repair` — looks up missing artist tags via MusicBrainz and writes them; same progress pattern
- **De-duplicate** — POST `/library/dedup/run` — title-only key dedup (not artist+title); shows count from `r.would_delete`
- **Title cleanup** — GET/POST `/library/suffixes` — editable suffix rules textarea in expandable panel; rules strip junk like "(Official Video)" from titles
- All cards appended synchronously before any `await` (no double-card race on re-navigation)

#### SEARCH screen
- Text query or URL paste
- Parallel search: SC tracks + SC users + YouTube via `Promise.allSettled`
- SC artist chips at top (with avatar + follower count); click chip to browse artist's tracks
- Result rows: SC artwork thumbnail (falls back to "SC" label), title, artist (truncated at 30 chars), duration
- Source filter pills (all / soundcloud / youtube)
- `+` button: shows `…` while downloading, `✓` on success, `!` on error (2 s), `dup` on 409
- URL paste goes to `/acquire` directly (not search); YouTube playlist URLs rejected at server with 400

#### SETUP screen
- Schema-driven settings form: groups (Discovery / Sources / Maintenance / Server / Credentials)
- Secrets show as `••••••` on read; write-only fields hidden in GET response
- "changed only" POST — diffs current vs original, sends only dirty fields
- Collapsible groups; first group open by default

### Server — route inventory

| Route | Method | Status |
|---|---|---|
| `/` | GET | Serve SIGNAL app shell |
| `/` | POST | Browser extension download dispatch |
| `/mixes` | GET | List profiles + `next_runs` + `last_runs` |
| `/mixes` | POST | Create/update profile (validates via `validate_profile`) |
| `/mixes/<id>` | DELETE | Delete profile |
| `/mixes/<id>/run` | POST | Run profile immediately |
| `/mixes/suggest` | POST | Auto-suggest genre profiles from Navidrome genres |
| `/settings` | GET/POST | Schema-driven config read/write |
| `/acquire` | POST | Download single track (YT or SC); full post-processing pipeline |
| `/yt/search` | GET | YouTube flat search via `_YT_DLP` (venv-resolved) |
| `/sc/search/tracks` | GET | SoundCloud track search |
| `/sc/search/users` | GET | SoundCloud artist/user search |
| `/sc/resolve` | GET | Resolve SC URL → track list |
| `/sc/preview` | GET | Returns HLS stream URL for SC track (client_id appended) |
| `/library/enrich` | POST | Start Last.fm genre enrichment thread |
| `/library/enrich/status` | GET | Enrichment progress |
| `/library/repair` | POST | Start MusicBrainz artist-tag repair thread |
| `/library/repair/status` | GET | Repair progress |
| `/library/dedup/run` | POST | Scan and optionally delete title duplicates |
| `/library/dedup/report` | POST | Dry-run dedup report (no delete) |
| `/library/suffixes` | GET/POST | Title-cleanup suffix rules |
| `/playlists` | GET | Proxy Navidrome playlist list |
| `/discover/config` | GET/POST | Read/write `discover.*` config keys (legacy; superseded by Mixes UI) |
| `/discover/run` | POST | Legacy: run weekly discovery once |
| `/discover/run_daily` | POST | Legacy: run daily discovery once |
| `/discover/playlist_mix` | POST | Legacy: playlist-seeded discovery |
| `/preview` | GET | Get audio stream URL for SC or YT track |
| `/import/tracks` | POST | Batch download a track list; creates Navidrome playlist on completion |
| `/import/status` | GET | Poll batch import job progress by job_id |
| `/share/link` | GET | Encode a single track as a share URL |
| `/share/code` | GET | Encode a Navidrome playlist as pipe-delimited text |
| `/share/parse` | POST | Decode a share URL or text block |
| `/share/import` | GET | Receive share link (currently redirects to deleted `/explore`) |
| `/spotify/artist` | POST | Spotify artist lookup |
| `/spotify/playlist` | POST | Spotify playlist fetch |
| `/spotify/search` | GET | Spotify track search |

### Discovery engine (`discover/`)
- `run_profile(deps, cfg, profile)` — main entry; handles all 4 seed modes
- Seed modes: `history` (Last.fm `user.getTopArtists`), `genre` (Last.fm `tag.getTopArtists`), `manual` (artist list in profile), `playlist` (use a Navidrome playlist as seeds via `seed_playlist` field)
- Similar-artist expansion via Last.fm `artist.getSimilar`
- Quality gate: `min_artist_listeners`, `candidate_oversample`, `seed_artist_count`, `lastfm_period`/`lastfm_periods`
- Library blend: `new_ratio` controls fraction of new vs. library tracks; `library_pick.py` fills the rest
- Per-profile `last_runs` written to `discover_state.json` after each run
- Scheduler thread: wakes on `_mix_wake` event or at next scheduled run time; runs all due profiles
- `suggest_genre_profiles()` auto-creates mixes from top library genres

### Library modules
- `library/tagger.py` — `apply_from_config` (title suffix cleanup) + `write_source_url` (WOAS ID3 tag)
- `library/enrich.py` — Last.fm genre tagging for all MP3s in `song_dir`
- `library/repair.py` — MusicBrainz artist lookup for MP3s missing artist tag
- `library/scanner.py` — recursive MP3 scan with ID3 tag read; dedup key = title-only (not artist+title)
- `library/dedupe.py` — group by key, pick newest per group, optionally delete older copies

### SoundCloud pipeline (`soundcloud/`)
- `client.py` — token refresh via headless Chromium + Selenium; falls back to yt-dlp
- `search.py` — `search_tracks`, `search_users` via SC api-v2
- `mirror.py` — `_track_from_raw`, `resolve()` (URL → track list or playlist), `_user_from_raw`
- `discovery.py` — SC-based discovery (separate from Last.fm path)

### Spotify module (`spotify/`)
- `client.py` + `queries.py` — artist lookup, playlist fetch, track search
- Routes wired (`/spotify/*`) but **not surfaced in UI** — backend only

### Share codec (`share/codec.py`)
- `encode_track` → base64url JSON share URL (`/share/import?v=1&d=...`)
- `encode_playlist` → pipe-delimited text block (`PLAYLIST:name\nartist|title|url\n...`)
- `decode` — auto-detects single track URL vs playlist text
- `/share/import` route currently redirects to `/explore` which was **deleted** — broken redirect

### Browser extension (`sWebExt/`)
- Manifest V3; works on Firefox and Chromium
- Popup: send current URL to server, pick target playlist
- Options: server URL, pull playlists from Navidrome, test connection
- `chrome.storage.local` for playlists; `chrome.storage.sync` for server URL
- Host permissions cover localhost, `*.local`, common RFC1918 ranges

### Windows packaging (`windows/`)
- PyInstaller onedir + Inno Setup 6 per-user installer
- `tray_app.py` — pystray tray icon wrapping the Flask server
- `updater.py` — polls GitHub + Codeberg API for new releases, offers update via tray
- CI: `.github/workflows/build-windows.yml` — builds on Windows runner, publishes to GitHub releases; Codeberg publish via `CODEBERG_CI` secret

### Infrastructure
- mDNS / zeroconf service registration at startup (hostname from config)
- `config.json` — single source of truth; atomic write with lock
- `discover_state.json` — dedupe TTL, `next_runs`, `last_runs` per profile
- `logs/` — server log + per-import failure logs

---

## Known gaps and bugs

### Broken
- **`/share/import` redirects to `/explore`** which was deleted. Any incoming share link will 404. Fix: redirect to `/#search` with the payload in a query param instead, and handle decode in `renderSearch`.
- **`/preview` route uses hardcoded `.venv/bin/yt-dlp`** path (not the `_YT_DLP` constant). Should use `_YT_DLP`.
- **No UI for `/preview`** — the endpoint exists and works but is not called from `app.js`. Audio preview (30 s listen before downloading) was planned but not built.

### Not surfaced in UI
- **Spotify routes** (`/spotify/artist`, `/spotify/playlist`, `/spotify/search`) — fully implemented backend; no UI entry point.
- **`/import/tracks` + `/import/status`** — batch import pipeline fully implemented (downloads list of tracks, creates Navidrome playlist, writes failure log); no UI card to trigger it.
- **`/share/link`, `/share/code`, `/share/parse`** — share codec fully implemented; no UI to generate or receive share codes.
- **`/sc/preview`** — returns raw HLS stream URL; not called from UI.
- **`/library/dedup/report`** — dry-run dedup; UI only calls `/library/dedup/run`.

### UI gaps
- **No audio preview** in Search results — you cannot listen before downloading. `/preview` endpoint is ready.
- **No share UI** — there is no button to share a playlist or single track with another instance of the server.
- **No batch import UI** — `/import/tracks` can download a Spotify CSV export or any track list to a named playlist; no UI card exposes this.
- **Mixes screen: manual seed mode has no artist input** — the `seeds.artists` array is saved and loaded but the mix card has no input field for it. If you set mode=manual, there is no way to add artists via the UI.
- **Quality settings not editable per-mix** — `profile.quality` object (overrides for `min_artist_listeners`, `candidate_oversample`, `seed_artist_count`, `lastfm_period`) is persisted and respected by the engine but there is no UI to edit it. Only global defaults via Setup screen.
- **Repair / Enrich: no result details** — the status endpoint returns `fixed` / `enriched` counts but not which files were changed or what errors occurred.
- **Dedup: no review step** — clicking "run" immediately deletes duplicates. There is a `/library/dedup/report` endpoint for dry-run preview that is never called.
- **Setup screen: no `discover.*` quality keys** — `suggested_ttl_days`, `min_artist_listeners`, `candidate_oversample` are in the schema but editing them via Setup does not currently wire back to per-profile quality (they feed the legacy `_run_discover_once` path).

### Technical debt
- **Legacy discover routes** (`/discover/run`, `/discover/run_daily`, `/discover/playlist_mix`, `/discover/config`) are still present and tested, but the Mixes screen supersedes them completely. They could be removed once the test suite is updated.
- **Scheduler still calls old code paths** — `_profiles_due_now` / `_run_profile_once` in `server.py` call `run_profile()` correctly, but the legacy `_run_discover_once` / `_run_discover_daily_once` functions remain as separate paths and are tested independently.
- **`/preview` uses hardcoded cwd path** — subprocess in `preview()` uses `cwd=_PROJECT_ROOT` + hardcoded `.venv/bin/yt-dlp` instead of `_YT_DLP`.
- **`discover/state.py` `last_runs`** — `_record_last_run()` is called from `_run_profile_once()` in server.py, but not from the `run_profile()` path in `discover/engine.py`. If engine is called directly (not via server), last_runs is not updated.
- **No auth on Setup screen** — documented in the UI with a warning banner; intentional for LAN use but is a real gap for any public-facing deployment.

---

## Backlog / mentioned but not built

These were discussed or designed in earlier sessions but not implemented:

| Feature | Status | Spec/plan |
|---|---|---|
| **Audio preview** (30 s listen before download) | Discussed; backend endpoint exists, no UI | — |
| **Share codes UI** (share a playlist with another user's server instance) | Designed, codec built, no UI | `docs/superpowers/specs/2026-06-10-sc-spotify-ui-share-design.md` |
| **Batch Spotify CSV import UI** | Backend done, no UI card | `docs/superpowers/plans/2026-06-10-library-management-setup-wizard.md` |
| **SoundCloud discovery** (discover new tracks via SC tag search, not just Last.fm) | Designed | `docs/superpowers/specs/2026-06-12-soundcloud-discovery-design.md` |
| **Discovery quality improvements** | Designed | `docs/superpowers/specs/2026-06-12-discovery-quality-improvements-design.md` |
| **Per-mix quality editor** (edit `profile.quality` fields inline in mix card) | Not built | — |
| **Dedup dry-run review** (show groups before deleting) | Not built | — |
| **Manual seed artist input** in mix card | Not built | — |
| **Setup wizard** (guided first-run onboarding) | Designed | `docs/superpowers/specs/2026-06-10-library-management-setup-wizard-design.md` |
| **Windows tray — update notification for Linux/Mac** | Windows only currently | — |

---

## Test suite

- 386 tests (`pytest tests/ -q`)
- `tests/server/test_routes.py` (~840 lines) — route integration tests using Flask test client
- `tests/discover/` — engine, seeds, profiles, state unit tests
- `tests/library/` — scanner, tagger, enrich, repair, dedupe unit tests
- `tests/lastfm/` — Last.fm client unit tests
- `tests/test_setup_wizard.py` — setup/autostart helpers
- No frontend tests (vanilla JS, no test harness)

---

## Key file index

| File | Purpose |
|---|---|
| `sWebExt/py_server/server.py` | All Flask routes, scheduler thread, background workers (~1790 lines) |
| `web/static/app.js` | Entire SPA: router, 4 screens, all DOM logic (~1250 lines) |
| `web/static/app.css` | SIGNAL design tokens + all component styles (~122 lines) |
| `web/templates/app.html` | Shell with 4 screen divs + bottom nav |
| `web/static/fonts/` | Self-hosted woff2: Unbounded (500/800), Archivo (400/600), JetBrains Mono (400/700) |
| `discover/engine.py` | `run_profile()` — core discovery logic |
| `discover/profiles.py` | `validate_profile()`, `suggest_genre_profiles()`, `migrate_config()` |
| `discover/seeds.py` | `collect_seeds()` via Last.fm + playlist mode |
| `discover/state.py` | `DiscoverState` — dedupe TTL, discover_state.json I/O |
| `library/scanner.py` | MP3 scan + dedup key (title-only) |
| `library/tagger.py` | `apply_from_config()` (title cleanup), `write_source_url()` (WOAS tag) |
| `library/enrich.py` | Last.fm genre enrichment |
| `library/repair.py` | MusicBrainz artist tag repair |
| `soundcloud/mirror.py` | `_track_from_raw()`, `resolve()`, `_user_from_raw()` |
| `soundcloud/client.py` | SC token refresh (headless Chromium → yt-dlp fallback) |
| `share/codec.py` | `encode_track`, `encode_playlist`, `decode` |
| `config.example.json` | Reference config with all keys + comments |
| `discover_state.json` | Runtime state: dedupe TTL, next_runs, last_runs (gitignored) |
