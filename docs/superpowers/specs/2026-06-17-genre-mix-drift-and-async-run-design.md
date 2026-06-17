# Genre Mix Drift + Async Run — Design

**Date:** 2026-06-17
**Branch:** `fix/genre-mix-drift-async-run`
**Status:** Approved, pending implementation plan

## Problem

A user created a genre mix for "phonk" (`mode: genre`, genres `["Phonk","phonk"]`,
`new_ratio: 1`, `count: 30`). Three distinct problems were observed and confirmed in
code + `logs/server.log`:

1. **UI errored immediately, but songs still downloaded.** The run route is fully
   synchronous (`server.py:1626 mixes_run` → `_run_profile_once`, blocking for the whole
   ~9-minute download). An earlier run already held the `_discover_running` lock
   (`server.py:375`), so re-clicks returned `409` instantly (`server.py:1633`), surfaced by
   the UI (`app.js:377-381`) as an error. The original run completed and downloaded 30
   tracks. Log timeline:
   ```
   16:17:16  POST /mixes/PHONK/run → 409   (immediate "error")
   16:17:33  POST /mixes/PHONK/run → 409
   16:38:59  POST /mixes/PHONK/run → 200   acquired 30   (the run that downloaded)
   ```

2. **Downloaded tracks were rap, not phonk.** The genre pipeline
   (`discover/engine.py:237-255`) uses the genre **only** to pick seed artists, then
   discards it. There is no genre reference anywhere in the
   expand → enrich → resolve → acquire path. Two amplifiers turn "phonk" into "rap":
   - `expand_similar` (`engine.py:249`) expands each phonk seed into 20 *similar* artists;
     phonk's nearest neighbors on Last.fm are Memphis rap / trap / hip-hop.
   - `min_artist_listeners = 5000` (`engine.py:253`, default in `expand.py:134`) filters out
     underground phonk artists and keeps surviving mainstream rap.
   There is no relevance gate verifying a downloaded track is actually phonk.

3. **Rap and phonk ended up in the same playlist.** `write_weekly_mix` (`engine.py:297`)
   **appends** to the named `PHONK MIX.m3u` (cap 100) every run, so tracks from a rap-heavy
   run and a later phonk run accumulate together. A consequence of #2, not a separate bug.

## Decisions

- **Issue 2:** Both seed-direct *and* a genre-relevance gate.
- **Issue 1:** Background thread + live status polling.
- **Issue 3:** Rebuild `PHONK MIX` from scratch after the fix (operational, not code).

## Design

### 1. Genre mode rework

Files: `discover/engine.py`, `discover/seeds.py`, `lastfm/tags.py` (reuse existing tag fetch).

Give `mode == "genre"` its own pipeline in `run_profile`, bypassing both drift sources:

- **Seed-direct.** Fetch genre top artists with margin: `genre_seed_artists(lastfm_client,
  genres, limit_per_genre=60)`. Resolve tracks directly from these artists. **Skip**
  `expand_similar` and **skip** `enrich_artist_info` (the `min_artist_listeners` floor).
- **Genre gate.** New helper `filter_artists_by_genre(lastfm_client, artists, genres)` in
  `discover/seeds.py`, reusing the artist-tag fetch in `lastfm/tags.py`. For each artist,
  fetch Last.fm top tags; keep the artist iff any target genre token appears in any tag.
  - Normalization: casefold + strip both genres and tags. Match by substring so
    `"drift phonk"` and `"phonk, rap"` pass while `"rap, hip-hop"` fails.
  - Dedupe genres case-insensitively (`["Phonk","phonk"]` → one).
  - On Last.fm error for an artist: drop that artist (fail-closed) but never crash the run.
- **Margin.** The larger seed pool (≈40–60 artists) plus `per_artist=1` keeps enough
  candidates to reach `count=30` after gate drops and download failures.
- Other modes (history / manual / playlist) are unchanged.

Boundary: the gate is a pure-ish filter — input list of `{name,...}` artists + genres,
output filtered list. Testable with a mocked `lastfm_client`.

### 2. Async run + live status

Files: `sWebExt/py_server/server.py`, `web/static/app.js`.

- **Status store.** Module-level dict guarded by a lock, keyed by mix id:
  `{running: bool, result: <last run result dict | None>}`. Single global
  `_discover_running` lock still guarantees one run at a time.
- **`POST /mixes/<id>/run`.** If a run is already in flight, return `409`
  `{"status":"running"}` (UI treats this as "running", not an error). Otherwise spawn a
  background `threading.Thread` running the existing `_run_profile_once(profile)`, mark the
  mix running, and return `202 {"status":"started","mix_id":id}` immediately. The thread
  stores the result in the status store and clears `running` on completion (success or
  error).
- **`GET /mixes/<id>/status`.** Returns `{"running": bool, "result": <last result|null>}`.
- **Frontend** (`app.js:365` run button). On click: POST; on `202`/`409-running`, show
  "Running…" and poll `GET /mixes/<id>/status` every ~3s; when `running` goes false, render
  `Done. acquired: N` (or the error from the result). Stop polling on completion or screen
  change.

Scope: the follow (`app.js:1454`) and dedup (`app.js:609`) run buttons share this
synchronous shape but are **out of scope**.

### 3. Playlist rebuild (operational)

Not code. After deploy + server restart: clear `PHONK MIX.m3u` and trigger one fresh run so
it repopulates with correct phonk. Existing rap mp3s remain on disk.

## Testing

- **Genre gate** (`tests/`): unit tests — passes phonk variants (`"drift phonk"`,
  `"phonk, rap"`), drops rap-only (`"rap, hip-hop"`), case/whitespace normalization, genre
  dedupe, per-artist Last.fm error → artist dropped without crash.
- **Genre-mode `run_profile`**: with mocked deps, assert `expand_similar` /
  `enrich_artist_info` are **not** called for genre mode and that the gate filters the
  candidate artists before resolve/acquire.
- **Async run**: status store transitions; `POST` returns `202` and spawns; in-flight `POST`
  returns `409 running`; `GET /status` reflects running → result. Join the thread in tests
  for determinism.

## Out of scope / YAGNI

- Async-ifying follow/dedup buttons.
- Track-level genre gating (artist-level is sufficient for phonk).
- Deleting miscategorized rap mp3s from disk (user chose to keep them).
