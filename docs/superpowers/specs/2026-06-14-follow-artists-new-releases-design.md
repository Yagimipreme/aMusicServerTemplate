# Follow Artists → NEW RELEASES — Design Spec

**Date:** 2026-06-14
**Status:** Approved design, ready for implementation plan
**Branch:** bare_bones

## Goal

Let the user **follow** specific artists, be **informed** when those artists put out new
music, **auto-download** the new tracks, and have them collected into a single
**"NEW RELEASES"** playlist.

This is distinct from the existing discovery engine, which does *similarity-based
exploration* ("artists like the ones you play"). Following is *targeted new-release
detection* for an explicit list of artists.

## Key decisions (from brainstorming)

| Decision | Choice |
|---|---|
| Release detection source | **ListenBrainz fresh-releases** (global daily feed, no API key) filtered by followed artist, keyed on **MusicBrainz Artist ID (MBID)** |
| Artist identity | MBID, not name — resolved once at follow-time via MusicBrainz |
| Follow-list management | Search by name → MusicBrainz resolution with disambiguation → add |
| Download scope | Singles/EPs in full; full albums → **one representative track** |
| Notifications | In-app feed + unseen badge **and** optional external push (webhook / ntfy) |
| Follow-time behavior | **Configurable backfill window** (default ~30 days), then watch forward |
| Architecture | **Approach C** — dedicated `follow/` detection module, reuse existing acquisition, playlist writer, scheduler |
| Follow-list storage | **Separate `follows.json` file** (not `config.json`) |

## Architecture overview

Two layers:

- **Detection layer (new, `follow/` package):** ListenBrainz + MusicBrainz tell us *what*
  is newly released by followed artists.
- **Acquisition layer (existing):** the SC/YT + yt-dlp pipeline gets the *audio*;
  ListenBrainz/MusicBrainz provide metadata only.

## 1. Data model & persistence

### `follows.json` (new, separate file)

Holds the followed-artist list only:

```json
{
  "artists": [
    {
      "mbid": "10adbe5b-6c3c-477c-9f9c-83b03b57d0a4",
      "name": "Massive Attack",
      "disambiguation": "Bristol trip-hop",
      "followed_at": "2026-06-14T00:00:00Z"
    }
  ]
}
```

Atomic write (`.tmp` + `os.replace`) under a dedicated lock, mirroring existing config
write discipline. Created with `{"artists": []}` if absent.

### `config.json` — new `follow` settings block

Settings (not the artist list) live in `config.json` alongside `discover.*`:

```json
"follow": {
  "enabled": true,
  "run_hour": 4,
  "lookback_days": 7,
  "default_backfill_days": 30,
  "playlist_name": "NEW RELEASES",
  "playlist_cap": 100,
  "notify": { "webhook_url": "", "ntfy_topic": "" }
}
```

Injected with defaults on config load if absent (mirrors profile auto-generation).
`config.example.json` updated.

### `follow_state.json` (new, sibling of `discover_state.json`, gitignored)

```json
{
  "acquired_release_groups": { "<rg_mbid>": "<iso_ts>" },
  "backfilled_mbids": [ "<artist_mbid>" ],
  "pending": [ { "rg_mbid": "...", "artist": "...", "title": "...", "attempts": 1 } ],
  "feed": [
    { "artist": "...", "title": "...", "release_name": "...", "release_date": "...",
      "primary_type": "Single", "status": "acquired", "ts": "<iso_ts>" }
  ],
  "unseen_count": 0,
  "last_run": "<iso_ts>",
  "next_run": "<iso_ts>"
}
```

- `acquired_release_groups` **never expires** — idempotency guard so a release is never
  re-downloaded. This is why `DiscoverState`'s 90-day TTL is deliberately **not** reused.
- `backfilled_mbids` marks artists whose one-time backfill has already run, so detect
  never re-backfills the same artist.
- `pending` carries a retry counter for releases detected but not yet acquired.
- `feed` is capped (e.g. last 200 events).

## 2. New `follow/` package

Each module single-purpose and independently testable:

- **`follow/store.py`** — load/save `follows.json` under a lock. `list_follows()`,
  `add_follow(mbid, name, disambiguation)`, `remove_follow(mbid)`.
- **`follow/musicbrainz.py`** — MusicBrainz client, **1 req/s** token bucket + descriptive
  `User-Agent` (app name + contact). JSON, no API key.
  - `search_artist(name, limit)` → `[{mbid, name, disambiguation, score}]` (add/disambiguation UI)
  - `get_release_groups(mbid, limit)` → `[{rg_mbid, title, first_release_date, primary_type}]` (backfill)
  - `get_release_tracks(rg_mbid)` → `[track_title, ...]` (tracklist expansion)
  - Typed errors (`MBNotFound`, `MBRateLimited`, `MBTimeout`).
- **`follow/listenbrainz.py`** — `fresh_releases(pivot_date, days, past=True)` →
  `[{artist_mbids, release_date, release_group_mbid, release_name, primary_type}]`.
- **`follow/detect.py`** — core. Inputs: follow list, `follow_state`, settings. Produces the
  set of new release-groups to acquire this run and maps each to `(artist, title)` targets:
  1. ListenBrainz feed filtered to followed MBIDs within `lookback_days`.
  2. Per-artist MusicBrainz backfill within `default_backfill_days`, **only for followed
     MBIDs not in `backfilled_mbids`**; each such MBID is added to `backfilled_mbids`
     after its backfill so it runs exactly once.
  3. Dedupe against `acquired_release_groups`.
  4. Map each release-group to targets via scope rule (below).
- **`follow/notify.py`** — append a `feed` entry, bump `unseen_count`, and if configured
  POST a summary to `webhook_url` (JSON) and/or `ntfy_topic` (`https://ntfy.sh/<topic>`).
- **`follow/runner.py`** — orchestrates one run: `detect → resolve → download → write
  playlist → notify → save state`. Called by the scheduler and the manual-run route.
  Returns a status dict (`{acquired, unavailable, feed_added}`).

### Download-scope rule (singles full, album = 1 track)

For each new release-group, `get_release_tracks(rg_mbid)`:
- `primary_type` in {Single, EP} → **all** tracks become targets.
- `primary_type` == Album (or other) → **one representative track**: the track whose
  title matches the release-group title (case-insensitive), else the **first** track.

## 3. Reused infrastructure (Approach C)

- **Acquisition:** `(artist, title)` targets flow through the existing
  `discover/resolve.py::resolve_tracks(search_fn, …)` (SC/YT search) and the existing
  yt-dlp / SoundCloud download path. No changes to the download mechanics.
- **Playlist:** existing `discover/assemble.py::write_weekly_mix(song_dir, mp3_paths,
  "NEW RELEASES", cap)` — sliding-window M3U; Navidrome `startScan` trigger unchanged.
- **Scheduler:** add a daily follow job inside the existing `_mix_scheduler_loop`
  (next_run from `follow.run_hour`; reuse `_mix_wake`). No second thread.

## 4. Data flow (one run)

```
followed MBIDs ─┐
                ├─► detect ─► (artist,title) targets ─► resolve_tracks ─► download
ListenBrainz ───┘                                                            │
fresh feed (lookback)                                                        ▼
+ MB backfill (new follows)                          write "NEW RELEASES" M3U → Navidrome scan
                                                                             │
                                          notify (feed entry + unseen badge + webhook/ntfy)
                                                                             │
                                              save follow_state (acquired_release_groups, …)
```

## 5. Flask API (in `sWebExt/py_server/server.py`)

| Route | Method | Purpose |
|---|---|---|
| `/follow/search?q=` | GET | MusicBrainz artist search → candidates w/ disambiguation |
| `/follow` | GET | List followed artists + state summary (last/next run) |
| `/follow` | POST | Add follow (mbid, name, disambiguation); kicks a background backfill for that artist |
| `/follow/<mbid>` | DELETE | Unfollow (keeps already-downloaded tracks + playlist) |
| `/follow/run` | POST | Run detection now |
| `/follow/feed` | GET | Recent release events |
| `/follow/feed/seen` | POST | Mark feed seen → reset unseen badge |
| `/follow/settings` | POST | Update `follow` config block |

Adding a follow appends to `follows.json` and triggers a detection run in a background
thread (so the feed feels alive immediately) rather than blocking the request. Because the
new MBID is not yet in `backfilled_mbids`, that run performs its one-time backfill.

## 6. Web UI (SIGNAL)

New 5th screen, **Follows**, added to the SPA hash router in `web/static/app.js`, using the
existing `createElement` / `textContent` discipline (no `innerHTML` with data):

- Search box → results list with disambiguation text → **Follow** button.
- Followed-artists list with **unfollow**.
- **NEW RELEASES feed** (recent drops; `acquired` vs `unavailable` status).
- Unseen-count **badge** in the nav, cleared via `/follow/feed/seen`.
- Settings inputs: run hour, lookback days, backfill days, playlist cap, webhook URL,
  ntfy topic.

## 7. Error handling

- MB/LB network or rate-limit errors → log, skip the run gracefully, never corrupt
  `follow_state.json`.
- MusicBrainz: 1 req/s token bucket (mirrors `lastfm/client.py`) + descriptive
  `User-Agent` with contact info, per MusicBrainz policy.
- Disambiguation resolved at add-time in the search UI; the chosen MBID is stored, so
  detection is name-independent thereafter.
- Acquisition miss / no source found → release recorded as **unavailable** in the feed and
  kept in `pending` with an `attempts` counter; retried up to **3 runs**, then dropped from
  `pending` (release-group is *not* added to `acquired_release_groups`, so a later genuine
  re-detect could still try, but it won't loop forever). We rely on existing
  `resolve_tracks` match quality rather than re-solving fuzzy matching here.
- Idempotency: `acquired_release_groups` keyed by release-group MBID guarantees no
  re-downloads across runs and across restarts.

## 8. Testing

Unit tests under `tests/follow/`, mirroring `tests/discover/` style, with fixture JSON for
MusicBrainz / ListenBrainz responses:

- `musicbrainz` parsing (search, release-groups, tracks) + rate-limit behavior.
- `listenbrainz` parsing + window/`days` filtering.
- `detect`: feed-filter by followed MBID, dedupe against acquired set, backfill window,
  singles-vs-album scope (representative-track selection).
- `store`: add / remove / load / atomic save.
- `notify`: webhook + ntfy payloads, feed append, unseen-count increment.
- `runner`: end-to-end with stubbed resolve/download/assemble — asserts M3U append, state
  update, feed/notify, and **idempotent re-run** (second run with same input downloads
  nothing).

## 9. Config & migration

- On config load, inject the `follow` settings block with defaults if absent.
- Create `follows.json` (`{"artists": []}`) and `follow_state.json` on first use.
- Update `config.example.json` with the `follow` block.
- Add `follows.json` and `follow_state.json` to `.gitignore` (follow_state already
  matches the `discover_state.json` pattern; verify).

## Out of scope (YAGNI)

- Spotify OAuth / Spotify new-releases (ListenBrainz covers detection without auth).
- Per-artist custom backfill overrides (single global default for now).
- Re-download retry of permanently unavailable releases beyond 3 runs.
- Notification of *failed* downloads via external push (failures appear in the in-app feed
  only; external push fires for successful acquisitions).
