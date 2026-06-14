# Listening Insights — Phase 1: Store + Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the local SQLite store and incremental Last.fm scrobble-history ingestion, exposed via a background sync worker and two server endpoints — the foundation every later insights phase reads from.

**Architecture:** A new `insights/` package mirrors `discover/` and `library/`. `insights/db.py` owns the SQLite schema (idempotent init, WAL, one connection per thread). `insights/scrobbles.py` parses `user.getRecentTracks` pages and incrementally syncs them into the `scrobbles` table, resuming from a stored `last_ts`. `server.py` wires a background worker (reusing the existing enrich worker pattern at `server.py:526`) plus `POST /insights/sync` and `GET /insights/sync/status`.

**Tech Stack:** Python stdlib `sqlite3` (no new dependency), existing `lastfm/client.py`, Flask (existing), `pytest` + `unittest.mock`.

**Spec:** `docs/superpowers/specs/2026-06-14-listening-insights-analytics-design.md` (Phase 1 = §1, §2, and the sync portion of §6).

---

## File Structure

- Create `insights/__init__.py` — package marker (one line, like `lastfm/__init__.py`).
- Create `insights/db.py` — SQLite connection, schema DDL, `connect()`, `init_schema()`, `get_state()`, `set_state()`. Sole owner of the schema.
- Create `insights/scrobbles.py` — `parse_recent_tracks()` (pure), `total_pages()` (pure), `insert_scrobbles()`, `sync_scrobbles()`.
- Modify `sWebExt/py_server/server.py` — add insights worker lock/state, `_insights_db_path()`, `_run_insights_sync_once()`, and the two routes. Insert near the enrich worker/routes.
- Create `tests/insights/__init__.py` — empty package marker.
- Create `tests/insights/test_db.py` — schema + state-store tests.
- Create `tests/insights/test_scrobbles.py` — parsing + incremental sync tests.
- Modify `tests/server/test_routes.py` — add `/insights/sync` + `/insights/sync/status` route tests.

---

## Task 1: insights package + SQLite schema

**Files:**
- Create: `insights/__init__.py`
- Create: `insights/db.py`
- Test: `tests/insights/__init__.py`, `tests/insights/test_db.py`

- [ ] **Step 1: Create the test package marker**

Create `tests/insights/__init__.py` as an empty file:

```python
```

- [ ] **Step 2: Write the failing tests**

Create `tests/insights/test_db.py`:

```python
"""Tests for insights/db.py — schema init and state store."""

from insights import db


def _tables(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    return {r[0] for r in rows}


def test_connect_creates_all_tables(tmp_path):
    conn = db.connect(str(tmp_path / "insights.db"))
    names = _tables(conn)
    assert {"scrobbles", "artist_tags", "track_features", "sync_state"} <= names


def test_init_schema_is_idempotent(tmp_path):
    path = str(tmp_path / "insights.db")
    conn = db.connect(path)
    # Re-running must not raise and must preserve data.
    conn.execute(
        "INSERT INTO scrobbles (ts, artist, track) VALUES (1, 'A', 'T')"
    )
    conn.commit()
    db.init_schema(conn)
    count = conn.execute("SELECT COUNT(*) FROM scrobbles").fetchone()[0]
    assert count == 1


def test_state_get_set_roundtrip(tmp_path):
    conn = db.connect(str(tmp_path / "insights.db"))
    assert db.get_state(conn, "last_ts") is None
    assert db.get_state(conn, "last_ts", "0") == "0"
    db.set_state(conn, "last_ts", "12345")
    assert db.get_state(conn, "last_ts") == "12345"
    # Overwrite.
    db.set_state(conn, "last_ts", "67890")
    assert db.get_state(conn, "last_ts") == "67890"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/insights/test_db.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'insights'`

- [ ] **Step 4: Create the package marker**

Create `insights/__init__.py`:

```python
# insights — listening behavior analytics package
```

- [ ] **Step 5: Implement `insights/db.py`**

Create `insights/db.py`:

```python
"""SQLite store for listening insights.

Sole owner of the insights schema. One connection per thread (sqlite3
connections are not safe to share across threads).
"""

import sqlite3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS scrobbles (
    ts             INTEGER NOT NULL,
    artist         TEXT    NOT NULL,
    track          TEXT    NOT NULL,
    album          TEXT,
    artist_mbid    TEXT,
    recording_mbid TEXT,
    PRIMARY KEY (ts, artist, track)
);
CREATE INDEX IF NOT EXISTS idx_scrobbles_ts     ON scrobbles(ts);
CREATE INDEX IF NOT EXISTS idx_scrobbles_artist ON scrobbles(artist);

CREATE TABLE IF NOT EXISTS artist_tags (
    artist        TEXT PRIMARY KEY,
    tags_json     TEXT,
    primary_genre TEXT,
    fetched_at    INTEGER
);

CREATE TABLE IF NOT EXISTS track_features (
    artist           TEXT NOT NULL,
    track            TEXT NOT NULL,
    recording_mbid   TEXT,
    bpm              REAL,
    key              TEXT,
    scale            TEXT,
    mood             TEXT,
    mood_scores_json TEXT,
    danceability     REAL,
    source           TEXT,
    analyzed_at      INTEGER,
    PRIMARY KEY (artist, track)
);

CREATE TABLE IF NOT EXISTS sync_state (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def init_schema(conn: sqlite3.Connection) -> None:
    """Create all tables/indexes if absent. Idempotent."""
    conn.executescript(_SCHEMA)
    conn.commit()


def connect(db_path: str) -> sqlite3.Connection:
    """Open (creating if needed) the insights DB with the schema applied."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    init_schema(conn)
    return conn


def get_state(conn: sqlite3.Connection, key: str, default=None):
    """Return a sync_state value, or default if the key is absent."""
    row = conn.execute(
        "SELECT value FROM sync_state WHERE key = ?", (key,)
    ).fetchone()
    return row[0] if row is not None else default


def set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Upsert a sync_state value."""
    conn.execute(
        "INSERT INTO sync_state (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/insights/test_db.py -v`
Expected: PASS (3 tests)

- [ ] **Step 7: Commit**

```bash
git add insights/__init__.py insights/db.py tests/insights/__init__.py tests/insights/test_db.py
git commit -m "feat(insights): SQLite store schema + state helpers"
```

---

## Task 2: parse `user.getRecentTracks` pages

**Files:**
- Create: `insights/scrobbles.py`
- Test: `tests/insights/test_scrobbles.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/insights/test_scrobbles.py`:

```python
"""Tests for insights/scrobbles.py — parsing and incremental sync."""

from insights import db, scrobbles


# A representative getRecentTracks page: one normal track, one now-playing
# (must be skipped), plus @attr pagination metadata.
_PAGE = {
    "recenttracks": {
        "track": [
            {
                "artist": {"#text": "Aphex Twin", "mbid": "am-1"},
                "name": "Xtal",
                "album": {"#text": "Selected Ambient Works"},
                "mbid": "rec-1",
                "date": {"uts": "1700000000", "#text": "..."},
            },
            {
                "artist": {"#text": "Boards of Canada", "mbid": ""},
                "name": "Roygbiv",
                "album": {"#text": ""},
                "mbid": "",
                "@attr": {"nowplaying": "true"},
            },
        ],
        "@attr": {"page": "1", "totalPages": "3", "total": "120"},
    }
}


def test_parse_skips_nowplaying_and_extracts_fields():
    rows = scrobbles.parse_recent_tracks(_PAGE)
    assert len(rows) == 1
    r = rows[0]
    assert r["ts"] == 1700000000
    assert r["artist"] == "Aphex Twin"
    assert r["track"] == "Xtal"
    assert r["album"] == "Selected Ambient Works"
    assert r["artist_mbid"] == "am-1"
    assert r["recording_mbid"] == "rec-1"


def test_parse_blank_optional_fields_become_none():
    page = {
        "recenttracks": {
            "track": {
                "artist": {"#text": "X", "mbid": ""},
                "name": "Y",
                "album": {"#text": ""},
                "mbid": "",
                "date": {"uts": "100"},
            }
        }
    }
    rows = scrobbles.parse_recent_tracks(page)
    assert len(rows) == 1
    assert rows[0]["album"] is None
    assert rows[0]["artist_mbid"] is None
    assert rows[0]["recording_mbid"] is None


def test_parse_empty_page_returns_empty_list():
    assert scrobbles.parse_recent_tracks({}) == []
    assert scrobbles.parse_recent_tracks({"recenttracks": {"track": []}}) == []


def test_total_pages_reads_attr():
    assert scrobbles.total_pages(_PAGE) == 3
    assert scrobbles.total_pages({}) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/insights/test_scrobbles.py -v`
Expected: FAIL with `ImportError: cannot import name 'scrobbles'`

- [ ] **Step 3: Implement parsing in `insights/scrobbles.py`**

Create `insights/scrobbles.py`:

```python
"""Last.fm scrobble-history ingestion into the insights SQLite store."""

import logging

logger = logging.getLogger(__name__)


def parse_recent_tracks(data: dict) -> list[dict]:
    """Parse one user.getRecentTracks JSON page into scrobble rows.

    Skips the "now playing" row (it has no timestamp). Blank optional
    string fields are normalised to None.
    """
    root = data.get("recenttracks", {}) or {}
    raw = root.get("track", []) or []
    if isinstance(raw, dict):  # single-result API quirk
        raw = [raw]

    rows = []
    for t in raw:
        attr = t.get("@attr") or {}
        if attr.get("nowplaying") == "true":
            continue
        date = t.get("date") or {}
        uts = date.get("uts")
        if not uts:
            continue
        artist = t.get("artist") or {}
        album = t.get("album") or {}

        def _clean(v):
            return (v or "").strip() or None

        rows.append({
            "ts": int(uts),
            "artist": (artist.get("#text") or artist.get("name") or "").strip(),
            "track": (t.get("name") or "").strip(),
            "album": _clean(album.get("#text")),
            "artist_mbid": _clean(artist.get("mbid")),
            "recording_mbid": _clean(t.get("mbid")),
        })
    return rows


def total_pages(data: dict) -> int:
    """Read totalPages from a getRecentTracks page (defaults to 1)."""
    root = data.get("recenttracks", {}) or {}
    attr = root.get("@attr", {}) or {}
    try:
        return int(attr.get("totalPages", 1))
    except (TypeError, ValueError):
        return 1
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/insights/test_scrobbles.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add insights/scrobbles.py tests/insights/test_scrobbles.py
git commit -m "feat(insights): parse getRecentTracks pages"
```

---

## Task 3: insert + incremental sync

**Files:**
- Modify: `insights/scrobbles.py`
- Test: `tests/insights/test_scrobbles.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/insights/test_scrobbles.py`:

```python
from unittest.mock import MagicMock


def test_insert_scrobbles_dedups_on_primary_key(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    rows = [{"ts": 1, "artist": "A", "track": "T", "album": None,
             "artist_mbid": None, "recording_mbid": None}]
    assert scrobbles.insert_scrobbles(conn, rows) == 1
    # Same PK again → ignored.
    assert scrobbles.insert_scrobbles(conn, rows) == 0
    assert conn.execute("SELECT COUNT(*) FROM scrobbles").fetchone()[0] == 1


def _page(tracks, total_pages):
    return {"recenttracks": {"track": tracks,
                             "@attr": {"totalPages": str(total_pages)}}}


def _track(uts, name):
    return {"artist": {"#text": "A", "mbid": ""}, "name": name,
            "album": {"#text": ""}, "mbid": "", "date": {"uts": str(uts)}}


def test_sync_walks_all_pages_and_records_last_ts(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    client = MagicMock()
    client.call.side_effect = [
        _page([_track(300, "c"), _track(250, "b")], 2),
        _page([_track(100, "a")], 2),
    ]
    result = scrobbles.sync_scrobbles(client, "user", conn, page_limit=2)
    assert result["inserted"] == 3
    assert result["pages"] == 2
    assert db.get_state(conn, "last_ts") == "300"
    assert conn.execute("SELECT COUNT(*) FROM scrobbles").fetchone()[0] == 3


def test_sync_resumes_from_stored_last_ts(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    db.set_state(conn, "last_ts", "200")
    client = MagicMock()
    client.call.return_value = _page([_track(300, "c")], 1)
    scrobbles.sync_scrobbles(client, "user", conn, page_limit=50)
    # The 'from' parameter must be passed so we only fetch newer plays.
    _, kwargs = client.call.call_args
    assert kwargs.get("from") == 200
    assert db.get_state(conn, "last_ts") == "300"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/insights/test_scrobbles.py -v`
Expected: FAIL with `AttributeError: module 'insights.scrobbles' has no attribute 'insert_scrobbles'`

- [ ] **Step 3: Implement `insert_scrobbles` and `sync_scrobbles`**

Append to `insights/scrobbles.py`:

```python
def insert_scrobbles(conn, rows: list[dict]) -> int:
    """INSERT OR IGNORE rows; return the number actually inserted."""
    if not rows:
        return 0
    before = conn.total_changes
    conn.executemany(
        "INSERT OR IGNORE INTO scrobbles "
        "(ts, artist, track, album, artist_mbid, recording_mbid) "
        "VALUES (:ts, :artist, :track, :album, :artist_mbid, :recording_mbid)",
        rows,
    )
    conn.commit()
    return conn.total_changes - before


def sync_scrobbles(client, username: str, conn, *, page_limit: int = 200,
                   max_pages: int | None = None) -> dict:
    """Incrementally pull scrobbles into the store, newest pages first.

    Resumes from sync_state['last_ts'] using the API's `from` filter, and
    relies on INSERT OR IGNORE to dedup the boundary play. Returns
    {"inserted", "pages", "last_ts"}.
    """
    from insights import db

    last_ts = int(db.get_state(conn, "last_ts", "0") or 0)
    inserted = 0
    page = 1
    while True:
        params = {"user": username, "limit": page_limit, "page": page}
        if last_ts:
            params["from"] = last_ts
        data = client.call("user.getRecentTracks", **params)
        rows = parse_recent_tracks(data)
        inserted += insert_scrobbles(conn, rows)
        pages = total_pages(data)
        if page >= pages or (max_pages and page >= max_pages) or not rows:
            break
        page += 1

    newest = conn.execute("SELECT MAX(ts) FROM scrobbles").fetchone()[0]
    if newest is not None:
        db.set_state(conn, "last_ts", str(newest))
    return {"inserted": inserted, "pages": page, "last_ts": newest}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/insights/test_scrobbles.py -v`
Expected: PASS (7 tests total in file)

- [ ] **Step 5: Commit**

```bash
git add insights/scrobbles.py tests/insights/test_scrobbles.py
git commit -m "feat(insights): incremental scrobble sync with resume"
```

---

## Task 4: server worker + sync endpoints

**Files:**
- Modify: `sWebExt/py_server/server.py` (add near the enrich worker at line ~526 and routes at ~990)
- Test: `tests/server/test_routes.py`

- [ ] **Step 1: Write the failing route tests**

First inspect how `tests/server/test_routes.py` builds its Flask client (look at the top of the file and an existing enrich/status test) so the new tests match the established fixture style. Run:

`grep -n "client\|app\|/library/enrich\|fixture\|def test_enrich" tests/server/test_routes.py | head -30`

Then append tests modelled on the existing `/library/enrich/status` test. Use the same client fixture the file already uses (shown as `client` below):

```python
def test_insights_sync_status_defaults_idle(client):
    resp = client.get("/insights/sync/status")
    assert resp.status_code == 200
    assert resp.get_json()["status"] in ("idle", "ok", "started", "skipped")


def test_insights_sync_starts_worker(client, monkeypatch):
    import sWebExt.py_server.server as server

    called = {}

    def fake_sync(limit=None):
        called["ran"] = True
        return {"status": "ok"}

    monkeypatch.setattr(server, "_run_insights_sync_once", fake_sync)
    resp = client.post("/insights/sync", json={})
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "started"
```

> If `tests/server/test_routes.py` imports the server module under a different path/name, mirror that import exactly (check the top of the file). Match the existing client-fixture name.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/server/test_routes.py -k insights -v`
Expected: FAIL with 404 (routes not registered yet)

- [ ] **Step 3: Add worker state + helpers near the enrich worker**

In `sWebExt/py_server/server.py`, next to the existing `_enrich_running` / `_enrich_last_result` declarations (around line 71-72), add:

```python
_insights_running = threading.Lock()
_insights_last_result: dict = {"status": "idle"}
```

Then, next to `_run_enrich_once` (around line 526), add:

```python
def _insights_db_path() -> str:
    cfg = _get_config()
    insights_cfg = cfg.get("insights") or {}
    return insights_cfg.get("db_path") or os.path.join(_PROJECT_ROOT, "insights.db")


def _run_insights_sync_once(limit=None) -> dict:
    global _insights_last_result
    if not _insights_running.acquire(blocking=False):
        return {"status": "skipped", "reason": "already running"}
    try:
        from discover.config import load_config
        cfg = load_config(_CONFIG_PATH)
        api_key = cfg.get("lastfm_api_key", "")
        username = cfg.get("lastfm_username", "")
        if not api_key or not username:
            result = {"status": "disabled",
                      "reason": "lastfm_api_key/lastfm_username not configured"}
            _insights_last_result = result
            return result

        from lastfm.client import LastFMClient
        from insights import db as insights_db
        from insights.scrobbles import sync_scrobbles

        lfm = LastFMClient(api_key)
        conn = insights_db.connect(_insights_db_path())
        try:
            synced = sync_scrobbles(lfm, username, conn, max_pages=limit)
        finally:
            conn.close()
        result = {"status": "ok", **synced}
        logger.info("[INSIGHTS] sync complete: %s", result)
        _insights_last_result = result
        return result
    except Exception as e:
        logger.exception("[INSIGHTS] sync failed")
        result = {"status": "error", "error": str(e)}
        _insights_last_result = result
        return result
    finally:
        _insights_running.release()
```

- [ ] **Step 4: Add the routes near the enrich routes**

Next to the `/library/enrich` routes (around line 981-992), add:

```python
@app.route("/insights/sync", methods=["POST"])
def insights_sync():
    body = request.get_json(force=True, silent=True) or {}
    limit = body.get("limit", None)
    t = threading.Thread(target=_run_insights_sync_once,
                         kwargs={"limit": limit}, daemon=True)
    t.start()
    return jsonify({"status": "started"})


@app.route("/insights/sync/status", methods=["GET"])
def insights_sync_status():
    return jsonify(_insights_last_result)
```

- [ ] **Step 5: Run the route tests to verify they pass**

Run: `pytest tests/server/test_routes.py -k insights -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Run the full insights + server suite**

Run: `pytest tests/insights tests/server/test_routes.py -q`
Expected: PASS (all green)

- [ ] **Step 7: Commit**

```bash
git add sWebExt/py_server/server.py tests/server/test_routes.py
git commit -m "feat(insights): sync worker + /insights/sync endpoints"
```

---

## Task 5: gitignore the DB + manual smoke check

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: Ignore the runtime DB**

Add to `.gitignore` (and its WAL/SHM sidecars):

```
insights.db
insights.db-wal
insights.db-shm
```

- [ ] **Step 2: Run the whole test suite to confirm no regressions**

Run: `pytest tests/ -q`
Expected: PASS — the pre-existing 386 tests plus the new insights tests, no failures.

- [ ] **Step 3: (Optional) live smoke check**

Only if a real `lastfm_api_key` + `lastfm_username` are configured, against the running server:

Run: `curl -s -X POST localhost:5000/insights/sync -d '{"limit": 2}' -H 'Content-Type: application/json' && sleep 5 && curl -s localhost:5000/insights/sync/status`
Expected: status transitions to `ok` with a non-zero `inserted`/`last_ts`; `insights.db` appears in the project root.

- [ ] **Step 4: Commit**

```bash
git add .gitignore
git commit -m "chore(insights): gitignore runtime SQLite db"
```

---

## Self-Review

**Spec coverage (Phase 1 scope):**
- §1 data layer / schema → Task 1 (all four tables + indexes, idempotent, WAL).
- §2 ingestion (pagination, now-playing skip, dedup, resume via `last_ts`, UTC) → Tasks 2-3.
- §6 sync endpoints + background worker → Task 4.
- Runtime DB gitignored (spec: "gitignored") → Task 5.

Genre/feature tables exist in the schema (Task 1) but are intentionally unused until Phases 2-3 — they live in the schema now so later phases need no migration.

**Placeholder scan:** No TBD/TODO; every code step shows complete code; commands have expected output. The two `> If ...` notes in Task 4 are explicit instructions to match the existing test fixture/import style, not deferred work.

**Type consistency:** `db.connect/init_schema/get_state/set_state`, `scrobbles.parse_recent_tracks/total_pages/insert_scrobbles/sync_scrobbles`, and `_run_insights_sync_once/_insights_db_path` are named identically everywhere referenced. `sync_scrobbles` returns `{"inserted","pages","last_ts"}` and the worker spreads it into the status dict consistently. The worker passes the route's `limit` as `max_pages` (a page cap for smoke tests) — matching `sync_scrobbles`'s signature.

---

## Next phases (separate plans, generated when we reach them)

2. Genre cache + temporal/genre analytics + read endpoints
3. Audio features (AcousticBrainz + librosa) + feature analytics
4. INSIGHTS UI screen + `charts.js`
5. Library cross-ref + discovery integration
