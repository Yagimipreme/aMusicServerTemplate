# Discover Phase 1 — Engine + Weekly Mix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a headless engine that turns the user's owned artists into a weekly "Weekly Mix" playlist of *new* (not-yet-owned) tracks, downloaded into the local library and imported by Navidrome.

**Architecture:** A small `discover` Python package at the project root, composed of single-responsibility modules wired by an orchestrator (`engine.run_weekly`). Each module takes its external dependencies (Subsonic client, search fn, download fn) as **injected callables**, so every unit is testable without network or a browser. Impure I/O (yt-dlp, urllib) is isolated in thin adapters. The pipeline is: `seeds → expand → resolve → dedupe → acquire → assemble`. Reuses the existing `scripts/sTownload/script_web.py` downloader and the project's `.m3u`-in-`song_dir` playlist convention.

**Tech Stack:** Python 3 (stdlib `http.server`, `urllib`), `yt-dlp` (existing), `eyed3` (existing), `pytest` (new, dev-only). No new runtime dependencies.

---

## Design decisions (deviations from spec, with rationale)

- **Playlist via `.m3u`, not Subsonic `createPlaylist`.** The project already builds playlists as `.m3u` files in `song_dir` that Navidrome imports. `assemble` writes a *fresh* `Weekly Mix.m3u` each run (weekly-replace semantics) + triggers a scan. DRY and consistent; avoids needing Subsonic song-IDs.
- **Phase-1 `resolve` uses yt-dlp search only.** The Spotify-web/SoundCloud sources arrive in Phase 3. In Phase 1, for each not-owned similar artist we run `ytsearchN:"<artist>"`; each search hit *is* a candidate track (it already carries the downloadable URL + title). No external metadata source needed, fully self-contained.
- **Dependency injection everywhere.** Subsonic HTTP, yt-dlp search, and yt-dlp download are passed in as callables. Production wiring lives in adapters + server integration; tests pass fakes.

## File structure (created in this plan)

```
discover/                      # NEW package at project root (aMusicServerTemplate/discover/)
  __init__.py
  config.py                    # load_config(path) -> dict
  subsonic.py                  # Subsonic client (frequent artists, artist info, song-exists, scan)
  state.py                     # DiscoverState (suggested-track memory in discover_state.json)
  seeds.py                     # collect_seeds(subsonic, limit) -> [Artist]
  expand.py                    # expand_similar(subsonic, seeds, per_seed) -> [ScoredArtist]
  resolve.py                   # resolve_tracks(search_fn, artists, per_artist) -> [Candidate]
  dedupe.py                    # track_key(); filter_fresh(is_owned, state, candidates) -> [Candidate]
  acquire.py                   # acquire(download_fn, candidate) -> [mp3_path]
  assemble.py                  # write_weekly_mix(song_dir, mp3_paths, name) -> m3u_path
  engine.py                    # run_weekly(...) orchestrator
  ytdlp_adapter.py             # IMPURE real search_fn/download_fn (not unit-tested)
conftest.py                    # NEW at project root: puts repo root on sys.path for tests
tests/
  discover/
    test_config.py
    test_subsonic.py
    test_state.py
    test_seeds.py
    test_expand.py
    test_resolve.py
    test_dedupe.py
    test_assemble.py
    test_engine.py
requirements-dev.txt           # NEW: pytest
```

Modified at the end: `sWebExt/py_server/server.py` (add `POST /discover/run` route + weekly background thread).

## Data shapes (used across modules — keep names identical)

- `Artist` = `dict` with keys `id` (str), `name` (str).
- `ScoredArtist` = `dict` with keys `name` (str), `score` (int). (Not-owned artists have no usable library id.)
- `Candidate` = `dict` with keys `artist` (str), `title` (str), `url` (str).

---

## Task 0: Test harness + package skeleton

**Files:**
- Create: `requirements-dev.txt`
- Create: `conftest.py`
- Create: `discover/__init__.py`
- Create: `tests/discover/__init__.py`

- [ ] **Step 1: Add the dev requirement**

Create `requirements-dev.txt`:

```
pytest>=8.0
```

- [ ] **Step 2: Install pytest into the project venv**

Run: `uv pip install -r requirements-dev.txt`
Expected: pytest installs successfully (no error).

- [ ] **Step 3: Create the package + test package markers**

Create `discover/__init__.py`:

```python
"""Discover addon — local music-exploration engine (Phase 1: Weekly Mix)."""
```

Create `tests/discover/__init__.py` (empty file):

```python
```

- [ ] **Step 4: Make the repo root importable in tests**

Create `conftest.py` at the project root:

```python
import os
import sys

# Put the project root on sys.path so tests can `import discover.*`.
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
```

- [ ] **Step 5: Verify pytest collects nothing yet (sanity)**

Run: `python -m pytest tests/ -q`
Expected: "no tests ran" (exit code 5) — confirms collection works without errors.

- [ ] **Step 6: Commit**

```bash
git add requirements-dev.txt conftest.py discover/__init__.py tests/discover/__init__.py
git commit -m "chore(discover): test harness + package skeleton"
```

---

## Task 1: Config loader

**Files:**
- Create: `discover/config.py`
- Test: `tests/discover/test_config.py`

- [ ] **Step 1: Write the failing test**

Create `tests/discover/test_config.py`:

```python
import json
from discover.config import load_config


def test_load_config_reads_json(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"song_dir": "/music", "navidrome_user": "x"}))
    cfg = load_config(str(p))
    assert cfg["song_dir"] == "/music"
    assert cfg["navidrome_user"] == "x"


def test_load_config_missing_file_returns_empty(tmp_path):
    cfg = load_config(str(tmp_path / "nope.json"))
    assert cfg == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/discover/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'discover.config'`

- [ ] **Step 3: Write minimal implementation**

Create `discover/config.py`:

```python
import json
import os


def load_config(path: str) -> dict:
    """Load the project config.json, returning {} if absent or unreadable."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/discover/test_config.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add discover/config.py tests/discover/test_config.py
git commit -m "feat(discover): config loader"
```

---

## Task 2: Subsonic client

A thin client over Navidrome's Subsonic REST API. The HTTP fetch is injected (`fetch_json`) so tests never touch the network.

**Files:**
- Create: `discover/subsonic.py`
- Test: `tests/discover/test_subsonic.py`

- [ ] **Step 1: Write the failing test**

Create `tests/discover/test_subsonic.py`:

```python
from discover.subsonic import Subsonic


def make_client(responses):
    """responses: dict mapping a substring-of-URL -> parsed json dict."""
    def fake_fetch(url):
        for needle, payload in responses.items():
            if needle in url:
                return payload
        raise AssertionError(f"unexpected url: {url}")
    return Subsonic("http://nd:4533", "user", "pw", fetch_json=fake_fetch)


def test_get_frequent_artists_dedupes_and_keeps_order():
    payload = {"subsonic-response": {"status": "ok", "albumList2": {"album": [
        {"artist": "Boards of Canada", "artistId": "a1"},
        {"artist": "Aphex Twin", "artistId": "a2"},
        {"artist": "Boards of Canada", "artistId": "a1"},
    ]}}}
    c = make_client({"getAlbumList2": payload})
    artists = c.get_frequent_artists(size=50)
    assert artists == [
        {"id": "a1", "name": "Boards of Canada"},
        {"id": "a2", "name": "Aphex Twin"},
    ]


def test_get_artist_info2_returns_not_owned_similar():
    payload = {"subsonic-response": {"status": "ok", "artistInfo2": {"similarArtist": [
        {"id": "-1", "name": "Zmajor"},
        {"id": "b9", "name": "OwnedGuy"},
    ]}}}
    c = make_client({"getArtistInfo2": payload})
    sim = c.get_artist_info2("a1", count=20)
    assert {"id": "-1", "name": "Zmajor"} in sim
    assert {"id": "b9", "name": "OwnedGuy"} in sim


def test_song_exists_true_when_search_returns_song():
    payload = {"subsonic-response": {"status": "ok", "searchResult3": {"song": [
        {"id": "s1", "title": "Roygbiv", "artist": "Boards of Canada"},
    ]}}}
    c = make_client({"search3": payload})
    assert c.song_exists("Boards of Canada", "Roygbiv") is True


def test_song_exists_false_when_no_song():
    payload = {"subsonic-response": {"status": "ok", "searchResult3": {}}}
    c = make_client({"search3": payload})
    assert c.song_exists("Nobody", "Nothing") is False


def test_start_scan_returns_true_on_ok():
    payload = {"subsonic-response": {"status": "ok"}}
    c = make_client({"startScan": payload})
    assert c.start_scan() is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/discover/test_subsonic.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'discover.subsonic'`

- [ ] **Step 3: Write minimal implementation**

Create `discover/subsonic.py`:

```python
import json
import urllib.parse
import urllib.request

_API_VERSION = "1.16.1"
_CLIENT = "amusicserver-discover"


def _default_fetch_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


class Subsonic:
    """Minimal Navidrome/Subsonic client. HTTP is injected for testability."""

    def __init__(self, host, user, password, fetch_json=None):
        self.host = host.rstrip("/")
        self.user = user
        self.password = password
        self._fetch_json = fetch_json or _default_fetch_json

    def _url(self, view: str, **params) -> str:
        base = {
            "u": self.user, "p": self.password,
            "v": _API_VERSION, "c": _CLIENT, "f": "json",
        }
        base.update({k: v for k, v in params.items() if v is not None})
        return f"{self.host}/rest/{view}?{urllib.parse.urlencode(base)}"

    def _call(self, view: str, **params) -> dict:
        data = self._fetch_json(self._url(view, **params))
        return data.get("subsonic-response", {}) or {}

    def get_frequent_artists(self, size: int = 50):
        """Most-played albums -> ordered, de-duplicated artist list."""
        sr = self._call("getAlbumList2.view", type="frequent", size=size)
        albums = (sr.get("albumList2", {}) or {}).get("album", []) or []
        out, seen = [], set()
        for alb in albums:
            aid = alb.get("artistId")
            name = alb.get("artist")
            if not name or aid in seen:
                continue
            seen.add(aid)
            out.append({"id": aid, "name": name})
        return out

    def get_artist_info2(self, artist_id: str, count: int = 20):
        """Similar artists (includes not-owned, id == '-1')."""
        sr = self._call("getArtistInfo2.view", id=artist_id,
                        count=count, includeNotPresent="true")
        sim = (sr.get("artistInfo2", {}) or {}).get("similarArtist", []) or []
        return [{"id": s.get("id"), "name": s.get("name")} for s in sim if s.get("name")]

    def song_exists(self, artist: str, title: str) -> bool:
        sr = self._call("search3.view", query=f"{artist} {title}",
                        songCount=1, albumCount=0, artistCount=0)
        songs = (sr.get("searchResult3", {}) or {}).get("song", []) or []
        return len(songs) > 0

    def start_scan(self) -> bool:
        sr = self._call("startScan.view")
        return sr.get("status") == "ok"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/discover/test_subsonic.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add discover/subsonic.py tests/discover/test_subsonic.py
git commit -m "feat(discover): Subsonic client (frequent artists, similar, song-exists, scan)"
```

---

## Task 3: Suggestion state

Remembers track keys already suggested so weekly mixes don't repeat.

**Files:**
- Create: `discover/state.py`
- Test: `tests/discover/test_state.py`

- [ ] **Step 1: Write the failing test**

Create `tests/discover/test_state.py`:

```python
from discover.state import DiscoverState, load_state


def test_add_and_has(tmp_path):
    p = tmp_path / "discover_state.json"
    st = load_state(str(p))
    assert st.has("k1") is False
    st.add("k1")
    assert st.has("k1") is True


def test_persists_across_reload(tmp_path):
    p = tmp_path / "discover_state.json"
    st = load_state(str(p))
    st.add("k1")
    st.save()
    st2 = load_state(str(p))
    assert st2.has("k1") is True


def test_load_missing_file_is_empty(tmp_path):
    st = load_state(str(tmp_path / "nope.json"))
    assert st.has("anything") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/discover/test_state.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'discover.state'`

- [ ] **Step 3: Write minimal implementation**

Create `discover/state.py`:

```python
import json
import os


class DiscoverState:
    def __init__(self, path: str, suggested):
        self._path = path
        self._suggested = set(suggested)

    def has(self, key: str) -> bool:
        return key in self._suggested

    def add(self, key: str) -> None:
        self._suggested.add(key)

    def save(self) -> None:
        tmp = self._path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"suggested": sorted(self._suggested)}, f,
                      ensure_ascii=False, indent=2)
        os.replace(tmp, self._path)


def load_state(path: str) -> DiscoverState:
    suggested = []
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                suggested = json.load(f).get("suggested", []) or []
        except Exception:
            suggested = []
    return DiscoverState(path, suggested)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/discover/test_state.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add discover/state.py tests/discover/test_state.py
git commit -m "feat(discover): suggestion state (no-repeat memory)"
```

---

## Task 4: Seeds

**Files:**
- Create: `discover/seeds.py`
- Test: `tests/discover/test_seeds.py`

- [ ] **Step 1: Write the failing test**

Create `tests/discover/test_seeds.py`:

```python
from discover.seeds import collect_seeds


class FakeSubsonic:
    def __init__(self, artists):
        self._artists = artists
        self.last_size = None

    def get_frequent_artists(self, size=50):
        self.last_size = size
        return self._artists


def test_collect_seeds_returns_artists_capped_to_limit():
    fake = FakeSubsonic([
        {"id": "a1", "name": "BoC"},
        {"id": "a2", "name": "Aphex"},
        {"id": "a3", "name": "Plaid"},
    ])
    seeds = collect_seeds(fake, limit=2)
    assert seeds == [{"id": "a1", "name": "BoC"}, {"id": "a2", "name": "Aphex"}]


def test_collect_seeds_requests_at_least_limit_from_subsonic():
    fake = FakeSubsonic([])
    collect_seeds(fake, limit=10)
    assert fake.last_size >= 10
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/discover/test_seeds.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'discover.seeds'`

- [ ] **Step 3: Write minimal implementation**

Create `discover/seeds.py`:

```python
def collect_seeds(subsonic, limit: int = 20):
    """Ranked owned artists to seed discovery (most-played first)."""
    artists = subsonic.get_frequent_artists(size=max(limit, 50))
    return artists[:limit]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/discover/test_seeds.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add discover/seeds.py tests/discover/test_seeds.py
git commit -m "feat(discover): seed collection from owned artists"
```

---

## Task 5: Expand to not-owned similar artists

**Files:**
- Create: `discover/expand.py`
- Test: `tests/discover/test_expand.py`

- [ ] **Step 1: Write the failing test**

Create `tests/discover/test_expand.py`:

```python
from discover.expand import expand_similar


class FakeSubsonic:
    def __init__(self, mapping):
        self._mapping = mapping  # artist_id -> [similar artist dicts]

    def get_artist_info2(self, artist_id, count=20):
        return self._mapping.get(artist_id, [])


def test_expand_keeps_only_not_owned_and_scores_by_overlap():
    fake = FakeSubsonic({
        "a1": [{"id": "-1", "name": "Zmajor"}, {"id": "b1", "name": "Owned"}],
        "a2": [{"id": "-1", "name": "Zmajor"}, {"id": "-1", "name": "Rushex"}],
    })
    seeds = [{"id": "a1", "name": "BoC"}, {"id": "a2", "name": "Aphex"}]
    result = expand_similar(fake, seeds, per_seed=20)
    # Owned (id != -1) dropped; Zmajor seen twice -> top score.
    names = [r["name"] for r in result]
    assert "Owned" not in names
    assert result[0] == {"name": "Zmajor", "score": 2}
    assert {"name": "Rushex", "score": 1} in result


def test_expand_excludes_artists_already_seeds():
    fake = FakeSubsonic({"a1": [{"id": "-1", "name": "Aphex"}]})
    seeds = [{"id": "a1", "name": "BoC"}, {"id": "a2", "name": "Aphex"}]
    result = expand_similar(fake, seeds, per_seed=20)
    assert all(r["name"] != "Aphex" for r in result)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/discover/test_expand.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'discover.expand'`

- [ ] **Step 3: Write minimal implementation**

Create `discover/expand.py`:

```python
def _is_not_owned(artist: dict) -> bool:
    # Navidrome returns id == "-1" (or -1) for similar artists not in the library.
    return str(artist.get("id")) == "-1"


def expand_similar(subsonic, seeds, per_seed: int = 20):
    """Seeds -> scored not-owned similar artists (score = how many seeds suggested them)."""
    seed_names = {s["name"].casefold() for s in seeds}
    scores: dict[str, int] = {}
    for seed in seeds:
        for sim in subsonic.get_artist_info2(seed["id"], count=per_seed):
            name = sim.get("name")
            if not name or not _is_not_owned(sim):
                continue
            if name.casefold() in seed_names:
                continue
            scores[name] = scores.get(name, 0) + 1
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0].casefold()))
    return [{"name": n, "score": s} for n, s in ranked]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/discover/test_expand.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add discover/expand.py tests/discover/test_expand.py
git commit -m "feat(discover): expand seeds to scored not-owned similar artists"
```

---

## Task 6: Resolve artists to candidate tracks

`search_fn(artist_name, n)` returns a list of `{"title": ..., "url": ...}` (production wires it to yt-dlp search; tests inject a fake).

**Files:**
- Create: `discover/resolve.py`
- Test: `tests/discover/test_resolve.py`

- [ ] **Step 1: Write the failing test**

Create `tests/discover/test_resolve.py`:

```python
from discover.resolve import resolve_tracks


def test_resolve_tracks_calls_search_per_artist_and_flattens():
    calls = []

    def fake_search(name, n):
        calls.append((name, n))
        return [{"title": f"{name} song {i}", "url": f"http://y/{name}/{i}"}
                for i in range(n)]

    artists = [{"name": "Zmajor", "score": 2}, {"name": "Rushex", "score": 1}]
    out = resolve_tracks(fake_search, artists, per_artist=2)

    assert calls == [("Zmajor", 2), ("Rushex", 2)]
    assert len(out) == 4
    assert out[0] == {"artist": "Zmajor", "title": "Zmajor song 0", "url": "http://y/Zmajor/0"}


def test_resolve_tracks_skips_artist_when_search_errors():
    def flaky_search(name, n):
        if name == "Bad":
            raise RuntimeError("boom")
        return [{"title": "ok", "url": "http://y/ok"}]

    artists = [{"name": "Bad", "score": 1}, {"name": "Good", "score": 1}]
    out = resolve_tracks(flaky_search, artists, per_artist=1)
    assert out == [{"artist": "Good", "title": "ok", "url": "http://y/ok"}]


def test_resolve_tracks_drops_results_without_url():
    def search(name, n):
        return [{"title": "no url"}, {"title": "yes", "url": "http://y/1"}]

    out = resolve_tracks(search, [{"name": "A", "score": 1}], per_artist=5)
    assert out == [{"artist": "A", "title": "yes", "url": "http://y/1"}]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/discover/test_resolve.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'discover.resolve'`

- [ ] **Step 3: Write minimal implementation**

Create `discover/resolve.py`:

```python
import logging

logger = logging.getLogger(__name__)


def resolve_tracks(search_fn, artists, per_artist: int = 1):
    """For each artist, search for tracks; each hit becomes a Candidate.

    search_fn(artist_name, n) -> [{"title": str, "url": str}, ...]
    """
    out = []
    for artist in artists:
        name = artist["name"]
        try:
            hits = search_fn(name, per_artist)
        except Exception:
            logger.exception("resolve: search failed for %s", name)
            continue
        for hit in hits:
            url = hit.get("url")
            if not url:
                continue
            out.append({"artist": name, "title": hit.get("title", ""), "url": url})
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/discover/test_resolve.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add discover/resolve.py tests/discover/test_resolve.py
git commit -m "feat(discover): resolve artists to candidate tracks via injected search"
```

---

## Task 7: Dedupe / filter to fresh candidates

`is_owned(artist, title) -> bool` (production wires it to `Subsonic.song_exists`; tests inject a fake).

**Files:**
- Create: `discover/dedupe.py`
- Test: `tests/discover/test_dedupe.py`

- [ ] **Step 1: Write the failing test**

Create `tests/discover/test_dedupe.py`:

```python
from discover.dedupe import track_key, filter_fresh
from discover.state import DiscoverState


def test_track_key_is_normalized():
    assert track_key("Boards of Canada", "Roygbiv") == track_key("boards of canada", "  roygbiv ")


def test_filter_fresh_drops_owned_and_already_suggested():
    state = DiscoverState(path="/x", suggested={track_key("A", "owned-before")})
    owned = {("B", "in-library")}

    def is_owned(artist, title):
        return (artist, title) in owned

    candidates = [
        {"artist": "A", "title": "owned-before", "url": "u1"},  # in state -> drop
        {"artist": "B", "title": "in-library", "url": "u2"},    # owned -> drop
        {"artist": "C", "title": "brand-new", "url": "u3"},     # keep
    ]
    fresh = filter_fresh(is_owned, state, candidates)
    assert fresh == [{"artist": "C", "title": "brand-new", "url": "u3"}]


def test_filter_fresh_dedupes_within_batch():
    state = DiscoverState(path="/x", suggested=set())

    def is_owned(artist, title):
        return False

    candidates = [
        {"artist": "C", "title": "dup", "url": "u1"},
        {"artist": "c", "title": "  DUP ", "url": "u2"},  # same key, different casing/space
    ]
    fresh = filter_fresh(is_owned, state, candidates)
    assert len(fresh) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/discover/test_dedupe.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'discover.dedupe'`

- [ ] **Step 3: Write minimal implementation**

Create `discover/dedupe.py`:

```python
def track_key(artist: str, title: str) -> str:
    return f"{artist.strip().casefold()}|{title.strip().casefold()}"


def filter_fresh(is_owned, state, candidates):
    """Drop candidates already suggested (state), already owned, or duplicated in-batch."""
    fresh, seen = [], set()
    for c in candidates:
        key = track_key(c["artist"], c["title"])
        if key in seen or state.has(key):
            continue
        if is_owned(c["artist"], c["title"]):
            continue
        seen.add(key)
        fresh.append(c)
    return fresh
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/discover/test_dedupe.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add discover/dedupe.py tests/discover/test_dedupe.py
git commit -m "feat(discover): filter to fresh candidates (state + owned + in-batch dedupe)"
```

---

## Task 8: Acquire (download a candidate)

`download_fn(url) -> [mp3_path, ...]` (production wires it to the existing `scripts/sTownload/script_web.py:download`; tests inject a fake).

**Files:**
- Create: `discover/acquire.py`
- Test: `tests/discover/test_acquire.py`

> Add the test filename to the file list at the top if you track it; create `tests/discover/test_acquire.py`.

- [ ] **Step 1: Write the failing test**

Create `tests/discover/test_acquire.py`:

```python
from discover.acquire import acquire


def test_acquire_returns_downloaded_paths():
    def fake_download(url):
        assert url == "http://y/1"
        return ("ignored_title", ["/music/song.mp3"])

    paths = acquire(fake_download, {"artist": "A", "title": "t", "url": "http://y/1"})
    assert paths == ["/music/song.mp3"]


def test_acquire_returns_empty_on_download_error():
    def boom(url):
        raise RuntimeError("network")

    paths = acquire(boom, {"artist": "A", "title": "t", "url": "http://y/1"})
    assert paths == []


def test_acquire_handles_plain_list_return():
    def fake_download(url):
        return ["/music/a.mp3", "/music/b.mp3"]

    paths = acquire(fake_download, {"artist": "A", "title": "t", "url": "u"})
    assert paths == ["/music/a.mp3", "/music/b.mp3"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/discover/test_acquire.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'discover.acquire'`

- [ ] **Step 3: Write minimal implementation**

Create `discover/acquire.py`:

```python
import logging

logger = logging.getLogger(__name__)


def acquire(download_fn, candidate):
    """Download one candidate; return list of resulting mp3 paths ([] on failure).

    download_fn(url) may return either [paths] or (title, [paths]) — both handled.
    """
    try:
        result = download_fn(candidate["url"])
    except Exception:
        logger.exception("acquire: download failed for %s", candidate.get("url"))
        return []
    if isinstance(result, tuple):
        result = result[1]
    return list(result or [])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/discover/test_acquire.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add discover/acquire.py tests/discover/test_acquire.py
git commit -m "feat(discover): acquire (download a candidate via injected downloader)"
```

---

## Task 9: Assemble the Weekly Mix m3u

Writes a **fresh** `Weekly Mix.m3u` in `song_dir` (weekly-replace), entries as basenames (matching the existing m3u convention).

**Files:**
- Create: `discover/assemble.py`
- Test: `tests/discover/test_assemble.py`

- [ ] **Step 1: Write the failing test**

Create `tests/discover/test_assemble.py`:

```python
import os
from discover.assemble import write_weekly_mix


def test_write_weekly_mix_creates_m3u_with_basenames(tmp_path):
    song_dir = str(tmp_path)
    paths = [os.path.join(song_dir, "a.mp3"), os.path.join(song_dir, "b.mp3")]
    m3u = write_weekly_mix(song_dir, paths, name="Weekly Mix")

    assert os.path.basename(m3u) == "Weekly Mix.m3u"
    content = open(m3u, encoding="utf-8").read().splitlines()
    assert content[0] == "#EXTM3U"
    assert "a.mp3" in content
    assert "b.mp3" in content


def test_write_weekly_mix_overwrites_previous(tmp_path):
    song_dir = str(tmp_path)
    write_weekly_mix(song_dir, [os.path.join(song_dir, "old.mp3")], name="Weekly Mix")
    m3u = write_weekly_mix(song_dir, [os.path.join(song_dir, "new.mp3")], name="Weekly Mix")
    content = open(m3u, encoding="utf-8").read()
    assert "old.mp3" not in content
    assert "new.mp3" in content


def test_write_weekly_mix_sanitizes_name(tmp_path):
    m3u = write_weekly_mix(str(tmp_path), [], name="My/Bad:Name")
    assert os.path.basename(m3u) == "My_Bad_Name.m3u"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/discover/test_assemble.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'discover.assemble'`

- [ ] **Step 3: Write minimal implementation**

Create `discover/assemble.py`:

```python
import os
import re


def write_weekly_mix(song_dir: str, mp3_paths, name: str = "Weekly Mix") -> str:
    """Write a fresh .m3u listing the given tracks (basenames). Returns the m3u path."""
    safe = re.sub(r'[\\/:*?"<>|]', "_", name)
    m3u_path = os.path.join(song_dir, safe + ".m3u")
    lines = ["#EXTM3U"]
    for p in mp3_paths:
        lines.append(os.path.basename(p))
    with open(m3u_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return m3u_path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/discover/test_assemble.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add discover/assemble.py tests/discover/test_assemble.py
git commit -m "feat(discover): assemble fresh Weekly Mix m3u"
```

---

## Task 10: Engine orchestrator

Wires the pipeline. All external effects are injected via a small `deps` object so the whole flow is unit-testable with fakes.

**Files:**
- Create: `discover/engine.py`
- Test: `tests/discover/test_engine.py`

- [ ] **Step 1: Write the failing test**

Create `tests/discover/test_engine.py`:

```python
from types import SimpleNamespace
from discover.engine import run_weekly
from discover.state import DiscoverState


def build_deps(tmp_path, owned_titles=()):
    owned = set(owned_titles)

    subsonic = SimpleNamespace(
        get_frequent_artists=lambda size=50: [{"id": "a1", "name": "BoC"}],
        get_artist_info2=lambda artist_id, count=20: [
            {"id": "-1", "name": "Zmajor"}, {"id": "-1", "name": "Rushex"},
        ],
        song_exists=lambda artist, title: title in owned,
        start_scan=lambda: True,
    )

    def search_fn(name, n):
        return [{"title": f"{name} hit", "url": f"http://y/{name}"}]

    downloaded = []

    def download_fn(url):
        path = "/music/" + url.rsplit("/", 1)[-1] + ".mp3"
        downloaded.append(path)
        return (None, [path])

    state = DiscoverState(path=str(tmp_path / "state.json"), suggested=set())

    deps = SimpleNamespace(
        subsonic=subsonic,
        search_fn=search_fn,
        download_fn=download_fn,
        state=state,
        song_dir=str(tmp_path),
    )
    return deps, downloaded


def test_run_weekly_builds_playlist_and_records_state(tmp_path):
    deps, downloaded = build_deps(tmp_path)
    result = run_weekly(deps, count=2, seed_limit=5, per_seed=20, per_artist=1)

    # Two not-owned artists -> two candidates -> two downloads.
    assert len(downloaded) == 2
    assert result["acquired"] == 2
    # m3u written with both basenames.
    content = open(result["m3u"], encoding="utf-8").read()
    assert "BoC" not in content  # seed name, not a track filename
    assert content.count(".mp3") == 2
    # State now remembers them, so a second run acquires nothing new.
    deps2, downloaded2 = build_deps(tmp_path)
    deps2.state = deps.state  # carry forward in-memory state
    result2 = run_weekly(deps2, count=2, seed_limit=5, per_seed=20, per_artist=1)
    assert result2["acquired"] == 0


def test_run_weekly_respects_count_cap(tmp_path):
    deps, downloaded = build_deps(tmp_path)
    result = run_weekly(deps, count=1, seed_limit=5, per_seed=20, per_artist=1)
    assert result["acquired"] == 1
    assert len(downloaded) == 1


def test_run_weekly_triggers_scan(tmp_path):
    deps, _ = build_deps(tmp_path)
    scans = []
    deps.subsonic.start_scan = lambda: scans.append(True) or True
    run_weekly(deps, count=2, seed_limit=5, per_seed=20, per_artist=1)
    assert scans == [True]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/discover/test_engine.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'discover.engine'`

- [ ] **Step 3: Write minimal implementation**

Create `discover/engine.py`:

```python
import logging

from discover.seeds import collect_seeds
from discover.expand import expand_similar
from discover.resolve import resolve_tracks
from discover.dedupe import filter_fresh, track_key
from discover.acquire import acquire
from discover.assemble import write_weekly_mix

logger = logging.getLogger(__name__)


def run_weekly(deps, count=30, seed_limit=20, per_seed=20, per_artist=1,
               playlist_name="Weekly Mix"):
    """Run the full pipeline once and (re)build the Weekly Mix playlist.

    deps must provide: subsonic, search_fn, download_fn, state, song_dir.
    Returns {"acquired": int, "m3u": path|None}.
    """
    seeds = collect_seeds(deps.subsonic, limit=seed_limit)
    logger.info("discover: %d seeds", len(seeds))

    artists = expand_similar(deps.subsonic, seeds, per_seed=per_seed)
    logger.info("discover: %d not-owned similar artists", len(artists))

    candidates = resolve_tracks(deps.search_fn, artists, per_artist=per_artist)
    fresh = filter_fresh(deps.subsonic.song_exists, deps.state, candidates)
    logger.info("discover: %d fresh candidates", len(fresh))

    acquired_paths = []
    for c in fresh:
        if len(acquired_paths) >= count:
            break
        paths = acquire(deps.download_fn, c)
        if not paths:
            continue
        acquired_paths.extend(paths)
        deps.state.add(track_key(c["artist"], c["title"]))

    m3u = None
    if acquired_paths:
        m3u = write_weekly_mix(deps.song_dir, acquired_paths, name=playlist_name)
        deps.state.save()
        try:
            deps.subsonic.start_scan()
        except Exception:
            logger.exception("discover: scan trigger failed")

    return {"acquired": len(acquired_paths), "m3u": m3u}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/discover/test_engine.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Run the whole suite**

Run: `python -m pytest tests/ -q`
Expected: all tests pass (config, subsonic, state, seeds, expand, resolve, dedupe, acquire, assemble, engine).

- [ ] **Step 6: Commit**

```bash
git add discover/engine.py tests/discover/test_engine.py
git commit -m "feat(discover): engine orchestrator (seeds->expand->resolve->dedupe->acquire->assemble)"
```

---

## Task 11: yt-dlp adapter (impure wiring — not unit-tested)

Real `search_fn` and `download_fn`. `search_fn` uses yt-dlp's flat search; `download_fn` reuses the existing downloader.

**Files:**
- Create: `discover/ytdlp_adapter.py`

- [ ] **Step 1: Write the adapter**

Create `discover/ytdlp_adapter.py`:

```python
"""Impure adapters wiring the engine to yt-dlp and the existing downloader.

Not unit-tested (touches network/yt-dlp); exercised via the manual smoke test.
"""
import logging

logger = logging.getLogger(__name__)


def make_search_fn():
    """Return search_fn(artist_name, n) -> [{"title", "url"}] via yt-dlp flat search."""
    from yt_dlp import YoutubeDL

    def search_fn(artist_name, n):
        query = f"ytsearch{n}:{artist_name}"
        opts = {"quiet": True, "skip_download": True, "extract_flat": "in_playlist"}
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(query, download=False)
        entries = (info or {}).get("entries", []) or []
        out = []
        for e in entries:
            vid = e.get("id")
            url = e.get("url") or (f"https://www.youtube.com/watch?v={vid}" if vid else None)
            if url:
                out.append({"title": e.get("title", ""), "url": url})
        return out

    return search_fn


def make_download_fn(download_callable):
    """Wrap the existing scripts/sTownload/script_web.py:download into download_fn(url)."""
    def download_fn(url):
        return download_callable(url)
    return download_fn
```

- [ ] **Step 2: Verify it imports (syntax/structure only)**

Run: `python -c "import discover.ytdlp_adapter as a; print(hasattr(a, 'make_search_fn') and hasattr(a, 'make_download_fn'))"`
Expected: prints `True`

- [ ] **Step 3: Commit**

```bash
git add discover/ytdlp_adapter.py
git commit -m "feat(discover): yt-dlp adapter for real search/download wiring"
```

---

## Task 12: Server integration — route + weekly thread

Adds `POST /discover/run` (manual trigger) and a weekly background thread, wiring real deps. Mirrors the existing SC-refresher thread + path-routing patterns in `server.py`.

**Files:**
- Modify: `sWebExt/py_server/server.py`

- [ ] **Step 1: Add a deps builder + runner near the top of `server.py`**

Insert after the logging block (after line ~31, before `class SimpleHandler`):

```python
# ── Discover engine wiring ─────────────────────────────────────────────────────

def _build_discover_deps():
    """Assemble engine dependencies from config + existing downloader. Returns deps or None."""
    from types import SimpleNamespace

    sys.path.insert(0, _PROJECT_ROOT)  # make `discover` importable
    from discover.config import load_config
    from discover.subsonic import Subsonic
    from discover.state import load_state
    from discover.ytdlp_adapter import make_search_fn, make_download_fn

    cfg = load_config(_CONFIG_PATH)
    host = cfg.get("navidrome_url", "")
    user = cfg.get("navidrome_user", "")
    pw = cfg.get("navidrome_pass", "")
    if not host or not user or not pw:
        logger.warning("[DISCOVER] navidrome creds missing — engine disabled")
        return None

    # Reuse the existing YouTube downloader's download() + song_dir.
    dl_path = os.path.join(_PROJECT_ROOT, "scripts/sTownload/script_web.py")
    spec = importlib.util.spec_from_file_location("sTownload_web", dl_path)
    dl_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dl_mod)
    song_dir = dl_mod.get_config_song_dir()

    state_path = os.path.join(_PROJECT_ROOT, "discover_state.json")
    return SimpleNamespace(
        subsonic=Subsonic(host, user, pw),
        search_fn=make_search_fn(),
        download_fn=make_download_fn(dl_mod.download),
        state=load_state(state_path),
        song_dir=song_dir,
    )


def _run_discover_once():
    """Build deps and run one weekly pass. Best-effort; logs and swallows errors."""
    try:
        deps = _build_discover_deps()
        if deps is None:
            return {"status": "disabled", "reason": "navidrome creds missing"}
        from discover.engine import run_weekly
        cfg = {}
        try:
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            pass
        count = (cfg.get("discover") or {}).get("weekly_count", 30)
        result = run_weekly(deps, count=count)
        logger.info("[DISCOVER] run complete: %s", result)
        return {"status": "ok", **result}
    except Exception as e:
        logger.exception("[DISCOVER] run failed")
        return {"status": "error", "error": str(e)}


def _discover_weekly_loop(period_seconds=7 * 24 * 3600):
    while True:
        logger.info("[DISCOVER] weekly cycle starting")
        _run_discover_once()
        time.sleep(period_seconds)
```

- [ ] **Step 2: Add the `POST /discover/run` route**

In `do_POST`, immediately after `post_data = self.rfile.read(content_length)` (line ~50), add a path check before the JSON body parsing:

```python
        if self.path.rstrip('/') == '/discover/run':
            result = _run_discover_once()
            code = 200 if result.get("status") in ("ok", "disabled") else 500
            self._set_headers(code)
            self.wfile.write(json.dumps(result).encode())
            return
```

- [ ] **Step 3: Start the weekly thread in `start_background_server`**

In `start_background_server`, after the SC-refresher thread is started (after line ~265), add:

```python
    t_disc = threading.Thread(target=_discover_weekly_loop, daemon=True)
    t_disc.start()
    logger.info('Started background discover weekly thread')
```

- [ ] **Step 4: Smoke-test the route locally (manual)**

Run the server, then:

Run: `python -c "import sWebExt.py_server.server as s" 2>/dev/null; echo started`
Then in a separate shell, with the server running on :5000:
Run: `curl -s -X POST http://localhost:5000/discover/run | head -c 400`
Expected: a JSON object with `"status"` of `ok` (playlist built / acquired N), or `disabled` (if Navidrome creds absent), or `error` with a message. Confirm `logs/server.log` shows `[DISCOVER]` lines.

> Note: a full `ok` run requires Navidrome reachable + yt-dlp + ffmpeg. If running where those are unavailable, `disabled`/`error` is acceptable for this step; the unit suite is the correctness gate.

- [ ] **Step 5: Commit**

```bash
git add sWebExt/py_server/server.py
git commit -m "feat(discover): server route POST /discover/run + weekly background thread"
```

---

## Task 13: Documentation + config example

**Files:**
- Modify: `config.example.json`
- Modify: `README.md`

- [ ] **Step 1: Add the optional `discover` block to `config.example.json`**

Add this key to the JSON object (keep valid JSON — add a comma after the previous last entry):

```json
  "discover": {
    "enabled": true,
    "weekly_count": 30,
    "playlist_name": "Weekly Mix"
  }
```

- [ ] **Step 2: Document the feature in `README.md`**

Add a section near the playlist docs:

```markdown
## Weekly Mix (automatic discovery)

The server can build a **Weekly Mix** playlist of artists you don't own yet, based on
who's similar to the artists you already play. It uses Navidrome's similar-artist data,
downloads a handful of tracks from the new artists, and writes a `Weekly Mix.m3u` that
Navidrome imports — so the mix shows up in Symfonium with no effort.

- It runs automatically once a week.
- Trigger it now: `curl -X POST http://localhost:5000/discover/run`
- Requires Navidrome credentials in `config.json` and its Last.fm agent enabled
  (Navidrome → Settings; without it there are no similar artists to discover).
- Tune the size in `config.json` under `"discover": { "weekly_count": 30 }`.
```

- [ ] **Step 3: Run the full suite one more time**

Run: `python -m pytest tests/ -q`
Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add config.example.json README.md
git commit -m "docs(discover): document Weekly Mix + config example"
```

---

## Self-review (completed during planning)

**Spec coverage (Phase 1 rows of the spec):**
- `seeds` (owned artists) → Task 4 ✅
- `expand_canonical` (getArtistInfo2 not-owned) → Task 5 ✅
- `resolve` (artist→tracks; Phase-1 yt-dlp search) → Task 6 + Task 11 ✅
- `filter` (dedupe vs library + state) → Task 7 + Task 3 ✅
- `acquire` (reuse existing yt-dlp pipeline) → Task 8 + Task 11 ✅
- `assemble` (Weekly Mix; m3u per design decision) → Task 9 ✅
- `schedule` (weekly via existing background thread) → Task 12 ✅
- Config keys (`discover` block) + graceful "creds missing" + "enable Last.fm agent" hint → Task 12/13 ✅
- `discover_state.json` no-repeat → Task 3 + engine ✅
- Testing strategy (mocked deps per unit) → every task ✅

**Placeholder scan:** No TBD/TODO; every code step contains complete code; every command has expected output.

**Type consistency:** `Candidate` keys (`artist`/`title`/`url`) are identical across resolve, dedupe, acquire, engine. `Artist` keys (`id`/`name`) identical across subsonic/seeds/expand. `deps` attributes (`subsonic`, `search_fn`, `download_fn`, `state`, `song_dir`) match between Task 10 test, Task 10 engine, and Task 12 wiring. `Subsonic` method names (`get_frequent_artists`, `get_artist_info2`, `song_exists`, `start_scan`) consistent across Tasks 2, 4, 5, 10, 12.

**Known Phase-1 limitation (by design):** `resolve` quality is bounded by yt-dlp search (no Spotify-web/SoundCloud sources yet — those are Phase 3). Acceptable: Phase 1's job is a working end-to-end pipeline.
