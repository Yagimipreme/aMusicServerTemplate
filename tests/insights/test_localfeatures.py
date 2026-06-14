"""Tests for insights/localfeatures.py — librosa fallback (librosa mocked)."""

import sys
from unittest.mock import MagicMock

from insights import localfeatures


def test_analyze_file_returns_none_without_librosa(monkeypatch):
    monkeypatch.setitem(sys.modules, "librosa", None)
    assert localfeatures.analyze_file("/nope.mp3") is None


def test_mood_from_tempo_energy_quadrant():
    assert localfeatures._mood_from(150.0, 0.20) == "energetic"
    assert localfeatures._mood_from(70.0, 0.01) == "calm"
    assert localfeatures._mood_from(150.0, 0.01) == "frantic"
    assert localfeatures._mood_from(70.0, 0.20) == "warm"


def test_analyze_file_with_mocked_librosa(monkeypatch):
    import numpy as np
    fake = MagicMock()
    fake.load.return_value = (np.zeros(2048, dtype="float32"), 22050)
    fake.beat.beat_track.return_value = (128.0, None)
    chroma = np.zeros((12, 4)); chroma[0, :] = 1.0
    fake.feature.chroma_cqt.return_value = chroma
    fake.feature.rms.return_value = np.array([[0.05]])
    monkeypatch.setitem(sys.modules, "librosa", fake)

    feat = localfeatures.analyze_file("/song.mp3")
    assert feat["bpm"] == 128.0
    assert feat["key"] == "C"
    assert feat["source_hint"] == "librosa"
    assert feat["mood"] in ("calm", "warm", "energetic", "frantic")
