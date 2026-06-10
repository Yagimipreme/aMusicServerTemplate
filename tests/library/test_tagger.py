import json
import os
from unittest.mock import MagicMock, patch

import pytest

from library.tagger import apply_from_config, apply_to_file, clean_title


def test_clean_title_strips_parenthetical_suffix():
    assert clean_title("Artist - Song (Official Music Video)") == "Artist - Song"


def test_clean_title_strips_bracket_suffix():
    assert clean_title("DJ Chipstyler - The Community Rave [HD]") == "DJ Chipstyler - The Community Rave"


def test_clean_title_strips_multiple_suffixes():
    result = clean_title("DJ Chipstyler - The Community Rave (Official Music Video) [HD]")
    assert result == "DJ Chipstyler - The Community Rave"


def test_clean_title_strips_standalone_suffix():
    assert clean_title("Artist - Song Official Audio") == "Artist - Song"


def test_clean_title_no_change_when_already_clean():
    assert clean_title("Artist - Song") == "Artist - Song"


def test_clean_title_case_insensitive():
    assert clean_title("Song official music video") == "Song"


def test_clean_title_strips_trailing_dash():
    assert clean_title("Artist - Song - Official") == "Artist - Song"


def test_clean_title_extra_suffixes_file(tmp_path):
    suffix_file = tmp_path / "suffixes.txt"
    suffix_file.write_text("Exclusive\n# comment\nLive Performance\n")
    assert clean_title("Artist - Song (Exclusive)", str(suffix_file)) == "Artist - Song"
    assert clean_title("Artist - Song Live Performance", str(suffix_file)) == "Artist - Song"


def test_clean_title_extra_file_missing_is_silent():
    assert clean_title("Song HD", "/nonexistent/path.txt") == "Song"


def test_apply_to_file_strips_suffix(tmp_path):
    mp3 = tmp_path / "song.mp3"
    mp3.write_bytes(b"")
    mock_tag = MagicMock()
    mock_tag.title = "Song (Official Music Video)"
    mock_audio = MagicMock()
    mock_audio.tag = mock_tag
    with patch("library.tagger.eyed3") as mock_eyed3:
        mock_eyed3.load.return_value = mock_audio
        changed = apply_to_file(str(mp3))
    assert changed is True
    assert mock_tag.title == "Song"
    mock_tag.save.assert_called_once()


def test_apply_to_file_no_change_when_already_clean(tmp_path):
    mp3 = tmp_path / "song.mp3"
    mp3.write_bytes(b"")
    mock_tag = MagicMock()
    mock_tag.title = "Artist - Song"
    mock_audio = MagicMock()
    mock_audio.tag = mock_tag
    with patch("library.tagger.eyed3") as mock_eyed3:
        mock_eyed3.load.return_value = mock_audio
        changed = apply_to_file(str(mp3))
    assert changed is False
    mock_tag.save.assert_not_called()


def test_apply_to_file_returns_false_when_no_tag(tmp_path):
    mp3 = tmp_path / "song.mp3"
    mp3.write_bytes(b"")
    mock_audio = MagicMock()
    mock_audio.tag = None
    with patch("library.tagger.eyed3") as mock_eyed3:
        mock_eyed3.load.return_value = mock_audio
        changed = apply_to_file(str(mp3))
    assert changed is False


def test_apply_from_config_respects_enabled_false(tmp_path):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"title_cleanup": {"enabled": False}}))
    mp3 = tmp_path / "song.mp3"
    mp3.write_bytes(b"")
    with patch("library.tagger.eyed3") as mock_eyed3:
        result = apply_from_config(str(mp3), str(cfg_path))
    mock_eyed3.load.assert_not_called()
    assert result is False


def test_apply_from_config_calls_apply_to_file_when_enabled(tmp_path):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"title_cleanup": {"enabled": True, "extra_suffixes_file": ""}}))
    mp3 = tmp_path / "song.mp3"
    mp3.write_bytes(b"")
    mock_tag = MagicMock()
    mock_tag.title = "Song [HD]"
    mock_audio = MagicMock()
    mock_audio.tag = mock_tag
    with patch("library.tagger.eyed3") as mock_eyed3:
        mock_eyed3.load.return_value = mock_audio
        result = apply_from_config(str(mp3), str(cfg_path))
    assert result is True
    assert mock_tag.title == "Song"


def test_clean_title_does_not_strip_mid_word_suffix():
    # "HD" must not be stripped from inside a word
    assert clean_title("ADHD") == "ADHD"


def test_apply_to_file_does_not_write_empty_title(tmp_path):
    mp3 = tmp_path / "song.mp3"
    mp3.write_bytes(b"")
    mock_tag = MagicMock()
    mock_tag.title = "HD"  # entire title is a suffix
    mock_audio = MagicMock()
    mock_audio.tag = mock_tag
    with patch("library.tagger.eyed3") as mock_eyed3:
        mock_eyed3.load.return_value = mock_audio
        changed = apply_to_file(str(mp3))
    assert changed is False
    mock_tag.save.assert_not_called()
