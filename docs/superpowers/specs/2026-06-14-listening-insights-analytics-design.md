# Listening Insights & Analytics — Design

> Date: 2026-06-14 · Branch: `bare_bones` · Status: approved, pre-implementation

Adds an **INSIGHTS** capability to aMusicServer: detailed analysis of the user's
listening behavior — genre-by-time-of-day, temporal patterns, BPM/key/mood, and
library cross-reference — surfaced as a new web-UI screen and a set of read-only
JSON endpoints, backed by a local SQLite store of Last.fm scrobble history.

One spec, built in five sequenced phases (see end). Phases 1–2 deliver value with
data we already have; phase 3 (audio features) is data-source-constrained and
isolated so it cannot block the rest.

---

## Motivation & data-source findings

The user wants to analyze listening behavior in detail: which genre at which time,
BPM, mood, "and everything we can think of."

Researched 2026-06-14:

- **Last.fm `user.getRecentTracks`** is the source of truth for *timestamped*
  listening history (artist, track, album, recording MBID, unix-UTC timestamp).
  Already reachable via `lastfm/client.py`'s generic `call(method, **params)`.
  Username (`lastfm_username`) and API key are already configured.
- **AcousticBrainz** — the project ended in 2022 (no new submissions) **but the
  read-only API is live** (verified: `GET /api/v1/<recording-mbid>/high-level`
  returns a mood vector + danceability + genre; `/low-level` returns BPM + key).
  ~29M recordings covered. Coverage is **partial** — popular/older tracks hit,
  newer/obscure miss. Free, CC0, no local compute.
- **Spotify audio-features** — deprecated 2024-11-27; only apps approved before
  that date still work. **Not usable** for a new integration.
- **Local analysis** — `librosa` (pip, pure-Python, needs only ffmpeg which we
  already ship) gives BPM (beat-track), key (chroma), and spectral features that
  map to a coarse mood heuristic. Chosen as the fallback for AcousticBrainz misses.

**Decision (user-approved):** AcousticBrainz by MBID as primary feature source;
opt-in local `librosa` analysis as the fallback for misses.

---

## Architecture

```
Last.fm user.getRecentTracks ──► insights/scrobbles.py ──► SQLite (scrobbles)
                                                              │
lastfm/tags.py ──► insights/genres.py ──► SQLite (artist_tags)
                                                              │
MusicBrainz + AcousticBrainz / librosa ──► insights/features.py ──► SQLite (track_features)
                                                              │
                                          insights/analytics.py (pure SQL aggregations)
                                                              │
                                   server.py  GET /insights/*  (JSON)
                                                              │
                              web/static/app.js  INSIGHTS screen + charts.js (SVG)
```

New package `insights/` mirrors the existing `discover/` and `library/` packages.
Background workers reuse the established start-thread + poll-status pattern from
`library/enrich.py` / `library/repair.py`.

---

## 1. Data layer — `insights/db.py`

SQLite (`stdlib sqlite3`, no new dependency). DB path from `insights.db_path`
config key, defaulting beside `discover_state.json`; gitignored. Schema created/
migrated idempotently on first connect. WAL mode; one connection per worker thread
(sqlite3 is not safe to share a connection across threads).

```sql
CREATE TABLE scrobbles (
  ts             INTEGER NOT NULL,   -- unix UTC
  artist         TEXT    NOT NULL,
  track          TEXT    NOT NULL,
  album          TEXT,
  artist_mbid    TEXT,
  recording_mbid TEXT,
  PRIMARY KEY (ts, artist, track)
);
CREATE INDEX idx_scrobbles_ts     ON scrobbles(ts);
CREATE INDEX idx_scrobbles_artist ON scrobbles(artist);

CREATE TABLE artist_tags (
  artist        TEXT PRIMARY KEY,
  tags_json     TEXT,               -- [{name, weight}, ...]
  primary_genre TEXT,
  fetched_at    INTEGER
);

CREATE TABLE track_features (
  artist          TEXT NOT NULL,
  track           TEXT NOT NULL,
  recording_mbid  TEXT,
  bpm             REAL,
  key             TEXT,
  scale           TEXT,             -- major/minor
  mood            TEXT,             -- derived primary mood label
  mood_scores_json TEXT,           -- full mood probability vector
  danceability    REAL,
  source          TEXT,             -- 'acousticbrainz' | 'librosa'
  analyzed_at     INTEGER,
  PRIMARY KEY (artist, track)
);

CREATE TABLE sync_state (
  key   TEXT PRIMARY KEY,           -- last_ts, last_full_sync, coverage_*
  value TEXT
);
```

---

## 2. Ingestion — `insights/scrobbles.py`

`sync_scrobbles(client, username, db, *, page_limit=200)`:

- Pages `user.getRecentTracks` newest→oldest, `from=last_ts` (0 on first run).
- Skips the "now playing" row (no timestamp / `@attr.nowplaying`).
- Inserts with `INSERT OR IGNORE` on the PK (dedup; safe to re-run / resume).
- Updates `sync_state.last_ts` to the newest ingested timestamp on success.
- Resumable: a crash mid-backfill just resumes from the oldest stored gap on the
  next run because we page until we reach already-stored rows or the API end.

Runs as a background worker thread, triggered:
- on server start if `insights.sync_on_start`,
- on a timer every `insights.sync_interval_hours`,
- on demand via `POST /insights/sync`.

**Timezone:** Last.fm timestamps are UTC. "Which genre at which time" is only
meaningful in the user's local time, so all hour-of-day / day-of-week aggregation
shifts ts by a tz offset supplied per-request by the browser (`?tz=` minutes), with
an optional `insights.timezone` config override; falls back to UTC.

---

## 3. Genre — `insights/genres.py`

`ensure_artist_tags(client, db, artists)` fills the tag cache for any artist not
already present, reusing `lastfm/tags.py` (`get_artist_tags`, `build_genre_profile`).
Each artist is tagged once. `primary_genre` = highest-weighted tag after filtering
through a small genre allowlist/denylist (drops non-genre tags such as "seen live",
"favorites", "my music"). Runs opportunistically during/after scrobble sync for
newly seen artists.

---

## 4. Audio features — `insights/features.py`

`ensure_track_features(db, deps, tracks, *, enable_local)` per track not yet stored:

1. Resolve recording MBID — prefer the `recording_mbid` already stored on the
   scrobble; else MusicBrainz recording search (same client/UA pattern as
   `library/repair.py`).
2. AcousticBrainz `GET /api/v1/<mbid>/high-level` (mood vector, danceability,
   genre) + `/low-level` (`rhythm.bpm`, `tonal.key_key`/`key_scale`).
   `source='acousticbrainz'`.
3. On 404 / no MBID and `enable_local` is true: queue a `librosa` batch over the
   local MP3 (resolve path from the library scanner). BPM via `beat_track`, key via
   chroma profile correlation, mood via a documented heuristic on tempo + spectral
   energy + mode → an energy/valence quadrant (calm / melancholic / happy /
   energetic). `source='librosa'`; these moods are flagged heuristic in the API.
4. On miss with local disabled: leave features null; counts toward the "unknown"
   coverage figure shown in the UI.

**Mood label:** from AcousticBrainz, argmax over the positive-affect classifiers
(`mood_happy`, `mood_party`, `mood_relaxed`, `mood_aggressive`, `mood_sad`,
`mood_acoustic`, `mood_electronic`); the full vector is retained in
`mood_scores_json` so the UI can show nuance. Audio-feature analysis is itself a
background worker (slow, network/CPU bound) with its own status endpoint state.

---

## 5. Analytics — `insights/analytics.py`

Pure functions over the DB, each returning JSON-able dicts, each accepting
`period` (`all` | `year` | `90d` | `30d` | `7d`) and `tz` (offset minutes):

**Temporal**
- `listening_clock` — plays per hour-of-day (local)
- `hour_day_heatmap` — 7×24 matrix (day-of-week × hour)
- `plays_over_time` — daily/weekly/monthly counts across the period
- `weekday_weekend` — split + per-hour comparison

**Genre**
- `top_genres` — ranked with share %
- `genre_by_hour` — stacked genre composition per hour-of-day  *(explicit ask)*
- `genre_evolution` — top genres' share over time buckets
- `genre_diversity` — distinct-genre / entropy index over the period

**Entities**
- `top_entities(kind)` — artists / tracks / albums, per period
- `discovery_rate` — first-seen artists (and tracks) per time bucket
- `new_vs_repeat` — share of plays that are first-listens vs repeats

**Sound**
- `bpm_distribution` — histogram
- `bpm_curve` — average BPM (energy proxy) by hour-of-day
- `key_distribution` — counts per key, arranged for a Camelot wheel
- `mood_distribution` — counts per mood label
- `mood_by_time` — mood composition by hour-of-day

**Library cross-ref**
- `library_overlap` — % of scrobbles whose track exists in the local library
  (matched via `library/scanner.py`)
- `missing_favorites` — most-played tracks **not** in the library, shaped to feed
  the existing discovery/import engine (one-click acquire)

**Overview** — summary scalars: total scrobbles, unique artists/tracks, estimated
listening time, date range, top genre, average BPM, feature-coverage %.

---

## 6. API — `server.py`

Grouped to minimize client round-trips. All read endpoints accept `?period=&tz=`.

| Route | Method | Purpose |
|---|---|---|
| `/insights/sync` | POST | Start scrobble (+genre, +feature) sync worker |
| `/insights/sync/status` | GET | Worker progress: phase, counts, coverage, errors |
| `/insights/overview` | GET | Summary scalars |
| `/insights/temporal` | GET | clock + heatmap + over-time + weekday/weekend |
| `/insights/genres` | GET | top genres + genre-by-hour + evolution + diversity |
| `/insights/features` | GET | bpm distribution + curve + key + mood + mood-by-time |
| `/insights/discovery` | GET | discovery rate + new-vs-repeat + library overlap + missing favorites |

`missing_favorites` results reuse the existing `/import/tracks` / discovery seed
plumbing for one-click acquisition.

---

## 7. UI — INSIGHTS screen (`web/static/app.js`, `web/static/charts.js`)

Fifth screen, added to the bottom nav. One scrollable screen with labelled
sections (single scroll, not sub-tabbed — fewer interactions, all data visible;
revisit if it grows): **Overview** (summary cards) → **Time** → **Genres** →
**Sound** → **Discovery**.

- A period selector (all / year / 90d / 30d / 7d) at the top; browser tz sent on
  every request.
- "Sync now" button with progress (reuses the enrich/repair progress-bar pattern).
- Empty state when the DB is unpopulated ("Run sync to populate your insights").
- Coverage note in the Sound section (e.g. "BPM/mood known for 72% of plays").

**Charts:** new `web/static/charts.js` exposing `createElement`-built **inline SVG**
builders — `barChart`, `heatmap`, `lineChart`, `donut`, `camelotWheel`,
`stackedBars`. **No `innerHTML` with data** (honors the project security
invariant); zero new frontend dependencies. Colors from existing SIGNAL design
tokens in `app.css`.

---

## 8. Config keys (Setup screen, schema-driven)

| Key | Default | Meaning |
|---|---|---|
| `insights.db_path` | `<beside discover_state>/insights.db` | SQLite location |
| `insights.enable_local_analysis` | `false` | librosa fallback on/off |
| `insights.sync_on_start` | `true` | sync scrobbles at server start |
| `insights.sync_interval_hours` | `24` | background sync cadence |
| `insights.timezone` | `""` (use browser/UTC) | optional tz override |

---

## 9. Error handling

- Last.fm rate limit — already bucketed in `lastfm/client.py`; sync is resumable
  from `last_ts`, partial progress preserved.
- AcousticBrainz 404 / network error — mark feature unavailable, try librosa if
  enabled, else count as unknown coverage.
- `librosa` not installed though enabled — degrade gracefully (log once, skip
  local analysis, surface coverage gap); never crash the worker.
- Empty DB / no scrobbles yet — endpoints return empty payloads; UI shows the
  empty state rather than broken charts.
- Missing/blank `lastfm_username` — sync endpoint returns a clear error; UI
  prompts to configure it in Setup.

---

## 10. Testing

`tests/insights/`:
- `test_scrobbles.py` — pagination, now-playing skip, dedup/`INSERT OR IGNORE`,
  resume from `last_ts`, UTC handling (mocked client).
- `test_genres.py` — tag caching (no refetch), primary-genre selection, allowlist
  filtering.
- `test_features.py` — AcousticBrainz high/low-level parsing, librosa fallback
  gating (enabled/disabled), mood-label mapping (both sources mocked).
- `test_analytics.py` — fixture scrobble set → assert each aggregation, with
  explicit tz-conversion correctness (e.g. a 23:30 UTC play lands in the right
  local hour bucket).

`tests/server/test_routes.py` — add `/insights/*` route tests (sync start/status,
each read endpoint's JSON shape, `period`/`tz` parameter handling, empty-DB path).

No frontend tests (project convention — vanilla JS, no FE harness).

---

## Build phases (one spec, sequenced)

1. **Store + ingestion** — `insights/db.py`, `insights/scrobbles.py`, sync worker,
   `POST /insights/sync` + status. Backfill works end-to-end.
2. **Genre + temporal/genre analytics** — `insights/genres.py`,
   `insights/analytics.py` (temporal + genre + entities), `/insights/overview`,
   `/insights/temporal`, `/insights/genres`.
3. **Audio features** — `insights/features.py` (AcousticBrainz + librosa),
   feature analytics, `/insights/features`.
4. **UI** — INSIGHTS screen + `web/static/charts.js`, period selector, sync
   progress, empty state.
5. **Library cross-ref + discovery integration** — `library_overlap`,
   `missing_favorites`, `/insights/discovery`, one-click acquire wiring.

---

## Out of scope (v1)

- Multi-user / multiple Last.fm accounts (single configured user only).
- ListenBrainz as an alternate history source (Last.fm only for now).
- Essentia pretrained mood models (librosa heuristic is the only local path).
- Predictive/recommendation modeling beyond the existing discovery seed feed.
- Frontend test harness.
