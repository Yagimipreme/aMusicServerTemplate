# Listening Insights — Phase 5: Library Cross-Ref + Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax. Tasks 1–4 are TDD (pytest); Task 5 is the UI (visual check, no JS harness).

**Goal:** Cross-reference scrobbles against the local library to show how much of your listening is in your collection, surface your **most-played tracks that are NOT in the library** as one-click acquisitions, and add the **Discovery** section to the INSIGHTS screen.

**Architecture:** A new `library_tracks` table in the insights DB holds normalized `(artist, track)` keys of the local library, populated by `insights/library_index.py` (scans `song_dir` via `library/scanner.py`). `insights/analytics.py` gains `library_overlap` + `missing_favorites` (JOIN scrobbles against `library_tracks`). `server.py` adds `GET /insights/discovery` and indexes the library during the scrobble-sync worker (best-effort). The INSIGHTS screen gains a Discovery section whose "acquire" button feeds the existing `/import/tracks` pipeline.

**Tech Stack:** stdlib `sqlite3`, `library/scanner.py`, Flask, `pytest`; vanilla JS for the UI.

**Spec:** `docs/superpowers/specs/2026-06-14-listening-insights-analytics-design.md` §5 (library cross-ref), §6 (`/insights/discovery`), §7 (Discovery section).

**Builds on:** all prior phases (shipped to `bare_bones`). `analytics.py` helpers (`_period_where`, `_and`), the scrobble-sync worker `_run_insights_sync_once`, `renderInsights()`, and the existing `/import/tracks` endpoint.

**Test command:** `/home/taichi/repos/musicServer/aMusicServerTemplate/.venv/bin/python -m pytest <args>` from the worktree root.

**Normalization note:** library names (ID3 tags) and scrobble names (Last.fm) differ in case/whitespace and sometimes spelling. We match on `strip().lower()` of artist + track — `library_index` stores that form, and the SQL JOIN uses `lower(trim(...))` on the scrobbles side (aligned for ASCII; spelling mismatches are an accepted, documented limitation).

---

## File Structure

- Modify `insights/db.py` — add the `library_tracks` table to `_SCHEMA` (idempotent `IF NOT EXISTS`).
- Create `insights/library_index.py` — `normalize(s)`, `index_library(conn, song_dir, scan=None)`.
- Modify `insights/analytics.py` — append `library_overlap`, `missing_favorites`.
- Modify `sWebExt/py_server/server.py` — `GET /insights/discovery`; index the library in `_run_insights_sync_once`.
- Modify `web/static/app.js` — Discovery section in `renderInsights` + acquire wiring.
- Tests: `tests/insights/test_library_index.py`, extend `tests/insights/test_analytics.py`, `tests/server/test_routes.py`.

---

## Task 1: library_tracks table (`insights/db.py`)

**Files:** Modify `insights/db.py`; Test `tests/insights/test_db.py`.

- [ ] **Step 1: Add a failing test** to `tests/insights/test_db.py`:

```python
def test_connect_creates_library_tracks(tmp_path):
    conn = db.connect(str(tmp_path / "insights.db"))
    names = _tables(conn)
    assert "library_tracks" in names
    # columns
    cols = {r[1] for r in conn.execute("PRAGMA table_info(library_tracks)").fetchall()}
    assert {"artist", "track"} <= cols
```

- [ ] **Step 2: Run, verify fail** — `.venv/bin/python -m pytest tests/insights/test_db.py::test_connect_creates_library_tracks -v` → FAIL (`library_tracks` not in tables).

- [ ] **Step 3: Add the table** to `_SCHEMA` in `insights/db.py` (after the `track_features` table block, before `sync_state`):

```sql

CREATE TABLE IF NOT EXISTS library_tracks (
    artist TEXT NOT NULL,
    track  TEXT NOT NULL,
    PRIMARY KEY (artist, track)
);
```

- [ ] **Step 4: Run** — `pytest tests/insights/test_db.py -v` → all pass.

- [ ] **Step 5: Commit**

```bash
git add insights/db.py tests/insights/test_db.py
git commit -m "feat(insights): library_tracks table for cross-reference

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: library indexer (`insights/library_index.py`)

**Files:** Create `insights/library_index.py`; Test `tests/insights/test_library_index.py`.

- [ ] **Step 1: Write failing tests** — create `tests/insights/test_library_index.py`:

```python
"""Tests for insights/library_index.py — local library → library_tracks."""

from insights import db, library_index


def test_normalize():
    assert library_index.normalize("  Aphex Twin ") == "aphex twin"
    assert library_index.normalize(None) == ""


def test_index_library_populates_normalized(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    fake = [
        {"artist": "Aphex Twin", "title": "Xtal"},
        {"artist": "  BURIAL ", "title": "Archangel"},
        {"artist": "", "title": "Untagged"},          # skipped (no artist)
        {"artist": "Aphex Twin", "title": "Xtal"},      # dup → one row
    ]
    n = library_index.index_library(conn, "/music", scan=lambda d: fake)
    rows = conn.execute("SELECT artist, track FROM library_tracks ORDER BY artist").fetchall()
    assert ("aphex twin", "xtal") in [(r["artist"], r["track"]) for r in rows]
    assert ("burial", "archangel") in [(r["artist"], r["track"]) for r in rows]
    assert len(rows) == 2          # blank-artist skipped, dup collapsed
    assert n == 2


def test_index_library_is_idempotent_and_refreshes(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    library_index.index_library(conn, "/m", scan=lambda d: [{"artist": "A", "title": "t1"}])
    # Re-index with a DIFFERENT library → old rows cleared, new ones present.
    library_index.index_library(conn, "/m", scan=lambda d: [{"artist": "B", "title": "t2"}])
    rows = {(r["artist"], r["track"]) for r in
            conn.execute("SELECT artist, track FROM library_tracks").fetchall()}
    assert rows == {("b", "t2")}
```

- [ ] **Step 2: Run, verify fail** — `No module named 'insights.library_index'`.

- [ ] **Step 3: Implement `insights/library_index.py`**

```python
"""Index the local library's (artist, track) keys into library_tracks.

Stored normalized (strip().lower()) so the analytics JOIN can match
Last.fm scrobble names via SQL lower(trim(...)). Spelling differences
between ID3 tags and Last.fm names are an accepted limitation.
"""

import logging

logger = logging.getLogger(__name__)


def normalize(s) -> str:
    return (s or "").strip().lower()


def index_library(conn, song_dir, scan=None) -> int:
    """Rebuild library_tracks from a fresh scan of song_dir.

    Clears the table then repopulates, so tracks removed from disk drop out.
    `scan` is injectable for tests; defaults to library.scanner.scan.
    Returns the number of distinct library tracks indexed.
    """
    if scan is None:
        from library.scanner import scan as _scan
        scan = _scan
    try:
        records = scan(song_dir)
    except Exception:
        logger.warning("library_index: scan failed for %s", song_dir, exc_info=True)
        return 0

    rows = []
    for rec in records:
        a = normalize(rec.get("artist"))
        t = normalize(rec.get("title"))
        if a and t:
            rows.append((a, t))

    conn.execute("DELETE FROM library_tracks")
    conn.executemany(
        "INSERT OR IGNORE INTO library_tracks (artist, track) VALUES (?, ?)", rows)
    conn.commit()
    return conn.execute("SELECT COUNT(*) FROM library_tracks").fetchone()[0]
```

- [ ] **Step 4: Run** — `pytest tests/insights/test_library_index.py -v` → 3 pass.

- [ ] **Step 5: Commit**

```bash
git add insights/library_index.py tests/insights/test_library_index.py
git commit -m "feat(insights): library indexer (normalized track keys)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: cross-ref analytics (`insights/analytics.py`)

**Files:** Modify `insights/analytics.py`; Test `tests/insights/test_analytics.py` (append).

- [ ] **Step 1: Append failing tests** to `tests/insights/test_analytics.py`:

```python
def _seed_library(conn, scrobble_rows, library_keys):
    conn.executemany(
        "INSERT INTO scrobbles (ts, artist, track) VALUES (?, ?, ?)", scrobble_rows)
    for a, t in library_keys:
        conn.execute("INSERT OR IGNORE INTO library_tracks (artist, track) VALUES (?, ?)",
                     (a, t))
    conn.commit()


def test_library_overlap(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    # 3 distinct tracks; "A/t1" (played twice) is in library, the others are not.
    _seed_library(conn,
        [(1, "A", "t1"), (2, "A", "t1"), (3, "B", "t2"), (4, "C", "t3")],
        [("a", "t1")])
    ov = analytics.library_overlap(conn, period="all", tz_offset_min=0, now_ts=NOW)
    assert ov["tracks_total"] == 3
    assert ov["tracks_in_library"] == 1
    assert ov["plays_total"] == 4
    assert ov["plays_in_library"] == 2          # t1 played twice
    assert abs(ov["plays_pct"] - 0.5) < 1e-6


def test_missing_favorites(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    _seed_library(conn,
        [(1, "A", "t1"), (2, "B", "t2"), (3, "B", "t2"), (4, "B", "t2")],
        [("a", "t1")])
    mf = analytics.missing_favorites(conn, period="all", tz_offset_min=0, now_ts=NOW, limit=10)
    # B/t2 is most-played and NOT in library; A/t1 is in library → excluded.
    assert mf[0] == {"artist": "B", "track": "t2", "plays": 3}
    assert all(not (m["artist"] == "A") for m in mf)
```

- [ ] **Step 2: Run, verify fail** — `module 'insights.analytics' has no attribute 'library_overlap'`.

- [ ] **Step 3: Append implementation** to `insights/analytics.py`:

```python
def library_overlap(conn, period="all", tz_offset_min=0, now_ts=None):
    """How much in-period listening is in the local library.

    Returns {tracks_total, tracks_in_library, track_pct, plays_total,
    plays_in_library, plays_pct}. Match is on normalized lower(trim) keys.
    """
    where, params = _period_where(period, now_ts)
    clause = _and(where)
    in_lib = ("EXISTS (SELECT 1 FROM library_tracks l "
              "WHERE l.artist = lower(trim(s.artist)) AND l.track = lower(trim(s.track)))")
    row = conn.execute(
        f"SELECT COUNT(*) AS plays_total, "
        f"SUM(CASE WHEN {in_lib} THEN 1 ELSE 0 END) AS plays_in, "
        f"COUNT(DISTINCT s.artist || char(31) || s.track) AS tracks_total, "
        f"COUNT(DISTINCT CASE WHEN {in_lib} THEN s.artist || char(31) || s.track END) AS tracks_in "
        f"FROM scrobbles s {clause}", params).fetchone()
    plays_total = row["plays_total"] or 0
    plays_in = row["plays_in"] or 0
    tracks_total = row["tracks_total"] or 0
    tracks_in = row["tracks_in"] or 0
    return {
        "tracks_total": tracks_total,
        "tracks_in_library": tracks_in,
        "track_pct": (tracks_in / tracks_total) if tracks_total else 0.0,
        "plays_total": plays_total,
        "plays_in_library": plays_in,
        "plays_pct": (plays_in / plays_total) if plays_total else 0.0,
    }


def missing_favorites(conn, period="all", tz_offset_min=0, now_ts=None, limit=25):
    """Most-played in-period tracks NOT in the local library.

    [{"artist","track","plays"}] — shaped to feed /import/tracks for one-click
    acquisition.
    """
    where, params = _period_where(period, now_ts)
    clause = _and(where)
    rows = conn.execute(
        f"SELECT s.artist AS artist, s.track AS track, COUNT(*) AS n FROM scrobbles s "
        f"{clause} {'AND' if where else 'WHERE'} NOT EXISTS "
        f"(SELECT 1 FROM library_tracks l "
        f" WHERE l.artist = lower(trim(s.artist)) AND l.track = lower(trim(s.track))) "
        f"GROUP BY s.artist, s.track ORDER BY n DESC LIMIT ?",
        params + [limit]).fetchall()
    return [{"artist": r["artist"], "track": r["track"], "plays": r["n"]} for r in rows]
```

- [ ] **Step 4: Run** — `pytest tests/insights/test_analytics.py -v` → all pass.

- [ ] **Step 5: Commit**

```bash
git add insights/analytics.py tests/insights/test_analytics.py
git commit -m "feat(insights): library_overlap + missing_favorites analytics

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: /insights/discovery + library indexing in the sync worker

**Files:** Modify `sWebExt/py_server/server.py`; Test `tests/server/test_routes.py`.

- [ ] **Step 1: Append failing route test** to `tests/server/test_routes.py` (real `client` fixture):

```python
def _seed_discovery_db(path):
    from insights import db as idb
    conn = idb.connect(path)
    conn.executemany(
        "INSERT INTO scrobbles (ts, artist, track) VALUES (?, ?, ?)",
        [(1700000000, "A", "t1"), (1700000001, "B", "t2"), (1700000002, "B", "t2")])
    conn.execute("INSERT INTO library_tracks (artist, track) VALUES ('a', 't1')")
    conn.commit(); conn.close()


def test_insights_discovery_endpoint(client, monkeypatch, tmp_path):
    import sWebExt.py_server.server as server
    dbp = str(tmp_path / "i.db"); _seed_discovery_db(dbp)
    monkeypatch.setattr(server, "_insights_db_path", lambda: dbp)
    resp = client.get("/insights/discovery?tz=0")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "overlap" in body and "missing_favorites" in body
    assert body["overlap"]["tracks_in_library"] == 1
    assert body["missing_favorites"][0]["track"] == "t2"   # most-played not in library
```

- [ ] **Step 2: Run, verify fail** — 404.

- [ ] **Step 3: Add the route** next to the other `/insights` read routes in `server.py`:

```python
@app.route("/insights/discovery", methods=["GET"])
def insights_discovery():
    from insights import db as insights_db, analytics
    period, tz = _insights_query_args()
    conn = insights_db.connect(_insights_db_path())
    try:
        return jsonify({
            "overlap": analytics.library_overlap(conn, period=period, tz_offset_min=tz),
            "missing_favorites": analytics.missing_favorites(conn, period=period, tz_offset_min=tz),
        })
    finally:
        conn.close()
```

- [ ] **Step 4: Index the library during scrobble sync.** In `_run_insights_sync_once`, inside the inner `try` after the genre-tagging block (after `synced["artists_tagged"] = tagged`), add a best-effort library index:

```python
            from insights.library_index import index_library
            song_dir = cfg.get("song_dir", "")
            if song_dir:
                synced["library_tracks"] = index_library(conn, song_dir)
```

(`cfg` and `conn` are already in scope in that function; `index_library` is best-effort — it returns 0 and logs on scan failure, so it won't break sync.)

- [ ] **Step 5: Run** — `pytest tests/server/test_routes.py -k "discovery" -v` (pass) then `pytest tests/ -q` (all green).

- [ ] **Step 6: Commit**

```bash
git add sWebExt/py_server/server.py tests/server/test_routes.py
git commit -m "feat(insights): /insights/discovery endpoint + library indexing on sync

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Discovery section in the INSIGHTS screen (`web/static/app.js`)

**Files:** Modify `web/static/app.js`. Visual verification (no JS harness).

- [ ] **Step 1: Add a Discovery section** to `renderInsights`, after the Sound section. It fetches `/insights/discovery`, shows the overlap stat, and lists missing favorites with an "Acquire all" button that posts to `/import/tracks`:

```javascript
  // Discovery
  let disc;
  try { disc = await API('/insights/discovery' + q); } catch (e) { disc = null; }
  if (disc) {
    const sD = _section('Discovery');
    const o = disc.overlap;
    const stat = document.createElement('div'); stat.className = 'ins-cov';
    stat.textContent = `${(o.plays_pct * 100).toFixed(0)}% of your plays are in your library ` +
      `(${o.tracks_in_library}/${o.tracks_total} tracks)`;
    sD.append(stat);
    if (disc.missing_favorites.length) {
      const cap = document.createElement('div'); cap.className = 'ins-chart';
      const t = document.createElement('div'); t.className = 'cap';
      t.textContent = 'Most-played tracks not in your library';
      cap.append(t);
      disc.missing_favorites.slice(0, 15).forEach(m => {
        const row = document.createElement('div'); row.className = 'ins-missing';
        const name = document.createElement('span');
        name.textContent = `${m.artist} — ${m.track}`;
        const plays = document.createElement('span'); plays.className = 'plays';
        plays.textContent = `${m.plays}`;
        row.append(name, plays); cap.append(row);
      });
      const acq = document.createElement('button'); acq.textContent = 'Acquire all';
      acq.onclick = () => _acquireMissing(disc.missing_favorites.slice(0, 15), acq);
      cap.append(acq);
      sD.append(cap);
    }
    body.append(sD);
  }
```

(place this block just before the end of `renderInsights`, after `body.append(sS);`)

- [ ] **Step 2: Add the acquire helper** near `_runSync`:

```javascript
async function _acquireMissing(items, btn) {
  const orig = btn.textContent; btn.disabled = true; btn.textContent = '…queued';
  try {
    await API('/import/tracks', {method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        playlist_name: 'Insights favourites',
        tracks: items.map(m => ({artist: m.artist, title: m.track})),
      })});
    btn.textContent = '✓ queued';
  } catch (e) { btn.textContent = '! failed'; }
  setTimeout(() => { btn.disabled = false; btn.textContent = orig; }, 2500);
}
```

- [ ] **Step 3: Add styles** to `web/static/app.css` (append):

```css
.ins-missing{display:flex;justify-content:space-between;padding:4px 0;
  border-bottom:1px solid var(--line);font-size:12px;color:var(--txt)}
.ins-missing .plays{color:var(--mut);font-family:'JetBrains Mono',monospace}
```

- [ ] **Step 4: Syntax check** — `node --check web/static/app.js` → no output. `grep -c innerHTML web/static/app.js` → unchanged (0 new).

> NOTE: confirm the `/import/tracks` body shape (`playlist_name` + `tracks:[{artist,title}]`) against the existing `import_tracks()` handler in server.py before finalizing — adjust the field names in `_acquireMissing` if the handler expects different keys. Read `def import_tracks` first.

- [ ] **Step 5: Commit**

```bash
git add web/static/app.js web/static/app.css
git commit -m "feat(insights-ui): Discovery section — library overlap + acquire missing favourites

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 6: Visual verification (controller-run)** — seed a DB with scrobbles + a partial `library_tracks` set, launch the server read-only, and confirm (browser if available; else the Node-DOM-shim approach used in Phase 4) that the Discovery section shows the overlap stat + missing-favourites list and the Acquire button posts. Confirm no console errors.

---

## Self-Review

**Spec coverage (Phase 5):** library cross-ref `library_overlap` + `missing_favorites` (§5) → Tasks 1–3; `/insights/discovery` (§6) → Task 4; library indexing wired into sync → Task 4; Discovery UI section feeding `/import/tracks` (§5 "one-click acquire", §7 Discovery section) → Task 5. This completes the spec's 5-section screen.

**Placeholder scan:** complete code throughout. Task 5 step 4 has an explicit "confirm `/import/tracks` body shape" instruction (read the handler) — a real verification step, not deferred work.

**Type consistency:** `library_index.normalize` = `strip().lower()`, matching the SQL `lower(trim(...))` in `library_overlap`/`missing_favorites`. `missing_favorites` returns `{artist,track,plays}`, consumed by the UI and mapped to `{artist,title}` for `/import/tracks`. `library_overlap` dict shape matches the route + UI consumers. Reuses `_period_where`/`_and`.

**Edge cases:** empty library (`library_tracks` empty) → overlap 0%, all favourites "missing" (correct); empty scrobbles → guarded pcts (div-by-zero → 0.0); `index_library` scan failure → returns 0, logged, sync continues; re-index clears stale rows.

---

## Done after this

Insights P1–P5 complete: store → analytics → audio features → UI → discovery. The feature is feature-complete per the original spec. Remaining nice-to-haves (not in the 5-phase spec): per-track mood-vector drill-down, a "re-scan NULL-source features" recovery path, scheduled (vs on-demand) sync triggers.
