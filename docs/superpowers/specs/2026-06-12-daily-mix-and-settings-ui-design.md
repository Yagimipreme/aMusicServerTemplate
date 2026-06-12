# Daily Mix + Web UI Settings Editor — Design

**Date:** 2026-06-12
**Status:** Approved (pending spec review)

## Goals

1. **Daily Mix** — a small daily discovery drop: ~7 freshly acquired tracks per day in a
   separate `Daily Mix` playlist that holds a rolling window of the last 7 days.
   The Weekly Mix returns to a weekly cadence and stays the big refresh.
2. **Settings editor** — a Settings tab on the explore page that can edit all of
   `config.json`, including credentials (masked, write-only), driven by a single
   server-side schema.

## Part 1 — Daily Mix

### Config (`config.json` → `discover`)

```json
"discover": {
  "schedule": "weekly",          // restored: weekly mix runs Sundays again
  "daily": {
    "enabled": true,
    "count": 7,                  // fresh tracks acquired per day
    "run_hour": 7,               // 07:00 local
    "window_days": 7,            // playlist holds count × window_days tracks
    "playlist_name": "Daily Mix"
  }
}
```

`config.example.json` gets the same block with comments stripped.

### Engine: `run_daily(deps, cfg)` in `discover/engine.py`

Mirrors the Last.fm branch of `run_mix()` with a small count:

1. Same readiness gate (`lastfm_is_ready`); if not ready, return
   `{"status": "skipped", "reason": "lastfm not ready"}` — no bootstrap variant for
   daily (the Starter Mix already covers cold start).
2. Seeds → `expand_similar` → `enrich_artist_info` → `resolve_tracks` →
   `filter_fresh` → `acquire`, reusing the weekly path's config knobs
   (`min_artist_listeners`, `candidate_oversample`, `seed_playlist`,
   `lastfm_periods`). Acquisition stops at `daily.count`.
3. **Shared dedupe:** uses the same `deps.state` (`discover_state.json` suggested
   keys + TTL), so daily and weekly never acquire the same track twice.
4. **Rolling window via existing assembler:** call `write_weekly_mix(song_dir,
   acquired_paths, name=daily.playlist_name, cap=daily.count * daily.window_days)`.
   That function already appends, dedupes, and drops oldest-over-cap — the m3u file
   itself is the window state. No new state structures.
5. Trigger `subsonic.start_scan()` and `deps.state.save(stamp_last_run=False)` —
   the daily run must not refresh `last_run`, which belongs to the weekly loop's
   initial-run detection. (Known nuance: `DiscoverState.save()` stamps anyway when
   `last_run` is `None`; acceptable, because the weekly initial-run check happens
   at server startup, before any daily cycle can fire.)

`run_daily_from_config(project_root)` companion mirrors `run_mix_from_config` for
CLI / manual use.

### Scheduler: `sWebExt/py_server/server.py`

- New `_discover_daily_loop()` thread, started alongside the weekly loop:
  reads `discover.daily` each cycle, sleeps until the next `run_hour`
  (reuse `_seconds_until_next_run("daily", "", run_hour)`), runs
  `_run_discover_daily_once()`, repeats. If `daily.enabled` is false, it checks
  again in an hour rather than exiting (so enabling via the UI takes effect
  without restart).
- `_run_discover_daily_once()` mirrors `_run_discover_once()` but calls
  `run_daily`.
- New route `POST /discover/run_daily` → manual trigger, returns the run result.
- Errors: same pattern as weekly — log with `logger.exception`, sleep 3600, retry.

## Part 2 — Settings editor

### Server: schema-driven `/settings`

A module-level `SETTINGS_SCHEMA` — an ordered list of entries:

```python
{"path": "discover.daily.count", "type": "int", "label": "Daily mix size",
 "group": "Discovery", "min": 1, "max": 50}
{"path": "navidrome_pass", "type": "secret", "label": "Navidrome password",
 "group": "Credentials"}
{"path": "sp_playlist_ids", "type": "list[str]", "label": "Spotify playlist IDs",
 "group": "Sources"}
```

Types: `str`, `int`, `bool`, `secret`, `list[str]`. Groups: **Discovery**
(weekly + daily + quality knobs), **Sources** (sc_username, sp_playlist_ids,
sc_topsong, spotify_playlists_dir), **Maintenance** (dedup.*, title_cleanup.*),
**Server** (hostname, song_dir, path), **Credentials** (navidrome_url/user/pass,
lastfm_api_key/api_secret/username).

- `GET /settings` → `{"schema": [...], "values": {...}}`. Values are read from
  `config.json` by dot-path. **`secret` fields return `""` plus `"set": true/false`
  — current secret values are never sent to the browser.**
- `POST /settings` → body `{path: value, ...}`. Validation: unknown path → 400
  listing offending keys; type mismatch / out-of-range → 400 with field errors;
  `secret` fields with empty string are ignored (means "unchanged"). Valid updates
  are deep-merged into `config.json` by dot-path (creating intermediate dicts),
  written atomically (`.tmp` + `os.replace`, matching `state.py`).
- The existing `GET/POST /discover/config` routes and `_DISCOVER_CONFIG_KEYS`
  stay untouched (Discover tab keeps working); `/settings` is additive.

### UI: Settings tab in `web/templates/explore.html` + `web/static/explore.js`

- New tab button **Settings** following the existing tab pattern.
- On open, fetch `GET /settings`, render one `<fieldset>` per group, inputs by
  type: number/text/checkbox; `secret` → `<input type="password">` with
  placeholder "(unchanged)" when `set` is true; `list[str]` → textarea, one per
  line.
- One **Save** button per group → POST only that group's changed fields; inline
  success/error feedback per group (reuse existing status-line styling from the
  Discover tab).
- A short warning banner in the Credentials group: "This page has no
  authentication — anyone on your network can change these."

## Security note (accepted trade-off)

The server is unauthenticated on the LAN. The user explicitly chose to expose
credentials as editable. Mitigations: masked inputs, write-only secrets (never
echoed in GET), and the warning banner. No auth layer is in scope here.

## Testing

`tests/discover/test_daily.py` + route tests in the existing test layout:

1. `run_daily` acquires at most `count`, writes m3u with `cap = count*window_days`,
   does not stamp `last_run`, and skips when Last.fm not ready.
2. Rolling window: two consecutive runs append; over-cap drops oldest entries
   (exercise via `write_weekly_mix` with daily cap).
3. Shared dedupe: a track suggested by the weekly path is filtered from daily.
4. `_seconds_until_next_run("daily", ...)` math for the daily hour.
5. `GET /settings`: secrets masked (`""` + `set` flag), schema groups present.
6. `POST /settings`: unknown key → 400; type mismatch → 400; empty secret ignored;
   valid nested path deep-merges and persists; atomic write leaves valid JSON.

## Out of scope

- Auth for the web server.
- Mix profiles refactor (generalized N-mixes) — `run_daily` is shaped so this
  stays a straightforward later refactor.
- Editing `sp_playlist_ids` semantics beyond raw list editing.
