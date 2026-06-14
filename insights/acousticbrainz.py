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


def _primary_mood(highlevel: dict) -> "str | None":
    """Return the strongest positive mood label (e.g. 'happy'), or None.

    AcousticBrainz exposes mood_* binary classifiers; we pick the classifier
    whose predicted value is the positive class with the highest probability.
    """
    best, best_p = None, -1.0
    for key, clf in highlevel.items():
        if not key.startswith("mood_"):
            continue
        value = clf.get("value") or ""
        prob = clf.get("probability", 0.0)
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
        if hl.status_code == 200:
            high = hl.json()
        else:
            if hl.status_code != 404:
                logger.warning("acousticbrainz: high-level returned %s for %s; "
                               "keeping low-level only", hl.status_code, mbid)
            high = {}
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
