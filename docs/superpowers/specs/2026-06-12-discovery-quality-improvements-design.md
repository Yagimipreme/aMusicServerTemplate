# Design: Discovery Quality Improvements

**Date:** 2026-06-12
**Status:** Approved
**Branch:** discover-addon-spec

---

## Overview

Four targeted improvements to the weekly mix discovery pipeline, addressing YouTube junk downloads, poor artist quality from the Last.fm similar-artist tail, underused Navidrome play-count signal, and missing artist metadata on SoundCloud downloads.

All changes are backward-compatible. Each degrades gracefully when Last.fm is unconfigured.

---

## 1. YouTube Junk Filtering

**Problem:** `ytsearch{n}:{artist} music` collides with unrelated YouTube channels. Duration is the only filter, letting through tech reviews, cooking videos, astrology content, etc.

### Changes to `discover/ytdlp_adapter.py`

**1a. Better default query suffix.**
Change `"music"` to `"official audio"` when no `top_track` hint is available. Biases YouTube ranking toward music content without restricting to official uploads.

```python
suffix = track_hint if track_hint else "official audio"
```

**1b. Oversampled filtering.**
Fetch `n × OVERSAMPLE` candidates (default `OVERSAMPLE = 5`), apply `_is_music_result()`, return up to `n` that pass. Prevents one piece of junk from producing zero results for an artist.

**1c. Relevance predicate `_is_music_result(entry, artist_name)`.**
A result passes if both conditions hold:
- The artist name appears (case-insensitive) in the video title **or** the uploader/channel name.
- The title contains none of the junk keyword blocklist.

Default blocklist (configurable via `discover.junk_keywords`):
```python
_DEFAULT_JUNK_KEYWORDS = {
    "review", "tutorial", "reaction", "cooking", "recipe",
    "horoscope", "astrology", "type beat", "asmr", "unboxing",
    "vlog", "podcast", "gameplay", "walkthrough",
}
```

No extra API calls. Pure title/channel string matching.

### Config additions
```jsonc
"discover": {
  "yt_oversample": 5,           // fetch n×this candidates, filter down to n
  "junk_keywords": []           // extra keywords appended to the built-in blocklist
}
```

---

## 2. Artist Quality Gate + Unified Scoring

**Problem:** `expand_similar` returns hundreds of artists ranked by cumulative Last.fm similarity score only. No signal for how established an artist is — causes obscure/irrelevant artists (e.g. low-listener Indian folk) to appear alongside good matches.

### Changes to `discover/expand.py` and `discover/engine.py`

**2a. Pre-resolve trim.**
After `expand_similar`, take only the top `K` candidates by raw similarity score before any enrichment. Default `K = seed_limit × candidate_oversample` (e.g. 60 when seed_limit=20). The rest are discarded — no Last.fm or YouTube calls are made for them.

**2b. Unified enrichment: replace `enrich_top_tracks` with `enrich_artist_info`.**

New function `enrich_artist_info(lastfm_client, artists)` calls `artist.getInfo` per artist (same rate-limited 1 req/s client). Returns:
- `stats.listeners` — global Last.fm listener count
- `tags.tag[]` — top genre tags (kept on artist dict for future use)

Then calls `artist.getTopTracks(limit=1)` only for artists that survive the listener floor (2c below). Net API call count is **lower** than today because the full list is no longer enriched unconditionally.

**2c. Listener floor.**
Drop artists with fewer than `min_artist_listeners` global listeners. Default: `5000`. Configurable — operators with niche tastes may lower this.

```jsonc
"discover": {
  "min_artist_listeners": 5000
}
```

If `artist.getInfo` fails for an artist (network error, unknown artist), the artist is **kept** with `listeners = 0` so a transient API error doesn't silently empty the candidate list.

**2d. Unified score.**
Re-rank surviving artists before `resolve_tracks`:

```
final_score = similarity_score × log10(max(listeners, 10))
```

`log10` compresses the listener range so a 10M-listener artist isn't 10× better than a 1M-listener one. Similarity remains the primary signal; listener count breaks ties and kills the obscure tail.

### Config additions
```jsonc
"discover": {
  "min_artist_listeners": 5000,
  "candidate_oversample": 3      // K = seed_limit × this (trim before enrichment)
}
```

---

## 3. Seed Weighting from Navidrome Play Counts

**Problem:** `collect_seeds` discards the `playCount` from Navidrome's `getAlbumList2` response and treats all seed artists as equal after ranking. Your most-played artists should produce stronger expansion signals.

### Changes to `discover/subsonic.py` and `discover/seeds.py`

**3a. `Subsonic.get_frequent_artists` accumulates play counts.**
Currently the method de-duplicates by artist but drops `playCount`. Updated to sum album `playCount` values per artist:

```python
out.append({"id": aid, "name": name, "play_count": total_play_count})
```

**3b. `collect_seeds` normalises weights.**
After merging Navidrome and Last.fm artists, compute:

```
weight = play_count / max_play_count_in_seed_list
```

Seeds with no Navidrome play count (Last.fm-only artists) get `weight = 1.0`. This keeps weights in the 0–1 range regardless of total listen volume.

**3c. `expand_similar` uses weights.**
Similarity score accumulation becomes:

```python
scores[key] += match_val × seed["weight"]
```

Artists similar to your most-played seeds rank higher than artists similar only to seeds you've barely touched. Last.fm-only seeds (weight=1.0) still contribute equally to each other, preserving their value as discovery signals.

No new config keys needed.

---

## 4. Missing Artist Metadata Repair

**Problem:** SoundCloud downloads frequently have title and album tags but no artist tag. These tracks are invisible to the discovery seed pipeline and Navidrome play-count aggregation.

### New module `library/repair.py`

Three-stage lookup chain, stopping at the first confident match.

**Stage 1: Title field parsing (free, instant)**
Many SoundCloud downloads embed the artist in the title: `"Artist Name - Track Title"` or `"Artist Name — Track Title"`. A regex pass extracts and splits:

```python
_SEPARATOR_RE = re.compile(r'^(.+?)\s*[-–—]\s*(.+)$')
```

If matched: set artist = group(1), clean title = group(2). No API call.

**Stage 2: Last.fm `track.search(track=title)`**
If stage 1 fails, search Last.fm by the title field. Accept the first result only if its global `listeners` count is ≥ `min_lastfm_listeners` (default: 10,000). The listener threshold prevents wrong-artist matches on common title words.

Uses the existing `LastFMClient` — no new credentials needed.

**Stage 3: MusicBrainz recording search**
If Last.fm returns nothing or below the confidence floor, query:

```
GET https://musicbrainz.org/ws/2/recording/?query=recording:"{title}"&fmt=json
```

No API key required. Accept the top result's credited artist if MusicBrainz returns a score ≥ `min_musicbrainz_score` (default: 90). MusicBrainz is significantly stronger than Last.fm for underground electronic, techno, and SoundCloud-native artists.

MusicBrainz requires a descriptive `User-Agent` header (their ToS) — use `amusicserver/1.0 (asbalk@gmx.de)`. Requests are rate-limited to 1 req/s, matching the existing Last.fm client discipline.

**What gets written:**
- Artist ID3 tag (only field being repaired)
- Title tag cleaned to the post-separator portion when stage 1 fires
- Files where all three stages fail are skipped and logged — no guesses below the confidence thresholds are written

### New endpoints

| Endpoint | Body | Behaviour |
|---|---|---|
| `POST /library/repair` | `{}` or `{"limit": 200}` | Scan `song_dir` for tracks missing artist tag; run repair chain on each. Background thread, same lock pattern as `/library/enrich`. |
| `GET /library/repair/status` | — | `{"status": "idle"}` or last run result |

### Result dict
```json
{
  "processed": 120,
  "repaired_stage1": 80,
  "repaired_stage2": 15,
  "repaired_stage3": 8,
  "skipped": 17,
  "errors": 0
}
```

### Config additions
```jsonc
"repair": {
  "enabled": true,
  "min_lastfm_listeners": 10000,
  "min_musicbrainz_score": 90
}
```

---

## 5. File Changes Summary

| Action | Path | Reason |
|---|---|---|
| Modified | `discover/ytdlp_adapter.py` | Junk filter predicate, oversampling, better query suffix |
| Modified | `discover/expand.py` | Weighted scoring, `enrich_artist_info`, listener floor, pre-resolve trim |
| Modified | `discover/engine.py` | Call `enrich_artist_info` instead of `enrich_top_tracks`, apply trim |
| Modified | `discover/seeds.py` | Attach and normalise `weight` from Navidrome play counts |
| Modified | `discover/subsonic.py` | `get_frequent_artists` accumulates `playCount` per artist |
| New | `library/repair.py` | Three-stage artist metadata repair |
| Modified | `sWebExt/py_server/server.py` | `POST /library/repair`, `GET /library/repair/status` |
| Modified | `config.example.json` | New `discover.*` and `repair.*` keys |
| New | `tests/discover/test_ytdlp_filter.py` | Unit tests for `_is_music_result` |
| New | `tests/library/test_repair.py` | Unit tests for all three repair stages |

---

## 6. Out of Scope

- BPM / key / energy matching — requires local audio analysis (essentia/librosa); deferred
- Scrobbling historical Navidrome play counts to Last.fm — user opted for direct play-count weighting instead
- Multi-user Last.fm accounts — single server, single username; future concern
- ListenBrainz — potential future alternative to Last.fm, not in scope
- Full genre fingerprint scoring for the weekly mix (playlist-mix feature already specced separately in `2026-06-10-lastfm-integration-design.md`)
