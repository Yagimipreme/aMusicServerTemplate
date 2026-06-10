# Design: Last.fm Integration — Weekly Mix, Playlist Mix, Library Enrichment

**Date:** 2026-06-10
**Status:** Approved

---

## Overview

Three interconnected features powered by a shared `lastfm/` package:

1. **Improved Weekly Mix seeds** — replace Navidrome-only play counts with real scrobble history from Last.fm, and widen the similar-artist expansion from 20 to 100 candidates
2. **Playlist-based mix generation** — "give me more like this playlist" using a genre fingerprint built from Last.fm tags + artist similarity scoring
3. **Library tag enrichment** — backfill missing ID3 genre tags on existing MP3s using Last.fm track/artist tags

All three degrade gracefully when Last.fm is unconfigured or unreachable. Nothing in the existing download or discover pipeline is broken.

---

## 1. Package Structure

New `lastfm/` package at project root, alongside `discover/` and `library/`:

```
lastfm/
├── __init__.py
├── client.py     — API key auth, HTTP calls, error code handling, rate-limit (1 req/s)
├── seeds.py      — user.getTopArtists, user.getWeeklyArtistChart
├── tags.py       — track.getTopTags, artist.getTopTags, noise filtering, genre profile builder
└── similar.py    — artist.getSimilar, artist.getTopTracks, candidate scoring
```

Each module takes a `LastFMClient` instance as its only dependency. No module reads `config.json` directly — the server wiring constructs the client and passes it in. This keeps every module independently unit-testable with mocked HTTP.

---

## 2. Config

All new keys are optional. Features degrade gracefully when absent (see Section 6).

```jsonc
{
  // existing
  "lastfm_api_key": "",
  "lastfm_api_secret": "",

  // new
  "lastfm_username": "",         // Last.fm username — required for Weekly Mix and playlist mix
                                 // (user.getTopArtists needs a scrobble history target)
                                 // not needed for enrichment (API key only)

  "discover": {
    // existing
    "enabled": true,
    "weekly_count": 30,
    "playlist_name": "Weekly Mix",

    // new
    "lastfm_period": "1month",        // seed window: 7day | 1month | 3month | 6month | overall
    "seed_artist_count": 20,          // how many top artists pulled from Last.fm as Weekly Mix seeds
    "playlist_mix_count": 20,         // output track count for playlist-based mix
    "playlist_seed_artist_count": 10  // artists from source playlist used as seeds
                                      // (top N by track count in playlist; 0 = use all)
  },

  "enrich": {
    "enabled": false,              // off by default — user opts in
    "only_missing_genre": true     // true = skip files that already have a genre ID3 tag
                                   // false = re-tag the entire library
  }
}
```

`config.example.json` is updated to match.

---

## 3. `lastfm/client.py`

Thin wrapper around the Last.fm REST API (`https://ws.audioscrobbler.com/2.0/`):

- All calls use `format=json`, `api_key=<key>`
- Enforces 1 req/s rate limit (token bucket, in-process)
- Returns parsed JSON on success
- Raises typed exceptions on known error codes (see Section 6)
- No disk caching — callers are responsible for not calling redundantly

---

## 4. Weekly Mix Improvements

Two modules in `discover/` are upgraded. Everything downstream is unchanged.

### `discover/seeds.py` — blended seeds

Previously: Navidrome most-played / starred artists only.

Now:
1. Fetch Navidrome top artists (existing path)
2. If `lastfm_username` configured: fetch `user.getTopArtists(period=lastfm_period, limit=seed_artist_count)` via `lastfm.seeds`
3. Merge: artists appearing in both sources get a boosted rank. Last.fm-only artists fill remaining slots up to `seed_artist_count`.
4. If Last.fm unavailable: fall back silently to Navidrome-only (today's behaviour — no regression).

### `discover/expand.py` — wider similar-artist expansion

Previously: Navidrome `getArtistInfo2` → 20 similar artists, bounded to owned library.

Now:
1. For each seed artist: call `lastfm.similar.get_similar_artists(artist, limit=100)`
2. Filter out already-owned artists (Subsonic search, existing logic)
3. If `lastfm_api_key` absent: fall back to `getArtistInfo2` (existing behaviour).

`discover/resolve.py`, `acquire.py`, `assemble.py` — unchanged.

---

## 5. Playlist-Based Mix

### New endpoint

`POST /discover/playlist_mix`

```json
// request
{ "playlist_id": "abc123", "count": 20 }

// response
{ "status": "ok", "playlist_name": "Mix: Chill Evenings", "acquired": 18, "skipped": 2 }
```

`count` defaults to `playlist_mix_count` from config if omitted.

### Flow

```
Navidrome getPlaylist(playlist_id)
  → extract unique artists from tracks
  → cap to playlist_seed_artist_count (top N by frequency; 0 = all)

For each seed artist (parallel, rate-limited to 1 req/s):
  lastfm.tags.get_artist_tags(artist)    → weighted tag set

Build genre fingerprint for the playlist:
  1. Normalize each artist's tag set to their own max weight (each artist contributes equally)
  2. Sum across all artists
  3. Re-normalize the total to sum to 1.0

  Example result: { "electronic": 0.41, "ambient": 0.22, "industrial": 0.12, ... }

For each seed artist:
  lastfm.similar.get_similar_artists(artist, limit=50)

Merge + dedup all similar artists
  → filter already-owned (Subsonic search)

For each candidate similar artist:
  lastfm.tags.get_artist_tags(candidate)    → candidate tag set
  genre_overlap = dot_product(playlist_fingerprint, candidate_tags)
  final_score   = lastfm_similarity_score × genre_overlap

  Edge case: if playlist_fingerprint is empty (all artists unknown to Last.fm)
             → final_score = lastfm_similarity_score (pure similarity, no genre weighting)

Take top N candidates by final_score

For each top-N candidate:
  lastfm.similar.get_artist_top_tracks(artist, limit=5)
  → filter already-owned + discover_state.json

→ existing acquire() pipeline (yt-dlp download)
→ assemble() → Navidrome playlist "Mix: {source_playlist_name}"
```

The output playlist is always named `"Mix: {source_playlist_name}"` and overwritten on re-run, matching the Weekly Mix pattern. The `discover_state.json` is updated so these tracks are not re-suggested in future weekly mixes either.

---

## 6. Library Tag Enrichment

### New endpoints

| Endpoint | Body | Behaviour |
|---|---|---|
| `POST /library/enrich` | `{}` or `{"limit": 200}` | Enrich up to `limit` files (default: all). Runs in a background thread with the same lock pattern as dedup — concurrent calls are rejected with `{"status": "skipped"}`. |
| `GET /library/enrich/status` | — | Returns last run's result dict or `{"status": "idle"}`. |

### Flow

```
library/scanner.py → all MP3s in song_dir

For each track (up to limit):
  if only_missing_genre and ID3 genre already set → skip

  lastfm.tags.get_track_tags(artist, title)
    → not found (error 6) → fallback: lastfm.tags.get_artist_tags(artist)
    → still nothing          → skip, log, continue

  filter raw tags through NOISE_TAGS blocklist
  keep tags with weight ≥ 10 (Last.fm's 0–100 scale)
  take top 3 by weight
  if no tags survive filter → skip (don't write empty/garbage genre)

  write top 3 to ID3 genre field via eyed3, comma-separated
  e.g. "electronic, ambient, idm"

Returns: { "processed": N, "tagged": N, "skipped": N, "errors": N }
```

### Noise tag filtering

Last.fm community tags include many non-genre labels. We maintain a blocklist:

```python
NOISE_TAGS = {
    # engagement
    "seen live", "favorites", "favourite", "love", "loved", "awesome", "good", "cool",
    # demographic
    "male vocalists", "female vocalists", "singer-songwriter",
    # nationality (filtered by default)
    "american", "british", "german", "swedish", "french", "canadian", "australian",
    # era
    "00s", "90s", "80s", "70s", "60s",
    # misc
    "all", "music", "albums i own",
}
```

Tags not in this list and with weight ≥ 10 are considered genre candidates. Track-level lookup is attempted first (more specific); artist-level is the fallback (broader but better than nothing).

### Rate limiting

1 req/s throughout — Last.fm allows 5/s, but staying conservative avoids bans on large libraries. A 500-track library with no genres takes ~8–10 minutes to fully enrich.

---

## 7. Error Handling and Graceful Degradation

### Last.fm API error codes

| Code | Meaning | Response |
|---|---|---|
| 6 | Artist / track not found | Skip item, log at DEBUG, continue |
| 10 | Invalid API key | Log ERROR at startup, disable all Last.fm features for the session |
| 11 | Service offline | Retry once after 5s, then skip |
| 29 | Rate limit exceeded | Back off 10s, retry once, then skip |
| Network timeout | — | Skip item, log WARNING, continue |

### Degradation chain

```
No lastfm_api_key
  → enrichment disabled
  → playlist mix disabled
  → Weekly Mix: Navidrome-only seeds (today's behaviour, zero regression)

lastfm_api_key set, no lastfm_username
  → enrichment works (needs API key only)
  → Weekly Mix and playlist mix disabled (need scrobble history)

Last.fm unreachable during weekly run
  → log warning, continue with Navidrome seeds
  → Weekly Mix still runs, just with narrower seeds

All playlist artists unknown to Last.fm (empty genre fingerprint)
  → playlist mix falls back to pure lastfm_similarity_score, no genre weighting
  → mix still runs

Enrichment: track not found + artist not found
  → file skipped, not tagged; counter incremented in result
```

---

## 8. Testing

Each `lastfm/` module is unit-tested with recorded HTTP fixture responses, matching the pattern already established in `tests/discover/`.

| Test target | What is tested |
|---|---|
| `lastfm/client.py` | Error code handling, rate-limit backoff, timeout, retry logic |
| `lastfm/seeds.py` | Navidrome + Last.fm artist blending, dedup, ranking |
| `lastfm/tags.py` | Noise tag filtering, weight threshold (≥ 10), top-3 selection, artist fallback |
| `lastfm/similar.py` | Genre fingerprint building, dot-product scoring, empty-fingerprint fallback |
| `library/enrich` | Fixture MP3 with no genre → gets tagged; with genre → skipped (`only_missing=true`); re-tagged (`only_missing=false`) |
| End-to-end playlist mix | Mock Last.fm + mock Subsonic → correct playlist assembled in Navidrome |

---

## 9. File Changes Summary

| Action | Path |
|---|---|
| New | `lastfm/__init__.py` |
| New | `lastfm/client.py` |
| New | `lastfm/seeds.py` |
| New | `lastfm/tags.py` |
| New | `lastfm/similar.py` |
| New | `tests/lastfm/__init__.py` |
| New | `tests/lastfm/test_client.py` |
| New | `tests/lastfm/test_seeds.py` |
| New | `tests/lastfm/test_tags.py` |
| New | `tests/lastfm/test_similar.py` |
| New | `tests/library/test_enrich.py` |
| Modified | `discover/seeds.py` — blend Navidrome + Last.fm top artists |
| Modified | `discover/expand.py` — use `lastfm.similar.get_similar_artists` (limit=100) |
| Modified | `sWebExt/py_server/server.py` — POST /discover/playlist_mix, POST /library/enrich, GET /library/enrich/status |
| Modified | `config.example.json` — new lastfm_username, discover.*, enrich.* keys |
| Modified | `setup.py` — add step for lastfm_username (shown only when api_key is provided) |

---

## 10. Out of Scope

- BPM / key / energy matching — requires local audio analysis (essentia) or a live AcousticBrainz; deferred
- MusicBrainz ID enrichment — separate concern, future spec
- Scrobbling from this server — Navidrome handles scrobbling; we only read the history
- ListenBrainz — potential future alternative to Last.fm, not in scope now
- Per-user Last.fm accounts — single server, single Last.fm username; multi-user is a future concern
