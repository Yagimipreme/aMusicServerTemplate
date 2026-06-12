# SoundCloud Discovery Universe — Design (DRAFT, pending user review)

**Date:** 2026-06-12
**Status:** Draft — API surface verified by live probes; awaiting user sign-off
**Depends on:** mix-profiles (extends `run_profile` candidate sourcing)

## Goal

Extend recommendation sourcing beyond Last.fm to SoundCloud's catalog (tags +
genres), gated hard enough that free-string tag spam doesn't poison mixes.
SC adds what Last.fm can't see: unreleased edits, bootlegs, underground uploads.

## Verified API surface (probed 2026-06-12, api-v2 + client_id)

| Capability | Endpoint | Status |
|---|---|---|
| Tag/genre search | `/search/tracks?q=*&filter.genre_or_tag=<tag>&sort=popular` | ✅ works |
| Recency window | `+ &filter.created_at=last_month` (also last_week/year) | ✅ works |
| Related tracks | `/tracks/<id>/related` | ✅ works (already wrapped: `soundcloud/discovery.py get_related`) |
| Genre charts | `/charts?kind=top&genre=soundcloud:genres:<g>` | ❌ 404 — removed from api-v2 |
| Gate metadata | per track: `playback_count, likes_count, tag_list, genre, duration, created_at, user` | ✅ present everywhere |

Probe evidence of tag noise: top "phonk" result was an afro-house track with
`phonk` buried in 10+ spam tags → gates are mandatory, tag presence is not
genre membership.

## Integration: profile `sources`

Profile schema gains `"sources": ["lastfm"]` (default; valid subset of
`{"lastfm","soundcloud"}`; validation: non-empty, known values). `run_profile`
new-share gathers candidates per source and interleaves them
(lastfm-first round-robin) before resolve/acquire:

- **lastfm** — existing path, unchanged.
- **soundcloud** — by seed mode:
  - `genre`: for each profile genre/tag → two queries: `sort=popular` (depth)
    and `sort=popular&filter.created_at=last_month` (freshness), ~50 each.
  - `history`/`manual`/`playlist`: seed artists → SC profile resolve
    (existing `mirror.get_profile`) → top tracks + reposts → `get_related`
    per top track (~20 each).

SC candidates carry `{"artist": user.username, "title", "url": permalink_url,
"source": "sc", ...gate fields}`. Resolution skips yt search for sc candidates
(URL already known); acquisition uses the existing download pipeline (yt-dlp
handles SC permalinks today — same path as likes mirroring).

## Quality gates (SC-specific, applied before dedupe)

Config block `discover.sc_gates` (global; per-profile override via
`profile.quality.sc_gates`):

```json
{
  "min_plays": 20000,
  "min_likes_per_1k_plays": 4,
  "duration_s": [90, 720],
  "max_age_years": 8,
  "genre_match": "primary_or_first3",
  "max_per_uploader": 2,
  "junk_tags": ["type beat", "free download", "preview", "snippet", "sample pack"]
}
```

- **Popularity floor** `min_plays`: kills zero-traction uploads (SC analog of
  Last.fm `min_artist_listeners`).
- **Engagement ratio** `likes_count / playback_count * 1000 >= 4`: kills
  botted/spam-tagged plays (the afro-house-as-phonk case had healthy plays but
  the ratio gate + genre check kill it for a phonk profile).
- **Duration window**: drops snippets (<90s) and full DJ sets (>12min) —
  values configurable; sets are a separate future feature.
- **Genre match** `primary_or_first3`: track passes only if profile tag ==
  `genre` field (case-insensitive) OR appears in the first 3 entries of
  `tag_list` (tag_list order is uploader-chosen salience; spam tags trail).
- **Per-uploader cap**: variety guard within one run.
- **Junk tags/title**: existing `junk_keywords` config + the sc-specific list
  applied to both `tag_list` and title.

Cross-source dedupe: existing `DiscoverState` keys (`track_key(artist,title)`)
apply unchanged — an SC acquisition blocks the same track arriving via
Last.fm→yt later, and vice versa. Library ownership check stays
`subsonic.song_exists`.

## Files (implementation sketch — full plan after sign-off)

- `soundcloud/recommend.py` (new): `tag_candidates(client, tags, gates)`,
  `artist_candidates(client, seed_artists, gates)`, `apply_gates(tracks, gates)`
  — pure functions, fully unit-testable with canned API JSON.
- `discover/engine.py`: source interleave in `run_profile` new-share.
- `discover/profiles.py`: `sources` validation + migration default.
- Settings schema: `discover.sc_gates.*` rows.
- Mixes UI: source checkboxes per profile card (SIGNAL plan follow-up).

## Open questions for user

1. Default `sources` for new genre profiles: `["lastfm","soundcloud"]` or
   lastfm-only until SC proves itself?
2. `min_plays` 20k is conservative; underground variety wants lower (5k?) with
   the engagement ratio carrying more weight. Preference?
3. Should the freshness query (last_month) get a guaranteed share of each SC
   batch (e.g. 30%) so mixes always contain genuinely new uploads?

## Out of scope

- SC playlists/sets as candidates; SC user-following ingestion beyond existing
  likes mirror; OAuth (client_id flow only); charts (endpoint dead).
