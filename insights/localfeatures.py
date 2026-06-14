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
        "scale": None,
        "mood": _mood_from(tempo, rms),
        "mood_scores": None,
        "danceability": None,
        "source_hint": "librosa",
    }
