# Follow Artists → NEW RELEASES Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user follow specific artists by MusicBrainz ID, auto-detect their new releases via the ListenBrainz fresh-releases feed, auto-download the new tracks through the existing yt-dlp/SoundCloud pipeline, collect them into a "NEW RELEASES" playlist, and surface them in an in-app feed plus optional webhook/ntfy push.

**Architecture:** Approach C — a new, self-contained `follow/` package owns the genuinely new logic (follow-list store, MusicBrainz resolution, ListenBrainz polling, detection, notification, run orchestration). It reuses existing infrastructure for everything downstream: `discover/resolve.py::resolve_tracks`, `discover/acquire.py::acquire`, `discover/assemble.py::write_weekly_mix`, the `_build_discover_deps()` dependency bundle, and a dedicated daemon scheduler thread modeled on the existing `_dedup_scheduled_loop`.

**Tech Stack:** Python 3.8+, Flask, `requests` (MB/LB HTTP), pytest, vanilla-JS SPA (SIGNAL design).

---

## Reuse reference (read before starting — these already exist and MUST NOT be reimplemented)

- `discover/resolve.py::resolve_tracks(search_fn, artists, per_artist=1)` — `artists` is a list of dicts `{"name": str, "top_track": str?}`; `search_fn(name, n, track_hint=…)` returns `[{"title", "url"}]`. Returns candidates `[{"artist", "title", "url"}]`. **We pass `top_track` = the release/track title so the YouTube query becomes "Artist <title>".**
- `discover/acquire.py::acquire(download_fn, candidate)` — downloads one candidate dict (`{"artist","title","url",…}`), returns `[mp3_path, …]` (relative paths) or `[]` on failure.
- `discover/assemble.py::write_weekly_mix(song_dir, mp3_paths, name="Weekly Mix", cap=100)` — appends basenames to `<name>.m3u` (sliding window), returns m3u path.
- `sWebExt/py_server/server.py::_build_discover_deps()` — returns a `SimpleNamespace(subsonic, search_fn, download_fn, state, song_dir, lastfm_client)` or `None` if Navidrome creds are missing. **The follow runner reuses `deps.search_fn`, `deps.download_fn`, `deps.song_dir`.**
- `lastfm/client.py` — copy its token-bucket + typed-exception structure for the MusicBrainz client (1 req/s).
- `_PROJECT_ROOT` (server.py:25) — repo root; state/follows files live here next to `discover_state.json`.
- Existing daemon-thread pattern: `_dedup_scheduled_loop`, `_refresh_sc_client_id_loop`, `_mix_scheduler_loop` started near server.py:1769–1778.

**Deviation from spec (intentional):** the spec said "reuse `_mix_scheduler_loop`." Instead we add a *dedicated* `_follow_scheduler_loop` daemon thread. Rationale: the existing codebase already isolates separate recurring concerns into their own daemon loops (dedup, SC-refresh), the profile loop is profile-shaped, and a separate loop matches Approach C's isolation goal with lower risk. Functionally identical (daily run at `follow.run_hour`).

## File structure

**New files:**
- `follow/__init__.py` — empty package marker.
- `follow/store.py` — `follows.json` read/write (artist list).
- `follow/musicbrainz.py` — MusicBrainz client (search, release-groups, tracks).
- `follow/listenbrainz.py` — ListenBrainz fresh-releases client.
- `follow/fstate.py` — follow runtime state (`follow_state.json`) load/save + mutation helpers.
- `follow/detect.py` — pure detection logic (feed-filter + backfill + scope mapping).
- `follow/notify.py` — feed append, unseen count, webhook/ntfy push.
- `follow/runner.py` — one-run orchestration wiring detect → reuse acquisition → playlist → notify → state.
- `tests/follow/__init__.py`, `tests/follow/conftest.py`, and one `test_*.py` per module.

**Modified files:**
- `sWebExt/py_server/server.py` — follow routes, `follow` config defaults injection, `_follow_scheduler_loop`, thread start.
- `web/templates/app.html` — Follows screen markup + nav entry.
- `web/static/app.js` — Follows screen logic + router entry + nav badge.
- `web/static/app.css` — minimal Follows styles (reuse existing tokens/components).
- `config.example.json` — add `follow` block.
- `.gitignore` — add `follow_state.json`, `follows.json`.

**Module name note:** the runtime-state module is `follow/fstate.py` (not `state.py`) to avoid confusion with `discover/state.py`.

---

## Task 1: Package skeleton + follows.json store

**Files:**
- Create: `follow/__init__.py` (empty)
- Create: `follow/store.py`
- Create: `tests/follow/__init__.py` (empty)
- Create: `tests/follow/conftest.py`
- Test: `tests/follow/test_store.py`

- [ ] **Step 1: Create empty package markers**

Create `follow/__init__.py` and `tests/follow/__init__.py` as empty files.

- [ ] **Step 2: Write conftest with a tmp-path helper**

`tests/follow/conftest.py`:

```python
import pytest


@pytest.fixture
def follows_path(tmp_path):
    return str(tmp_path / "follows.json")


@pytest.fixture
def state_path(tmp_path):
    return str(tmp_path / "follow_state.json")
```

- [ ] **Step 3: Write the failing test**

`tests/follow/test_store.py`:

```python
from follow import store


def test_list_follows_missing_file_returns_empty(follows_path):
    assert store.list_follows(follows_path) == []


def test_add_follow_then_list(follows_path):
    store.add_follow(follows_path, mbid="abc", name="Massive Attack",
                     disambiguation="Bristol trip-hop")
    got = store.list_follows(follows_path)
    assert len(got) == 1
    assert got[0]["mbid"] == "abc"
    assert got[0]["name"] == "Massive Attack"
    assert got[0]["disambiguation"] == "Bristol trip-hop"
    assert "followed_at" in got[0]


def test_add_follow_is_idempotent_by_mbid(follows_path):
    store.add_follow(follows_path, mbid="abc", name="A", disambiguation="")
    store.add_follow(follows_path, mbid="abc", name="A (dup)", disambiguation="")
    got = store.list_follows(follows_path)
    assert len(got) == 1


def test_remove_follow(follows_path):
    store.add_follow(follows_path, mbid="abc", name="A", disambiguation="")
    store.add_follow(follows_path, mbid="def", name="B", disambiguation="")
    store.remove_follow(follows_path, "abc")
    got = store.list_follows(follows_path)
    assert [g["mbid"] for g in got] == ["def"]
```

- [ ] **Step 4: Run test to verify it fails**

Run: `python -m pytest tests/follow/test_store.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'follow.store'`)

- [ ] **Step 5: Implement `follow/store.py`**

```python
"""Followed-artist list persisted to follows.json (separate from config.json).

Schema: {"artists": [{"mbid", "name", "disambiguation", "followed_at"}]}
Atomic writes via .tmp + os.replace under a module lock.
"""
import datetime
import json
import os
import threading

_lock = threading.RLock()


def _load(path: str) -> dict:
    if not os.path.exists(path):
        return {"artists": []}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not isinstance(data.get("artists"), list):
            return {"artists": []}
        return data
    except Exception:
        return {"artists": []}


def _save(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def list_follows(path: str) -> list:
    return _load(path)["artists"]


def add_follow(path: str, mbid: str, name: str, disambiguation: str = "") -> None:
    with _lock:
        data = _load(path)
        if any(a.get("mbid") == mbid for a in data["artists"]):
            return
        data["artists"].append({
            "mbid": mbid,
            "name": name,
            "disambiguation": disambiguation,
            "followed_at": datetime.datetime.now().isoformat(),
        })
        _save(path, data)


def remove_follow(path: str, mbid: str) -> None:
    with _lock:
        data = _load(path)
        data["artists"] = [a for a in data["artists"] if a.get("mbid") != mbid]
        _save(path, data)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/follow/test_store.py -v`
Expected: PASS (4 tests)

- [ ] **Step 7: Commit**

```bash
git add follow/__init__.py follow/store.py tests/follow/
git commit -m "feat(follow): follows.json store for followed-artist list"
```

---

## Task 2: MusicBrainz client

**Files:**
- Create: `follow/musicbrainz.py`
- Test: `tests/follow/test_musicbrainz.py`

MusicBrainz JSON API, no key, **1 req/s** required, descriptive `User-Agent` required. Endpoints:
- Search: `GET https://musicbrainz.org/ws/2/artist?query=artist:"NAME"&fmt=json&limit=N`
- Release-groups: `GET https://musicbrainz.org/ws/2/release-group?artist=MBID&fmt=json&limit=N`
- Release-group browse includes `first-release-date`, `primary-type`, `title`, `id`.
- Tracks: resolve a release-group to a release, then its recordings:
  `GET https://musicbrainz.org/ws/2/release?release-group=RGID&fmt=json&inc=recordings&limit=1`
  → `releases[0].media[*].tracks[*].title`.

- [ ] **Step 1: Write the failing test (parsing with a fake session)**

`tests/follow/test_musicbrainz.py`:

```python
from follow import musicbrainz as mb


class FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.exceptions.HTTPError(f"{self.status_code}")

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, payloads):
        # payloads: list popped in call order
        self._payloads = list(payloads)
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, params))
        return FakeResp(self._payloads.pop(0))


def test_search_artist_parses_candidates():
    payload = {"artists": [
        {"id": "mbid-1", "name": "Massive Attack",
         "disambiguation": "Bristol trip-hop", "score": 100},
        {"id": "mbid-2", "name": "Massive Attack Tribute", "score": 60},
    ]}
    client = mb.MusicBrainzClient(session=FakeSession([payload]), min_interval=0)
    got = client.search_artist("Massive Attack", limit=5)
    assert got[0] == {"mbid": "mbid-1", "name": "Massive Attack",
                      "disambiguation": "Bristol trip-hop", "score": 100}
    assert got[1]["disambiguation"] == ""  # missing field defaults to ""


def test_get_release_groups_parses():
    payload = {"release-groups": [
        {"id": "rg-1", "title": "Mezzanine",
         "first-release-date": "1998-04-20", "primary-type": "Album"},
        {"id": "rg-2", "title": "Ritual Spirit",
         "first-release-date": "2016-01-28", "primary-type": "EP"},
    ]}
    client = mb.MusicBrainzClient(session=FakeSession([payload]), min_interval=0)
    got = client.get_release_groups("mbid-1", limit=100)
    assert got[0] == {"rg_mbid": "rg-1", "title": "Mezzanine",
                      "first_release_date": "1998-04-20", "primary_type": "Album"}


def test_get_release_tracks_parses():
    payload = {"releases": [
        {"media": [{"tracks": [{"title": "Angel"}, {"title": "Risingson"}]}]}
    ]}
    client = mb.MusicBrainzClient(session=FakeSession([payload]), min_interval=0)
    got = client.get_release_tracks("rg-1")
    assert got == ["Angel", "Risingson"]


def test_get_release_tracks_empty_when_no_releases():
    client = mb.MusicBrainzClient(session=FakeSession([{"releases": []}]), min_interval=0)
    assert client.get_release_tracks("rg-x") == []


def test_user_agent_header_sent():
    fake = FakeSession([{"artists": []}])
    client = mb.MusicBrainzClient(session=fake, min_interval=0)
    client.search_artist("x")
    _, params = fake.calls[0]
    # header is asserted via a separate capture below
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/follow/test_musicbrainz.py -v`
Expected: FAIL (`No module named 'follow.musicbrainz'`)

- [ ] **Step 3: Implement `follow/musicbrainz.py`**

```python
"""MusicBrainz JSON API client.

No API key. MusicBrainz requires <=1 req/s and a descriptive User-Agent.
Mirrors lastfm/client.py structure (instance-level rate limiter + typed errors).
"""
import logging
import threading
import time

import requests

logger = logging.getLogger(__name__)

_BASE = "https://musicbrainz.org/ws/2"
_TIMEOUT = 10
_USER_AGENT = "aMusicServer/1.0 (https://github.com/Yagimipreme/aMusicServer)"


class MBError(Exception):
    pass


class MBTimeout(MBError):
    pass


class MusicBrainzClient:
    def __init__(self, session=None, min_interval: float = 1.0):
        self._session = session or requests.Session()
        self._min_interval = min_interval
        self._last = 0.0
        self._lock = threading.Lock()

    def _throttle(self):
        if self._min_interval <= 0:
            return
        with self._lock:
            wait = self._min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()

    def _get(self, path: str, params: dict) -> dict:
        self._throttle()
        full = {"fmt": "json", **params}
        try:
            resp = self._session.get(
                f"{_BASE}/{path}", params=full,
                headers={"User-Agent": _USER_AGENT}, timeout=_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.Timeout as exc:
            raise MBTimeout("MusicBrainz timed out") from exc
        except requests.exceptions.RequestException as exc:
            raise MBTimeout(f"MusicBrainz network error: {exc}") from exc

    def search_artist(self, name: str, limit: int = 5) -> list:
        data = self._get("artist", {"query": f'artist:"{name}"', "limit": limit})
        out = []
        for a in data.get("artists", []) or []:
            out.append({
                "mbid": a.get("id", ""),
                "name": a.get("name", ""),
                "disambiguation": a.get("disambiguation", "") or "",
                "score": a.get("score", 0),
            })
        return out

    def get_release_groups(self, artist_mbid: str, limit: int = 100) -> list:
        data = self._get("release-group",
                         {"artist": artist_mbid, "limit": limit})
        out = []
        for rg in data.get("release-groups", []) or []:
            out.append({
                "rg_mbid": rg.get("id", ""),
                "title": rg.get("title", ""),
                "first_release_date": rg.get("first-release-date", "") or "",
                "primary_type": rg.get("primary-type", "") or "",
            })
        return out

    def get_release_tracks(self, rg_mbid: str) -> list:
        data = self._get("release",
                         {"release-group": rg_mbid, "inc": "recordings", "limit": 1})
        releases = data.get("releases", []) or []
        if not releases:
            return []
        titles = []
        for medium in releases[0].get("media", []) or []:
            for track in medium.get("tracks", []) or []:
                if track.get("title"):
                    titles.append(track["title"])
        return titles
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/follow/test_musicbrainz.py -v`
Expected: PASS (drop/adjust the placeholder `test_user_agent_header_sent` if not asserting — keep it only if you extend FakeSession to capture headers; otherwise delete it.)

- [ ] **Step 5: Commit**

```bash
git add follow/musicbrainz.py tests/follow/test_musicbrainz.py
git commit -m "feat(follow): MusicBrainz client (search, release-groups, tracks)"
```

---

## Task 3: ListenBrainz fresh-releases client

**Files:**
- Create: `follow/listenbrainz.py`
- Test: `tests/follow/test_listenbrainz.py`

Endpoint: `GET https://api.listenbrainz.org/1/explore/fresh-releases/?release_date=YYYY-MM-DD&days=N&past=true&future=false`. Response: `{"payload": {"releases": [ {artist_credit_name, artist_mbids, release_date, release_group_mbid, release_name, release_group_primary_type} ]}}`.

- [ ] **Step 1: Write the failing test**

`tests/follow/test_listenbrainz.py`:

```python
from follow import listenbrainz as lb


class FakeResp:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, payload):
        self._payload = payload
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, params))
        return FakeResp(self._payload)


def test_fresh_releases_parses_and_passes_params():
    payload = {"payload": {"releases": [
        {"artist_credit_name": "Massive Attack",
         "artist_mbids": ["mbid-1"],
         "release_date": "2026-06-12",
         "release_group_mbid": "rg-9",
         "release_name": "New Thing",
         "release_group_primary_type": "Single"},
    ]}}
    fake = FakeSession(payload)
    client = lb.ListenBrainzClient(session=fake)
    got = client.fresh_releases(pivot_date="2026-06-14", days=7, past=True)
    assert got[0] == {
        "artist_mbids": ["mbid-1"],
        "release_date": "2026-06-12",
        "release_group_mbid": "rg-9",
        "release_name": "New Thing",
        "primary_type": "Single",
        "artist_name": "Massive Attack",
    }
    _, params = fake.calls[0]
    assert params["release_date"] == "2026-06-14"
    assert params["days"] == 7
    assert params["past"] == "true"
    assert params["future"] == "false"


def test_fresh_releases_handles_missing_payload():
    client = lb.ListenBrainzClient(session=FakeSession({}))
    assert client.fresh_releases(pivot_date="2026-06-14", days=7) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/follow/test_listenbrainz.py -v`
Expected: FAIL (`No module named 'follow.listenbrainz'`)

- [ ] **Step 3: Implement `follow/listenbrainz.py`**

```python
"""ListenBrainz fresh-releases client (global new-release feed, no API key)."""
import logging

import requests

logger = logging.getLogger(__name__)

_URL = "https://api.listenbrainz.org/1/explore/fresh-releases/"
_TIMEOUT = 10
_USER_AGENT = "aMusicServer/1.0 (https://github.com/Yagimipreme/aMusicServer)"


class ListenBrainzClient:
    def __init__(self, session=None):
        self._session = session or requests.Session()

    def fresh_releases(self, pivot_date: str, days: int = 7,
                       past: bool = True, future: bool = False) -> list:
        params = {
            "release_date": pivot_date,
            "days": days,
            "past": "true" if past else "false",
            "future": "true" if future else "false",
        }
        try:
            resp = self._session.get(
                _URL, params=params,
                headers={"User-Agent": _USER_AGENT}, timeout=_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.RequestException as exc:
            logger.warning("ListenBrainz fetch failed: %s", exc)
            return []
        releases = ((data or {}).get("payload") or {}).get("releases") or []
        out = []
        for r in releases:
            out.append({
                "artist_mbids": r.get("artist_mbids", []) or [],
                "release_date": r.get("release_date", "") or "",
                "release_group_mbid": r.get("release_group_mbid", "") or "",
                "release_name": r.get("release_name", "") or "",
                "primary_type": r.get("release_group_primary_type", "") or "",
                "artist_name": r.get("artist_credit_name", "") or "",
            })
        return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/follow/test_listenbrainz.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add follow/listenbrainz.py tests/follow/test_listenbrainz.py
git commit -m "feat(follow): ListenBrainz fresh-releases client"
```

---

## Task 4: Follow runtime state (follow_state.json)

**Files:**
- Create: `follow/fstate.py`
- Test: `tests/follow/test_fstate.py`

State shape (see spec §1). Key operations: load, has_acquired, mark_acquired, is_backfilled, mark_backfilled, pending add/get/bump/drop, append_feed (caps at 200, bumps unseen_count), mark_seen, summary, save.

- [ ] **Step 1: Write the failing test**

`tests/follow/test_fstate.py`:

```python
from follow import fstate


def test_load_missing_returns_empty(state_path):
    st = fstate.load(state_path)
    assert st.has_acquired("rg-1") is False
    assert st.is_backfilled("mbid-1") is False
    assert st.summary()["unseen_count"] == 0


def test_mark_acquired_persists(state_path):
    st = fstate.load(state_path)
    st.mark_acquired("rg-1")
    st.save()
    st2 = fstate.load(state_path)
    assert st2.has_acquired("rg-1") is True


def test_backfill_marker(state_path):
    st = fstate.load(state_path)
    st.mark_backfilled("mbid-1")
    st.save()
    assert fstate.load(state_path).is_backfilled("mbid-1") is True


def test_append_feed_bumps_unseen(state_path):
    st = fstate.load(state_path)
    st.append_feed({"artist": "A", "title": "T", "release_name": "R",
                    "release_date": "2026-06-12", "primary_type": "Single",
                    "status": "acquired"})
    assert st.summary()["unseen_count"] == 1
    assert st.feed()[0]["artist"] == "A"
    assert "ts" in st.feed()[0]


def test_mark_seen_resets_unseen(state_path):
    st = fstate.load(state_path)
    st.append_feed({"artist": "A", "title": "T", "release_name": "R",
                    "release_date": "", "primary_type": "Single", "status": "acquired"})
    st.mark_seen()
    assert st.summary()["unseen_count"] == 0


def test_feed_capped_at_200(state_path):
    st = fstate.load(state_path)
    for i in range(205):
        st.append_feed({"artist": f"A{i}", "title": "T", "release_name": "R",
                        "release_date": "", "primary_type": "Single",
                        "status": "acquired"})
    assert len(st.feed()) == 200
    assert st.feed()[0]["artist"] == "A5"   # oldest 5 dropped


def test_pending_add_bump_drop(state_path):
    st = fstate.load(state_path)
    st.add_pending("rg-1", "A", "T")
    assert st.pending()[0]["attempts"] == 1
    st.bump_pending("rg-1")
    assert st.pending()[0]["attempts"] == 2
    st.drop_pending("rg-1")
    assert st.pending() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/follow/test_fstate.py -v`
Expected: FAIL (`No module named 'follow.fstate'`)

- [ ] **Step 3: Implement `follow/fstate.py`**

```python
"""Runtime state for the follow feature (follow_state.json).

Unlike discover/state.py, acquired_release_groups never expires — a release
must never be re-downloaded.
"""
import datetime
import json
import os
import threading

_FEED_CAP = 200
_lock = threading.RLock()


class FollowState:
    def __init__(self, path, data):
        self._path = path
        self._d = data

    # ── acquired (idempotency) ──
    def has_acquired(self, rg_mbid: str) -> bool:
        return rg_mbid in self._d["acquired_release_groups"]

    def mark_acquired(self, rg_mbid: str) -> None:
        self._d["acquired_release_groups"][rg_mbid] = datetime.datetime.now().isoformat()

    # ── backfill markers ──
    def is_backfilled(self, mbid: str) -> bool:
        return mbid in self._d["backfilled_mbids"]

    def mark_backfilled(self, mbid: str) -> None:
        if mbid not in self._d["backfilled_mbids"]:
            self._d["backfilled_mbids"].append(mbid)

    # ── pending (retry) ──
    def pending(self) -> list:
        return self._d["pending"]

    def add_pending(self, rg_mbid: str, artist: str, title: str) -> None:
        if any(p["rg_mbid"] == rg_mbid for p in self._d["pending"]):
            return
        self._d["pending"].append(
            {"rg_mbid": rg_mbid, "artist": artist, "title": title, "attempts": 1})

    def bump_pending(self, rg_mbid: str) -> None:
        for p in self._d["pending"]:
            if p["rg_mbid"] == rg_mbid:
                p["attempts"] += 1

    def drop_pending(self, rg_mbid: str) -> None:
        self._d["pending"] = [p for p in self._d["pending"] if p["rg_mbid"] != rg_mbid]

    # ── feed + unseen ──
    def feed(self) -> list:
        return self._d["feed"]

    def append_feed(self, entry: dict) -> None:
        entry = {**entry, "ts": datetime.datetime.now().isoformat()}
        self._d["feed"].append(entry)
        if len(self._d["feed"]) > _FEED_CAP:
            self._d["feed"] = self._d["feed"][-_FEED_CAP:]
        self._d["unseen_count"] += 1

    def mark_seen(self) -> None:
        self._d["unseen_count"] = 0

    # ── scheduling stamps ──
    def set_runs(self, last_run=None, next_run=None) -> None:
        if last_run is not None:
            self._d["last_run"] = last_run
        if next_run is not None:
            self._d["next_run"] = next_run

    def summary(self) -> dict:
        return {
            "unseen_count": self._d["unseen_count"],
            "last_run": self._d.get("last_run"),
            "next_run": self._d.get("next_run"),
            "acquired_count": len(self._d["acquired_release_groups"]),
        }

    def save(self) -> None:
        with _lock:
            tmp = self._path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._d, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)


def _empty() -> dict:
    return {
        "acquired_release_groups": {},
        "backfilled_mbids": [],
        "pending": [],
        "feed": [],
        "unseen_count": 0,
        "last_run": None,
        "next_run": None,
    }


def load(path: str) -> FollowState:
    data = _empty()
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                loaded = json.load(f)
            for k, v in _empty().items():
                data[k] = loaded.get(k, v)
        except Exception:
            pass
    return FollowState(path, data)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/follow/test_fstate.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add follow/fstate.py tests/follow/test_fstate.py
git commit -m "feat(follow): non-expiring runtime state (follow_state.json)"
```

---

## Task 5: Detection logic

**Files:**
- Create: `follow/detect.py`
- Test: `tests/follow/test_detect.py`

`detect.py` is **pure** — it takes already-fetched data plus the MB client (for backfill + tracklist) and returns download targets. To keep it testable, inject a `mb_client` and the already-fetched `fresh_releases` list.

Core function:
```
detect_targets(mb_client, fresh_releases, follows, state, default_backfill_days, today) -> list[Target]
```
where `Target = {"rg_mbid", "artist", "title", "release_name", "release_date", "primary_type"}`.

Algorithm:
1. Build `followed = {mbid: name}` from `follows`.
2. **Feed branch:** for each fresh release whose `artist_mbids` intersects `followed` and not `state.has_acquired(rg)`: collect release-group `(rg_mbid, artist_name, release_name, release_date, primary_type)`.
3. **Backfill branch:** for each followed mbid where `not state.is_backfilled(mbid)`: `mb_client.get_release_groups(mbid)`, keep those with `first_release_date` within `default_backfill_days` of `today` and not acquired; then `state.mark_backfilled(mbid)`.
4. Merge feed + backfill release-groups, dedupe by `rg_mbid`.
5. For each release-group, map to targets via scope rule using `mb_client.get_release_tracks(rg_mbid)`:
   - `primary_type` in {"Single", "EP"} → one target per track title.
   - else (Album/empty/other) → one representative track: the track whose title casefold-equals the release-group title, else the first track. If the tracklist is empty, fall back to a single target using `release_name` as the title.

- [ ] **Step 1: Write the failing test**

`tests/follow/test_detect.py`:

```python
from follow import detect
from follow import fstate


class FakeMB:
    def __init__(self, release_groups=None, tracks=None):
        self._rgs = release_groups or {}      # mbid -> list[rg dict]
        self._tracks = tracks or {}           # rg_mbid -> list[title]
        self.rg_calls = []

    def get_release_groups(self, mbid, limit=100):
        self.rg_calls.append(mbid)
        return self._rgs.get(mbid, [])

    def get_release_tracks(self, rg_mbid):
        return self._tracks.get(rg_mbid, [])


def _follow(mbid, name):
    return {"mbid": mbid, "name": name, "disambiguation": "", "followed_at": ""}


def test_single_from_feed_yields_all_tracks(state_path):
    st = fstate.load(state_path)
    mb = FakeMB(tracks={"rg-1": ["Lead", "B-side"]})
    fresh = [{"artist_mbids": ["m1"], "release_date": "2026-06-12",
              "release_group_mbid": "rg-1", "release_name": "Lead",
              "primary_type": "Single", "artist_name": "A"}]
    targets = detect.detect_targets(mb, fresh, [_follow("m1", "A")], st,
                                    default_backfill_days=30, today="2026-06-14")
    assert [t["title"] for t in targets] == ["Lead", "B-side"]
    assert all(t["rg_mbid"] == "rg-1" for t in targets)


def test_album_from_feed_yields_one_representative(state_path):
    st = fstate.load(state_path)
    mb = FakeMB(tracks={"rg-2": ["Intro", "Mezzanine", "Outro"]})
    fresh = [{"artist_mbids": ["m1"], "release_date": "2026-06-12",
              "release_group_mbid": "rg-2", "release_name": "Mezzanine",
              "primary_type": "Album", "artist_name": "A"}]
    targets = detect.detect_targets(mb, fresh, [_follow("m1", "A")], st,
                                    default_backfill_days=30, today="2026-06-14")
    assert [t["title"] for t in targets] == ["Mezzanine"]   # title-track match


def test_album_without_title_match_uses_first_track(state_path):
    st = fstate.load(state_path)
    mb = FakeMB(tracks={"rg-3": ["First", "Second"]})
    fresh = [{"artist_mbids": ["m1"], "release_date": "2026-06-12",
              "release_group_mbid": "rg-3", "release_name": "Some Album",
              "primary_type": "Album", "artist_name": "A"}]
    targets = detect.detect_targets(mb, fresh, [_follow("m1", "A")], st,
                                    default_backfill_days=30, today="2026-06-14")
    assert [t["title"] for t in targets] == ["First"]


def test_skips_unfollowed_artist(state_path):
    st = fstate.load(state_path)
    mb = FakeMB(tracks={"rg-1": ["X"]})
    fresh = [{"artist_mbids": ["OTHER"], "release_date": "2026-06-12",
              "release_group_mbid": "rg-1", "release_name": "X",
              "primary_type": "Single", "artist_name": "Z"}]
    targets = detect.detect_targets(mb, fresh, [_follow("m1", "A")], st,
                                    default_backfill_days=30, today="2026-06-14")
    assert targets == []


def test_skips_already_acquired(state_path):
    st = fstate.load(state_path)
    st.mark_acquired("rg-1")
    mb = FakeMB(tracks={"rg-1": ["X"]})
    fresh = [{"artist_mbids": ["m1"], "release_date": "2026-06-12",
              "release_group_mbid": "rg-1", "release_name": "X",
              "primary_type": "Single", "artist_name": "A"}]
    targets = detect.detect_targets(mb, fresh, [_follow("m1", "A")], st,
                                    default_backfill_days=30, today="2026-06-14")
    assert targets == []


def test_backfill_within_window_marks_backfilled(state_path):
    st = fstate.load(state_path)
    mb = FakeMB(
        release_groups={"m1": [
            {"rg_mbid": "rg-old", "title": "Old", "first_release_date": "2000-01-01",
             "primary_type": "Album"},
            {"rg_mbid": "rg-new", "title": "Recent", "first_release_date": "2026-05-20",
             "primary_type": "Single"},
        ]},
        tracks={"rg-new": ["Recent"]},
    )
    targets = detect.detect_targets(mb, [], [_follow("m1", "A")], st,
                                    default_backfill_days=30, today="2026-06-14")
    assert [t["rg_mbid"] for t in targets] == ["rg-new"]
    assert st.is_backfilled("m1") is True


def test_backfill_runs_once(state_path):
    st = fstate.load(state_path)
    st.mark_backfilled("m1")
    mb = FakeMB(release_groups={"m1": [
        {"rg_mbid": "rg-new", "title": "Recent", "first_release_date": "2026-05-20",
         "primary_type": "Single"}]}, tracks={"rg-new": ["Recent"]})
    detect.detect_targets(mb, [], [_follow("m1", "A")], st,
                          default_backfill_days=30, today="2026-06-14")
    assert mb.rg_calls == []   # backfill skipped for already-backfilled artist
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/follow/test_detect.py -v`
Expected: FAIL (`No module named 'follow.detect'`)

- [ ] **Step 3: Implement `follow/detect.py`**

```python
"""Pure detection: combine the ListenBrainz feed with one-time MusicBrainz
backfill, dedupe against acquired releases, and map each new release-group to
download targets per the scope rule (singles/EPs full, albums = 1 track)."""
import datetime
import logging

logger = logging.getLogger(__name__)

_FULL_TYPES = {"single", "ep"}


def _within_days(date_str: str, days: int, today: str) -> bool:
    try:
        d = datetime.date.fromisoformat(date_str[:10])
        t = datetime.date.fromisoformat(today[:10])
    except Exception:
        return False
    return 0 <= (t - d).days <= days


def _targets_for_release(mb_client, rg, today_unused=None) -> list:
    """rg: {rg_mbid, artist, release_name, release_date, primary_type}."""
    try:
        tracks = mb_client.get_release_tracks(rg["rg_mbid"])
    except Exception:
        logger.warning("detect: get_release_tracks failed for %s", rg["rg_mbid"])
        tracks = []

    ptype = (rg["primary_type"] or "").casefold()
    if ptype in _FULL_TYPES and tracks:
        titles = tracks
    elif tracks:
        # representative track: title-track match, else first
        match = next((t for t in tracks
                      if t.casefold() == (rg["release_name"] or "").casefold()), None)
        titles = [match or tracks[0]]
    else:
        titles = [rg["release_name"]] if rg["release_name"] else []

    return [{
        "rg_mbid": rg["rg_mbid"],
        "artist": rg["artist"],
        "title": title,
        "release_name": rg["release_name"],
        "release_date": rg["release_date"],
        "primary_type": rg["primary_type"],
    } for title in titles if title]


def detect_targets(mb_client, fresh_releases, follows, state,
                   default_backfill_days: int, today: str) -> list:
    followed = {f["mbid"]: f["name"] for f in follows}
    release_groups = {}   # rg_mbid -> rg dict (deduped)

    # 1. Feed branch
    for r in fresh_releases:
        if state.has_acquired(r["release_group_mbid"]):
            continue
        matched = [m for m in r["artist_mbids"] if m in followed]
        if not matched:
            continue
        rg_mbid = r["release_group_mbid"]
        if not rg_mbid or rg_mbid in release_groups:
            continue
        release_groups[rg_mbid] = {
            "rg_mbid": rg_mbid,
            "artist": r["artist_name"] or followed[matched[0]],
            "release_name": r["release_name"],
            "release_date": r["release_date"],
            "primary_type": r["primary_type"],
        }

    # 2. Backfill branch (once per artist)
    for f in follows:
        mbid = f["mbid"]
        if state.is_backfilled(mbid):
            continue
        try:
            rgs = mb_client.get_release_groups(mbid)
        except Exception:
            logger.warning("detect: get_release_groups failed for %s", mbid)
            rgs = []
        for rg in rgs:
            if not _within_days(rg["first_release_date"], default_backfill_days, today):
                continue
            if state.has_acquired(rg["rg_mbid"]) or rg["rg_mbid"] in release_groups:
                continue
            release_groups[rg["rg_mbid"]] = {
                "rg_mbid": rg["rg_mbid"],
                "artist": f["name"],
                "release_name": rg["title"],
                "release_date": rg["first_release_date"],
                "primary_type": rg["primary_type"],
            }
        state.mark_backfilled(mbid)

    # 3. Map to targets
    targets = []
    for rg in release_groups.values():
        targets.extend(_targets_for_release(mb_client, rg))
    return targets
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/follow/test_detect.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add follow/detect.py tests/follow/test_detect.py
git commit -m "feat(follow): detection (feed filter + backfill + scope mapping)"
```

---

## Task 6: Notifications

**Files:**
- Create: `follow/notify.py`
- Test: `tests/follow/test_notify.py`

`notify.py` appends feed entries (via the passed `state`) and, if configured, POSTs to a webhook (JSON) and/or ntfy topic (plain text to `https://ntfy.sh/<topic>`). HTTP is done via an injectable `post_fn` for testability (default `requests.post`).

- [ ] **Step 1: Write the failing test**

`tests/follow/test_notify.py`:

```python
from follow import notify
from follow import fstate


def test_record_acquired_appends_feed(state_path):
    st = fstate.load(state_path)
    notify.record_event(st, artist="A", title="T", release_name="R",
                        release_date="2026-06-12", primary_type="Single",
                        status="acquired")
    assert st.feed()[0]["status"] == "acquired"
    assert st.summary()["unseen_count"] == 1


def test_push_webhook_and_ntfy_called():
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        class R:
            status_code = 200
            def raise_for_status(self): pass
        return R()

    notify.push_summary(
        ["A – T", "B – U"],
        webhook_url="https://hook.example/x",
        ntfy_topic="mytopic",
        post_fn=fake_post,
    )
    urls = [c[0] for c in calls]
    assert "https://hook.example/x" in urls
    assert "https://ntfy.sh/mytopic" in urls
    # webhook gets JSON
    webhook_call = next(c for c in calls if c[0] == "https://hook.example/x")
    assert "json" in webhook_call[1]
    assert webhook_call[1]["json"]["count"] == 2


def test_push_summary_noop_when_unconfigured():
    calls = []
    notify.push_summary(["A – T"], webhook_url="", ntfy_topic="",
                        post_fn=lambda *a, **k: calls.append(1))
    assert calls == []


def test_push_summary_noop_when_empty_list():
    calls = []
    notify.push_summary([], webhook_url="https://x", ntfy_topic="t",
                        post_fn=lambda *a, **k: calls.append(1))
    assert calls == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/follow/test_notify.py -v`
Expected: FAIL (`No module named 'follow.notify'`)

- [ ] **Step 3: Implement `follow/notify.py`**

```python
"""Feed recording + optional external push (webhook JSON / ntfy plain text)."""
import logging

logger = logging.getLogger(__name__)


def record_event(state, artist, title, release_name, release_date,
                 primary_type, status) -> None:
    state.append_feed({
        "artist": artist, "title": title, "release_name": release_name,
        "release_date": release_date, "primary_type": primary_type,
        "status": status,
    })


def push_summary(lines, webhook_url="", ntfy_topic="", post_fn=None) -> None:
    """POST a short summary of newly-acquired tracks. No-op if nothing to send."""
    if not lines:
        return
    if post_fn is None:
        import requests
        post_fn = requests.post

    message = f"{len(lines)} new release(s) from your follows:\n" + "\n".join(lines)

    if webhook_url:
        try:
            resp = post_fn(webhook_url,
                           json={"count": len(lines), "tracks": lines,
                                 "message": message},
                           timeout=10)
            resp.raise_for_status()
        except Exception:
            logger.warning("follow: webhook push failed", exc_info=True)

    if ntfy_topic:
        try:
            resp = post_fn(f"https://ntfy.sh/{ntfy_topic}",
                           data=message.encode("utf-8"),
                           headers={"Title": "New Releases"}, timeout=10)
            resp.raise_for_status()
        except Exception:
            logger.warning("follow: ntfy push failed", exc_info=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/follow/test_notify.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add follow/notify.py tests/follow/test_notify.py
git commit -m "feat(follow): feed recording + webhook/ntfy push"
```

---

## Task 7: Run orchestration (runner)

**Files:**
- Create: `follow/runner.py`
- Test: `tests/follow/test_runner.py`

`run_once` ties everything together. It is given the already-built pieces (so it stays testable without network): `mb_client`, `lb_client`, `follows`, `state`, `search_fn`, `download_fn`, `song_dir`, and a `cfg` dict (the `follow` settings block). It must:

1. `fresh = lb_client.fresh_releases(today, days=lookback_days, past=True)`.
2. `targets = detect.detect_targets(mb_client, fresh, follows, state, default_backfill_days, today)`.
3. Re-add unresolved `pending` items (from a prior run) as targets too.
4. For each target: build artist dict `{"name": artist, "top_track": title}`, call `resolve_tracks(search_fn, [that], per_artist=1)`; for each resolved candidate `acquire(download_fn, candidate)`.
   - On success: collect mp3 paths, `state.mark_acquired(rg_mbid)`, `state.drop_pending(rg_mbid)`, `notify.record_event(... status="acquired")`, add a summary line `"Artist – Title"`.
   - On failure (no candidate or empty paths): if already pending and `attempts >= 3` → `state.drop_pending(rg)` + `notify.record_event(status="unavailable")`; else `state.add_pending(...)` or `state.bump_pending(...)`.
5. If any mp3 paths collected → `write_weekly_mix(song_dir, paths, name=playlist_name, cap=playlist_cap)`.
6. `notify.push_summary(summary_lines, webhook_url, ntfy_topic)`.
7. `state.set_runs(last_run=now_iso)`, `state.save()`.
8. Return `{"acquired": n, "unavailable": m}`.

Inject `resolve_fn`, `acquire_fn`, `assemble_fn`, `push_fn`, and `today` as parameters with real defaults so tests can stub them.

- [ ] **Step 1: Write the failing test**

`tests/follow/test_runner.py`:

```python
from follow import runner
from follow import fstate


def _follow(mbid, name):
    return {"mbid": mbid, "name": name, "disambiguation": "", "followed_at": ""}


class FakeLB:
    def __init__(self, fresh):
        self._fresh = fresh
    def fresh_releases(self, pivot_date, days=7, past=True, future=False):
        return self._fresh


class FakeMB:
    def __init__(self, tracks):
        self._tracks = tracks
    def get_release_groups(self, mbid, limit=100):
        return []
    def get_release_tracks(self, rg_mbid):
        return self._tracks.get(rg_mbid, [])


def _fresh_single(mbid="m1", rg="rg-1", name="Song"):
    return [{"artist_mbids": [mbid], "release_date": "2026-06-12",
             "release_group_mbid": rg, "release_name": name,
             "primary_type": "Single", "artist_name": "A"}]


def test_happy_path_acquires_and_writes_playlist(state_path):
    st = fstate.load(state_path)
    written = {}

    def fake_resolve(search_fn, artists, per_artist=1):
        a = artists[0]
        return [{"artist": a["name"], "title": a["top_track"], "url": "u"}]

    def fake_acquire(download_fn, candidate):
        return [f"/songs/{candidate['title']}.mp3"]

    def fake_assemble(song_dir, paths, name, cap):
        written["paths"] = list(paths)
        written["name"] = name
        return "/songs/" + name + ".m3u"

    result = runner.run_once(
        mb_client=FakeMB({"rg-1": ["Song"]}),
        lb_client=FakeLB(_fresh_single()),
        follows=[_follow("m1", "A")],
        state=st,
        search_fn=None, download_fn=None, song_dir="/songs",
        cfg={"lookback_days": 7, "default_backfill_days": 30,
             "playlist_name": "NEW RELEASES", "playlist_cap": 100,
             "notify": {"webhook_url": "", "ntfy_topic": ""}},
        resolve_fn=fake_resolve, acquire_fn=fake_acquire,
        assemble_fn=fake_assemble, push_fn=lambda *a, **k: None,
        today="2026-06-14",
    )
    assert result["acquired"] == 1
    assert written["name"] == "NEW RELEASES"
    assert written["paths"] == ["/songs/Song.mp3"]
    assert st.has_acquired("rg-1") is True
    assert st.feed()[0]["status"] == "acquired"


def test_idempotent_second_run_downloads_nothing(state_path):
    st = fstate.load(state_path)
    calls = {"n": 0}

    def fake_resolve(search_fn, artists, per_artist=1):
        return [{"artist": artists[0]["name"], "title": artists[0]["top_track"], "url": "u"}]

    def fake_acquire(download_fn, candidate):
        calls["n"] += 1
        return [f"/songs/{candidate['title']}.mp3"]

    kwargs = dict(
        mb_client=FakeMB({"rg-1": ["Song"]}), lb_client=FakeLB(_fresh_single()),
        follows=[_follow("m1", "A")], state=st, search_fn=None, download_fn=None,
        song_dir="/songs",
        cfg={"lookback_days": 7, "default_backfill_days": 30,
             "playlist_name": "NEW RELEASES", "playlist_cap": 100,
             "notify": {"webhook_url": "", "ntfy_topic": ""}},
        resolve_fn=fake_resolve, acquire_fn=fake_acquire,
        assemble_fn=lambda *a, **k: "x", push_fn=lambda *a, **k: None,
        today="2026-06-14",
    )
    runner.run_once(**kwargs)
    runner.run_once(**kwargs)
    assert calls["n"] == 1   # second run acquires nothing


def test_failure_marks_pending_then_unavailable_after_3(state_path):
    st = fstate.load(state_path)

    def fake_resolve(search_fn, artists, per_artist=1):
        return []   # no source found

    kwargs = dict(
        mb_client=FakeMB({"rg-1": ["Song"]}), lb_client=FakeLB(_fresh_single()),
        follows=[_follow("m1", "A")], state=st, search_fn=None, download_fn=None,
        song_dir="/songs",
        cfg={"lookback_days": 7, "default_backfill_days": 30,
             "playlist_name": "NEW RELEASES", "playlist_cap": 100,
             "notify": {"webhook_url": "", "ntfy_topic": ""}},
        resolve_fn=fake_resolve, acquire_fn=lambda *a, **k: [],
        assemble_fn=lambda *a, **k: "x", push_fn=lambda *a, **k: None,
        today="2026-06-14",
    )
    runner.run_once(**kwargs)
    assert st.pending()[0]["attempts"] == 1
    runner.run_once(**kwargs)
    assert st.pending()[0]["attempts"] == 2
    runner.run_once(**kwargs)
    # third attempt → dropped + recorded unavailable
    assert st.pending() == []
    assert any(e["status"] == "unavailable" for e in st.feed())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/follow/test_runner.py -v`
Expected: FAIL (`No module named 'follow.runner'`)

- [ ] **Step 3: Implement `follow/runner.py`**

```python
"""One follow run: detect → resolve → acquire → playlist → notify → save."""
import datetime
import logging

from follow import detect, notify

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 3


def _today_iso():
    return datetime.date.today().isoformat()


def run_once(mb_client, lb_client, follows, state, search_fn, download_fn,
             song_dir, cfg, resolve_fn=None, acquire_fn=None, assemble_fn=None,
             push_fn=None, today=None) -> dict:
    if resolve_fn is None:
        from discover.resolve import resolve_tracks as resolve_fn
    if acquire_fn is None:
        from discover.acquire import acquire as acquire_fn
    if assemble_fn is None:
        from discover.assemble import write_weekly_mix as assemble_fn
    if push_fn is None:
        push_fn = notify.push_summary
    today = today or _today_iso()

    lookback = int(cfg.get("lookback_days", 7))
    backfill = int(cfg.get("default_backfill_days", 30))
    playlist_name = cfg.get("playlist_name", "NEW RELEASES")
    playlist_cap = int(cfg.get("playlist_cap", 100))
    notify_cfg = cfg.get("notify") or {}

    fresh = lb_client.fresh_releases(today, days=lookback, past=True)
    targets = detect.detect_targets(mb_client, fresh, follows, state, backfill, today)

    # Re-attempt previously-pending releases (use stored artist/title)
    target_rgs = {t["rg_mbid"] for t in targets}
    for p in list(state.pending()):
        if p["rg_mbid"] not in target_rgs:
            targets.append({
                "rg_mbid": p["rg_mbid"], "artist": p["artist"], "title": p["title"],
                "release_name": p["title"], "release_date": "", "primary_type": "",
            })

    paths = []
    summary_lines = []
    acquired = 0
    unavailable = 0

    for t in targets:
        candidates = []
        try:
            candidates = resolve_fn(
                search_fn, [{"name": t["artist"], "top_track": t["title"]}],
                per_artist=1)
        except Exception:
            logger.warning("follow: resolve failed for %s – %s", t["artist"], t["title"])

        got = []
        for c in candidates:
            try:
                got = acquire_fn(download_fn, c)
            except Exception:
                got = []
            if got:
                break

        if got:
            paths.extend(got)
            state.mark_acquired(t["rg_mbid"])
            state.drop_pending(t["rg_mbid"])
            notify.record_event(state, t["artist"], t["title"], t["release_name"],
                                t["release_date"], t["primary_type"], "acquired")
            summary_lines.append(f"{t['artist']} – {t['title']}")
            acquired += 1
        else:
            existing = next((p for p in state.pending()
                             if p["rg_mbid"] == t["rg_mbid"]), None)
            if existing is None:
                state.add_pending(t["rg_mbid"], t["artist"], t["title"])
            elif existing["attempts"] >= _MAX_ATTEMPTS:
                state.drop_pending(t["rg_mbid"])
                notify.record_event(state, t["artist"], t["title"], t["release_name"],
                                    t["release_date"], t["primary_type"], "unavailable")
                unavailable += 1
            else:
                state.bump_pending(t["rg_mbid"])

    if paths:
        assemble_fn(song_dir, paths, playlist_name, playlist_cap)

    push_fn(summary_lines,
            webhook_url=notify_cfg.get("webhook_url", ""),
            ntfy_topic=notify_cfg.get("ntfy_topic", ""))

    state.set_runs(last_run=datetime.datetime.now().isoformat())
    state.save()
    return {"acquired": acquired, "unavailable": unavailable}
```

Note on `assemble_fn` signature: `write_weekly_mix(song_dir, mp3_paths, name, cap)` — call positionally as `assemble_fn(song_dir, paths, playlist_name, playlist_cap)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/follow/test_runner.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the whole follow suite**

Run: `python -m pytest tests/follow/ -v`
Expected: PASS (all ~27 tests)

- [ ] **Step 6: Commit**

```bash
git add follow/runner.py tests/follow/test_runner.py
git commit -m "feat(follow): run orchestration (detect→acquire→playlist→notify)"
```

---

## Task 8: Server wiring — config defaults, deps, scheduler, thread

**Files:**
- Modify: `sWebExt/py_server/server.py`

- [ ] **Step 1: Add follow config defaults injection**

Find `_get_config()` (server.py:728). Immediately after it, add a helper and the follows/state paths near the top-level constants (after `_PROJECT_ROOT`, server.py:25). Add:

```python
_FOLLOWS_PATH = os.path.join(_PROJECT_ROOT, "follows.json")
_FOLLOW_STATE_PATH = os.path.join(_PROJECT_ROOT, "follow_state.json")

_FOLLOW_DEFAULTS = {
    "enabled": True,
    "run_hour": 4,
    "lookback_days": 7,
    "default_backfill_days": 30,
    "playlist_name": "NEW RELEASES",
    "playlist_cap": 100,
    "notify": {"webhook_url": "", "ntfy_topic": ""},
}


def _follow_cfg() -> dict:
    cfg = _get_config()
    fc = dict(_FOLLOW_DEFAULTS)
    fc.update(cfg.get("follow") or {})
    notify = dict(_FOLLOW_DEFAULTS["notify"])
    notify.update((cfg.get("follow") or {}).get("notify") or {})
    fc["notify"] = notify
    return fc
```

(Place the constants block with the other module constants and `_follow_cfg` near `_get_config`.)

- [ ] **Step 2: Add a follow deps builder**

After `_build_discover_deps()` (server.py:143), add:

```python
def _build_follow_clients():
    """Return (mb_client, lb_client) or (None, None) if requests unavailable."""
    from follow.musicbrainz import MusicBrainzClient
    from follow.listenbrainz import ListenBrainzClient
    return MusicBrainzClient(), ListenBrainzClient()


def _run_follow_once() -> dict:
    from follow import store, fstate, runner
    deps = _build_discover_deps()
    if deps is None:
        return {"status": "disabled", "reason": "navidrome creds missing"}
    mb, lb = _build_follow_clients()
    follows = store.list_follows(_FOLLOWS_PATH)
    state = fstate.load(_FOLLOW_STATE_PATH)
    fc = _follow_cfg()
    result = runner.run_once(
        mb_client=mb, lb_client=lb, follows=follows, state=state,
        search_fn=deps.search_fn, download_fn=deps.download_fn,
        song_dir=deps.song_dir, cfg=fc)
    logger.info("[FOLLOW] run complete: %s", result)
    return {"status": "ok", **result}
```

- [ ] **Step 3: Add the follow scheduler loop**

After `_mix_scheduler_loop()` (ends ~server.py:413), add (modeled on the dedup loop):

```python
_follow_wake = threading.Event()


def _follow_next_run(now: datetime.datetime, run_hour: int) -> datetime.datetime:
    run_hour = max(0, min(23, int(run_hour)))
    candidate = now.replace(hour=run_hour, minute=0, second=0, microsecond=0)
    if candidate <= now:
        candidate += datetime.timedelta(days=1)
    return candidate


def _follow_scheduler_loop():
    while True:
        try:
            fc = _follow_cfg()
            now = datetime.datetime.now()
            if not fc.get("enabled", True):
                _follow_wake.wait(3600)
                _follow_wake.clear()
                continue
            nxt = _follow_next_run(now, fc.get("run_hour", 4))
            # persist next_run for UI
            try:
                from follow import fstate
                st = fstate.load(_FOLLOW_STATE_PATH)
                st.set_runs(next_run=nxt.isoformat())
                st.save()
            except Exception:
                logger.warning("[FOLLOW] could not persist next_run", exc_info=True)
            _follow_wake.wait(max(1.0, (nxt - now).total_seconds()))
            _follow_wake.clear()
            now = datetime.datetime.now()
            if _follow_cfg().get("enabled", True) and now >= nxt:
                _run_follow_once()
        except Exception:
            logger.exception("[FOLLOW] scheduler iteration failed; retry in 3600s")
            time.sleep(3600)
```

- [ ] **Step 4: Start the follow thread**

At the thread-start block (server.py ~1772, where `t_mix` is started), add after it:

```python
    t_follow = threading.Thread(target=_follow_scheduler_loop, daemon=True)
    t_follow.start()
```

- [ ] **Step 5: Smoke-check the server imports**

Run: `python -c "import importlib.util, sys; sys.path.insert(0, '.'); import sWebExt.py_server.server"` 

If that path import fails due to package layout, instead run:
Run: `python -m pyflakes sWebExt/py_server/server.py` (or `python -c "import ast; ast.parse(open('sWebExt/py_server/server.py').read())"` to confirm it parses).
Expected: no syntax errors.

- [ ] **Step 6: Commit**

```bash
git add sWebExt/py_server/server.py
git commit -m "feat(follow): config defaults, deps, scheduler loop + thread"
```

---

## Task 9: Server wiring — Flask routes

**Files:**
- Modify: `sWebExt/py_server/server.py`

Add routes near the other routes (after the `/mixes` routes is a sensible spot). All return JSON.

- [ ] **Step 1: Add the routes**

```python
@app.route("/follow/search", methods=["GET"])
def follow_search():
    from flask import request, jsonify
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"results": []})
    mb, _ = _build_follow_clients()
    try:
        results = mb.search_artist(q, limit=8)
    except Exception:
        logger.warning("[FOLLOW] search failed", exc_info=True)
        return jsonify({"results": [], "error": "search_failed"}), 502
    return jsonify({"results": results})


@app.route("/follow", methods=["GET"])
def follow_list():
    from flask import jsonify
    from follow import store, fstate
    follows = store.list_follows(_FOLLOWS_PATH)
    summary = fstate.load(_FOLLOW_STATE_PATH).summary()
    return jsonify({"artists": follows, "state": summary})


@app.route("/follow", methods=["POST"])
def follow_add():
    from flask import request, jsonify
    from follow import store
    body = request.get_json(force=True, silent=True) or {}
    mbid = (body.get("mbid") or "").strip()
    name = (body.get("name") or "").strip()
    if not mbid or not name:
        return jsonify({"error": "mbid and name required"}), 400
    store.add_follow(_FOLLOWS_PATH, mbid=mbid, name=name,
                     disambiguation=body.get("disambiguation", ""))
    # kick a background run so backfill happens immediately
    threading.Thread(target=_run_follow_once, daemon=True).start()
    return jsonify({"status": "ok"})


@app.route("/follow/<mbid>", methods=["DELETE"])
def follow_remove(mbid):
    from flask import jsonify
    from follow import store
    store.remove_follow(_FOLLOWS_PATH, mbid)
    return jsonify({"status": "ok"})


@app.route("/follow/run", methods=["POST"])
def follow_run():
    from flask import jsonify
    result = _run_follow_once()
    return jsonify(result)


@app.route("/follow/feed", methods=["GET"])
def follow_feed():
    from flask import jsonify
    from follow import fstate
    st = fstate.load(_FOLLOW_STATE_PATH)
    return jsonify({"feed": list(reversed(st.feed())),
                    "unseen_count": st.summary()["unseen_count"]})


@app.route("/follow/feed/seen", methods=["POST"])
def follow_feed_seen():
    from flask import jsonify
    from follow import fstate
    st = fstate.load(_FOLLOW_STATE_PATH)
    st.mark_seen()
    st.save()
    return jsonify({"status": "ok"})


@app.route("/follow/settings", methods=["POST"])
def follow_settings():
    from flask import request, jsonify
    body = request.get_json(force=True, silent=True) or {}
    with _config_lock:
        cfg = _get_config()
        follow = dict(_FOLLOW_DEFAULTS)
        follow.update(cfg.get("follow") or {})
        for key in ("enabled", "run_hour", "lookback_days",
                    "default_backfill_days", "playlist_name", "playlist_cap"):
            if key in body:
                follow[key] = body[key]
        if "notify" in body and isinstance(body["notify"], dict):
            notify = dict(follow.get("notify") or {})
            notify.update(body["notify"])
            follow["notify"] = notify
        cfg["follow"] = follow
        _atomic_write_config(cfg)
    _follow_wake.set()
    return jsonify({"status": "ok", "follow": follow})
```

- [ ] **Step 2: Verify the file parses**

Run: `python -c "import ast; ast.parse(open('sWebExt/py_server/server.py').read())"`
Expected: no output (parses cleanly).

- [ ] **Step 3: Manual smoke test (network-touching — run if a dev server is available)**

Start the server (`python sWebExt/py_server/server.py`), then:
```bash
curl 'http://localhost:5000/follow/search?q=massive%20attack'
curl -X POST http://localhost:5000/follow -H 'Content-Type: application/json' \
  -d '{"mbid":"10adbe5b-6c3c-477c-9f9c-83b03b57d0a4","name":"Massive Attack"}'
curl http://localhost:5000/follow
```
Expected: search returns candidates with disambiguation; add returns `{"status":"ok"}`; list shows the artist.

- [ ] **Step 4: Commit**

```bash
git add sWebExt/py_server/server.py
git commit -m "feat(follow): Flask routes (search/list/add/remove/run/feed/settings)"
```

---

## Task 10: Web UI — Follows screen

**Files:**
- Modify: `web/templates/app.html`
- Modify: `web/static/app.js`
- Modify: `web/static/app.css`

**Before writing:** read the existing `web/static/app.js` to match the router pattern, the nav structure, the `createElement`/`textContent` discipline (no `innerHTML` with data), and the existing fetch-helper conventions. Mirror an existing screen (e.g. the Search screen) for structure.

- [ ] **Step 1: Add the nav entry + screen container in `app.html`**

Add a nav button/link for "Follows" (with a `<span>` badge element, hidden when count is 0) alongside the existing nav items, and an empty screen container div with the id the router expects (match the existing pattern, e.g. `<div id="screen-follows" class="screen"></div>`).

- [ ] **Step 2: Implement the Follows screen in `app.js`**

Add a render function `renderFollows(container)` and wire it into the hash router exactly like the other screens. It must (using `createElement`/`textContent` only):
- A **search row**: text input + Search button → `GET /follow/search?q=` → render results, each row showing `name` + `disambiguation` (muted) + a **Follow** button → `POST /follow {mbid,name,disambiguation}` → on success refresh the followed list.
- A **followed list**: `GET /follow` → rows with name + **Unfollow** button → `DELETE /follow/<mbid>` → refresh.
- A **NEW RELEASES feed**: `GET /follow/feed` → rows `Artist – Title` + status chip (`acquired`/`unavailable`) + date. On entering the screen, call `POST /follow/feed/seen` and clear the nav badge.
- A **settings block**: inputs for run_hour, lookback_days, default_backfill_days, playlist_cap, webhook_url, ntfy_topic → Save → `POST /follow/settings`.
- A **Run now** button → `POST /follow/run` → show the returned `{acquired, unavailable}`.

- [ ] **Step 3: Add the nav badge updater in `app.js`**

On app init and after feed loads, fetch `GET /follow` (or reuse the feed response) and set the nav badge text to `unseen_count`, hiding it when 0.

- [ ] **Step 4: Add minimal styles in `app.css`**

Reuse existing SIGNAL tokens/components. Add only what's needed: a `.badge` style for the unseen count and a `.chip` style for the feed status, if not already present.

- [ ] **Step 5: Manual UI smoke test**

Load `http://localhost:5000`, open the Follows screen: search an artist, follow, confirm it appears in the followed list and the feed populates after a Run now. Confirm no console errors and the badge clears on view.

- [ ] **Step 6: Commit**

```bash
git add web/templates/app.html web/static/app.js web/static/app.css
git commit -m "feat(follow): SIGNAL Follows screen (search, follows, feed, settings)"
```

---

## Task 11: Config example, gitignore, README/ROADMAP

**Files:**
- Modify: `config.example.json`
- Modify: `.gitignore`
- Modify: `README.md`, `docs/ROADMAP.md`

- [ ] **Step 1: Add the `follow` block to `config.example.json`**

Add (matching existing formatting):

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

- [ ] **Step 2: Add state/list files to `.gitignore`**

Add lines:
```
follows.json
follow_state.json
```

Verify `git status` no longer shows them after a run.

- [ ] **Step 3: Document the feature**

Add a short "Follow Artists / NEW RELEASES" section to `README.md` (what it does, the `follow` config keys, the NEW RELEASES playlist, webhook/ntfy notes) and tick it off / move it in `docs/ROADMAP.md`.

- [ ] **Step 4: Commit**

```bash
git add config.example.json .gitignore README.md docs/ROADMAP.md
git commit -m "docs(follow): config example, gitignore, README + ROADMAP"
```

---

## Task 12: Full suite + final verification

- [ ] **Step 1: Run the entire test suite**

Run: `python -m pytest -q`
Expected: all tests pass (existing discover tests + new ~27 follow tests). If any pre-existing test was already failing before this work, note it but do not let it block — confirm no *new* failures were introduced.

- [ ] **Step 2: Confirm parse + no stray artifacts**

Run: `python -c "import ast; ast.parse(open('sWebExt/py_server/server.py').read())"`
Run: `git status` — confirm only intended files are tracked; `follows.json` / `follow_state.json` are ignored.

- [ ] **Step 3: Final commit (if anything outstanding)**

```bash
git add -A
git commit -m "chore(follow): finalize follow-artists NEW RELEASES feature"
```

---

## Self-review notes (for the implementer)

- **Spec coverage:** store (T1), MB (T2), LB (T3), state w/ non-expiring acquired + backfill markers (T4), detect feed+backfill+scope (T5), notify feed+webhook+ntfy (T6), runner idempotent + retry-3 (T7), config defaults+scheduler+deps (T8), all routes (T9), SIGNAL UI w/ badge (T10), config.example+gitignore+docs (T11), verification (T12). Every spec section maps to a task.
- **Type consistency:** target dict keys (`rg_mbid, artist, title, release_name, release_date, primary_type`) are identical across detect, runner, and notify. State method names (`has_acquired, mark_acquired, is_backfilled, mark_backfilled, pending, add_pending, bump_pending, drop_pending, feed, append_feed, mark_seen, set_runs, summary, save`) are used consistently in T4/T5/T7/T8/T9.
- **Reuse:** `resolve_tracks`/`acquire`/`write_weekly_mix`/`_build_discover_deps` are called with their real signatures; the runner injects them so tests don't touch the network.
- **TDD:** every logic module has tests written first and run-to-fail before implementation.
