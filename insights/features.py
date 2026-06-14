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
