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
    assert result == {"groups": 0, "would_delete": [], "deleted": [], "groups_detail": []}


def test_run_returns_groups_detail_shape(tmp_path):
    """groups_detail carries the keep record and the removable records per group."""
    a = tmp_path / "a.mp3"; a.write_bytes(b"x")
    b = tmp_path / "b.mp3"; b.write_bytes(b"x")
    import os, time
    os.utime(a, (1000, 1000))
    os.utime(b, (2000, 2000))
    records = [
        {"path": str(a), "key": "same title", "artist": "A", "title": "Same Title", "has_tags": True},
        {"path": str(b), "key": "same title", "artist": "",  "title": "Same Title", "has_tags": False},
    ]
    with patch("library.dedupe.scan", return_value=records):
        result = run(str(tmp_path), auto_delete=False)

    assert "groups_detail" in result
    assert len(result["groups_detail"]) == 1
    g = result["groups_detail"][0]
    assert g["key"] == "same title"
    assert g["keep"] == {"path": str(a), "artist": "A", "title": "Same Title"}
    assert g["remove"] == [{"path": str(b), "artist": "", "title": "Same Title"}]


def test_run_groups_detail_omits_singleton_groups(tmp_path):
    """Files with a unique key produce no group at all."""
    a = tmp_path / "a.mp3"; a.write_bytes(b"x")
    records = [{"path": str(a), "key": "solo", "artist": "A", "title": "Solo", "has_tags": True}]
    with patch("library.dedupe.scan", return_value=records):
        result = run(str(tmp_path), auto_delete=False)
    assert result["groups_detail"] == []
    assert result["groups"] == 0


def test_run_groups_detail_matches_would_delete(tmp_path):
    """Every path in groups_detail[*].remove appears in would_delete, and vice versa."""
    paths = []
    for n in ("a", "b", "c"):
        p = tmp_path / f"{n}.mp3"; p.write_bytes(b"x")
        paths.append(str(p))
    import os
    for i, p in enumerate(paths):
        os.utime(p, (1000 + i, 1000 + i))
    records = [{"path": p, "key": "k", "artist": "A", "title": "T", "has_tags": True} for p in paths]
    with patch("library.dedupe.scan", return_value=records):
        result = run(str(tmp_path), auto_delete=False)
    flat = [r["path"] for g in result["groups_detail"] for r in g["remove"]]
    assert sorted(flat) == sorted(result["would_delete"])
