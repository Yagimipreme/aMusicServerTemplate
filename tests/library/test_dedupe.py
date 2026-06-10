import time
from unittest.mock import patch

import pytest

from library.dedupe import _pick_keep, find_groups, run


def _rec(path, key, has_tags=True):
    return {"path": path, "key": key, "has_tags": has_tags, "artist": "", "title": ""}


def test_find_groups_returns_only_duplicate_keys():
    records = [
        _rec("/a.mp3", "artist|song"),
        _rec("/b.mp3", "artist|song"),
        _rec("/c.mp3", "artist|other"),
    ]
    groups = find_groups(records)
    assert "artist|song" in groups
    assert len(groups["artist|song"]) == 2
    assert "artist|other" not in groups


def test_find_groups_empty_when_no_duplicates():
    records = [_rec("/a.mp3", "a|b"), _rec("/c.mp3", "c|d")]
    assert find_groups(records) == {}


def test_find_groups_empty_on_empty_input():
    assert find_groups([]) == {}


def test_pick_keep_prefers_tagged_record(tmp_path):
    p1 = tmp_path / "a.mp3"
    p2 = tmp_path / "b.mp3"
    p1.write_bytes(b"")
    p2.write_bytes(b"")
    group = [_rec(str(p1), "k", has_tags=False), _rec(str(p2), "k", has_tags=True)]
    assert _pick_keep(group)["path"] == str(p2)


def test_pick_keep_breaks_tie_by_oldest_mtime(tmp_path):
    p1 = tmp_path / "a.mp3"
    p2 = tmp_path / "b.mp3"
    p1.write_bytes(b"")
    time.sleep(0.02)
    p2.write_bytes(b"")
    group = [_rec(str(p1), "k", has_tags=True), _rec(str(p2), "k", has_tags=True)]
    assert _pick_keep(group)["path"] == str(p1)


def test_run_dry_run_does_not_delete_files(tmp_path):
    p1 = tmp_path / "a.mp3"
    p2 = tmp_path / "b.mp3"
    p1.write_bytes(b"")
    p2.write_bytes(b"")
    records = [_rec(str(p1), "k"), _rec(str(p2), "k")]
    with patch("library.dedupe.scan", return_value=records):
        result = run(str(tmp_path), auto_delete=False)
    assert result["groups"] == 1
    assert len(result["would_delete"]) == 1
    assert result["deleted"] == []
    assert p1.exists() and p2.exists()


def test_run_auto_delete_removes_newer_duplicate(tmp_path):
    p1 = tmp_path / "a.mp3"
    p2 = tmp_path / "b.mp3"
    p1.write_bytes(b"")
    time.sleep(0.02)
    p2.write_bytes(b"")
    records = [_rec(str(p1), "k", has_tags=True), _rec(str(p2), "k", has_tags=True)]
    with patch("library.dedupe.scan", return_value=records):
        result = run(str(tmp_path), auto_delete=True)
    assert result["groups"] == 1
    assert len(result["deleted"]) == 1
    assert p1.exists()
    assert not p2.exists()


def test_run_returns_zero_groups_when_no_duplicates(tmp_path):
    records = [_rec(str(tmp_path / "a.mp3"), "a|b"), _rec(str(tmp_path / "c.mp3"), "c|d")]
    with patch("library.dedupe.scan", return_value=records):
        result = run(str(tmp_path), auto_delete=False)
    assert result == {"groups": 0, "would_delete": [], "deleted": []}
