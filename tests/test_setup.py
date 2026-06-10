import json
import os
from unittest.mock import MagicMock, patch

import pytest


def test_load_config_reads_existing_file(tmp_path):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"song_dir": "/music"}))
    import setup as _setup_mod
    orig = _setup_mod.CONFIG_PATH
    _setup_mod.CONFIG_PATH = str(cfg_path)
    try:
        result = _setup_mod._load_config()
    finally:
        _setup_mod.CONFIG_PATH = orig
    assert result["song_dir"] == "/music"


def test_load_config_returns_empty_dict_when_file_missing(tmp_path):
    import setup as _setup_mod
    orig_cfg = _setup_mod.CONFIG_PATH
    orig_ex = _setup_mod.CONFIG_EXAMPLE_PATH
    _setup_mod.CONFIG_PATH = str(tmp_path / "nonexistent.json")
    _setup_mod.CONFIG_EXAMPLE_PATH = str(tmp_path / "nonexistent2.json")
    try:
        result = _setup_mod._load_config()
    finally:
        _setup_mod.CONFIG_PATH = orig_cfg
        _setup_mod.CONFIG_EXAMPLE_PATH = orig_ex
    assert result == {}


def test_save_and_reload_config(tmp_path):
    import setup as _setup_mod
    cfg_path = tmp_path / "config.json"
    orig_cfg = _setup_mod.CONFIG_PATH
    orig_ex = _setup_mod.CONFIG_EXAMPLE_PATH
    _setup_mod.CONFIG_PATH = str(cfg_path)
    _setup_mod.CONFIG_EXAMPLE_PATH = str(tmp_path / "nonexistent.json")
    try:
        _setup_mod._save_config({"song_dir": "/music", "navidrome_url": "http://localhost:4533"})
        result = _setup_mod._load_config()
    finally:
        _setup_mod.CONFIG_PATH = orig_cfg
        _setup_mod.CONFIG_EXAMPLE_PATH = orig_ex
    assert result["song_dir"] == "/music"
    assert result["navidrome_url"] == "http://localhost:4533"


def test_ping_navidrome_returns_true_on_ok_response():
    import setup as _setup_mod
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps({
        "subsonic-response": {"status": "ok"}
    }).encode()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    with patch("setup.urllib.request.urlopen", return_value=mock_resp):
        assert _setup_mod._ping_navidrome("http://localhost:4533", "admin", "pass") is True


def test_ping_navidrome_returns_false_on_exception():
    import setup as _setup_mod
    with patch("setup.urllib.request.urlopen", side_effect=Exception("connection refused")):
        assert _setup_mod._ping_navidrome("http://localhost:4533", "admin", "wrong") is False


def test_ping_navidrome_returns_false_when_status_not_ok():
    import setup as _setup_mod
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps({
        "subsonic-response": {"status": "failed"}
    }).encode()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    with patch("setup.urllib.request.urlopen", return_value=mock_resp):
        assert _setup_mod._ping_navidrome("http://localhost:4533", "admin", "bad") is False
