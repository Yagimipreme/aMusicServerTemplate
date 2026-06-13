from unittest.mock import MagicMock, patch

import pytest

from library.scanner import _make_record, _normalize, scan


def test_normalize_strips_and_casefolds():
    assert _normalize("  Artist  ") == "artist"
    assert _normalize("") == ""
    assert _normalize(None) == ""


def test_make_record_uses_id3_key_when_tags_present():
    r = _make_record("/path/song.mp3", "Boards of Canada", "Roygbiv", True)
    assert r["key"] == "roygbiv"
    assert r["has_tags"] is True
    assert r["artist"] == "Boards of Canada"
    assert r["title"] == "Roygbiv"


def test_make_record_falls_back_to_filename_when_no_tags():
    r = _make_record("/path/boards of canada - roygbiv.mp3", "", "", False)
    assert r["key"] == "boards of canada - roygbiv"
    assert r["has_tags"] is False


def test_scan_returns_record_for_each_mp3(tmp_path):
    (tmp_path / "song.mp3").write_bytes(b"")
    (tmp_path / "other.flac").write_bytes(b"")  # ignored

    mock_tag = MagicMock()
    mock_tag.artist = "Boards of Canada"
    mock_tag.title = "Roygbiv"
    mock_audio = MagicMock()
    mock_audio.tag = mock_tag

    with patch("library.scanner.eyed3") as mock_eyed3:
        mock_eyed3.load.return_value = mock_audio
        results = scan(str(tmp_path))

    assert len(results) == 1
    assert results[0]["artist"] == "Boards of Canada"
    assert results[0]["title"] == "Roygbiv"
    assert results[0]["has_tags"] is True


def test_scan_handles_missing_tags(tmp_path):
    (tmp_path / "song.mp3").write_bytes(b"")
    mock_audio = MagicMock()
    mock_audio.tag = None

    with patch("library.scanner.eyed3") as mock_eyed3:
        mock_eyed3.load.return_value = mock_audio
        results = scan(str(tmp_path))

    assert len(results) == 1
    assert results[0]["has_tags"] is False
    assert results[0]["key"] == "song"


def test_scan_ignores_non_mp3_files(tmp_path):
    (tmp_path / "song.flac").write_bytes(b"")
    (tmp_path / "notes.txt").write_bytes(b"")

    with patch("library.scanner.eyed3") as mock_eyed3:
        results = scan(str(tmp_path))

    mock_eyed3.load.assert_not_called()
    assert results == []


def test_scan_is_recursive(tmp_path):
    subdir = tmp_path / "subfolder"
    subdir.mkdir()
    (subdir / "deep.mp3").write_bytes(b"")

    mock_audio = MagicMock()
    mock_audio.tag = None

    with patch("library.scanner.eyed3") as mock_eyed3:
        mock_eyed3.load.return_value = mock_audio
        results = scan(str(tmp_path))

    assert len(results) == 1
    assert "deep.mp3" in results[0]["path"]
