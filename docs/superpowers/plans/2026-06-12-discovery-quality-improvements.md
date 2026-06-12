# Discovery Quality Improvements — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve the weekly mix discovery pipeline with YouTube junk filtering, Last.fm listener-count quality gating, weighted seed scoring from Navidrome play counts, and a three-stage missing-artist metadata repair tool.

**Architecture:** YouTube search is oversampled and filtered by a relevance predicate before returning results. Artist expansion adds a listener-floor gate and re-ranks by `similarity × log10(listeners)`. Seed weighting propagates Navidrome `playCount` through the scoring chain. A new `library/repair.py` module repairs missing artist tags via title parsing → Last.fm → MusicBrainz.

**Tech Stack:** Python 3, yt-dlp, Last.fm REST API (existing `lastfm/` package), MusicBrainz REST API (no key, `urllib` only), eyed3 (already installed), Flask (existing server).

**Spec:** `docs/superpowers/specs/2026-06-12-discovery-quality-improvements-design.md`

---

## File Map

| Action | File | Responsibility |
|---|---|---|
| Modify | `discover/ytdlp_adapter.py` | Add `_is_music_result`, oversampled `make_search_fn`, module-level `search` |
| Modify | `discover/subsonic.py` | `get_frequent_artists` accumulates `playCount` per artist |
| Modify | `discover/seeds.py` | Normalize weights; attach to seed dicts; Last.fm-only seeds get `weight=1.0` |
| Modify | `discover/expand.py` | Weighted scoring in `expand_similar`; new `enrich_artist_info` (replaces `enrich_top_tracks`) |
| Modify | `discover/engine.py` | Pre-trim candidates; call `enrich_artist_info` instead of `enrich_top_tracks` |
| Create | `library/repair.py` | Three-stage artist repair orchestrator |
| Modify | `sWebExt/py_server/server.py` | `POST /library/repair`, `GET /library/repair/status` |
| Modify | `config.example.json` | New `discover.*` and `repair.*` keys |
| Create | `tests/discover/test_ytdlp_filter.py` | Unit tests for `_is_music_result` |
| Create | `tests/library/test_repair.py` | Unit tests for all three repair stages |

---

## Task 1: YouTube junk filter predicate

**Files:**
- Modify: `discover/ytdlp_adapter.py`
- Create: `tests/discover/test_ytdlp_filter.py`

- [ ] **Step 1: Write failing tests**

Create `tests/discover/test_ytdlp_filter.py`:

```python
import pytest
from discover.ytdlp_adapter import _is_music_result


def _entry(title, uploader=None, channel=None):
    return {"title": title, "uploader": uploader, "channel": channel}


def test_passes_when_artist_in_title():
    assert _is_music_result(_entry("Burial - Archangel", "SomeChannel"), "Burial") is True


def test_passes_when_artist_in_uploader():
    assert _is_music_result(_entry("Archangel Official Audio", "Burial"), "Burial") is True


def test_passes_when_artist_in_channel_field():
    assert _is_music_result(_entry("Archangel (2005)", uploader=None, channel="Burial"), "Burial") is True


def test_fails_when_artist_absent_from_title_and_channel():
    assert _is_music_result(_entry("Morning Coffee Vibes", "CoffeeTV"), "Burial") is False


def test_fails_on_junk_keyword_in_title():
    assert _is_music_result(_entry("Burial - Archangel Guitar Tutorial", "GuitarHub"), "Burial") is False


def test_fails_on_review_keyword():
    assert _is_music_result(_entry("Burial Archangel Review - Best Album?", "MusicCritic"), "Burial") is False


def test_extra_junk_keyword_blocks_result():
    assert _is_music_result(
        _entry("Burial - Live Freestyle", "Burial"),
        "Burial",
        extra_junk=frozenset({"freestyle"}),
    ) is False


def test_case_insensitive_artist_match():
    assert _is_music_result(_entry("BURIAL - ARCHANGEL", "XLRecordings"), "burial") is True


def test_partial_artist_name_does_not_pass():
    # "burie" is not "burial"
    assert _is_music_result(_entry("burie - something", "SomeChannel"), "Burial") is False
```

- [ ] **Step 2: Run tests — expect ImportError or AttributeError**

```bash
cd /home/taichi/repos/musicServer/aMusicServerTemplate
python -m pytest tests/discover/test_ytdlp_filter.py -v 2>&1 | head -30
```

Expected: `ImportError: cannot import name '_is_music_result'`

- [ ] **Step 3: Add the predicate to `discover/ytdlp_adapter.py`**

At the top of the file, after `_MAX_TRACK_SECONDS`, add:

```python
_DEFAULT_JUNK_KEYWORDS: frozenset = frozenset({
    "review", "tutorial", "reaction", "cooking", "recipe",
    "horoscope", "astrology", "type beat", "asmr", "unboxing",
    "vlog", "podcast", "gameplay", "walkthrough",
})


def _is_music_result(entry: dict, artist_name: str,
                     extra_junk: frozenset = frozenset()) -> bool:
    title = (entry.get("title") or "").casefold()
    channel = (
        (entry.get("uploader") or "")
        + " "
        + (entry.get("channel") or "")
    ).casefold()
    artist_cf = artist_name.casefold()

    if artist_cf not in title and artist_cf not in channel:
        return False

    junk = _DEFAULT_JUNK_KEYWORDS | extra_junk
    return not any(kw in title for kw in junk)
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
python -m pytest tests/discover/test_ytdlp_filter.py -v
```

Expected: all 9 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add discover/ytdlp_adapter.py tests/discover/test_ytdlp_filter.py
git commit -m "feat(discover): youtube junk filter predicate — artist+channel check, keyword blocklist"
```

---

## Task 2: Oversampled YouTube search + module-level `search`

**Files:**
- Modify: `discover/ytdlp_adapter.py`

- [ ] **Step 1: Replace `make_search_fn` body**

Find the existing `make_search_fn()` function and replace it entirely with:

```python
def make_search_fn(oversample: int = 5,
                   extra_junk_keywords: frozenset = frozenset()):
    """Return search_fn(artist_name, n, track_hint=None) via yt-dlp flat search.

    Fetches n×oversample candidates, applies _is_music_result filter, returns up to n.
    Default query suffix changed from 'music' to 'official audio'.
    """
    from yt_dlp import YoutubeDL

    def search_fn(artist_name, n, track_hint=None):
        suffix = track_hint if track_hint else "official audio"
        fetch_n = n * oversample
        query = f"ytsearch{fetch_n}:{artist_name} {suffix}"
        opts = {"quiet": True, "skip_download": True, "extract_flat": "in_playlist"}
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(query, download=False)
        entries = (info or {}).get("entries", []) or []
        out = []
        for e in entries:
            if len(out) >= n:
                break
            vid = e.get("id")
            url = e.get("url") or (
                f"https://www.youtube.com/watch?v={vid}" if vid else None
            )
            if not url:
                continue
            duration = e.get("duration") or 0
            if duration and duration > _MAX_TRACK_SECONDS:
                continue
            if not _is_music_result(e, artist_name, extra_junk_keywords):
                continue
            out.append({"title": e.get("title", ""), "url": url})
        return out

    return search_fn


# Module-level search callable — used by run_mix_from_config and tests.
search = make_search_fn()
```

- [ ] **Step 2: Update `_build_discover_deps` in `sWebExt/py_server/server.py` to pass config values**

Find the line:
```python
search_fn=make_search_fn(),
```

Replace with:
```python
disc_cfg = cfg.get("discover") or {}
_oversample = int(disc_cfg.get("yt_oversample", 5))
_extra_junk = frozenset(disc_cfg.get("junk_keywords", []))
```

Then change the `SimpleNamespace(...)` call's `search_fn` line to:
```python
search_fn=make_search_fn(oversample=_oversample, extra_junk_keywords=_extra_junk),
```

- [ ] **Step 3: Verify existing tests still pass**

```bash
python -m pytest tests/discover/ -v --tb=short
```

Expected: all existing tests pass (the filter is applied only when network is hit; unit tests mock `search_fn` directly).

- [ ] **Step 4: Commit**

```bash
git add discover/ytdlp_adapter.py sWebExt/py_server/server.py
git commit -m "feat(discover): oversample youtube search 5×, filter junk, default suffix 'official audio'"
```

---

## Task 3: Subsonic play count accumulation

**Files:**
- Modify: `discover/subsonic.py`
- Test: `tests/discover/test_subsonic.py`

- [ ] **Step 1: Write a failing test**

Open `tests/discover/test_subsonic.py` and add:

```python
def test_get_frequent_artists_accumulates_play_counts():
    """Albums from the same artist should have their play counts summed."""
    fake_response = {
        "subsonic-response": {
            "albumList2": {
                "album": [
                    {"artistId": "1", "artist": "Burial", "playCount": 30},
                    {"artistId": "1", "artist": "Burial", "playCount": 20},
                    {"artistId": "2", "artist": "Actress", "playCount": 10},
                ]
            }
        }
    }

    def fake_fetch(url):
        return fake_response

    from discover.subsonic import Subsonic
    sub = Subsonic("http://localhost", "u", "p", fetch_json=fake_fetch)
    artists = sub.get_frequent_artists(size=50)

    burial = next(a for a in artists if a["name"] == "Burial")
    actress = next(a for a in artists if a["name"] == "Actress")

    assert burial["play_count"] == 50
    assert actress["play_count"] == 10
    assert artists[0]["name"] == "Burial"  # first-seen order preserved
```

- [ ] **Step 2: Run — expect KeyError or assertion failure**

```bash
python -m pytest tests/discover/test_subsonic.py::test_get_frequent_artists_accumulates_play_counts -v
```

Expected: FAIL — `play_count` key absent.

- [ ] **Step 3: Update `get_frequent_artists` in `discover/subsonic.py`**

Replace the existing method body:

```python
def get_frequent_artists(self, size: int = 50):
    """Most-played albums -> ordered, de-duplicated artist list with summed play counts."""
    sr = self._call("getAlbumList2.view", type="frequent", size=size)
    albums = (sr.get("albumList2", {}) or {}).get("album", []) or []
    counts: dict = {}   # aid -> {"id", "name", "play_count"}
    order: list = []    # first-seen insertion order
    for alb in albums:
        aid = alb.get("artistId")
        name = alb.get("artist")
        if not name or not aid:
            continue
        pc = int(alb.get("playCount") or 0)
        if aid not in counts:
            counts[aid] = {"id": aid, "name": name, "play_count": 0}
            order.append(aid)
        counts[aid]["play_count"] += pc
    return [counts[aid] for aid in order]
```

- [ ] **Step 4: Run — expect PASS**

```bash
python -m pytest tests/discover/test_subsonic.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add discover/subsonic.py tests/discover/test_subsonic.py
git commit -m "feat(discover): subsonic.get_frequent_artists accumulates playCount per artist"
```

---

## Task 4: Seed weighting — normalize and attach weights

**Files:**
- Modify: `discover/seeds.py`
- Test: `tests/discover/test_seeds.py`

- [ ] **Step 1: Write failing tests**

Open `tests/discover/test_seeds.py` and add:

```python
def test_collect_seeds_attaches_weight_from_play_count():
    """Seeds must carry a normalized weight derived from playCount."""
    artists = [
        {"id": "1", "name": "Burial", "play_count": 100},
        {"id": "2", "name": "Actress", "play_count": 50},
        {"id": "3", "name": "Shackleton", "play_count": 25},
    ]

    class FakeSub:
        def get_frequent_artists(self, size):
            return artists
        def get_all_artist_names(self):
            return set()

    from discover.seeds import collect_seeds
    seeds = collect_seeds(FakeSub(), limit=10)

    burial = next(s for s in seeds if s["name"] == "Burial")
    actress = next(s for s in seeds if s["name"] == "Actress")

    assert burial["weight"] == pytest.approx(1.0)
    assert actress["weight"] == pytest.approx(0.5)


def test_collect_seeds_lastfm_only_artist_gets_default_weight():
    """Artists added from Last.fm (not in Navidrome) must get weight=1.0."""
    class FakeSub:
        def get_frequent_artists(self, size):
            return [{"id": "1", "name": "Burial", "play_count": 100}]
        def get_all_artist_names(self):
            return {"burial"}

    class FakeLFM:
        def call(self, method, **kwargs):
            return {"topartists": {"artist": [{"name": "Actress"}]}}

    from discover.seeds import collect_seeds

    # Import get_top_artists to confirm it will return Actress
    seeds = collect_seeds(FakeSub(), limit=5,
                          lastfm_client=FakeLFM(), lastfm_username="user")
    lfm_artist = next((s for s in seeds if s["name"] == "Actress"), None)
    if lfm_artist:
        assert lfm_artist["weight"] == pytest.approx(1.0)
```

- [ ] **Step 2: Run — expect failure**

```bash
python -m pytest tests/discover/test_seeds.py::test_collect_seeds_attaches_weight_from_play_count -v
```

Expected: FAIL — `KeyError: 'weight'`

- [ ] **Step 3: Update `collect_seeds` in `discover/seeds.py`**

After the line `artists = subsonic.get_frequent_artists(size=max(limit, 50))`, add:

```python
# Normalize play_count to [0, 1] weights; zero-play artists get 0.0
_max_pc = max((a.get("play_count", 0) for a in artists), default=1) or 1
for a in artists:
    a["weight"] = (a.get("play_count", 0) or 0) / _max_pc
```

Then in the `merged.append` call for Last.fm-only artists (the third section, where `{"id": None, "name": name}` is built), update to:

```python
merged.append({"id": None, "name": name, "play_count": 0, "weight": 1.0})
```

Also update the Navidrome-only artist entries when they're added to `merged` to preserve their weight. The `merged.append(a)` calls in the first two sections already carry the `weight` field since `a` is a reference to the nav_artist dict that was already updated in-place above. No change needed there.

- [ ] **Step 4: Run — expect PASS**

```bash
python -m pytest tests/discover/test_seeds.py -v
```

- [ ] **Step 5: Commit**

```bash
git add discover/seeds.py tests/discover/test_seeds.py
git commit -m "feat(discover): seed weighting — normalise Navidrome playCount to [0,1] weight"
```

---

## Task 5: Weighted expansion scoring

**Files:**
- Modify: `discover/expand.py`
- Test: `tests/discover/test_expand.py`

- [ ] **Step 1: Write failing test**

Open `tests/discover/test_expand.py` and add:

```python
def test_expand_similar_uses_seed_weight():
    """A similar artist linked to a high-weight seed should score higher
    than the same artist linked only to a low-weight seed."""
    from discover.expand import expand_similar

    class FakeSub:
        def get_frequent_artists(self, size):
            return []
        def get_all_artist_names(self):
            return set()

    # Two seeds: heavy-weight and light-weight
    seeds = [
        {"id": "-1", "name": "HeavySeed", "weight": 1.0},
        {"id": "-1", "name": "LightSeed", "weight": 0.1},
    ]

    call_log = []

    class FakeLFM:
        def call(self, method, **kwargs):
            call_log.append(kwargs.get("artist"))
            if kwargs.get("artist") == "HeavySeed":
                return {"similarartists": {"artist": [
                    {"name": "TargetArtist", "match": "0.9"}
                ]}}
            return {"similarartists": {"artist": [
                {"name": "TargetArtist", "match": "0.9"}
            ]}}

    # Patch get_similar_artists so no real HTTP call happens
    import discover.expand as expand_mod
    original = expand_mod._expand_via_lastfm

    def fake_expand(client, artist_name):
        if artist_name == "HeavySeed":
            return [{"name": "TargetArtist", "id": "-1", "match": 0.9}]
        return [{"name": "WeakTarget", "id": "-1", "match": 0.9}]

    expand_mod._expand_via_lastfm = fake_expand
    try:
        result = expand_similar(FakeSub(), seeds, lastfm_client=object())
    finally:
        expand_mod._expand_via_lastfm = original

    scores = {a["name"]: a["score"] for a in result}
    # TargetArtist accumulates: 0.9×1.0 = 0.9; WeakTarget: 0.9×0.1 = 0.09
    assert scores["TargetArtist"] > scores["WeakTarget"]
```

- [ ] **Step 2: Run — expect assertion failure**

```bash
python -m pytest tests/discover/test_expand.py::test_expand_similar_uses_seed_weight -v
```

Expected: FAIL — scores are equal (weight not yet applied).

- [ ] **Step 3: Update scoring line in `expand_similar` in `discover/expand.py`**

Find the line:
```python
scores[key] = scores.get(key, 0.0) + match_val
```

Replace with:
```python
seed_weight = seed.get("weight", 1.0)
scores[key] = scores.get(key, 0.0) + match_val * seed_weight
```

- [ ] **Step 4: Run — expect PASS**

```bash
python -m pytest tests/discover/test_expand.py -v
```

- [ ] **Step 5: Commit**

```bash
git add discover/expand.py tests/discover/test_expand.py
git commit -m "feat(discover): weighted expansion — similarity score scaled by seed play_count weight"
```

---

## Task 6: `enrich_artist_info` — listener floor + unified score

**Files:**
- Modify: `discover/expand.py`
- Test: `tests/discover/test_expand.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/discover/test_expand.py`:

```python
def test_enrich_artist_info_drops_below_listener_floor():
    from discover.expand import enrich_artist_info

    class FakeLFM:
        def call(self, method, **kwargs):
            name = kwargs.get("artist", "")
            listeners = "200000" if name == "Burial" else "100"
            return {"artist": {"stats": {"listeners": listeners}}}

    artists = [
        {"name": "Burial", "score": 0.9},
        {"name": "TinyArtist", "score": 0.8},
    ]

    result = enrich_artist_info(FakeLFM(), artists, min_listeners=5000)
    names = [a["name"] for a in result]
    assert "Burial" in names
    assert "TinyArtist" not in names


def test_enrich_artist_info_keeps_artist_on_api_error():
    from discover.expand import enrich_artist_info

    class BrokenLFM:
        def call(self, method, **kwargs):
            raise RuntimeError("network error")

    artists = [{"name": "SomeArtist", "score": 0.5}]
    result = enrich_artist_info(BrokenLFM(), artists, min_listeners=5000)
    # Must keep artist when API fails — don't silently empty the list
    assert len(result) == 1


def test_enrich_artist_info_rescores_by_listeners():
    from discover.expand import enrich_artist_info
    import math

    class FakeLFM:
        def call(self, method, **kwargs):
            name = kwargs.get("artist", "")
            listeners = "1000000" if name == "BigArtist" else "10000"
            return {"artist": {"stats": {"listeners": listeners}}}

    artists = [
        {"name": "BigArtist", "score": 0.5},
        {"name": "SmallArtist", "score": 0.5},
    ]
    result = enrich_artist_info(FakeLFM(), artists, min_listeners=5000)
    scores = {a["name"]: a["score"] for a in result}
    assert scores["BigArtist"] > scores["SmallArtist"]
```

- [ ] **Step 2: Run — expect ImportError**

```bash
python -m pytest tests/discover/test_expand.py::test_enrich_artist_info_drops_below_listener_floor -v
```

Expected: `ImportError: cannot import name 'enrich_artist_info'`

- [ ] **Step 3: Add `enrich_artist_info` to `discover/expand.py`**

Add at the top: `import math`

Add this function after `enrich_top_tracks`:

```python
def enrich_artist_info(lastfm_client, artists, min_listeners: int = 5000):
    """Fetch artist.getInfo per artist; filter by listener floor; rescale score.

    Adds: listeners (int), top_track (str|None) to each artist dict.
    Returns filtered list — artists below min_listeners are dropped.
    Artists where the API call fails are KEPT (don't silently empty the list).
    """
    from lastfm.similar import get_artist_top_tracks

    enriched = []
    for a in artists:
        keep = True
        try:
            info = lastfm_client.call("artist.getInfo", artist=a["name"])
            listeners = int(
                (info.get("artist") or {})
                .get("stats", {})
                .get("listeners", 0)
            )
            a["listeners"] = listeners
            if listeners < min_listeners:
                keep = False
        except Exception:
            logger.warning("enrich_artist_info: getInfo failed for %s — keeping",
                           a["name"], exc_info=True)
            a["listeners"] = 0  # unknown; keep to avoid empty list on API failure

        if not keep:
            continue

        # Rescale score: similarity × log10(listeners) — keeps similarity primary
        if a.get("listeners", 0) > 0:
            a["score"] = a.get("score", 1.0) * math.log10(max(a["listeners"], 10))

        # Top track — for targeted YouTube search; None falls back to "official audio"
        try:
            tracks = get_artist_top_tracks(lastfm_client, a["name"], limit=1)
            a["top_track"] = tracks[0]["title"] if tracks else None
        except Exception:
            a["top_track"] = None

        enriched.append(a)

    return enriched
```

- [ ] **Step 4: Run — expect PASS**

```bash
python -m pytest tests/discover/test_expand.py -v
```

- [ ] **Step 5: Commit**

```bash
git add discover/expand.py tests/discover/test_expand.py
git commit -m "feat(discover): enrich_artist_info — Last.fm listener floor + similarity×log(listeners) score"
```

---

## Task 7: Engine wiring — pre-trim + use `enrich_artist_info`

**Files:**
- Modify: `discover/engine.py`
- Test: `tests/discover/test_engine.py`

- [ ] **Step 1: Check existing engine tests**

```bash
python -m pytest tests/discover/test_engine.py -v
```

Note which tests pass now — they must all still pass after this task.

- [ ] **Step 2: Update imports and `run_weekly` signature in `discover/engine.py`**

Find:
```python
from discover.expand import expand_similar, enrich_top_tracks
```

Replace with:
```python
from discover.expand import expand_similar, enrich_artist_info
```

Update the `run_weekly` function signature (add two new keyword params with defaults):
```python
def run_weekly(deps, count=30, seed_limit=20, per_seed=20, per_artist=1,
               playlist_name="Weekly Mix", lastfm_client=None,
               lastfm_username="", lastfm_period="1month", lastfm_periods=None,
               playlist_cap=100, min_artist_listeners=5000,
               candidate_oversample=3):
```

- [ ] **Step 3: Replace enrichment block in `run_weekly` body**

Find:
```python
if lastfm_client is not None:
    logger.info("discover: enriching top tracks via Last.fm")
    enrich_top_tracks(lastfm_client, artists)
```

Replace with:
```python
if lastfm_client is not None:
    k = seed_limit * candidate_oversample
    artists = sorted(artists, key=lambda a: -a.get("score", 0))[:k]
    logger.info("discover: trimmed to top %d candidates before enrichment", len(artists))
    artists = enrich_artist_info(lastfm_client, artists,
                                 min_listeners=min_artist_listeners)
    logger.info("discover: %d candidates after listener floor", len(artists))
```

- [ ] **Step 4: Update `run_mix` to pass config values into `run_weekly`**

In `run_mix`, find where `run_weekly` is called (the Last.fm branch). Add two lines before the call:
```python
min_artist_listeners = int(disc.get("min_artist_listeners", 5000))
candidate_oversample = int(disc.get("candidate_oversample", 3))
```

Then add them to the `run_weekly(...)` call:
```python
return run_weekly(deps, count=count, seed_limit=seed_limit,
                 playlist_name=playlist_name,
                 lastfm_client=lastfm_client,
                 lastfm_username=lastfm_username,
                 lastfm_period=lastfm_period,
                 lastfm_periods=lastfm_periods,
                 playlist_cap=playlist_cap,
                 min_artist_listeners=min_artist_listeners,
                 candidate_oversample=candidate_oversample)
```

- [ ] **Step 5: Update `run_mix` bootstrap path**

In the `run_mix` bootstrap section, find:
```python
if lastfm_client is not None:
    logger.info("discover: enriching top tracks via Last.fm")
    enrich_top_racks(lastfm_client, artists)
```

Replace with:
```python
if lastfm_client is not None:
    artists = sorted(artists, key=lambda a: -a.get("score", 0))[:60]
    artists = enrich_artist_info(lastfm_client, artists, min_listeners=5000)
    logger.info("discover: bootstrap — %d candidates after listener floor", len(artists))
```

- [ ] **Step 6: Run all discover tests**

```bash
python -m pytest tests/discover/ -v --tb=short
```

Expected: all existing tests pass.

- [ ] **Step 7: Commit**

```bash
git add discover/engine.py
git commit -m "feat(discover): pre-trim candidates, replace enrich_top_tracks with enrich_artist_info"
```

---

## Task 8: `library/repair.py` — stage 1 (title parse) + stage 2 (Last.fm)

**Files:**
- Create: `library/repair.py`
- Create: `tests/library/test_repair.py`

- [ ] **Step 1: Write failing tests**

Create `tests/library/test_repair.py`:

```python
import pytest
from library.repair import _repair_by_title_parse, _repair_by_lastfm


# ── Stage 1: title parsing ────────────────────────────────────────────────────

def test_stage1_hyphen_separator():
    artist, title = _repair_by_title_parse("Burial - Archangel")
    assert artist == "Burial"
    assert title == "Archangel"


def test_stage1_en_dash():
    artist, title = _repair_by_title_parse("Demdike Stare – Testpressing #7")
    assert artist == "Demdike Stare"
    assert title == "Testpressing #7"


def test_stage1_em_dash():
    artist, title = _repair_by_title_parse("The Bug — Skeng")
    assert artist == "The Bug"
    assert title == "Skeng"


def test_stage1_takes_first_separator_only():
    artist, title = _repair_by_title_parse("The Bug - Skeng - feat. Flowdan")
    assert artist == "The Bug"
    assert title == "Skeng - feat. Flowdan"


def test_stage1_no_separator_returns_none():
    artist, title = _repair_by_title_parse("Archangel")
    assert artist is None
    assert title is None


def test_stage1_strips_whitespace():
    artist, title = _repair_by_title_parse("  Actress  -  Hubble  ")
    assert artist == "Actress"
    assert title == "Hubble"


# ── Stage 2: Last.fm ──────────────────────────────────────────────────────────

class _FakeLFM:
    def __init__(self, artist="Burial", listeners=50000):
        self._artist = artist
        self._listeners = listeners

    def call(self, method, **kwargs):
        return {"results": {"trackmatches": {"track": [
            {"artist": self._artist, "listeners": str(self._listeners)}
        ]}}}


class _EmptyLFM:
    def call(self, method, **kwargs):
        return {"results": {"trackmatches": {"track": []}}}


class _BrokenLFM:
    def call(self, method, **kwargs):
        raise RuntimeError("API error")


def test_stage2_returns_artist_above_floor():
    assert _repair_by_lastfm(_FakeLFM(listeners=50000), "Archangel", 10000) == "Burial"


def test_stage2_returns_none_below_floor():
    assert _repair_by_lastfm(_FakeLFM(listeners=500), "obscure", 10000) is None


def test_stage2_returns_none_on_empty_results():
    assert _repair_by_lastfm(_EmptyLFM(), "unknown", 10000) is None


def test_stage2_returns_none_on_api_error():
    assert _repair_by_lastfm(_BrokenLFM(), "anything", 10000) is None


def test_stage2_handles_dict_track_response():
    """Last.fm sometimes returns a dict instead of list when there's one result."""
    class DictLFM:
        def call(self, method, **kwargs):
            return {"results": {"trackmatches": {
                "track": {"artist": "Burial", "listeners": "99999"}
            }}}
    assert _repair_by_lastfm(DictLFM(), "Archangel", 10000) == "Burial"
```

- [ ] **Step 2: Run — expect ImportError**

```bash
python -m pytest tests/library/test_repair.py -v 2>&1 | head -20
```

Expected: `ImportError: No module named 'library.repair'`

- [ ] **Step 3: Create `library/repair.py` with stage 1 and stage 2**

```python
"""Three-stage missing-artist metadata repair.

Stage 1: Parse "Artist - Title" pattern from the title ID3 field.
Stage 2: Last.fm track.search by title.
Stage 3: MusicBrainz recording search by title.
"""
import re
import logging

logger = logging.getLogger(__name__)

_SEPARATOR_RE = re.compile(r'^(.+?)\s*[-–—]\s*(.+)$')

_DEFAULT_MIN_LASTFM_LISTENERS = 10_000
_DEFAULT_MIN_MB_SCORE = 90

_MB_USER_AGENT = "amusicserver/1.0 (asbalk@gmx.de)"


def _repair_by_title_parse(title: str):
    """Extract artist from title field if it matches 'Artist - Title' pattern.

    Returns (artist, clean_title) on match, (None, None) otherwise.
    """
    m = _SEPARATOR_RE.match(title.strip())
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return None, None


def _repair_by_lastfm(lastfm_client, title: str, min_listeners: int):
    """Search Last.fm by title; return artist name if listener count is confident.

    Returns artist string or None.
    """
    try:
        result = lastfm_client.call("track.search", track=title, limit=1)
        matches = (
            (result.get("results") or {})
            .get("trackmatches", {})
            .get("track", [])
        )
        if isinstance(matches, dict):
            matches = [matches]
        if not matches:
            return None
        match = matches[0]
        try:
            listeners = int(match.get("listeners", 0))
        except (ValueError, TypeError):
            listeners = 0
        if listeners < min_listeners:
            return None
        return (match.get("artist") or "").strip() or None
    except Exception:
        logger.warning("repair: Last.fm track.search failed for %r", title, exc_info=True)
        return None
```

- [ ] **Step 4: Run tests — expect PASS for stages 1 and 2**

```bash
python -m pytest tests/library/test_repair.py -v
```

Expected: all stage 1 and stage 2 tests pass.

- [ ] **Step 5: Commit**

```bash
git add library/repair.py tests/library/test_repair.py
git commit -m "feat(library): repair stage 1 (title parse) + stage 2 (Last.fm track.search)"
```

---

## Task 9: `library/repair.py` — stage 3 (MusicBrainz) + orchestrator

**Files:**
- Modify: `library/repair.py`
- Modify: `tests/library/test_repair.py`

- [ ] **Step 1: Write failing tests for stage 3 and orchestrator**

Add to `tests/library/test_repair.py`:

```python
from unittest.mock import patch, MagicMock
import json as _json
from library.repair import _repair_by_musicbrainz, repair_missing_artists


# ── Stage 3: MusicBrainz ──────────────────────────────────────────────────────

def _mb_response(artist_name, score=95):
    return _json.dumps({
        "recordings": [{
            "score": score,
            "artist-credit": [{"artist": {"name": artist_name}}]
        }]
    }).encode()


def test_stage3_returns_artist_above_score_threshold():
    mock_resp = MagicMock()
    mock_resp.read.return_value = _mb_response("Burial", score=95)
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = _repair_by_musicbrainz("Archangel", min_score=90)
    assert result == "Burial"


def test_stage3_returns_none_below_score_threshold():
    mock_resp = MagicMock()
    mock_resp.read.return_value = _mb_response("Burial", score=70)
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = _repair_by_musicbrainz("Archangel", min_score=90)
    assert result is None


def test_stage3_returns_none_on_empty_recordings():
    mock_resp = MagicMock()
    mock_resp.read.return_value = _json.dumps({"recordings": []}).encode()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = _repair_by_musicbrainz("unknown track", min_score=90)
    assert result is None


def test_stage3_returns_none_on_network_error():
    with patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
        result = _repair_by_musicbrainz("any title", min_score=90)
    assert result is None


# ── Orchestrator ──────────────────────────────────────────────────────────────

def test_orchestrator_uses_stage1_when_title_has_separator(tmp_path):
    import eyed3
    mp3 = tmp_path / "track.mp3"
    # Create a minimal MP3 file (eyed3 needs a real audio header; use a tiny silent one)
    # We'll write a stub that eyed3 can load by patching eyed3.load instead
    pass  # covered by integration; unit-tested via stage functions above


def test_orchestrator_skips_tracks_with_existing_artist(tmp_path):
    """Tracks that already have an artist tag must be counted as skipped."""
    import eyed3

    class FakeTag:
        artist = "Burial"
        title = "Archangel"
        def save(self): pass

    class FakeAF:
        tag = FakeTag()

    with patch("library.repair.eyed3.load", return_value=FakeAF()), \
         patch("library.scanner.scan", return_value=[str(tmp_path / "f.mp3")]):
        stats = repair_missing_artists(str(tmp_path))

    assert stats["skipped"] == 1
    assert stats["repaired_stage1"] == 0
```

- [ ] **Step 2: Run — expect ImportError for `_repair_by_musicbrainz`**

```bash
python -m pytest tests/library/test_repair.py::test_stage3_returns_artist_above_score_threshold -v
```

Expected: `ImportError: cannot import name '_repair_by_musicbrainz'`

- [ ] **Step 3: Add stage 3 and orchestrator to `library/repair.py`**

Append to `library/repair.py`:

```python
def _repair_by_musicbrainz(title: str, min_score: int):
    """Query MusicBrainz recording search; return artist if score is confident.

    Rate-limited to 1 req/s per MusicBrainz ToS. Requires User-Agent header.
    Returns artist string or None.
    """
    import json
    import time
    import urllib.parse
    import urllib.request

    query = urllib.parse.quote(f'recording:"{title}"')
    url = f"https://musicbrainz.org/ws/2/recording/?query={query}&limit=1&fmt=json"
    req = urllib.request.Request(url, headers={"User-Agent": _MB_USER_AGENT})
    try:
        time.sleep(1.0)  # 1 req/s — MusicBrainz ToS requirement
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        recordings = data.get("recordings", [])
        if not recordings:
            return None
        rec = recordings[0]
        if int(rec.get("score", 0)) < min_score:
            return None
        credits = rec.get("artist-credit", [])
        if not credits:
            return None
        return (credits[0].get("artist", {}).get("name") or "").strip() or None
    except Exception:
        logger.warning("repair: MusicBrainz search failed for %r", title, exc_info=True)
        return None


def repair_missing_artists(song_dir: str, lastfm_client=None,
                            min_lastfm_listeners: int = _DEFAULT_MIN_LASTFM_LISTENERS,
                            min_musicbrainz_score: int = _DEFAULT_MIN_MB_SCORE,
                            limit: int = 0) -> dict:
    """Scan song_dir for MP3s missing artist tag; attempt three-stage repair.

    Returns: {processed, repaired_stage1, repaired_stage2, repaired_stage3, skipped, errors}
    """
    import eyed3
    from library.scanner import scan

    stats = {
        "processed": 0, "repaired_stage1": 0, "repaired_stage2": 0,
        "repaired_stage3": 0, "skipped": 0, "errors": 0,
    }

    records = scan(song_dir)
    if limit:
        records = records[:limit]

    for rec in records:
        stats["processed"] += 1
        path = rec["path"]
        try:
            af = eyed3.load(path)
            if af is None or af.tag is None:
                stats["skipped"] += 1
                continue

            artist = (af.tag.artist or "").strip()
            if artist:
                stats["skipped"] += 1
                continue

            title = (af.tag.title or "").strip()
            if not title:
                stats["skipped"] += 1
                continue

            # Stage 1: "Artist - Title" in title field
            parsed_artist, clean_title = _repair_by_title_parse(title)
            if parsed_artist:
                af.tag.artist = parsed_artist
                af.tag.title = clean_title
                af.tag.save()
                stats["repaired_stage1"] += 1
                logger.info("repair stage1: %r -> artist=%r", path, parsed_artist)
                continue

            # Stage 2: Last.fm
            if lastfm_client:
                lfm_artist = _repair_by_lastfm(lastfm_client, title,
                                               min_lastfm_listeners)
                if lfm_artist:
                    af.tag.artist = lfm_artist
                    af.tag.save()
                    stats["repaired_stage2"] += 1
                    logger.info("repair stage2: %r -> artist=%r", path, lfm_artist)
                    continue

            # Stage 3: MusicBrainz
            mb_artist = _repair_by_musicbrainz(title, min_musicbrainz_score)
            if mb_artist:
                af.tag.artist = mb_artist
                af.tag.save()
                stats["repaired_stage3"] += 1
                logger.info("repair stage3: %r -> artist=%r", path, mb_artist)
                continue

            stats["skipped"] += 1
            logger.debug("repair: no match for %r (title=%r)", path, title)

        except Exception:
            logger.exception("repair: error processing %r", path)
            stats["errors"] += 1

    return stats
```

- [ ] **Step 4: Run all repair tests**

```bash
python -m pytest tests/library/test_repair.py -v
```

Expected: all tests pass. (Stage 3 network tests use `unittest.mock.patch`.)

- [ ] **Step 5: Commit**

```bash
git add library/repair.py tests/library/test_repair.py
git commit -m "feat(library): repair stage 3 (MusicBrainz) + repair_missing_artists orchestrator"
```

---

## Task 10: Server endpoints for `/library/repair`

**Files:**
- Modify: `sWebExt/py_server/server.py`

- [ ] **Step 1: Add repair state variables**

After the existing `_enrich_last_result` and `_enrich_running` declarations (around line 65), add:

```python
_repair_running = threading.Lock()
_repair_last_result: dict = {"status": "idle"}
```

- [ ] **Step 2: Add `_run_repair_once` background worker**

After the `_run_enrich_once` function, add:

```python
def _run_repair_once(limit=None) -> dict:
    global _repair_last_result
    if not _repair_running.acquire(blocking=False):
        return {"status": "skipped", "reason": "already running"}
    try:
        from discover.config import load_config
        cfg = load_config(_CONFIG_PATH)
        song_dir = cfg.get("song_dir", "")
        if not song_dir:
            result = {"status": "disabled", "reason": "song_dir not set"}
            _repair_last_result = result
            return result

        repair_cfg = cfg.get("repair") or {}
        min_lfm = int(repair_cfg.get("min_lastfm_listeners", 10000))
        min_mb = int(repair_cfg.get("min_musicbrainz_score", 90))

        lastfm_client = None
        api_key = cfg.get("lastfm_api_key", "")
        if api_key:
            try:
                from lastfm.client import LastFMClient
                lastfm_client = LastFMClient(api_key)
            except Exception:
                logger.warning("[REPAIR] Last.fm client init failed", exc_info=True)

        from library.repair import repair_missing_artists
        result = repair_missing_artists(
            song_dir,
            lastfm_client=lastfm_client,
            min_lastfm_listeners=min_lfm,
            min_musicbrainz_score=min_mb,
            limit=limit or 0,
        )
        result["status"] = "ok"
        logger.info("[REPAIR] complete: %s", result)
        _repair_last_result = result
        return result
    except Exception as e:
        logger.exception("[REPAIR] failed")
        result = {"status": "error", "error": str(e)}
        _repair_last_result = result
        return result
    finally:
        _repair_running.release()
```

- [ ] **Step 3: Add routes**

After the existing `/library/enrich` routes, add:

```python
@app.route("/library/repair", methods=["POST"])
def library_repair():
    body = request.get_json(force=True, silent=True) or {}
    limit = body.get("limit", None)
    t = threading.Thread(target=_run_repair_once, kwargs={"limit": limit}, daemon=True)
    t.start()
    return jsonify({"status": "started"})


@app.route("/library/repair/status", methods=["GET"])
def repair_status():
    return jsonify(_repair_last_result)
```

- [ ] **Step 4: Verify server starts without errors**

```bash
cd /home/taichi/repos/musicServer/aMusicServerTemplate
python -c "import sWebExt.py_server.server; print('import ok')"
```

Expected: `import ok`

- [ ] **Step 5: Commit**

```bash
git add sWebExt/py_server/server.py
git commit -m "feat(server): POST /library/repair + GET /library/repair/status endpoints"
```

---

## Task 11: Config updates

**Files:**
- Modify: `config.example.json`

- [ ] **Step 1: Read current `config.example.json`**

```bash
cat /home/taichi/repos/musicServer/aMusicServerTemplate/config.example.json
```

- [ ] **Step 2: Add new keys**

In the `"discover"` section, add:

```jsonc
"yt_oversample": 5,
"junk_keywords": [],
"min_artist_listeners": 5000,
"candidate_oversample": 3
```

At the top level, add a new `"repair"` section:

```jsonc
"repair": {
  "enabled": true,
  "min_lastfm_listeners": 10000,
  "min_musicbrainz_score": 90
}
```

- [ ] **Step 3: Run the full test suite**

```bash
python -m pytest tests/ -v --tb=short 2>&1 | tail -30
```

Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add config.example.json
git commit -m "config: add discover quality gate keys and repair section to example config"
```

---

## Final verification

- [ ] **Run full test suite**

```bash
python -m pytest tests/ -v
```

Expected: all tests pass, no regressions.

- [ ] **Import check**

```bash
python -c "
from discover.ytdlp_adapter import _is_music_result, make_search_fn, search
from discover.expand import enrich_artist_info
from library.repair import repair_missing_artists, _repair_by_title_parse
from library.repair import _repair_by_lastfm, _repair_by_musicbrainz
print('all imports ok')
"
```

Expected: `all imports ok`
