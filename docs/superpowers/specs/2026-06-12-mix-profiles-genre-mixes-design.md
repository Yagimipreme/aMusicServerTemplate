# Mix Profiles + Genre Mixes — Design

**Date:** 2026-06-12
**Status:** Approved (pending spec review)
**Depends on:** 2026-06-12-daily-mix-and-settings-ui-design.md (must land first — this
refactors the scheduler that spec touches)

## Goal

Generalize the discovery system from two hard-coded mixes (Weekly, Daily) to N
user-editable **mix profiles**, add **genre mixes** (auto-generated from the
library's top genres, fully editable afterwards), and give the webUI a Mixes tab
that serves as the mix editor.

## Data model

`config.json` gains a top-level `mixes` list. One profile:

```json
{
  "id": "techno",                    // slug, unique, immutable after create
  "name": "Techno Mix",              // playlist name (m3u + Navidrome)
  "enabled": true,
  "auto_generated": true,            // created by the bootstrapper; cleared on user edit
  "schedule": { "cadence": "weekly", "run_day": "tuesday", "run_hour": 7 },
  "count": 15,                       // tracks added per run
  "cap": 60,                         // rolling-window size (m3u cap)
  "new_ratio": 0.3,                  // 0.0 = library-only, 1.0 = pure discovery
  "seeds": {
    "mode": "genre",                 // history | genre | manual | playlist
    "genres": ["techno"],            // used by mode=genre (Last.fm tag names)
    "artists": [],                   // used by mode=manual
    "playlist": ""                   // used by mode=playlist (Navidrome playlist name)
  },
  "quality": {}                      // optional overrides: min_artist_listeners,
                                     // candidate_oversample, junk_keywords;
                                     // empty -> global discover.* values
}
```

Built-in profiles after migration:

- `weekly`: name "Weekly Mix", weekly/sunday/22, count 30, cap 100,
  new_ratio 1.0, seeds.mode "history" (+ `seed_playlist` carried into
  seeds.playlist with mode staying "history" — history mode internally keeps
  today's behavior incl. the seed_playlist preference).
- `daily`: name "Daily Mix", daily/07, count 7, cap 49, new_ratio 1.0,
  seeds.mode "history".

### Migration (back-compat)

On config load (server start or first `/mixes` access): if `mixes` is absent,
synthesize it from the legacy keys (`discover.schedule/run_day/run_hour/
weekly_count/playlist_cap/playlist_name/seed_playlist` and `discover.daily.*`)
and write it back. Legacy keys remain in place and continue to act as global
defaults for quality knobs; the scheduler reads only `mixes` from then on.
A config with both present: `mixes` wins, no re-migration.

## Engine: `run_profile(deps, cfg, profile)` in `discover/engine.py`

Replaces the bodies of `run_weekly`/`run_daily` (which become thin wrappers
mapping the built-in profiles, preserving their public signatures for tests/CLI).

1. **Split:** `new_count = round(count * new_ratio)`, `lib_count = count - new_count`.
2. **New share** (if `new_count > 0`): existing pipeline
   (seeds → `expand_similar` → `enrich_artist_info` → `resolve_tracks` →
   `filter_fresh` → `acquire`), seed source by `seeds.mode`:
   - `history` — current behavior (Last.fm top artists / seed playlist / frequent artists).
   - `genre` — for each tag in `seeds.genres`: Last.fm `tag.gettopartists`
     (new `LastFM.tag_top_artists(tag, limit)` method in `lastfm/client.py`),
     merged and deduped, then through the normal expansion/quality gates.
   - `manual` — `seeds.artists` as the seed list.
   - `playlist` — existing seed_playlist path with `seeds.playlist`.
   Readiness gate (`lastfm_is_ready`) applies only to `history` mode; genre/
   manual/playlist modes work without scrobble history.
3. **Library share** (if `lib_count > 0`): Subsonic `getSongsByGenre` for the
   profile's genres (for non-genre modes: songs by the seed artists via search),
   excluding tracks already in the profile's m3u, preferring least-recently-played
   (annotation `played` date ascending, unplayed first), random tie-break.
   Library picks are NOT added to `DiscoverState` (they're not suggestions) and
   are NOT subject to the acquisition dedupe.
3b. **Backfill:** if one share underdelivers (few candidates / few matching
   library tracks), the other share may fill the gap, best effort — total added
   never exceeds `count`.
4. **Assemble:** one `write_weekly_mix(song_dir, new_paths + lib_paths,
   name=profile.name, cap=profile.cap)` call; `start_scan()`;
   `state.save(stamp_last_run=False)`.
5. Returns `{"profile": id, "acquired": n, "library_added": m, "m3u": path}`.

Shared `DiscoverState` dedupes acquisitions across ALL profiles (same TTL).

## Auto-generation (genre profile bootstrapper)

`discover/profiles.py` → `suggest_genre_profiles(subsonic, existing_mixes, top_n=4)`:

- Source: Subsonic `getGenres` (genre, songCount, albumCount) weighted by
  songCount; genres mapped to Last.fm tag names by lowercasing (no fancy
  normalization in v1; junk/empty genres skipped).
- Skip genres already covered by an existing profile (by tag match).
- Defaults per created profile: weekly cadence staggered Mon→Thu, run_hour 7,
  count 15, cap 60, new_ratio 0.3, `auto_generated: true`.
- Trigger: once at server start when no `auto_generated` profiles exist AND the
  library has genres; and on demand via `POST /mixes/suggest`.
- Regeneration never modifies or deletes profiles the user edited
  (`auto_generated: false`) — editing any field through the UI clears the flag.

## Scheduler (sWebExt/py_server/server.py)

`_mix_scheduler_loop()` replaces `_discover_weekly_loop` + `_discover_daily_loop`:

- Every cycle: load profiles, compute each enabled profile's next-run datetime
  from its schedule, persist `next_runs` (id → iso) into `discover_state.json`
  for observability, sleep until the earliest, then run ALL due profiles
  **sequentially** (yt-dlp contention), then recompute.
- A profile run failure logs and skips to the next profile (one bad profile
  cannot stall the loop); loop-level failures keep the existing
  log + sleep-3600 + retry pattern.
- Config changes (new/edited profiles via UI) are picked up on the next cycle;
  after any `/mixes` mutation the loop is woken via a `threading.Event` so
  edits take effect immediately.
- The initial-run-on-empty-state behavior is kept for the `weekly` profile only
  (unchanged semantics).

## Routes

- `GET /mixes` → `{"mixes": [...], "next_runs": {id: iso}}`.
- `POST /mixes` → create or update (body = full profile; `id` new → create).
  Validation: unique id/name, cadence ∈ {daily, weekly}, 0 ≤ new_ratio ≤ 1,
  count ≥ 1, cap ≥ count, 0 ≤ run_hour ≤ 23, valid run_day for weekly cadence,
  seeds.mode valid and its required field non-empty
  (`genre` → genres, `manual` → artists, `playlist` → playlist). 400 with field
  errors otherwise. User edits clear `auto_generated`.
- `DELETE /mixes/<id>` → remove profile (does not delete the m3u/playlist).
- `POST /mixes/<id>/run` → manual run, returns `run_profile` result.
- `POST /mixes/suggest` → run the bootstrapper, return created profiles.
- Legacy aliases kept: `POST /discover/run` → run `weekly` profile,
  `POST /discover/run_daily` → run `daily` profile.

## UI: Mixes tab (explore.html / explore.js)

- One card per profile: name, enabled toggle, schedule summary
  (cadence/day/hour selects), count + cap number inputs, new/library ratio
  slider (labeled "Discovery ↔ Library", shown as %), seed mode select with the
  mode's field (genre chips input / artist list / playlist name), Run now,
  Delete (confirm dialog), "suggested" badge when `auto_generated`.
- "New Mix" button → blank card in edit state.
- "Regenerate suggested mixes" → `POST /mixes/suggest`, appends returned cards.
- Save per card → `POST /mixes`; inline status line per card (existing Discover
  tab styling). Next-run time displayed from `next_runs`.
- Settings tab (from the previous spec) keeps only global knobs; mix-specific
  fields live here. The previous spec's daily settings entries are superseded by
  this tab — remove `discover.daily.*` rows from `SETTINGS_SCHEMA` when this
  lands.

## Testing

`tests/discover/test_profiles.py`, `tests/discover/test_run_profile.py`, route tests:

1. Migration: legacy config (weekly+daily keys) → exact expected `mixes` list;
   idempotent (second load doesn't duplicate); `mixes` present → untouched.
2. Blend math: ratio 0 / 0.3 / 1.0 splits with rounding; count respected when a
   share underdelivers (library share may backfill new-share shortfall and vice
   versa — best effort, never exceed `count`).
3. Genre seeds: `tag_top_artists` parsing; multi-genre merge dedupes.
4. Library share: genre filter applied, least-recently-played preferred,
   tracks already in the m3u excluded, no `DiscoverState` writes.
5. Bootstrapper: top-N by songCount, skips covered genres, staggered days,
   never touches user-edited profiles on regeneration.
6. Scheduler: due-time math per cadence; sequential multi-due execution; one
   failing profile doesn't block the next; event wake on mutation.
7. Routes: CRUD validation matrix (bad ratio/cadence/mode → 400 with fields),
   edit clears `auto_generated`, delete keeps m3u, legacy aliases still work.

## Error handling

- Genre with no Last.fm tag results → that genre contributes nothing; if a
  profile's whole new-share yields zero candidates, the run still does the
  library share and reports `acquired: 0` (logged warning, not an error).
- `getSongsByGenre` empty (genre exists only on Last.fm side) → library share
  falls back to seed-artist search; if still empty, new share may backfill.
- Config writes atomic (`.tmp` + `os.replace`), same as the settings spec.

## Out of scope

- Cross-profile global dedupe of *library* picks (two genre mixes may both
  contain an owned track that fits both genres — acceptable).
- Genre tag normalization/aliasing beyond lowercasing (revisit if auto profiles
  come out wrong).
- Auth (unchanged stance from the settings spec).
- Search & acquire UI and the actions/status control panel (separate upcoming specs).
