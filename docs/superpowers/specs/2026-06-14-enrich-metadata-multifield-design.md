# Enrich Metadata — Status Contract Fix + Multi-Field Broadening

**Date:** 2026-06-14
**Status:** Approved design, pending implementation plan
**Branch:** feat/listening-insights

## Problem

The "Enrich metadata" web-UI card currently backfills only the ID3 **genre**
field, from Last.fm, for MP3 files. Two classes of problem:

1. **The UI status contract is broken.** The backend only publishes a terminal
   result and uses field names the frontend does not read, so:
   - The frontend never sees a `running`/`started` state (the backend never
     publishes one — `POST /library/enrich` returns `{"status":"started"}` as an
     HTTP body but does not store it). The first status poll reads the prior
     terminal state (`idle` or a previous `ok`), so the UI immediately stops
     polling, hides the bar, and re-enables the button. Live progress never
     shows.
   - The progress bar wants `files_total`/`files_done`, which the backend never
     produces (`run()` is fully synchronous with no per-file reporting).
   - The result line wants `s.enriched`, but the backend returns `tagged`, so
     `last run: N enriched` never renders — it falls through to printing the raw
     status string `ok`.

2. **Enrichment is narrow.** Only genre, only Last.fm, only when missing. No
   year, album, album-artist, MusicBrainz IDs, or cover art. The
   `enrich.enabled` config flag is defined but never checked.

## Goals

- **Part A:** Make the UI status contract correct — live progress bar
  (`running` state with `files_done`/`files_total`) and a correct result count
  (`enriched`).
- **Part B:** Broaden enrichment to fill, in addition to genre:
  **year/release date, album, album-artist, MusicBrainz IDs, and cover art** —
  primarily from MusicBrainz (+ Cover Art Archive for art), with per-field
  config toggles and a global match-score safety gate.

## Non-Goals (out of scope)

- Cancellation of an in-progress run.
- Triggering a Navidrome rescan after writing tags (possible follow-up).
- Non-MP3 formats (FLAC/M4A/etc.) — still MP3-only via the existing scanner.
- A caching layer for MusicBrainz/Last.fm lookups (YAGNI).
- Changing the "Repair library" feature (it keeps its own ad-hoc MB recording
  search; we do not refactor it here to stay focused).

## Architecture (Approach 1: one combined per-file pass)

Refactor the single-pass `library/enrich.py:run()` so that for each scanned file
it applies a set of field-fillers: genre from Last.fm (as today) and
year/album/album-artist/MBIDs/cover-art from a **single** MusicBrainz recording
resolution. Reuse the existing rate-limited `MusicBrainzClient`. The Part A
progress mechanism is the shared plumbing both parts rely on.

### Part A — Status contract

**Backend — `sWebExt/py_server/server.py`:**

- `POST /library/enrich` (`library_enrich`): if `_enrich_running.locked()`,
  return `{"status":"skipped","reason":"already running"}` without clobbering
  state. Otherwise set
  `_enrich_last_result = {"status":"running","files_done":0,"files_total":0}`
  **synchronously** before spawning the daemon thread, then return
  `{"status":"running"}`. This guarantees the first status poll cannot read a
  stale terminal state.
- `_run_enrich_once`: build a `progress(done, total)` callback that writes
  `_enrich_last_result = {"status":"running","files_done":done,"files_total":total}`
  and pass it into `run()`. `total` becomes known right after the scan and is
  reported once as `(0, total)`, then per file as `(i, total)`.
- Terminal result uses UI-aligned field names:
  `{"status":"ok","enriched":N,"files_total":T,"files_done":T,
  "per_field":{…},"skipped":N,"errors":N}` where `enriched` is the number of
  files that received at least one field write.
- Honor `enrich.enabled`: if false, return
  `{"status":"disabled","reason":"enrich disabled in config"}` (see Config).

**Frontend — `web/static/app.js`:** No markup changes required. The existing
`running` branch already reads `files_total`/`files_done`, and the result branch
already reads `s.enriched`; both are now satisfied. The per-file breakdown in the
status line is intentionally **not** added (kept minimal).

### Part B — Multi-field enrichment

**`follow/musicbrainz.py` — add `search_recording`:**

```
search_recording(self, artist: str, title: str, limit: int = 5) -> list[dict]
```

Queries `recording?query=artist:"<artist>" AND recording:"<title>"` (values
escaped/quoted). Returns normalized dicts:

```
{
  "mbid": str, "score": int, "title": str,
  "artist_mbid": str, "artist_name": str,
  "releases": [
    {"mbid": str, "title": str, "date": str,
     "rg_mbid": str, "primary_type": str, "status": str}
  ]
}
```

Recording search results already embed `artist-credit` and `releases`, so this
is **one** HTTP call per track. Same rate limiter and User-Agent as the existing
client methods. On network error, raise `MBTimeout` (consistent with existing
methods); callers treat failures as "no match".

**`library/mbmeta.py` (new) — resolve one track to canonical metadata:**

```
resolve(mb_client, artist, title, min_score) -> dict | None
```

- Calls `mb_client.search_recording(artist, title)`; takes the top recording.
- Returns `None` if no results, or if top recording `score < min_score`.
- Picks the canonical release via `_pick_release(releases)`:
  1. Prefer releases with `primary_type == "Album"` and `status == "Official"`.
  2. Among those, the earliest by `date`.
  3. Fall back to the earliest-dated release of any type if no official album
     exists; `None` if there are no releases.
- Returns:
  ```
  {
    "score": int,
    "recording_mbid": str,
    "artist_mbid": str,
    "album": str,          # chosen release title ("" if no release)
    "album_artist": str,   # recording artist_name
    "year": str,           # 4-digit year parsed from chosen release date ("" if none)
    "release_mbid": str,   # chosen release mbid ("" if none)
    "rg_mbid": str,        # chosen release-group mbid ("" if none)
  }
  ```
- On `MBError`/`MBTimeout`, returns `None` (logged at warning).

**`library/coverart.py` (new) — fetch front cover:**

```
fetch_front(release_mbid, size="500") -> tuple[bytes, str] | None
```

- `GET https://coverartarchive.org/release/{release_mbid}/front-{size}` with the
  same polite User-Agent and a 10s timeout; follows redirects to the image.
- Returns `(image_bytes, mime_type)` on success, `None` on 404/error/timeout.
- `size` is the CAA thumbnail suffix (`"250"`, `"500"`, `"1200"`); `"500"`
  default keeps embedded art reasonably small.

**`library/enrich.py` — refactored `run()`:**

```
run(song_dir, lastfm_client=None, mb_client=None,
    fields=None, min_musicbrainz_score=90, cover_art_size="500",
    limit=None, progress=None) -> dict
```

- `fields`: per-field config dict (see Config). If `None`, defaults to all six
  fields `enabled` + `only_missing`.
- Scans `song_dir` (existing `library.scanner.scan`), applies `limit`, reports
  `progress(0, total)`.
- For each record, load the MP3 once (eyed3), then:
  1. Compute **needed** fields = `enabled` AND
     (`only_missing` is false OR the file's current value is empty).
     - Empty checks: genre via `tag.genre`; year via `tag.getBestDate()`;
       album via `tag.album`; album_artist via `tag.album_artist`; mbids via the
       relevant TXXX/UFID frames; cover_art via `tag.images` being empty.
  2. If any of {year, album, album_artist, mbids, cover_art} is needed and
     `mb_client` is set → one `mbmeta.resolve(...)` call. If `cover_art` is
     needed and a `release_mbid` was resolved → one `coverart.fetch_front(...)`.
  3. If genre is needed and `lastfm_client` is set → existing
     `get_track_tags` → `get_artist_tags` fallback + `_clean_tags`.
  4. Write each resolved+needed field via eyed3:
     - **genre** → `tag.genre` (TCON), comma-joined top tags (today's behavior).
     - **year** → `tag.recording_date = eyed3.core.Date(int(year))`.
     - **album** → `tag.album` (TALB).
     - **album_artist** → `tag.album_artist` (TPE2).
     - **mbids** → TXXX user-text frames `MusicBrainz Artist Id`,
       `MusicBrainz Album Id` (release mbid), `MusicBrainz Release Group Id`,
       plus a UFID frame (owner `http://musicbrainz.org`) for the recording mbid
       — Picard/Navidrome-compatible. Only non-empty IDs are written.
     - **cover_art** → `tag.images.set(ImageFrame.FRONT_COVER, img_bytes, mime)`.
  5. `tag.save()` once. Increment `per_field[field]` for each written field; a
     file with ≥1 write increments `enriched`. Call `progress(i, total)`.
- A field that can't be resolved (no MB match, no tags, CAA 404, network error)
  is skipped silently for that file. Load/save failures increment `errors`.
- Returns:
  ```
  {
    "processed": int,    # files examined
    "files_total": int,  # == len(records after limit)
    "enriched": int,     # files with >=1 field written
    "per_field": {"genre": int, "year": int, "album": int,
                  "album_artist": int, "mbids": int, "cover_art": int},
    "skipped": int,      # files with nothing to do / no matches
    "errors": int,
  }
  ```

### Config schema — `config.example.json`

```json
"enrich": {
  "enabled": false,
  "min_musicbrainz_score": 90,
  "cover_art_size": "500",
  "fields": {
    "genre":        { "enabled": true, "only_missing": true },
    "year":         { "enabled": true, "only_missing": true },
    "album":        { "enabled": true, "only_missing": true },
    "album_artist": { "enabled": true, "only_missing": true },
    "mbids":        { "enabled": true, "only_missing": true },
    "cover_art":    { "enabled": true, "only_missing": true }
  }
}
```

- **`enabled`** is now honored. The example ships `false`; set it `true` in a
  real config to use the feature.
- **`min_musicbrainz_score`** gates every MB-sourced field (default 90, mirrors
  the Repair card).
- **Back-compat:** in `_run_enrich_once`, if `enrich.fields` is absent but the
  legacy `enrich.only_missing_genre` exists, map it to
  `fields.genre.only_missing` and default the other five fields to
  `enabled`/`only_missing`.
- Genre still requires `lastfm_api_key`; if absent, genre is skipped but MB
  fields still run. `song_dir` remains required (else `disabled`).

## Data flow

```
UI "run" → POST /library/enrich
  → set _enrich_last_result = running{0,0}; spawn thread
  → _run_enrich_once: load config, honor enabled, build LastFMClient +
    MusicBrainzClient, build progress cb
  → enrich.run(scan → per file: mbmeta.resolve (1 MB call) + coverart.fetch_front
    (if needed) + Last.fm genre; write needed fields; progress(i,total))
  → terminal _enrich_last_result = ok{enriched, files_total, per_field, ...}
UI polls GET /library/enrich/status every 2s → running shows bar, ok shows count
```

## Performance / rate limits

Worst case per file: Last.fm 1–2 calls (1 req/s) + MusicBrainz 1 call (1 req/s)
+ 1 Cover Art Archive fetch ⇒ ~3–4 s/file. The `limit` parameter, per-field
`enabled`, and `only_missing` (cover art fetched only when missing) bound the
call volume. No caching in scope.

## Error handling

- Per-field resolution/network failures: skip that field for that file, no error
  counted (logged at debug/warning).
- File load/save failures: increment `errors`, continue.
- MusicBrainz/Last.fm client errors are caught at the `mbmeta`/tags layer and
  surface as "no match".
- `enrich.enabled == false` → `disabled`; missing `song_dir` → `disabled`.

## Testing

- `library/mbmeta.resolve`: score gating; `_pick_release` heuristic (official
  album preferred, earliest date, fallbacks); year parsing; no-release case.
- `follow/musicbrainz.search_recording`: parse a sample JSON payload into the
  normalized shape.
- `library/coverart.fetch_front`: mocked HTTP — success returns `(bytes, mime)`,
  404/error returns `None`.
- `library/enrich.run`: per-field `only_missing` gating; MB score gate; genre
  behavior preserved; `progress` callback fired with `(done, total)`; result
  stats shape (`enriched`, `files_total`, `per_field`).
- Server routes: `/library/enrich/status` returns `running` immediately after
  `POST /library/enrich`, then `ok`; result carries `enriched`/`files_total`/
  `files_done`; `enabled == false` yields `disabled`.

## Files touched

- `sWebExt/py_server/server.py` — POST handler running-state + skip guard;
  `_run_enrich_once` progress callback, config (enabled/fields/score/size),
  back-compat mapping, terminal result shape.
- `follow/musicbrainz.py` — add `search_recording`.
- `library/mbmeta.py` — new.
- `library/coverart.py` — new.
- `library/enrich.py` — refactor `run()` to multi-field + progress.
- `config.example.json` — new `enrich` schema.
- `web/static/app.js` — no change expected (verify contract only).
- Tests: `tests/library/test_enrich.py` (update), `tests/library/test_mbmeta.py`
  (new), `tests/library/test_coverart.py` (new),
  `tests/follow/test_musicbrainz.py` (extend), `tests/server/test_routes.py`
  (extend).
```