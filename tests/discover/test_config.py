import json
from discover.config import load_config


def test_load_config_reads_json(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"song_dir": "/music", "navidrome_user": "x"}))
    cfg = load_config(str(p))
    assert cfg["song_dir"] == "/music"
    assert cfg["navidrome_user"] == "x"


def test_load_config_missing_file_returns_empty(tmp_path):
    cfg = load_config(str(tmp_path / "nope.json"))
    assert cfg == {}
