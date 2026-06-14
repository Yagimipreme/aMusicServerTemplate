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
        if d in (0, 6):
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


_GENRE_BASE_SQL = (
    "FROM scrobbles s JOIN artist_tags a ON a.artist = s.artist "
    "WHERE a.primary_genre IS NOT NULL"
)


def _genre_where(period, now_ts):
    """Genre-join WHERE clause + params (always filters NULL genre, plus period)."""
    frag, params = _period_where(period, now_ts)
    where = _GENRE_BASE_SQL
    if frag:
        where += f" AND s.{frag}"
    return where, params


def top_genres(conn, period="all", tz_offset_min=0, now_ts=None, limit=15):
    """Ranked genres with play share.

    `share` is each genre's fraction of plays WITHIN the returned top-N set
    (not of all listening), so shares across the returned rows sum to 1.0.
    Returns [{"genre", "plays", "share"}].
    """
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
        label = _iso_day(lo, tz_offset_min)
        return {"buckets": [label], "genres": genres,
                "data": {g: [0.0] for g in genres}}

    width = (hi - lo) / buckets
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
    return _dt.datetime.fromtimestamp(local, tz=_dt.timezone.utc).strftime("%Y-%m-%d")


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
        album_filter = (f"{clause} AND album IS NOT NULL" if clause
                        else "WHERE album IS NOT NULL")
        rows = conn.execute(
            f"SELECT album, COUNT(*) AS n FROM scrobbles {album_filter} "
            f"GROUP BY album ORDER BY n DESC LIMIT ?", params + [limit]).fetchall()
        return [{"album": r["album"], "plays": r["n"]} for r in rows]
    raise ValueError(f"unknown entity kind: {kind!r}")


def new_vs_repeat(conn, period="all", tz_offset_min=0, now_ts=None):
    """In-period plays split into first-ever listens vs repeats.

    A play is "first" if its ts is the earliest ts for that (artist, track)
    across the WHOLE history.
    """
    where, params = _period_where(period, now_ts)
    clause = _and(where)
    # `where` is a bare "ts >= ?" fragment; we must qualify it as s.ts here
    # because both `scrobbles s` and the `firsts f` CTE expose a `ts`/`fts`.
    period_filter = f"WHERE s.{where}" if where else ""
    first = conn.execute(
        "WITH firsts AS (SELECT artist, track, MIN(ts) AS fts FROM scrobbles "
        "GROUP BY artist, track) "
        "SELECT COUNT(*) FROM scrobbles s JOIN firsts f "
        "ON f.artist = s.artist AND f.track = s.track AND f.fts = s.ts "
        f"{period_filter}", params).fetchone()[0]
    total = conn.execute(
        f"SELECT COUNT(*) FROM scrobbles {clause}", params).fetchone()[0]
    return {"first": first, "repeat": total - first}


def discovery_rate(conn, period="all", tz_offset_min=0, now_ts=None):
    """New (first-seen) artists per local calendar day, within the period.

    Returns [{"date": "YYYY-MM-DD", "new_artists": n}] ordered by date.

    Only a lower bound is applied: for a finite period this returns artists
    whose all-time first listen falls in [period_start, now]. Since now_ts is
    the reference for period_start, this is the intended "newly discovered in
    the last N days" semantic.
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


def overview(conn, period="all", tz_offset_min=0, now_ts=None):
    """Summary scalars for the period."""
    where, params = _period_where(period, now_ts)
    clause = _and(where)
    row = conn.execute(
        f"SELECT COUNT(*) AS total, COUNT(DISTINCT artist) AS artists, "
        f"COUNT(DISTINCT artist || char(31) || track) AS tracks, "
        f"MIN(ts) AS lo, MAX(ts) AS hi FROM scrobbles {clause}", params).fetchone()
    tg = top_genres(conn, period=period, tz_offset_min=tz_offset_min, now_ts=now_ts, limit=1)
    fwhere, fparams = _feature_where(period, now_ts, require="f.bpm IS NOT NULL")
    avg_bpm_row = conn.execute(f"SELECT AVG(f.bpm) AS a {fwhere}", fparams).fetchone()
    avg_bpm = round(avg_bpm_row["a"], 1) if avg_bpm_row["a"] is not None else None
    return {
        "total_scrobbles": row["total"],
        "unique_artists": row["artists"],
        "unique_tracks": row["tracks"],
        "first_ts": row["lo"],
        "last_ts": row["hi"],
        "top_genre": tg[0]["genre"] if tg else None,
        "est_listening_seconds": row["total"] * AVG_TRACK_SECONDS,
        "avg_bpm": avg_bpm,
        "feature_coverage": feature_coverage(conn, period=period,
                                             tz_offset_min=tz_offset_min, now_ts=now_ts),
    }
