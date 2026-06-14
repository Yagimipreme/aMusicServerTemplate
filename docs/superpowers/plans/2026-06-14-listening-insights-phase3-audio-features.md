# Listening Insights — Phase 3: Audio Features Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Populate per-track audio features (BPM, key, mood, danceability) from AcousticBrainz by recording-MBID, with an opt-in local `librosa` fallback, then expose feature analytics via `GET /insights/features` and extend the overview with avg-BPM + coverage.

**Architecture:** `insights/acousticbrainz.py` fetches features for a recording MBID (read-only AcousticBrainz API). `insights/features.py` orchestrates: resolve each scrobbled track's recording MBID (prefer the one Last.fm already stored on the scrobble; else MusicBrainz recording search like `library/repair.py`), try AcousticBrainz, and on a miss optionally fall back to `insights/localfeatures.py` (librosa, lazily imported, config-gated). Results land in the existing `track_features` table (negative-cached so misses aren't retried). `insights/analytics.py` gains feature aggregations; `server.py` gains a dedicated feature-sync worker + the read endpoint.

**Tech Stack:** Python stdlib `sqlite3`/`urllib`, `requests` (already a dep) for AcousticBrainz, optional `librosa` (NOT a hard dep — lazy import, gated by `insights.enable_local_analysis`), Flask, `pytest` + `unittest.mock`.

**Spec:** `docs/superpowers/specs/2026-06-14-listening-insights-analytics-design.md` (§4 audio features, §5 Sound analytics + overview avg_bpm/coverage, `/insights/features` in §6).

**Builds on Phases 1–2:** `insights/db.py` (`track_features` table already exists), `insights/scrobbles.py` (scrobbles carry `recording_mbid`), `insights/analytics.py` (shares `_period_where`/`_and`/`_hour_expr`/`_offset_seconds`), and the worker/endpoint patterns in `server.py`.

**Test command (IMPORTANT):** system `python3` has no pytest. Use
`/home/taichi/repos/musicServer/aMusicServerTemplate/.venv/bin/python -m pytest <args>` from the worktree root
`/home/taichi/repos/musicServer/aMusicServerTemplate/.claude/worktrees/insights`. **Tests must never require `librosa`** (it is not installed) — always mock it.

---

## File Structure

- Create `insights/acousticbrainz.py` — `fetch_features(mbid, session=None)` + `_primary_mood`. Pure HTTP client.
- Create `insights/localfeatures.py` — `analyze_file(path)` (librosa, lazy import) + `_mood_from(tempo, rms, ...)`. Returns None when librosa absent or analysis fails.
- Create `insights/features.py` — `resolve_recording_mbid`, `ensure_track_features`, `_write_features`. Orchestration only; takes injected callables (testable, no network in unit tests).
- Modify `insights/analytics.py` — append `bpm_distribution`, `bpm_curve`, `key_distribution`, `mood_distribution`, `mood_by_time`, `feature_coverage`; extend `overview`.
- Modify `sWebExt/py_server/server.py` — feature-sync worker + `POST /insights/features/sync` + `GET /insights/features/sync/status` + `GET /insights/features`; config helper for `enable_local_analysis`.
- Create `tests/insights/test_acousticbrainz.py`, `tests/insights/test_features.py`, `tests/insights/test_localfeatures.py`; extend `tests/insights/test_analytics.py`, `tests/server/test_routes.py`.

Conventions: mirror existing `insights/` style (module docstring, `logger`). Analytics keep the `(conn, period, tz_offset_min, now_ts)` contract.

---

## Task 1: AcousticBrainz client (`insights/acousticbrainz.py`)

**Files:** Create `insights/acousticbrainz.py`; Test `tests/insights/test_acousticbrainz.py`.

- [ ] **Step 1: Write failing tests**

```python
"""Tests for insights/acousticbrainz.py — feature lookup by recording MBID."""

from unittest.mock import MagicMock

from insights import acousticbrainz as ab


def _resp(status, payload):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = payload
    r.raise_for_status.return_value = None
    return r


_LOWLEVEL = {"rhythm": {"bpm": 128.4}, "tonal": {"key_key": "A", "key_scale": "minor"}}
_HIGHLEVEL = {"highlevel": {
    "mood_happy": {"value": "happy", "probability": 0.81, "all": {"happy": 0.81, "not happy": 0.19}},
    "mood_sad": {"value": "not sad", "probability": 0.7, "all": {"sad": 0.3, "not sad": 0.7}},
    "danceability": {"value": "danceable", "probability": 0.9, "all": {"danceable": 0.9, "not_danceable": 0.1}},
}}


def test_fetch_features_parses_bpm_key_mood():
    session = MagicMock()
    session.get.side_effect = [_resp(200, _LOWLEVEL), _resp(200, _HIGHLEVEL)]
    feat = ab.fetch_features("mbid-1", session=session)
    assert feat["bpm"] == 128.4
    assert feat["key"] == "A" and feat["scale"] == "minor"
    assert feat["mood"] == "happy"          # highest positive-class probability
    assert feat["danceability"] == 0.9
    assert "mood_happy" in feat["mood_scores"]


def test_fetch_features_returns_none_on_404():
    session = MagicMock()
    session.get.return_value = _resp(404, {"message": "Not found"})
    assert ab.fetch_features("missing", session=session) is None


def test_fetch_features_returns_none_on_error():
    session = MagicMock()
    session.get.side_effect = RuntimeError("network")
    assert ab.fetch_features("x", session=session) is None


def test_primary_mood_ignores_negatives():
    hl = {"mood_aggressive": {"value": "not aggressive", "probability": 0.95},
          "mood_relaxed": {"value": "relaxed", "probability": 0.6}}
    assert ab._primary_mood(hl) == "relaxed"
```

- [ ] **Step 2: Run, verify fail** — `.venv/bin/python -m pytest tests/insights/test_acousticbrainz.py -v` → `No module named 'insights.acousticbrainz'`.

- [ ] **Step 3: Implement `insights/acousticbrainz.py`**

```python
"""Read-only AcousticBrainz client: audio features by recording MBID.

The AcousticBrainz project is frozen (no new submissions since 2022) but its
API still serves precomputed features for ~29M recordings. Coverage is partial;
fetch_features returns None when a recording has no data or on any error.
"""

import logging

import requests

logger = logging.getLogger(__name__)

_BASE = "https://acousticbrainz.org/api/v1"
_TIMEOUT = 10


def _primary_mood(highlevel: dict):
    """Return the strongest positive mood label (e.g. 'happy'), or None.

    AcousticBrainz exposes mood_* binary classifiers; we pick the classifier
    whose predicted value is the positive class with the highest probability.
    """
    best, best_p = None, -1.0
    for key, clf in highlevel.items():
        if not key.startswith("mood_"):
            continue
        value = clf.get("value") or ""
        prob = clf.get("probability") or 0.0
        if value and not value.startswith("not") and prob > best_p:
            best_p, best = prob, key[len("mood_"):]
    return best


def fetch_features(mbid: str, session=None) -> "dict | None":
    """Fetch {bpm, key, scale, mood, mood_scores, danceability} for a recording
    MBID, or None when AcousticBrainz has no data for it / on error."""
    http = session or requests
    try:
        ll = http.get(f"{_BASE}/{mbid}/low-level", timeout=_TIMEOUT)
        if ll.status_code == 404:
            return None
        ll.raise_for_status()
        low = ll.json()
        hl = http.get(f"{_BASE}/{mbid}/high-level", timeout=_TIMEOUT)
        high = hl.json() if hl.status_code == 200 else {}
    except Exception:
        logger.warning("acousticbrainz: fetch failed for %s", mbid, exc_info=True)
        return None

    rhythm = low.get("rhythm", {}) or {}
    tonal = low.get("tonal", {}) or {}
    highlevel = high.get("highlevel", {}) or {}

    mood_scores = {k: v.get("all", {}) for k, v in highlevel.items()
                   if k.startswith("mood_")}
    danceability = (highlevel.get("danceability", {}) or {}).get("all", {}).get("danceable")

    return {
        "bpm": rhythm.get("bpm"),
        "key": tonal.get("key_key"),
        "scale": tonal.get("key_scale"),
        "mood": _primary_mood(highlevel),
        "mood_scores": mood_scores or None,
        "danceability": danceability,
    }
```

- [ ] **Step 4: Run, verify pass** — 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add insights/acousticbrainz.py tests/insights/test_acousticbrainz.py
git commit -m "feat(insights): AcousticBrainz feature client

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: feature orchestration (`insights/features.py`)

**Files:** Create `insights/features.py`; Test `tests/insights/test_features.py`.

Dependency injection keeps this unit network-free: callers pass `ab_fetch`
(AcousticBrainz), `mb_search` (recording-MBID search), and `local_analyze`
(librosa). Tracks already having a `track_features` row are skipped; misses are
written with NULL fields + `source=NULL` so they aren't retried (negative cache).

- [ ] **Step 1: Write failing tests**

```python
"""Tests for insights/features.py — MBID resolution + feature orchestration."""

from insights import db, features


def _seed(conn, rows):
    conn.executemany(
        "INSERT INTO scrobbles (ts, artist, track, recording_mbid) VALUES (?, ?, ?, ?)",
        rows)
    conn.commit()


def test_resolve_prefers_stored_mbid(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    _seed(conn, [(1, "A", "t1", "stored-mbid")])
    assert features.resolve_recording_mbid(conn, "A", "t1") == "stored-mbid"


def test_resolve_falls_back_to_search(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    _seed(conn, [(1, "A", "t1", None)])
    assert features.resolve_recording_mbid(
        conn, "A", "t1", mb_search=lambda ar, tr: "searched-mbid") == "searched-mbid"


def test_ensure_writes_acousticbrainz_features(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    _seed(conn, [(1, "A", "t1", "mbid-1")])
    ab_fetch = lambda m: {"bpm": 120.0, "key": "C", "scale": "major",
                          "mood": "happy", "mood_scores": {"mood_happy": {}},
                          "danceability": 0.7}
    n = features.ensure_track_features(conn, ab_fetch=ab_fetch)
    assert n == 1
    row = conn.execute("SELECT bpm, key, mood, source FROM track_features "
                       "WHERE artist='A' AND track='t1'").fetchone()
    assert row["bpm"] == 120.0 and row["mood"] == "happy"
    assert row["source"] == "acousticbrainz"


def test_ensure_negative_caches_misses(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    _seed(conn, [(1, "A", "t1", "mbid-1")])
    calls = {"n": 0}
    def ab_fetch(m):
        calls["n"] += 1
        return None
    features.ensure_track_features(conn, ab_fetch=ab_fetch)
    row = conn.execute("SELECT source FROM track_features WHERE artist='A'").fetchone()
    assert row["source"] is None                      # miss recorded
    features.ensure_track_features(conn, ab_fetch=ab_fetch)  # second pass
    assert calls["n"] == 1                             # not retried


def test_ensure_uses_local_fallback_on_ab_miss(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    _seed(conn, [(1, "A", "t1", "mbid-1")])
    local = lambda ar, tr: {"bpm": 90.0, "key": "G", "scale": None,
                            "mood": "calm", "mood_scores": None, "danceability": None}
    features.ensure_track_features(conn, ab_fetch=lambda m: None, local_analyze=local)
    row = conn.execute("SELECT bpm, source FROM track_features WHERE artist='A'").fetchone()
    assert row["bpm"] == 90.0 and row["source"] == "librosa"


def test_ensure_respects_limit(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    _seed(conn, [(1, "A", "t1", "m1"), (2, "B", "t2", "m2"), (3, "C", "t3", "m3")])
    n = features.ensure_track_features(conn, ab_fetch=lambda m: None, limit=2)
    assert n == 2
```

- [ ] **Step 2: Run, verify fail** — `No module named 'insights.features'`.

- [ ] **Step 3: Implement `insights/features.py`**

```python
"""Per-track audio feature orchestration into the track_features table.

Network/CPU work is injected (ab_fetch / mb_search / local_analyze) so this unit
is testable without hitting AcousticBrainz, MusicBrainz, or librosa.
"""

import json
import logging
import time

logger = logging.getLogger(__name__)


def resolve_recording_mbid(conn, artist, track, *, mb_search=None):
    """Recording MBID for a track: prefer the one Last.fm stored on a scrobble,
    else delegate to mb_search(artist, track) if provided."""
    row = conn.execute(
        "SELECT recording_mbid FROM scrobbles "
        "WHERE artist = ? AND track = ? AND recording_mbid IS NOT NULL LIMIT 1",
        (artist, track),
    ).fetchone()
    if row and row[0]:
        return row[0]
    if mb_search:
        return mb_search(artist, track)
    return None


def _write_features(conn, artist, track, mbid, feat, source):
    feat = feat or {}
    scores = feat.get("mood_scores")
    conn.execute(
        "INSERT OR IGNORE INTO track_features "
        "(artist, track, recording_mbid, bpm, key, scale, mood, mood_scores_json, "
        " danceability, source, analyzed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (artist, track, mbid, feat.get("bpm"), feat.get("key"), feat.get("scale"),
         feat.get("mood"), json.dumps(scores) if scores else None,
         feat.get("danceability"), source, int(time.time())),
    )


def ensure_track_features(conn, *, ab_fetch, mb_search=None, local_analyze=None,
                          limit=None) -> int:
    """Compute + cache features for scrobbled tracks lacking a track_features row.

    For each: resolve recording MBID → AcousticBrainz; on miss, optionally
    local_analyze(artist, track). Misses are written with source=NULL so they
    are not retried. Returns the number of tracks processed (written).
    """
    sql = ("SELECT DISTINCT s.artist, s.track FROM scrobbles s "
           "LEFT JOIN track_features f ON f.artist = s.artist AND f.track = s.track "
           "WHERE f.artist IS NULL")
    params = []
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    todo = conn.execute(sql, params).fetchall()

    written = 0
    for artist, track in todo:
        mbid = resolve_recording_mbid(conn, artist, track, mb_search=mb_search)
        feat = ab_fetch(mbid) if mbid else None
        source = "acousticbrainz" if feat else None
        if not feat and local_analyze is not None:
            feat = local_analyze(artist, track)
            source = "librosa" if feat else None
        _write_features(conn, artist, track, mbid, feat, source)
        written += 1
    if written:
        conn.commit()
    return written
```

- [ ] **Step 4: Run, verify pass** — 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add insights/features.py tests/insights/test_features.py
git commit -m "feat(insights): track-feature orchestration with negative caching

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: librosa local fallback (`insights/localfeatures.py`)

**Files:** Create `insights/localfeatures.py`; Test `tests/insights/test_localfeatures.py`.

librosa is NOT a hard dependency. `analyze_file` imports it lazily and returns
None if it's missing or analysis fails, so the server and tests never require it.

- [ ] **Step 1: Write failing tests** (mock librosa entirely — never import the real one)

```python
"""Tests for insights/localfeatures.py — librosa fallback (librosa mocked)."""

import sys
from unittest.mock import MagicMock

from insights import localfeatures


def test_analyze_file_returns_none_without_librosa(monkeypatch):
    # Simulate librosa not installed.
    monkeypatch.setitem(sys.modules, "librosa", None)
    assert localfeatures.analyze_file("/nope.mp3") is None


def test_mood_from_tempo_energy_quadrant():
    # Fast + energetic → energetic; slow + quiet → calm.
    assert localfeatures._mood_from(150.0, 0.20) == "energetic"
    assert localfeatures._mood_from(70.0, 0.01) == "calm"
    assert localfeatures._mood_from(150.0, 0.01) == "frantic"
    assert localfeatures._mood_from(70.0, 0.20) == "warm"


def test_analyze_file_with_mocked_librosa(monkeypatch):
    import numpy as np
    fake = MagicMock()
    fake.load.return_value = (np.zeros(2048, dtype="float32"), 22050)
    fake.beat.beat_track.return_value = (128.0, None)
    # chroma: 12 x N; make pitch-class 0 ('C') dominant
    chroma = np.zeros((12, 4)); chroma[0, :] = 1.0
    fake.feature.chroma_cqt.return_value = chroma
    fake.feature.rms.return_value = np.array([[0.05]])
    monkeypatch.setitem(sys.modules, "librosa", fake)

    feat = localfeatures.analyze_file("/song.mp3")
    assert feat["bpm"] == 128.0
    assert feat["key"] == "C"
    assert feat["source_hint"] == "librosa"
    assert feat["mood"] in ("calm", "warm", "energetic", "frantic")
```

- [ ] **Step 2: Run, verify fail** — `No module named 'insights.localfeatures'`.

- [ ] **Step 3: Implement `insights/localfeatures.py`**

```python
"""Local audio feature extraction via librosa (optional, lazily imported).

Used only as a fallback when AcousticBrainz has no data and the operator has
enabled local analysis. Returns None when librosa is unavailable or analysis
fails — the caller treats that as "no features".
"""

import logging

logger = logging.getLogger(__name__)

_KEYS = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# Heuristic thresholds for the tempo/energy mood quadrant.
_FAST_BPM = 110.0
_LOUD_RMS = 0.04


def _mood_from(tempo: float, rms: float) -> str:
    """Coarse mood label from tempo + RMS energy (a heuristic, not a classifier)."""
    fast = tempo >= _FAST_BPM
    loud = rms >= _LOUD_RMS
    if fast and loud:
        return "energetic"
    if fast and not loud:
        return "frantic"
    if not fast and loud:
        return "warm"
    return "calm"


def analyze_file(path: str) -> "dict | None":
    """Extract {bpm, key, scale, mood, mood_scores, danceability, source_hint}
    from a local audio file, or None if librosa is missing / analysis fails."""
    try:
        import librosa
        import numpy as np
    except ImportError:
        logger.warning("localfeatures: librosa not installed; skipping local analysis")
        return None
    if librosa is None:  # explicit test/patch for "not installed"
        return None
    try:
        y, sr = librosa.load(path, mono=True, duration=120)
        tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
        tempo = float(tempo)
        chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
        key_idx = int(np.argmax(chroma.mean(axis=1)))
        rms = float(np.mean(librosa.feature.rms(y=y)))
    except Exception:
        logger.warning("localfeatures: analysis failed for %s", path, exc_info=True)
        return None
    return {
        "bpm": tempo,
        "key": _KEYS[key_idx],
        "scale": None,            # librosa chroma doesn't reliably give mode
        "mood": _mood_from(tempo, rms),
        "mood_scores": None,
        "danceability": None,
        "source_hint": "librosa",
    }
```

- [ ] **Step 4: Run, verify pass** — 3 PASS (no real librosa needed).

- [ ] **Step 5: Commit**

```bash
git add insights/localfeatures.py tests/insights/test_localfeatures.py
git commit -m "feat(insights): optional librosa local feature fallback

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: feature analytics + overview extension (`insights/analytics.py`)

**Files:** Modify `insights/analytics.py`; Test `tests/insights/test_analytics.py` (append).

Feature aggregations join `track_features` to in-period `scrobbles` (features
have no timestamp, so each PLAY of a feature-bearing track counts, period-filtered
on `scrobbles.ts`).

- [ ] **Step 1: Append failing tests**

```python
def _seed_with_features(conn, scrobble_rows, track_features):
    conn.executemany(
        "INSERT INTO scrobbles (ts, artist, track) VALUES (?, ?, ?)", scrobble_rows)
    for (artist, track), f in track_features.items():
        conn.execute(
            "INSERT INTO track_features (artist, track, bpm, key, scale, mood, source, analyzed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
            (artist, track, f.get("bpm"), f.get("key"), f.get("scale"),
             f.get("mood"), f.get("source", "acousticbrainz")))
    conn.commit()


def test_bpm_curve_by_hour(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    _seed_with_features(conn, [(1700000000, "A", "t1")],   # UTC hour 22
                        {("A", "t1"): {"bpm": 128.0}})
    bc = analytics.bpm_curve(conn, period="all", tz_offset_min=0, now_ts=NOW)
    assert len(bc["hours"]) == 24
    assert bc["hours"][22] == 128.0


def test_bpm_distribution_bins(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    _seed_with_features(conn, [(1700000000, "A", "t1"), (1700000001, "B", "t2")],
                        {("A", "t1"): {"bpm": 125.0}, ("B", "t2"): {"bpm": 128.0}})
    dist = analytics.bpm_distribution(conn, period="all", tz_offset_min=0, now_ts=NOW)
    # both fall in the 120-130 bin
    bin_120 = next(b for b in dist if b["min"] == 120)
    assert bin_120["count"] == 2


def test_key_and_mood_distributions(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    _seed_with_features(conn,
        [(1700000000, "A", "t1"), (1700000001, "A", "t1"), (1700000002, "B", "t2")],
        {("A", "t1"): {"key": "A", "scale": "minor", "mood": "happy"},
         ("B", "t2"): {"key": "C", "scale": "major", "mood": "sad"}})
    keys = analytics.key_distribution(conn, period="all", tz_offset_min=0, now_ts=NOW)
    assert {"key": "A", "scale": "minor", "count": 2} in keys
    moods = analytics.mood_distribution(conn, period="all", tz_offset_min=0, now_ts=NOW)
    assert {"mood": "happy", "count": 2} in moods


def test_mood_by_time_shape(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    _seed_with_features(conn, [(1700000000, "A", "t1")], {("A", "t1"): {"mood": "happy"}})
    mbt = analytics.mood_by_time(conn, period="all", tz_offset_min=0, now_ts=NOW)
    assert "happy" in mbt["moods"]
    assert len(mbt["data"]["happy"]) == 24
    assert mbt["data"]["happy"][22] == 1


def test_feature_coverage(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    # 3 distinct tracks scrobbled; 2 have bpm features, 1 has none.
    _seed_with_features(conn,
        [(1, "A", "t1"), (2, "B", "t2"), (3, "C", "t3")],
        {("A", "t1"): {"bpm": 120.0}, ("B", "t2"): {"bpm": 130.0}})
    cov = analytics.feature_coverage(conn, period="all", tz_offset_min=0, now_ts=NOW)
    assert cov["tracks_total"] == 3
    assert cov["tracks_with_bpm"] == 2
    assert abs(cov["bpm_pct"] - 2 / 3) < 1e-6


def test_overview_includes_avg_bpm_and_coverage(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    _seed_with_features(conn, [(1700000000, "A", "t1"), (1700000001, "B", "t2")],
                        {("A", "t1"): {"bpm": 120.0}, ("B", "t2"): {"bpm": 140.0}})
    ov = analytics.overview(conn, period="all", tz_offset_min=0, now_ts=NOW)
    assert ov["avg_bpm"] == 130.0
    assert "feature_coverage" in ov and ov["feature_coverage"]["tracks_with_bpm"] == 2
```

- [ ] **Step 2: Run, verify fail** — `module 'insights.analytics' has no attribute 'bpm_curve'`.

- [ ] **Step 3: Append implementation** to `insights/analytics.py`

```python
_FEATURE_JOIN = (
    "FROM scrobbles s JOIN track_features f ON f.artist = s.artist AND f.track = s.track"
)


def _feature_where(period, now_ts, *, require):
    """Feature-join WHERE clause + params. `require` is an f-column predicate
    (e.g. 'f.bpm IS NOT NULL'); period filters on s.ts."""
    frag, params = _period_where(period, now_ts)
    where = f"{_FEATURE_JOIN} WHERE {require}"
    if frag:
        where += f" AND s.{frag}"
    return where, params


def bpm_curve(conn, period="all", tz_offset_min=0, now_ts=None):
    """Average BPM per local hour-of-day. Returns {"hours": [24 floats|None]}."""
    where, params = _feature_where(period, now_ts, require="f.bpm IS NOT NULL")
    rows = conn.execute(
        f"SELECT {_hour_expr(tz_offset_min)} AS h, AVG(f.bpm) AS avg_bpm {where} GROUP BY h",
        params,
    ).fetchall()
    hours = [None] * 24
    for r in rows:
        hours[int(r["h"])] = round(r["avg_bpm"], 1)
    return {"hours": hours}


def bpm_distribution(conn, period="all", tz_offset_min=0, now_ts=None,
                     lo=60, hi=200, width=10):
    """Histogram of plays by BPM bucket. [{"min","max","count"}]."""
    where, params = _feature_where(period, now_ts, require="f.bpm IS NOT NULL")
    rows = conn.execute(f"SELECT f.bpm AS bpm {where}", params).fetchall()
    edges = list(range(lo, hi, width))
    bins = [{"min": e, "max": e + width, "count": 0} for e in edges]
    for r in rows:
        bpm = r["bpm"]
        if bpm is None:
            continue
        idx = int((bpm - lo) // width)
        if 0 <= idx < len(bins):
            bins[idx]["count"] += 1
    return bins


def key_distribution(conn, period="all", tz_offset_min=0, now_ts=None):
    """Play counts per (key, scale). [{"key","scale","count"}] for the Camelot wheel."""
    where, params = _feature_where(period, now_ts, require="f.key IS NOT NULL")
    rows = conn.execute(
        f"SELECT f.key AS key, f.scale AS scale, COUNT(*) AS n {where} "
        f"GROUP BY f.key, f.scale ORDER BY n DESC",
        params,
    ).fetchall()
    return [{"key": r["key"], "scale": r["scale"], "count": r["n"]} for r in rows]


def mood_distribution(conn, period="all", tz_offset_min=0, now_ts=None):
    """Play counts per mood label. [{"mood","count"}]."""
    where, params = _feature_where(period, now_ts, require="f.mood IS NOT NULL")
    rows = conn.execute(
        f"SELECT f.mood AS mood, COUNT(*) AS n {where} GROUP BY f.mood ORDER BY n DESC",
        params,
    ).fetchall()
    return [{"mood": r["mood"], "count": r["n"]} for r in rows]


def mood_by_time(conn, period="all", tz_offset_min=0, now_ts=None):
    """Per-local-hour mood composition. {"moods": [...], "data": {mood: [24]}}."""
    where, params = _feature_where(period, now_ts, require="f.mood IS NOT NULL")
    rows = conn.execute(
        f"SELECT f.mood AS mood, {_hour_expr(tz_offset_min)} AS h, COUNT(*) AS n "
        f"{where} GROUP BY mood, h",
        params,
    ).fetchall()
    moods = sorted({r["mood"] for r in rows})
    data = {m: [0] * 24 for m in moods}
    for r in rows:
        data[r["mood"]][int(r["h"])] = r["n"]
    return {"moods": moods, "data": data}


def feature_coverage(conn, period="all", tz_offset_min=0, now_ts=None):
    """How many DISTINCT in-period tracks have BPM/mood features.

    {"tracks_total","tracks_with_bpm","tracks_with_mood","bpm_pct","mood_pct"}.
    """
    where, params = _period_where(period, now_ts)
    clause = _and(where)
    total = conn.execute(
        f"SELECT COUNT(DISTINCT artist || char(31) || track) FROM scrobbles {clause}",
        params).fetchone()[0]
    fwhere, fparams = _feature_where(period, now_ts, require="f.bpm IS NOT NULL")
    with_bpm = conn.execute(
        f"SELECT COUNT(DISTINCT s.artist || char(31) || s.track) {fwhere}", fparams
    ).fetchone()[0]
    mwhere, mparams = _feature_where(period, now_ts, require="f.mood IS NOT NULL")
    with_mood = conn.execute(
        f"SELECT COUNT(DISTINCT s.artist || char(31) || s.track) {mwhere}", mparams
    ).fetchone()[0]
    return {
        "tracks_total": total,
        "tracks_with_bpm": with_bpm,
        "tracks_with_mood": with_mood,
        "bpm_pct": (with_bpm / total) if total else 0.0,
        "mood_pct": (with_mood / total) if total else 0.0,
    }
```

Then EXTEND `overview` — locate its `return {...}` dict and add two keys before the closing brace (compute avg_bpm just above the return):

```python
    avg_bpm_row = conn.execute(
        f"SELECT AVG(f.bpm) AS a FROM scrobbles s "
        f"JOIN track_features f ON f.artist = s.artist AND f.track = s.track "
        f"WHERE f.bpm IS NOT NULL" + (f" AND s.{where}" if where else ""), params
    ).fetchone()
    avg_bpm = round(avg_bpm_row["a"], 1) if avg_bpm_row["a"] is not None else None
```

and add to the returned dict:

```python
        "avg_bpm": avg_bpm,
        "feature_coverage": feature_coverage(conn, period=period,
                                             tz_offset_min=tz_offset_min, now_ts=now_ts),
```

- [ ] **Step 4: Run, verify pass** — all analytics tests PASS (existing + 6 new).

- [ ] **Step 5: Commit**

```bash
git add insights/analytics.py tests/insights/test_analytics.py
git commit -m "feat(insights): audio-feature analytics + overview avg_bpm/coverage

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: feature-sync worker + endpoints (`server.py`)

**Files:** Modify `sWebExt/py_server/server.py`; Test `tests/server/test_routes.py`.

A dedicated background worker computes features incrementally (bounded per run),
mirroring the scrobble-sync worker. Local analysis is gated by
`insights.enable_local_analysis`; MBID search mirrors `library/repair.py`.

- [ ] **Step 1: Write failing route tests** (append; match the existing `client` fixture)

```python
def _seed_features_db(path):
    from insights import db as idb
    conn = idb.connect(path)
    conn.executemany(
        "INSERT INTO scrobbles (ts, artist, track) VALUES (?, ?, ?)",
        [(1700000000, "A", "t1"), (1700000001, "A", "t1")])
    conn.execute("INSERT INTO track_features (artist, track, bpm, key, scale, mood, source, analyzed_at) "
                 "VALUES ('A','t1',128.0,'A','minor','happy','acousticbrainz',1)")
    conn.commit(); conn.close()


def test_insights_features_endpoint(client, monkeypatch, tmp_path):
    import sWebExt.py_server.server as server
    dbp = str(tmp_path / "i.db"); _seed_features_db(dbp)
    monkeypatch.setattr(server, "_insights_db_path", lambda: dbp)
    resp = client.get("/insights/features?tz=0")
    assert resp.status_code == 200
    body = resp.get_json()
    for k in ("bpm_distribution", "bpm_curve", "key_distribution",
              "mood_distribution", "mood_by_time", "coverage"):
        assert k in body
    assert body["bpm_curve"]["hours"][22] == 128.0


def test_insights_features_sync_starts_worker(client, monkeypatch):
    import sWebExt.py_server.server as server

    called = {}
    def fake(max_tracks=None):
        called["ran"] = True; called["max"] = max_tracks; return {"status": "ok"}

    class _Imm:
        def __init__(self, target=None, kwargs=None, daemon=None, **_):
            self._t = target; self._k = kwargs or {}
        def start(self): self._t(**self._k)

    monkeypatch.setattr(server, "_run_insights_features_once", fake)
    monkeypatch.setattr(server.threading, "Thread", _Imm)
    resp = client.post("/insights/features/sync", json={"max_tracks": 50})
    assert resp.status_code == 200 and resp.get_json()["status"] == "started"
    assert called.get("ran") and called.get("max") == 50


def test_insights_features_sync_status_idle(client):
    resp = client.get("/insights/features/sync/status")
    assert resp.status_code == 200
    assert resp.get_json()["status"] in ("idle", "ok", "started", "skipped", "disabled", "running")
```

Also add `srv._insights_features_last_result = {"status": "idle"}` to the test
`app` fixture, next to the existing `_insights_last_result` reset.

- [ ] **Step 2: Run, verify fail** — 404 / missing attr.

- [ ] **Step 3: Add worker state + the feature worker** in `server.py` (next to the insights sync worker)

```python
_insights_features_running = threading.Lock()
_insights_features_last_result: dict = {"status": "idle"}


def _mb_recording_search(artist, track):
    """Resolve a recording MBID via MusicBrainz (mirrors library/repair.py)."""
    import urllib.parse, urllib.request, json as _json
    q = urllib.parse.quote(f'recording:"{track}" AND artist:"{artist}"')
    url = f"https://musicbrainz.org/ws/2/recording/?query={q}&limit=1&fmt=json"
    req = urllib.request.Request(url, headers={"User-Agent":
        "aMusicServer/1.0 (insights features)"})
    try:
        time.sleep(1.0)  # MusicBrainz 1 req/s ToS
        with urllib.request.urlopen(req, timeout=10) as r:
            recs = _json.loads(r.read()).get("recordings", [])
        return recs[0]["id"] if recs else None
    except Exception:
        logger.warning("[INSIGHTS] MB recording search failed for %s / %s", artist, track)
        return None


def _run_insights_features_once(max_tracks=200) -> dict:
    """Compute audio features for tracks lacking them (bounded per run)."""
    global _insights_features_last_result
    if not _insights_features_running.acquire(blocking=False):
        return {"status": "skipped", "reason": "already running"}
    try:
        from discover.config import load_config
        from insights import db as insights_db
        from insights.features import ensure_track_features
        from insights.acousticbrainz import fetch_features
        cfg = load_config(_CONFIG_PATH)
        enable_local = bool((cfg.get("insights") or {}).get("enable_local_analysis", False))
        local_analyze = None
        if enable_local:
            from insights.localfeatures import analyze_file
            song_dir = cfg.get("song_dir", "")
            index = _build_track_path_index(song_dir) if song_dir else {}
            def local_analyze(artist, track, _idx=index):
                path = _idx.get((artist.lower(), track.lower()))
                return analyze_file(path) if path else None
        conn = insights_db.connect(_insights_db_path())
        try:
            n = ensure_track_features(
                conn, ab_fetch=fetch_features, mb_search=_mb_recording_search,
                local_analyze=local_analyze, limit=max_tracks)
        finally:
            conn.close()
        result = {"status": "ok", "processed": n}
        logger.info("[INSIGHTS] feature sync complete: %s", result)
        _insights_features_last_result = result
        return result
    except Exception as e:
        logger.exception("[INSIGHTS] feature sync failed")
        result = {"status": "error", "error": str(e)}
        _insights_features_last_result = result
        return result
    finally:
        _insights_features_running.release()


def _build_track_path_index(song_dir):
    """Map (artist_lower, title_lower) -> file path using the library scanner."""
    try:
        from library.scanner import scan
        idx = {}
        for rec in scan(song_dir):
            a = (rec.get("artist") or "").lower()
            t = (rec.get("title") or "").lower()
            if a and t:
                idx[(a, t)] = rec["path"]
        return idx
    except Exception:
        logger.warning("[INSIGHTS] could not build track path index", exc_info=True)
        return {}
```

- [ ] **Step 4: Add the routes** (next to the other `/insights/*` routes)

```python
@app.route("/insights/features/sync", methods=["POST"])
def insights_features_sync():
    body = request.get_json(force=True, silent=True) or {}
    max_tracks = body.get("max_tracks", 200)
    t = threading.Thread(target=_run_insights_features_once,
                         kwargs={"max_tracks": max_tracks}, daemon=True)
    t.start()
    return jsonify({"status": "started"})


@app.route("/insights/features/sync/status", methods=["GET"])
def insights_features_sync_status():
    return jsonify(_insights_features_last_result)


@app.route("/insights/features", methods=["GET"])
def insights_features():
    from insights import db as insights_db, analytics
    period, tz = _insights_query_args()
    conn = insights_db.connect(_insights_db_path())
    try:
        return jsonify({
            "bpm_distribution": analytics.bpm_distribution(conn, period=period, tz_offset_min=tz),
            "bpm_curve": analytics.bpm_curve(conn, period=period, tz_offset_min=tz),
            "key_distribution": analytics.key_distribution(conn, period=period, tz_offset_min=tz),
            "mood_distribution": analytics.mood_distribution(conn, period=period, tz_offset_min=tz),
            "mood_by_time": analytics.mood_by_time(conn, period=period, tz_offset_min=tz),
            "coverage": analytics.feature_coverage(conn, period=period, tz_offset_min=tz),
        })
    finally:
        conn.close()
```

- [ ] **Step 5: Add config keys to the Setup schema** — in the settings-schema builder (search the function that returns the `schema` list, e.g. grep `"group": "Maintenance"`), add a `Maintenance` entry for `insights.enable_local_analysis` (type bool). Keep it consistent with how existing bool fields are described. Verify by reading the surrounding entries first.

- [ ] **Step 6: Run the new route tests, then the full suite**

Run: `.venv/bin/python -m pytest tests/server/test_routes.py -k "features" -v` → PASS.
Run: `.venv/bin/python -m pytest tests/ -q` → all green.

- [ ] **Step 7: Commit**

```bash
git add sWebExt/py_server/server.py tests/server/test_routes.py
git commit -m "feat(insights): feature-sync worker + /insights/features endpoint

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage (Phase 3 scope):**
- §4 AcousticBrainz primary → Task 1. MBID resolution (stored-first, MB fallback) → Task 2 + Task 5 `_mb_recording_search`. librosa opt-in fallback → Task 3, gated in Task 5. Negative cache → Task 2.
- §5 Sound analytics (bpm distribution + curve, key/Camelot, mood distribution, mood-by-time) + coverage → Task 4. overview avg_bpm + coverage → Task 4.
- §6 `/insights/features` + the feature worker → Task 5. Config `insights.enable_local_analysis` → Task 5 step 5.

Deferred (correctly absent): library cross-ref / `missing_favorites` / `/insights/discovery` (Phase 5), the UI screen + charts (Phase 4).

**Placeholder scan:** No TBD/TODO; every code step is complete. The two "locate/grep then add" steps (overview extension in Task 4; settings-schema entry in Task 5 step 5) are explicit edit instructions against existing code, with the exact lines to add — not deferred work.

**Type consistency:** `fetch_features(mbid, session)`→dict|None used as `ab_fetch` in `ensure_track_features`. `analyze_file(path)`→dict|None wrapped by the `local_analyze(artist, track)` closure in Task 5 (closure resolves path via the index, matching `ensure_track_features`'s `local_analyze(artist, track)` contract). `_write_features` keys match `fetch_features`/`analyze_file` output keys (bpm/key/scale/mood/mood_scores/danceability). Feature analytics reuse `_period_where`/`_and`/`_hour_expr`/`_offset_seconds` and the `(conn, period, tz_offset_min, now_ts)` contract. `feature_coverage` returns the dict shape asserted in both the analytics test and consumed by `overview`.

**Edge cases:** AcousticBrainz 404/error → None → negative cache; librosa absent → None (suite never imports real librosa); empty feature set → bpm_curve all-None, distributions empty, coverage pct guarded against div-by-zero; period filter applied on `s.ts` for all feature joins.

---

## Next phases (separate plans)

4. INSIGHTS UI screen + `web/static/charts.js` (renders Phases 2–3 endpoints).
5. Library cross-ref + discovery integration + `/insights/discovery`.

## Integration note

`insights-impl` is based on the pre-enrich `bare_bones`. When insights ships, the
merge into the current `bare_bones` (enrich + follow) will need conflict
resolution in `sWebExt/py_server/server.py` (route regions) and
`tests/server/test_routes.py` — the `insights/` package and `tests/insights/` are
disjoint and will merge cleanly.
