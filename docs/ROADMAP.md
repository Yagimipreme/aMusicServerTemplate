# aMusicServer — Project Roadmap

> Accurate as of 2026-08-24, branch `bare_bones`.
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
- Per-mix editor fields: name, blend slider (new/library ratio), schedule (weekly/daily + day + hour), seed mode (history / genre / manual / playlist), genre chips, manual-artist chips, playlist input, size / cap
- Manual seed mode: artist chip row (add via Enter, remove via ✕) — only visible when mode=manual
- Advanced (collapsible): per-mix `quality` overrides — `min_artist_listeners`, `candidate_oversample`, `seed_artist_count`, `lastfm_period`; blank field inherits the global default and is omitted from the saved profile
- RUN NOW button with status feedback
- DELETE button
- + NEW button creates blank card
- SUGGEST button calls `/mixes/suggest` to auto-generate genre profiles from library
- `isFresh` badge: mix shows "fresh" if it ran within one cadence period (uses `last_runs` from server)
- `suggested` badge for auto-generated-but-not-yet-enabled profiles

#### LIBRARY screen
- **Enrich metadata** — POST `/library/enrich` — tags MP3s with Last.fm genre data; progress bar via 2 s poll of `/library/enrich/status`
- **Repair library** — POST `/library/repair` — looks up missing artist tags via MusicBrainz and writes them; same progress pattern
- **De-duplicate** — two-step review: "scan" calls `/library/dedup/report` and lists each duplicate group with a KEEP row and checked-by-default duplicates; unchecking narrows the selection; "Delete N files" calls `/library/dedup/delete` with the checked paths only
- **Title cleanup** — GET/POST `/library/suffixes` — editable suffix rules textarea in expandable panel; rules strip junk like "(Official Video)" from titles
- **Share** — send half: pick a Navidrome playlist, "get code" fetches `/share/code` and shows a copyable text block; receive half: paste a share link or `PLAYLIST:` block, "preview" calls `/share/parse` and lists tracks, "import" runs the shared batch-import job. A `/share/import` link lands here pre-filled via `?share=`.
- **Import playlist** — accepts a Spotify playlist link (`/spotify/playlist`), an Exportify CSV (parsed client-side, no upload endpoint), or plain "Artist - Title" lines; previews the track list then runs the same batch-import job as Share
- All cards appended synchronously before any `await` (no double-card race on re-navigation)

#### SEARCH screen
- Text query or URL paste
- Parallel search: SC tracks + SC users + YouTube via `Promise.allSettled`
- SC artist chips at top (with avatar + follower count); click chip to browse artist's tracks
- Result rows: SC artwork thumbnail (falls back to "SC" label), title, artist (truncated at 30 chars), duration, `▶` preview button
- `▶` resolves `/sc/preview` (SC, via `progressive_url`) or `/preview` (YouTube) and plays through a shared mini-player bar docked above the nav; only one stream plays at a time
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
| `/sc/preview` | GET | Resolve SC progressive transcoding → playable CDN mp3 |
| `/library/enrich` | POST | Start Last.fm genre enrichment thread |
| `/library/enrich/status` | GET | Enrichment progress |
| `/library/repair` | POST | Start MusicBrainz artist-tag repair thread |
| `/library/repair/status` | GET | Repair progress |
| `/library/dedup/run` | POST | Scan and optionally delete title duplicates |
| `/library/dedup/report` | POST | Dry-run dedup report (no delete) |
| `/library/dedup/delete` | POST | Delete an explicit list of duplicate files (song_dir containment checked) |
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
| `/share/import` | GET | Redirect to `/?share=<d>#library` |
| `/spotify/artist` | POST | Spotify artist lookup |
| `/spotify/playlist` | POST | Spotify playlist fetch |
| `/spotify/search` | GET | Spotify track search |
| `/follow/search` | GET | MusicBrainz artist search |
| `/follow` | GET | List followed artists + state summary |
| `/follow` | POST | Follow an artist (triggers immediate backfill) |
| `/follow/<mbid>` | DELETE | Unfollow an artist |
| `/follow/run` | POST | Trigger a follow run immediately |
| `/follow/feed` | GET | NEW RELEASES feed (reversed, newest first) |
| `/follow/feed/seen` | POST | Mark feed as seen (clears unseen badge) |
| `/follow/settings` | POST | Update `follow.*` config keys live |

### Follow Artists / NEW RELEASES (`follow/`)

- `follow/store.py` — `follows.json` read/write (atomic, idempotent by MBID)
- `follow/musicbrainz.py` — MusicBrainz client (1 req/s, descriptive User-Agent); search, release-groups, release tracks
- `follow/listenbrainz.py` — ListenBrainz fresh-releases feed client
- `follow/fstate.py` — `follow_state.json` — non-expiring acquired set, backfill markers, pending retry queue, feed (capped at 200), unseen count
- `follow/detect.py` — pure detection: feed filter (intersect followed MBIDs) + one-time per-artist backfill + scope mapping (Singles/EPs full, Albums one track)
- `follow/notify.py` — feed append + optional webhook JSON + ntfy push
- `follow/runner.py` — one-run orchestration: detect → resolve → acquire → playlist → notify → state save; retry-3 pending queue; idempotent (acquired set prevents re-download)
- Server wiring: `_follow_cfg()`, `_build_follow_clients()`, `_run_follow_once()`, `_follow_scheduler_loop()` daemon thread; follow routes added to `server.py`
- Web UI: Follows screen (artist search, followed list, NEW RELEASES feed, settings panel, Run now button, nav badge)

### Discovery engine (`discover/`)
- `run_profile(deps, cfg, profile)` — main entry; handles all 4 seed modes
- Seed modes: `history` (Last.fm `user.getTopArtists`), `genre` (Last.fm `tag.getTopArtists`), `manual` (artist list in profile), `playlist` (use a Navidrome playlist as seeds via `seed_playlist` field)
- Similar-artist expansion via Last.fm `artist.getSimilar`
- Quality gate: `min_artist_listeners`, `candidate_oversample`, `seed_artist_count`, `lastfm_period`/`lastfm_periods`
- Library blend: `new_ratio` controls fraction of new vs. library tracks; `library_pick.py` fills the rest
- Per-profile `last_runs` written to `discover_state.json` after each run
- Scheduler thread: wakes on `_mix_wake` event or at next scheduled run time; runs all due profiles
- `suggest_genre_profiles()` auto-creates mixes from top library genres
- `discover/playlist_sync.py` — merge-aware Navidrome playlist sync (`merge_playlist`, `resolve_paths`, `migrate_from_m3u`, `sync_playlist`). `run_profile` and the follow runner use this against the Subsonic API instead of overwriting an `.m3u` file, so user edits to a playlist in Navidrome survive the next run. `discover_state.json` carries a per-profile `playlists` ledger (`{playlist_id, owned, pending, migrated}`); `follow_state.json` carries the single `playlist` ledger for NEW RELEASES.

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
- `/share/import` route redirects to `/?share=<d>#library`, where the Library screen's Share card picks up the payload and previews it

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

### UI gaps
- **Repair / Enrich: no result details** — the status endpoint returns `fixed` / `enriched` counts but not which files were changed or what errors occurred.
- **Setup screen: no `discover.*` quality keys** — `suggested_ttl_days`, `min_artist_listeners`, `candidate_oversample` are in the schema but editing them via Setup does not currently wire back to per-profile quality (they feed the legacy `_run_discover_once` path).

### Technical debt
- **Legacy discover routes** (`/discover/run`, `/discover/run_daily`, `/discover/playlist_mix`, `/discover/config`) are still present and tested, but the Mixes screen supersedes them completely. They could be removed once the test suite is updated.
- **Scheduler still calls old code paths** — `_profiles_due_now` / `_run_profile_once` in `server.py` call `run_profile()` correctly, but the legacy `_run_discover_once` / `_run_discover_daily_once` functions remain as separate paths and are tested independently.
- **`discover/state.py` `last_runs`** — `_record_last_run()` is called from `_run_profile_once()` in server.py, but not from the `run_profile()` path in `discover/engine.py`. If engine is called directly (not via server), last_runs is not updated.
- **No auth on Setup screen** — documented in the UI with a warning banner; intentional for LAN use but is a real gap for any public-facing deployment.
- **Legacy `run_weekly` / `run_mix` still write m3u** — `run_profile` and the follow runner now use `discover/playlist_sync.py` against the Subsonic API, but the deprecated bootstrap paths in `discover/engine.py` (lines ~63-70 and ~188-195) still call `write_weekly_mix`. `discover/assemble.py` is retained as the credential-less fallback.

---

## Backlog / mentioned but not built

These were discussed or designed in earlier sessions but not implemented:

| Feature | Status | Spec/plan |
|---|---|---|
| **SoundCloud discovery** (discover new tracks via SC tag search, not just Last.fm) | Designed | `docs/superpowers/specs/2026-06-12-soundcloud-discovery-design.md` |
| **Discovery quality improvements** | Designed | `docs/superpowers/specs/2026-06-12-discovery-quality-improvements-design.md` |
| **Setup wizard** (guided first-run onboarding) | Designed | `docs/superpowers/specs/2026-06-10-library-management-setup-wizard-design.md` |
| **Windows tray — update notification for Linux/Mac** | Windows only currently | — |

---

## Test suite

- 637 passed, 1 skipped (`pytest tests/ -q`)
- `tests/server/test_routes.py` (~840 lines) — route integration tests using Flask test client
- `tests/discover/` — engine, seeds, profiles, state, subsonic, `test_playlist_sync.py` (merge/resolve/migrate/sync) unit tests
- `tests/follow/` — store, MusicBrainz client, ListenBrainz client, fstate, detect, notify, runner unit tests (~27 tests)
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
| `discover/state.py` | `DiscoverState` — dedupe TTL, discover_state.json I/O, per-playlist merge ledgers |
| `discover/playlist_sync.py` | Merge-aware Navidrome playlist sync — `merge_playlist`, `resolve_paths`, `migrate_from_m3u`, `sync_playlist` |
| `library/scanner.py` | MP3 scan + dedup key (title-only) |
| `library/tagger.py` | `apply_from_config()` (title cleanup), `write_source_url()` (WOAS tag) |
| `library/enrich.py` | Last.fm genre enrichment |
| `library/repair.py` | MusicBrainz artist tag repair |
| `soundcloud/mirror.py` | `_track_from_raw()`, `resolve()`, `_user_from_raw()` |
| `soundcloud/client.py` | SC token refresh (headless Chromium → yt-dlp fallback) |
| `share/codec.py` | `encode_track`, `encode_playlist`, `decode` |
| `config.example.json` | Reference config with all keys + comments |
| `discover_state.json` | Runtime state: dedupe TTL, next_runs, last_runs (gitignored) |
| `follows.json` | Followed-artist list (gitignored, written at runtime) |
| `follow_state.json` | Follow runtime state: acquired set, backfill markers, feed, pending (gitignored) |
| `follow/store.py` | Followed-artist list I/O |
| `follow/musicbrainz.py` | MusicBrainz API client |
| `follow/listenbrainz.py` | ListenBrainz fresh-releases client |
| `follow/fstate.py` | `FollowState` — non-expiring state, feed, pending queue |
| `follow/detect.py` | Feed filter + backfill + scope mapping → download targets |
| `follow/notify.py` | Feed append + webhook/ntfy push |
| `follow/runner.py` | One follow run orchestration |
