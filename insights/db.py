"""SQLite store for listening insights.

Sole owner of the insights schema. One connection per thread (sqlite3
connections are not safe to share across threads).
"""

import logging
import sqlite3

logger = logging.getLogger(__name__)

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

CREATE TABLE IF NOT EXISTS library_tracks (
    artist TEXT NOT NULL,
    track  TEXT NOT NULL,
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
    # CREATE ... statements run in autocommit; commit() here is a harmless safety net.
    conn.commit()


def connect(db_path: str) -> sqlite3.Connection:
    """Open (creating if needed) the insights DB with the schema applied."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
    if mode != "wal":
        logger.warning("insights db: WAL mode unavailable (got %r); concurrent reads may degrade", mode)
    init_schema(conn)
    return conn


def get_state(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
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
