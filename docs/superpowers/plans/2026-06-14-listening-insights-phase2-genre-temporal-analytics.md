# Listening Insights — Phase 2: Genre Cache + Temporal/Genre Analytics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the stored scrobble history into insight: cache each artist's genre, compute temporal/genre/entity aggregations in SQL, and expose them via three read-only endpoints (`/insights/overview`, `/insights/temporal`, `/insights/genres`).

**Architecture:** `insights/genres.py` fills the `artist_tags` cache (reusing `lastfm/tags.py`). `insights/analytics.py` holds pure query functions over the SQLite store — each takes `(conn, period, tz_offset_min, now_ts)` and returns JSON-able dicts. All hour/day bucketing converts the UTC `ts` to the user's local time via a tz offset (minutes). `server.py` adds three GET endpoints (each accepting `?period=&tz=`) and extends the sync worker to opportunistically tag newly-seen artists.

**Tech Stack:** Python stdlib `sqlite3`, `time`; existing `lastfm/client.py` + `lastfm/tags.py`; Flask; `pytest` + `unittest.mock`.

**Spec:** `docs/superpowers/specs/2026-06-14-listening-insights-analytics-design.md` (Phase 2 = §3, §5 temporal/genre/entities + overview, and the `/insights/overview|temporal|genres` part of §6).

**Builds on Phase 1:** `insights/db.py` (`connect`, `get_state`, `set_state`), `insights/scrobbles.py` (`sync_scrobbles`), the `scrobbles` + `artist_tags` tables, and the `_run_insights_sync_once` worker.

**Test command (IMPORTANT):** system `python3` has no pytest. Use
`/home/taichi/repos/musicServer/aMusicServerTemplate/.venv/bin/python -m pytest <args>` run from the worktree root
`/home/taichi/repos/musicServer/aMusicServerTemplate/.claude/worktrees/insights`.

---

## File Structure

- Create `insights/genres.py` — `ensure_artist_tags(client, conn, artists)`, `primary_genre_for(tags)`. Owns the genre cache.
- Create `insights/analytics.py` — period/tz helpers + all temporal/genre/entity/overview query functions. Pure reads; no network.
- Modify `sWebExt/py_server/server.py` — three GET endpoints; extend `_run_insights_sync_once` to tag new artists after a sync.
- Create `tests/insights/test_genres.py`, `tests/insights/test_analytics.py`.
- Modify `tests/server/test_routes.py` — read-endpoint tests.

Conventions: mirror Phase 1 style (module docstring, defensive parsing, `logger = logging.getLogger(__name__)`). Analytics functions accept `now_ts: int | None = None`, defaulting to `int(time.time())`, so tests are deterministic.

---

## Task 1: genre cache (`insights/genres.py`)

**Files:** Create `insights/genres.py`; Test `tests/insights/test_genres.py`.

- [ ] **Step 1: Write failing tests** — create `tests/insights/test_genres.py`:

```python
"""Tests for insights/genres.py — artist genre cache."""

from unittest.mock import MagicMock

from insights import db, genres


def test_primary_genre_for_picks_highest_weight():
    tags = [{"name": "techno", "weight": 100}, {"name": "house", "weight": 40}]
    assert genres.primary_genre_for(tags) == "techno"


def test_primary_genre_for_empty_is_none():
    assert genres.primary_genre_for([]) is None


def test_ensure_artist_tags_fetches_and_caches(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    conn.execute("INSERT INTO scrobbles (ts, artist, track) VALUES (1, 'Surgeon', 'X')")
    conn.commit()

    client = MagicMock()
    # lastfm/tags.get_artist_tags calls client.call("artist.getTopTags", ...)
    client.call.return_value = {"toptags": {"tag": [
        {"name": "techno", "count": 100},
        {"name": "seen live", "count": 90},  # noise — filtered by lastfm.tags
        {"name": "industrial techno", "count": 50},
    ]}}

    n = genres.ensure_artist_tags(client, conn, ["Surgeon"])
    assert n == 1
    row = conn.execute(
        "SELECT primary_genre, tags_json, fetched_at FROM artist_tags WHERE artist='Surgeon'"
    ).fetchone()
    assert row["primary_genre"] == "techno"
    assert "techno" in row["tags_json"]
    assert row["fetched_at"] is not None


def test_ensure_artist_tags_skips_already_cached(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    conn.execute(
        "INSERT INTO artist_tags (artist, tags_json, primary_genre, fetched_at) "
        "VALUES ('Surgeon', '[]', 'techno', 123)"
    )
    conn.commit()
    client = MagicMock()
    n = genres.ensure_artist_tags(client, conn, ["Surgeon"])
    assert n == 0
    client.call.assert_not_called()


def test_ensure_artist_tags_caches_empty_result_to_avoid_refetch(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    client = MagicMock()
    client.call.return_value = {"toptags": {"tag": []}}
    n = genres.ensure_artist_tags(client, conn, ["Unknown Artist"])
    assert n == 1
    row = conn.execute(
        "SELECT primary_genre, tags_json FROM artist_tags WHERE artist='Unknown Artist'"
    ).fetchone()
    assert row["primary_genre"] is None
    assert row["tags_json"] == "[]"
    # A second pass must not re-call the API (negative cache).
    genres.ensure_artist_tags(client, conn, ["Unknown Artist"])
    assert client.call.call_count == 1
```

- [ ] **Step 2: Run, verify fail**

Run: `.venv/bin/python -m pytest tests/insights/test_genres.py -v` → FAIL (`No module named 'insights.genres'`).

- [ ] **Step 3: Implement `insights/genres.py`**

```python
"""Artist genre cache for listening insights.

Reuses lastfm/tags.py (noise-filtered, weight-ranked top tags). Each artist
is fetched once; an artist with no usable tags is cached with an empty tag
list + NULL primary_genre so we never re-query it (negative cache).
"""

import json
import logging
import time

logger = logging.getLogger(__name__)


def primary_genre_for(tags: list[dict]) -> "str | None":
    """Highest-weighted tag name, or None for an empty tag set.

    lastfm.tags.get_artist_tags already returns tags sorted by descending
    weight, but we do not rely on order here — we pick the max explicitly.
    """
    if not tags:
        return None
    return max(tags, key=lambda t: t.get("weight", 0)).get("name")


def cached_artists(conn) -> set:
    """Artist names already present in the artist_tags cache."""
    rows = conn.execute("SELECT artist FROM artist_tags").fetchall()
    return {r[0] for r in rows}


def ensure_artist_tags(client, conn, artists) -> int:
    """Fetch + cache genre tags for any of `artists` not already cached.

    Returns the number of artists newly written. Network failures for a
    single artist are swallowed by lastfm.tags (returns []), so that artist
    is cached as "no genre" and not retried.
    """
    from lastfm.tags import get_artist_tags

    have = cached_artists(conn)
    written = 0
    for artist in artists:
        if not artist or artist in have:
            continue
        tags = get_artist_tags(client, artist)
        conn.execute(
            "INSERT OR REPLACE INTO artist_tags "
            "(artist, tags_json, primary_genre, fetched_at) VALUES (?, ?, ?, ?)",
            (artist, json.dumps(tags), primary_genre_for(tags), int(time.time())),
        )
        have.add(artist)
        written += 1
    if written:
        conn.commit()
    return written
```

- [ ] **Step 4: Run, verify pass**

Run: `.venv/bin/python -m pytest tests/insights/test_genres.py -v` → 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add insights/genres.py tests/insights/test_genres.py
git commit -m "feat(insights): artist genre cache with negative caching

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: temporal analytics (`insights/analytics.py`)

**Files:** Create `insights/analytics.py`; Test `tests/insights/test_analytics.py`.

Note on time math: `ts` is unix-UTC. Local-time bucketing uses
`strftime(fmt, ts + <offset_seconds>, 'unixepoch')`, where `<offset_seconds>`
is `int(tz_offset_min) * 60` (coerced to int, then inlined — safe, never user
string). `%H` → hour `'00'..'23'`; `%w` → day-of-week `'0'`(Sun)`..'6'`(Sat).

- [ ] **Step 1: Write failing tests** — create `tests/insights/test_analytics.py`:

```python
"""Tests for insights/analytics.py — temporal aggregations."""

from insights import db, analytics

# Fixture timestamps (unix UTC):
#   1700000000 = 2023-11-14 22:13:20 UTC  (Tuesday)
#   1700003600 = 2023-11-14 23:13:20 UTC  (Tuesday)
#   1699920000 = 2023-11-14 00:00:00 UTC  (Tuesday)
NOW = 1700100000  # 2023-11-16, used as "now" for period cutoffs


def _seed(conn, rows):
    conn.executemany(
        "INSERT INTO scrobbles (ts, artist, track) VALUES (?, ?, ?)", rows
    )
    conn.commit()


def test_listening_clock_buckets_by_local_hour(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    _seed(conn, [(1700000000, "A", "t1"), (1700003600, "A", "t2")])
    # UTC hours 22 and 23; with +60 min offset they become 23 and 00.
    clock = analytics.listening_clock(conn, period="all", tz_offset_min=60, now_ts=NOW)
    assert len(clock["hours"]) == 24
    assert clock["hours"][23] == 1  # 22:13 UTC + 1h = 23:xx local
    assert clock["hours"][0] == 1   # 23:13 UTC + 1h = 00:xx local

    # With no offset, both fall in UTC hours 22 and 23.
    clock_utc = analytics.listening_clock(conn, period="all", tz_offset_min=0, now_ts=NOW)
    assert clock_utc["hours"][22] == 1
    assert clock_utc["hours"][23] == 1


def test_listening_clock_respects_period(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    old = NOW - 40 * 86400   # 40 days before NOW
    recent = NOW - 2 * 86400
    _seed(conn, [(old, "A", "t1"), (recent, "A", "t2")])
    total_30d = sum(analytics.listening_clock(
        conn, period="30d", tz_offset_min=0, now_ts=NOW)["hours"])
    assert total_30d == 1  # only the recent play
    total_all = sum(analytics.listening_clock(
        conn, period="all", tz_offset_min=0, now_ts=NOW)["hours"])
    assert total_all == 2


def test_hour_day_heatmap_shape_and_placement(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    _seed(conn, [(1700000000, "A", "t1")])  # Tue 22:13 UTC → dow '2'
    hm = analytics.hour_day_heatmap(conn, period="all", tz_offset_min=0, now_ts=NOW)
    assert len(hm["matrix"]) == 7
    assert all(len(row) == 24 for row in hm["matrix"])
    assert hm["matrix"][2][22] == 1  # Tuesday, hour 22


def test_weekday_weekend_split(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    # 1700000000 = Tue (weekday); 1699747200 = 2023-11-12 00:00 UTC = Sun (weekend)
    _seed(conn, [(1700000000, "A", "t1"), (1699747200, "A", "t2")])
    ww = analytics.weekday_weekend(conn, period="all", tz_offset_min=0, now_ts=NOW)
    assert ww["weekday"] == 1
    assert ww["weekend"] == 1


def test_plays_over_time_daily_buckets(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    _seed(conn, [(1700000000, "A", "t1"), (1700003600, "A", "t2")])  # same UTC day
    pot = analytics.plays_over_time(conn, period="all", tz_offset_min=0, now_ts=NOW)
    assert pot == [{"date": "2023-11-14", "plays": 2}]
```

- [ ] **Step 2: Run, verify fail**

Run: `.venv/bin/python -m pytest tests/insights/test_analytics.py -v` → FAIL (`No module named 'insights.analytics'`).

- [ ] **Step 3: Implement `insights/analytics.py`**

```python
"""Pure SQL aggregations over the insights store.

Every public function takes (conn, period, tz_offset_min, now_ts) and returns
JSON-able data. `ts` is unix-UTC; hour/day buckets are computed in the user's
local time via a tz offset in minutes. `now_ts` defaults to the current time
but is injectable for deterministic tests.
"""

import math
import time

# Period token -> lookback window in days. 'all' (or unknown) -> no cutoff.
_PERIOD_DAYS = {"7d": 7, "30d": 30, "90d": 90, "year": 365}


def _now(now_ts):
    return int(time.time()) if now_ts is None else int(now_ts)


def _period_where(period, now_ts):
    """Return (sql_fragment, params) restricting to the period (or ('', []))."""
    days = _PERIOD_DAYS.get(period)
    if days is None:
        return "", []
    return "ts >= ?", [_now(now_ts) - days * 86400]


def _and(where):
    """Turn a bare period fragment into a usable WHERE clause."""
    return f"WHERE {where}" if where else ""


def _offset_seconds(tz_offset_min):
    return int(tz_offset_min) * 60


def _hour_expr(tz_offset_min):
    return f"strftime('%H', ts + {_offset_seconds(tz_offset_min)}, 'unixepoch')"


def _dow_expr(tz_offset_min):
    return f"strftime('%w', ts + {_offset_seconds(tz_offset_min)}, 'unixepoch')"


def _date_expr(tz_offset_min):
    return f"strftime('%Y-%m-%d', ts + {_offset_seconds(tz_offset_min)}, 'unixepoch')"


def listening_clock(conn, period="all", tz_offset_min=0, now_ts=None):
    """Plays per local hour-of-day. Returns {"hours": [c0..c23]}."""
    where, params = _period_where(period, now_ts)
    rows = conn.execute(
        f"SELECT {_hour_expr(tz_offset_min)} AS h, COUNT(*) AS n "
        f"FROM scrobbles {_and(where)} GROUP BY h",
        params,
    ).fetchall()
    hours = [0] * 24
    for r in rows:
        hours[int(r["h"])] = r["n"]
    return {"hours": hours}


def hour_day_heatmap(conn, period="all", tz_offset_min=0, now_ts=None):
    """7x24 matrix of plays, indexed [day_of_week][hour]. dow 0 = Sunday."""
    where, params = _period_where(period, now_ts)
    rows = conn.execute(
        f"SELECT {_dow_expr(tz_offset_min)} AS d, {_hour_expr(tz_offset_min)} AS h, "
        f"COUNT(*) AS n FROM scrobbles {_and(where)} GROUP BY d, h",
        params,
    ).fetchall()
    matrix = [[0] * 24 for _ in range(7)]
    for r in rows:
        matrix[int(r["d"])][int(r["h"])] = r["n"]
    return {"matrix": matrix, "dow_labels": ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]}


def weekday_weekend(conn, period="all", tz_offset_min=0, now_ts=None):
    """Weekday (Mon-Fri) vs weekend (Sat/Sun) play counts + per-hour curves."""
    where, params = _period_where(period, now_ts)
    rows = conn.execute(
        f"SELECT {_dow_expr(tz_offset_min)} AS d, {_hour_expr(tz_offset_min)} AS h, "
        f"COUNT(*) AS n FROM scrobbles {_and(where)} GROUP BY d, h",
        params,
    ).fetchall()
    weekday = weekend = 0
    weekday_by_hour = [0] * 24
    weekend_by_hour = [0] * 24
    for r in rows:
        d, h, n = int(r["d"]), int(r["h"]), r["n"]
        if d in (0, 6):  # Sunday / Saturday
            weekend += n
            weekend_by_hour[h] += n
        else:
            weekday += n
            weekday_by_hour[h] += n
    return {
        "weekday": weekday, "weekend": weekend,
        "weekday_by_hour": weekday_by_hour, "weekend_by_hour": weekend_by_hour,
    }


def plays_over_time(conn, period="all", tz_offset_min=0, now_ts=None):
    """Plays per local calendar day. Returns [{"date": "YYYY-MM-DD", "plays": n}]."""
    where, params = _period_where(period, now_ts)
    rows = conn.execute(
        f"SELECT {_date_expr(tz_offset_min)} AS d, COUNT(*) AS n "
        f"FROM scrobbles {_and(where)} GROUP BY d ORDER BY d",
        params,
    ).fetchall()
    return [{"date": r["d"], "plays": r["n"]} for r in rows]
```

- [ ] **Step 4: Run, verify pass**

Run: `.venv/bin/python -m pytest tests/insights/test_analytics.py -v` → 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add insights/analytics.py tests/insights/test_analytics.py
git commit -m "feat(insights): temporal analytics (clock, heatmap, weekday/weekend, over-time)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: genre analytics (`insights/analytics.py`)

**Files:** Modify `insights/analytics.py`; Test `tests/insights/test_analytics.py` (append).

Genre functions JOIN `scrobbles.artist = artist_tags.artist` and use
`primary_genre` (rows with NULL primary_genre are excluded from genre stats).

- [ ] **Step 1: Append failing tests** to `tests/insights/test_analytics.py`:

```python
def _seed_with_genres(conn, scrobble_rows, artist_genre):
    conn.executemany(
        "INSERT INTO scrobbles (ts, artist, track) VALUES (?, ?, ?)", scrobble_rows)
    for artist, genre in artist_genre.items():
        conn.execute(
            "INSERT INTO artist_tags (artist, tags_json, primary_genre, fetched_at) "
            "VALUES (?, '[]', ?, 1)", (artist, genre))
    conn.commit()


def test_top_genres_ranks_with_share(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    _seed_with_genres(
        conn,
        [(1700000000, "A", "t1"), (1700000001, "A", "t2"), (1700000002, "B", "t3")],
        {"A": "techno", "B": "house"},
    )
    tg = analytics.top_genres(conn, period="all", tz_offset_min=0, now_ts=NOW, limit=10)
    assert tg[0]["genre"] == "techno"
    assert tg[0]["plays"] == 2
    assert abs(tg[0]["share"] - 2 / 3) < 1e-6


def test_top_genres_excludes_untagged(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    _seed_with_genres(conn, [(1700000000, "A", "t1"), (1700000001, "C", "t2")],
                      {"A": "techno"})  # C has no tag row
    tg = analytics.top_genres(conn, period="all", tz_offset_min=0, now_ts=NOW, limit=10)
    assert [g["genre"] for g in tg] == ["techno"]


def test_genre_by_hour_shape(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    _seed_with_genres(conn, [(1700000000, "A", "t1")], {"A": "techno"})  # UTC hour 22
    gbh = analytics.genre_by_hour(conn, period="all", tz_offset_min=0, now_ts=NOW, top_n=5)
    assert "techno" in gbh["genres"]
    assert len(gbh["data"]["techno"]) == 24
    assert gbh["data"]["techno"][22] == 1


def test_genre_diversity_counts_distinct_and_entropy(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    _seed_with_genres(
        conn,
        [(1700000000, "A", "t1"), (1700000001, "B", "t2")],
        {"A": "techno", "B": "house"},
    )
    div = analytics.genre_diversity(conn, period="all", tz_offset_min=0, now_ts=NOW)
    assert div["distinct"] == 2
    # Two equally-played genres → maximal normalized entropy ~1.0
    assert abs(div["normalized_entropy"] - 1.0) < 1e-6


def test_genre_evolution_buckets(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    # Two plays ~60 days apart, same genre.
    _seed_with_genres(
        conn,
        [(NOW - 60 * 86400, "A", "t1"), (NOW - 2 * 86400, "A", "t2")],
        {"A": "techno"},
    )
    ev = analytics.genre_evolution(conn, period="all", tz_offset_min=0, now_ts=NOW, top_n=5)
    assert "techno" in ev["genres"]
    assert len(ev["buckets"]) == len(ev["data"]["techno"])
    assert len(ev["buckets"]) >= 1
```

- [ ] **Step 2: Run, verify fail**

Run: `.venv/bin/python -m pytest tests/insights/test_analytics.py -k "genre" -v` → FAIL (`module 'insights.analytics' has no attribute 'top_genres'`).

- [ ] **Step 3: Append implementation** to `insights/analytics.py`:

```python
_GENRE_JOIN = (
    "FROM scrobbles s JOIN artist_tags a ON a.artist = s.artist "
    "WHERE a.primary_genre IS NOT NULL"
)


def _genre_where(period, now_ts):
    """Genre-join WHERE clause + params (always filters NULL genre, plus period)."""
    frag, params = _period_where(period, now_ts)
    where = _GENRE_JOIN
    if frag:
        where += f" AND s.{frag}"
    return where, params


def top_genres(conn, period="all", tz_offset_min=0, now_ts=None, limit=15):
    """Ranked genres with play share. [{"genre", "plays", "share"}]."""
    where, params = _genre_where(period, now_ts)
    rows = conn.execute(
        f"SELECT a.primary_genre AS g, COUNT(*) AS n {where} "
        f"GROUP BY g ORDER BY n DESC LIMIT ?",
        params + [limit],
    ).fetchall()
    total = sum(r["n"] for r in rows)
    return [
        {"genre": r["g"], "plays": r["n"], "share": (r["n"] / total) if total else 0.0}
        for r in rows
    ]


def _top_genre_names(conn, where, params, top_n):
    rows = conn.execute(
        f"SELECT a.primary_genre AS g, COUNT(*) AS n {where} "
        f"GROUP BY g ORDER BY n DESC LIMIT ?",
        params + [top_n],
    ).fetchall()
    return [r["g"] for r in rows]


def genre_by_hour(conn, period="all", tz_offset_min=0, now_ts=None, top_n=8):
    """Per-local-hour genre composition for the top_n genres.

    Returns {"genres": [...], "data": {genre: [24 counts]}}.
    """
    where, params = _genre_where(period, now_ts)
    genres = _top_genre_names(conn, where, params, top_n)
    data = {g: [0] * 24 for g in genres}
    if not genres:
        return {"genres": [], "data": {}}
    placeholders = ",".join("?" for _ in genres)
    rows = conn.execute(
        f"SELECT a.primary_genre AS g, {_hour_expr(tz_offset_min)} AS h, COUNT(*) AS n "
        f"{where} AND a.primary_genre IN ({placeholders}) GROUP BY g, h",
        params + genres,
    ).fetchall()
    for r in rows:
        data[r["g"]][int(r["h"])] = r["n"]
    return {"genres": genres, "data": data}


def genre_diversity(conn, period="all", tz_offset_min=0, now_ts=None):
    """Distinct genre count + Shannon entropy (raw and normalized to [0,1])."""
    where, params = _genre_where(period, now_ts)
    rows = conn.execute(
        f"SELECT a.primary_genre AS g, COUNT(*) AS n {where} GROUP BY g",
        params,
    ).fetchall()
    counts = [r["n"] for r in rows]
    total = sum(counts)
    distinct = len(counts)
    if total == 0 or distinct <= 1:
        return {"distinct": distinct, "entropy": 0.0, "normalized_entropy": 0.0}
    entropy = -sum((c / total) * math.log2(c / total) for c in counts)
    return {
        "distinct": distinct,
        "entropy": entropy,
        "normalized_entropy": entropy / math.log2(distinct),
    }


def genre_evolution(conn, period="all", tz_offset_min=0, now_ts=None, top_n=6, buckets=6):
    """Top genres' play share across equal-width time buckets over the data range.

    Returns {"buckets": [labels], "genres": [...], "data": {genre: [share per bucket]}}.
    """
    where, params = _genre_where(period, now_ts)
    span = conn.execute(
        f"SELECT MIN(s.ts) AS lo, MAX(s.ts) AS hi {where}", params
    ).fetchone()
    if span is None or span["lo"] is None:
        return {"buckets": [], "genres": [], "data": {}}
    lo, hi = span["lo"], span["hi"]
    genres = _top_genre_names(conn, where, params, top_n)
    if not genres or hi == lo:
        label = f"{_iso_day(lo, tz_offset_min)}"
        return {"buckets": [label], "genres": genres,
                "data": {g: [0.0] for g in genres}}

    width = (hi - lo) / buckets
    # bucket index for a ts: clamp to [0, buckets-1]
    placeholders = ",".join("?" for _ in genres)
    rows = conn.execute(
        f"SELECT a.primary_genre AS g, s.ts AS ts {where} "
        f"AND a.primary_genre IN ({placeholders})",
        params + genres,
    ).fetchall()
    totals = [0] * buckets
    per = {g: [0] * buckets for g in genres}
    for r in rows:
        idx = min(buckets - 1, int((r["ts"] - lo) / width))
        per[r["g"]][idx] += 1
        totals[idx] += 1
    data = {
        g: [(per[g][i] / totals[i]) if totals[i] else 0.0 for i in range(buckets)]
        for g in genres
    }
    labels = [
        _iso_day(int(lo + width * (i + 0.5)), tz_offset_min) for i in range(buckets)
    ]
    return {"buckets": labels, "genres": genres, "data": data}


def _iso_day(ts, tz_offset_min):
    """Local YYYY-MM-DD for a unix ts (helper for bucket labels)."""
    import datetime as _dt
    local = ts + _offset_seconds(tz_offset_min)
    return _dt.datetime.utcfromtimestamp(local).strftime("%Y-%m-%d")
```

- [ ] **Step 4: Run, verify pass**

Run: `.venv/bin/python -m pytest tests/insights/test_analytics.py -v` → all PASS (10 in file).

- [ ] **Step 5: Commit**

```bash
git add insights/analytics.py tests/insights/test_analytics.py
git commit -m "feat(insights): genre analytics (top, by-hour, diversity, evolution)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: entity analytics + overview (`insights/analytics.py`)

**Files:** Modify `insights/analytics.py`; Test `tests/insights/test_analytics.py` (append).

`new_vs_repeat` / `discovery_rate` use **whole-history** first-occurrence (a play
is a "discovery" if its `ts` equals the earliest `ts` for that key across all
time), then apply the period filter to *which plays are counted*.

- [ ] **Step 1: Append failing tests**:

```python
def test_top_entities_artists_and_tracks(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    _seed(conn, [(1700000000, "A", "t1"), (1700000001, "A", "t1"),
                 (1700000002, "B", "t2")])
    arts = analytics.top_entities(conn, "artist", period="all", tz_offset_min=0,
                                  now_ts=NOW, limit=10)
    assert arts[0] == {"name": "A", "plays": 2}
    tracks = analytics.top_entities(conn, "track", period="all", tz_offset_min=0,
                                    now_ts=NOW, limit=10)
    assert tracks[0]["artist"] == "A" and tracks[0]["track"] == "t1"
    assert tracks[0]["plays"] == 2


def test_new_vs_repeat(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    # t1 played twice (2nd is a repeat), t2 once (new).
    _seed(conn, [(1700000000, "A", "t1"), (1700000001, "A", "t1"),
                 (1700000002, "B", "t2")])
    nvr = analytics.new_vs_repeat(conn, period="all", tz_offset_min=0, now_ts=NOW)
    assert nvr == {"first": 2, "repeat": 1}


def test_discovery_rate_buckets_first_seen_artists(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    _seed(conn, [(1700000000, "A", "t1"), (1700000001, "A", "t2"),
                 (1700000002, "B", "t3")])  # A and B each first-seen same day
    dr = analytics.discovery_rate(conn, period="all", tz_offset_min=0, now_ts=NOW)
    assert dr == [{"date": "2023-11-14", "new_artists": 2}]


def test_overview_summary(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    _seed_with_genres(
        conn,
        [(1700000000, "A", "t1"), (1700000001, "A", "t1"), (1700000002, "B", "t2")],
        {"A": "techno", "B": "house"},
    )
    ov = analytics.overview(conn, period="all", tz_offset_min=0, now_ts=NOW)
    assert ov["total_scrobbles"] == 3
    assert ov["unique_artists"] == 2
    assert ov["unique_tracks"] == 2
    assert ov["top_genre"] == "techno"
    assert ov["first_ts"] == 1700000000
    assert ov["last_ts"] == 1700000002
    assert ov["est_listening_seconds"] == 3 * analytics.AVG_TRACK_SECONDS
```

- [ ] **Step 2: Run, verify fail**

Run: `.venv/bin/python -m pytest tests/insights/test_analytics.py -k "entit or overview or repeat or discovery" -v` → FAIL.

- [ ] **Step 3: Append implementation**:

```python
# Rough constant for "estimated listening time" — we do not store track
# durations, so a play counts as this many seconds. Labelled "estimated" in UI.
AVG_TRACK_SECONDS = 210


def top_entities(conn, kind, period="all", tz_offset_min=0, now_ts=None, limit=20):
    """Most-played artists | tracks | albums in the period.

    kind == "artist" -> [{"name", "plays"}]
    kind == "track"  -> [{"artist", "track", "plays"}]
    kind == "album"  -> [{"album", "plays"}]
    """
    where, params = _period_where(period, now_ts)
    clause = _and(where)
    if kind == "artist":
        rows = conn.execute(
            f"SELECT artist AS name, COUNT(*) AS n FROM scrobbles {clause} "
            f"GROUP BY artist ORDER BY n DESC LIMIT ?", params + [limit]).fetchall()
        return [{"name": r["name"], "plays": r["n"]} for r in rows]
    if kind == "track":
        rows = conn.execute(
            f"SELECT artist, track, COUNT(*) AS n FROM scrobbles {clause} "
            f"GROUP BY artist, track ORDER BY n DESC LIMIT ?", params + [limit]).fetchall()
        return [{"artist": r["artist"], "track": r["track"], "plays": r["n"]} for r in rows]
    if kind == "album":
        album_clause = _and(where) or "WHERE 1=1"
        rows = conn.execute(
            f"SELECT album, COUNT(*) AS n FROM scrobbles {album_clause} "
            f"AND album IS NOT NULL GROUP BY album ORDER BY n DESC LIMIT ?",
            params + [limit]).fetchall()
        return [{"album": r["album"], "plays": r["n"]} for r in rows]
    raise ValueError(f"unknown entity kind: {kind!r}")


def new_vs_repeat(conn, period="all", tz_offset_min=0, now_ts=None):
    """In-period plays split into first-ever listens vs repeats.

    A play is "first" if its ts is the earliest ts for that (artist, track)
    across the WHOLE history.
    """
    where, params = _period_where(period, now_ts)
    clause = _and(where)
    first = conn.execute(
        "WITH firsts AS (SELECT artist, track, MIN(ts) AS fts FROM scrobbles "
        "GROUP BY artist, track) "
        f"SELECT COUNT(*) FROM scrobbles s JOIN firsts f "
        "ON f.artist = s.artist AND f.track = s.track AND f.fts = s.ts "
        + (f"WHERE s.{where}" if where else ""), params).fetchone()[0]
    total = conn.execute(
        f"SELECT COUNT(*) FROM scrobbles {clause}", params).fetchone()[0]
    return {"first": first, "repeat": total - first}


def discovery_rate(conn, period="all", tz_offset_min=0, now_ts=None):
    """New (first-seen) artists per local calendar day, within the period.

    Returns [{"date": "YYYY-MM-DD", "new_artists": n}] ordered by date.
    """
    where, params = _period_where(period, now_ts)
    first_filter = f"WHERE fts >= ?" if where else ""
    fparams = [params[0]] if where else []
    rows = conn.execute(
        "WITH firsts AS (SELECT artist, MIN(ts) AS fts FROM scrobbles GROUP BY artist) "
        f"SELECT strftime('%Y-%m-%d', fts + {_offset_seconds(tz_offset_min)}, 'unixepoch') "
        f"AS d, COUNT(*) AS n FROM firsts {first_filter} GROUP BY d ORDER BY d",
        fparams,
    ).fetchall()
    return [{"date": r["d"], "new_artists": r["n"]} for r in rows]


def overview(conn, period="all", tz_offset_min=0, now_ts=None):
    """Summary scalars for the period."""
    where, params = _period_where(period, now_ts)
    clause = _and(where)
    row = conn.execute(
        f"SELECT COUNT(*) AS total, COUNT(DISTINCT artist) AS artists, "
        f"COUNT(DISTINCT artist || char(31) || track) AS tracks, "
        f"MIN(ts) AS lo, MAX(ts) AS hi FROM scrobbles {clause}", params).fetchone()
    tg = top_genres(conn, period=period, tz_offset_min=tz_offset_min, now_ts=now_ts, limit=1)
    return {
        "total_scrobbles": row["total"],
        "unique_artists": row["artists"],
        "unique_tracks": row["tracks"],
        "first_ts": row["lo"],
        "last_ts": row["hi"],
        "top_genre": tg[0]["genre"] if tg else None,
        "est_listening_seconds": row["total"] * AVG_TRACK_SECONDS,
    }
```

- [ ] **Step 4: Run, verify pass**

Run: `.venv/bin/python -m pytest tests/insights/test_analytics.py -v` → all PASS.

- [ ] **Step 5: Commit**

```bash
git add insights/analytics.py tests/insights/test_analytics.py
git commit -m "feat(insights): entity analytics + overview summary

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: read endpoints + genre tagging in the sync worker

**Files:** Modify `sWebExt/py_server/server.py`; Test `tests/server/test_routes.py`.

Endpoints are read-only, accept `?period=` (default `all`) and `?tz=` (offset
minutes, default `0`), and open a short-lived insights DB connection per request
(connection-per-thread; Flask handles each request on one thread).

- [ ] **Step 1: Write failing route tests** — append to `tests/server/test_routes.py` (match the existing `client` fixture):

```python
def _seed_insights_db(path):
    from insights import db as idb
    conn = idb.connect(path)
    conn.executemany(
        "INSERT INTO scrobbles (ts, artist, track) VALUES (?, ?, ?)",
        [(1700000000, "A", "t1"), (1700000001, "A", "t1"), (1700000002, "B", "t2")])
    conn.execute("INSERT INTO artist_tags (artist, tags_json, primary_genre, fetched_at) "
                 "VALUES ('A', '[]', 'techno', 1)")
    conn.commit()
    conn.close()


def test_insights_overview_endpoint(client, monkeypatch, tmp_path):
    import sWebExt.py_server.server as server
    dbp = str(tmp_path / "i.db")
    _seed_insights_db(dbp)
    monkeypatch.setattr(server, "_insights_db_path", lambda: dbp)
    resp = client.get("/insights/overview?period=all&tz=0")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["total_scrobbles"] == 3
    assert body["top_genre"] == "techno"


def test_insights_temporal_endpoint(client, monkeypatch, tmp_path):
    import sWebExt.py_server.server as server
    dbp = str(tmp_path / "i.db")
    _seed_insights_db(dbp)
    monkeypatch.setattr(server, "_insights_db_path", lambda: dbp)
    resp = client.get("/insights/temporal?tz=0")
    assert resp.status_code == 200
    body = resp.get_json()
    assert len(body["clock"]["hours"]) == 24
    assert len(body["heatmap"]["matrix"]) == 7
    assert "weekday_weekend" in body and "over_time" in body


def test_insights_genres_endpoint(client, monkeypatch, tmp_path):
    import sWebExt.py_server.server as server
    dbp = str(tmp_path / "i.db")
    _seed_insights_db(dbp)
    monkeypatch.setattr(server, "_insights_db_path", lambda: dbp)
    resp = client.get("/insights/genres?tz=0")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["top"][0]["genre"] == "techno"
    assert "by_hour" in body and "evolution" in body and "diversity" in body
```

- [ ] **Step 2: Run, verify fail**

Run: `.venv/bin/python -m pytest tests/server/test_routes.py -k "insights_overview or insights_temporal or insights_genres" -v` → 404 FAIL.

- [ ] **Step 3: Add a request helper + the three routes** in `server.py`, next to the existing `/insights/sync` routes:

```python
def _insights_query_args():
    period = request.args.get("period", "all")
    try:
        tz = int(request.args.get("tz", 0))
    except (TypeError, ValueError):
        tz = 0
    return period, tz


@app.route("/insights/overview", methods=["GET"])
def insights_overview():
    from insights import db as insights_db, analytics
    period, tz = _insights_query_args()
    conn = insights_db.connect(_insights_db_path())
    try:
        return jsonify(analytics.overview(conn, period=period, tz_offset_min=tz))
    finally:
        conn.close()


@app.route("/insights/temporal", methods=["GET"])
def insights_temporal():
    from insights import db as insights_db, analytics
    period, tz = _insights_query_args()
    conn = insights_db.connect(_insights_db_path())
    try:
        return jsonify({
            "clock": analytics.listening_clock(conn, period=period, tz_offset_min=tz),
            "heatmap": analytics.hour_day_heatmap(conn, period=period, tz_offset_min=tz),
            "weekday_weekend": analytics.weekday_weekend(conn, period=period, tz_offset_min=tz),
            "over_time": analytics.plays_over_time(conn, period=period, tz_offset_min=tz),
        })
    finally:
        conn.close()


@app.route("/insights/genres", methods=["GET"])
def insights_genres():
    from insights import db as insights_db, analytics
    period, tz = _insights_query_args()
    conn = insights_db.connect(_insights_db_path())
    try:
        return jsonify({
            "top": analytics.top_genres(conn, period=period, tz_offset_min=tz),
            "by_hour": analytics.genre_by_hour(conn, period=period, tz_offset_min=tz),
            "evolution": analytics.genre_evolution(conn, period=period, tz_offset_min=tz),
            "diversity": analytics.genre_diversity(conn, period=period, tz_offset_min=tz),
        })
    finally:
        conn.close()
```

- [ ] **Step 4: Wire genre tagging into the sync worker**

In `_run_insights_sync_once`, after `synced = sync_scrobbles(...)` and BEFORE `conn.close()` in the `finally`, tag newly-seen artists opportunistically. Replace the inner try/finally body so it reads:

```python
        conn = insights_db.connect(_insights_db_path())
        try:
            synced = sync_scrobbles(lfm, username, conn, max_pages=max_pages)
            from insights.genres import ensure_artist_tags
            artists = [r[0] for r in conn.execute(
                "SELECT DISTINCT artist FROM scrobbles").fetchall()]
            tagged = ensure_artist_tags(lfm, conn, artists)
            synced["artists_tagged"] = tagged
        finally:
            conn.close()
```

(Leave the rest of the worker — status dict, lock, error handling — unchanged.)

- [ ] **Step 5: Run the new route tests, then the full suite**

Run: `.venv/bin/python -m pytest tests/server/test_routes.py -k insights -v` → all insights route tests PASS.
Run: `.venv/bin/python -m pytest tests/ -q` → all green.

- [ ] **Step 6: Commit**

```bash
git add sWebExt/py_server/server.py tests/server/test_routes.py
git commit -m "feat(insights): overview/temporal/genres endpoints + genre tagging on sync

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage (Phase 2 scope):**
- §3 genre cache (reuse lastfm/tags, primary_genre, negative cache) → Task 1 + Task 5 step 4 (opportunistic tagging on sync).
- §5 temporal (clock, heatmap, weekday/weekend, over-time) → Task 2.
- §5 genre (top, by-hour, evolution, diversity) → Task 3.
- §5 entities (top_entities, discovery_rate, new_vs_repeat) + overview → Task 4.
- §6 endpoints (overview, temporal, genres, all with period+tz) → Task 5.
- tz correctness (UTC ts → local buckets) is asserted explicitly in Task 2's clock test.

Deferred to later phases (correctly NOT here): `/insights/features` + `/insights/discovery` (Phase 3/5), the `library_overlap`/`missing_favorites` analytics (Phase 5), and `mood`/`bpm` fields. Overview's `avg_bpm`/`coverage` fields (spec §5) are added in Phase 3 when features exist — overview here returns only data available now.

**Placeholder scan:** No TBD/TODO; every code step is complete; commands give expected outcomes.

**Type consistency:** Analytics functions share the `(conn, period="all", tz_offset_min=0, now_ts=None, ...)` signature. Helpers `_period_where`, `_and`, `_hour_expr`, `_dow_expr`, `_date_expr`, `_offset_seconds`, `_genre_where`, `_top_genre_names`, `_iso_day`, and constant `AVG_TRACK_SECONDS` are defined once (Tasks 2–4) and referenced consistently. Endpoints in Task 5 call `overview`, `listening_clock`, `hour_day_heatmap`, `weekday_weekend`, `plays_over_time`, `top_genres`, `genre_by_hour`, `genre_evolution`, `genre_diversity` — all matching their definitions. `_insights_db_path` (Phase 1) is reused and monkeypatched in tests.

**Edge cases covered:** empty period result (clock returns zeros; genre fns return empty structures), untagged artists excluded from genre stats, single-genre diversity returns 0 entropy (avoids div-by-zero on `log2(1)`), `genre_evolution` handles `hi == lo` and empty data.

---

## Next phases (separate plans)

3. Audio features (AcousticBrainz + librosa) + feature analytics + `/insights/features`; extend `overview` with avg BPM + coverage.
4. INSIGHTS UI screen + `web/static/charts.js`.
5. Library cross-ref + discovery integration + `/insights/discovery`.
