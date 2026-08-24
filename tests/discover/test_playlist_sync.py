"""Unit tests for the merge-aware playlist sync (discover/playlist_sync.py)."""
import pytest
from discover.playlist_sync import merge_playlist


def test_new_ids_append_to_an_empty_playlist():
    r = merge_playlist(current_ids=[], owned_ids=[], new_ids=["a", "b"], cap=10)
    assert r["final"] == ["a", "b"]
    assert r["owned"] == ["a", "b"]
    assert r["evicted"] == []
    assert r["user"] == []


def test_user_added_tracks_are_preserved_and_kept_first():
    r = merge_playlist(current_ids=["u1", "e1", "u2"], owned_ids=["e1"],
                       new_ids=["e2"], cap=10)
    assert r["user"] == ["u1", "u2"]
    assert r["final"] == ["u1", "u2", "e1", "e2"]
    assert r["owned"] == ["e1", "e2"]


def test_user_deletion_of_an_engine_track_is_respected():
    # e1 was engine-owned but the user removed it from the playlist.
    r = merge_playlist(current_ids=["e2"], owned_ids=["e1", "e2"],
                       new_ids=[], cap=10)
    assert "e1" not in r["final"]
    assert r["owned"] == ["e2"]


def test_deleted_engine_track_is_not_readded_even_if_rediscovered():
    r = merge_playlist(current_ids=["e2"], owned_ids=["e1", "e2"],
                       new_ids=["e2"], cap=10)
    assert r["final"] == ["e2"]
    assert r["owned"] == ["e2"]


def test_cap_evicts_oldest_engine_tracks_only():
    r = merge_playlist(current_ids=["e1", "e2", "e3"], owned_ids=["e1", "e2", "e3"],
                       new_ids=["e4"], cap=3)
    assert r["evicted"] == ["e1"]
    assert r["final"] == ["e2", "e3", "e4"]
    assert r["owned"] == ["e2", "e3", "e4"]


def test_cap_never_counts_or_evicts_user_tracks():
    r = merge_playlist(current_ids=["u1", "u2", "u3", "e1"], owned_ids=["e1"],
                       new_ids=["e2", "e3"], cap=2)
    assert r["user"] == ["u1", "u2", "u3"]
    assert r["evicted"] == ["e1"]
    assert r["final"] == ["u1", "u2", "u3", "e2", "e3"]
    assert len(r["owned"]) == 2


def test_cap_zero_or_none_means_unbounded():
    r = merge_playlist(current_ids=["e1"], owned_ids=["e1"], new_ids=["e2", "e3"], cap=0)
    assert r["evicted"] == []
    assert r["owned"] == ["e1", "e2", "e3"]


def test_new_ids_are_deduped_against_user_tracks():
    # The user already added this track by hand — do not duplicate it.
    r = merge_playlist(current_ids=["x"], owned_ids=[], new_ids=["x", "y"], cap=10)
    assert r["final"] == ["x", "y"]
    assert r["owned"] == ["y"]


def test_new_ids_are_deduped_against_each_other():
    r = merge_playlist(current_ids=[], owned_ids=[], new_ids=["a", "a", "b"], cap=10)
    assert r["owned"] == ["a", "b"]


def test_ordering_of_owned_ids_drives_eviction_not_current_order():
    # owned_ids is the ledger's insertion order = discovery order.
    r = merge_playlist(current_ids=["e3", "e1", "e2"], owned_ids=["e1", "e2", "e3"],
                       new_ids=[], cap=2)
    assert r["evicted"] == ["e1"]
    assert r["owned"] == ["e2", "e3"]


def test_merge_does_not_mutate_its_inputs():
    current, owned, new = ["e1"], ["e1"], ["e2"]
    merge_playlist(current, owned, new, cap=1)
    assert current == ["e1"] and owned == ["e1"] and new == ["e2"]


from types import SimpleNamespace
from discover.playlist_sync import wait_for_scan, resolve_paths


def _tags(mapping):
    return lambda path: mapping.get(path, ("", ""))


def test_wait_for_scan_triggers_scan_and_polls_until_idle():
    calls = {"scan": 0, "status": 0}

    def status():
        calls["status"] += 1
        return {"scanning": calls["status"] < 3, "count": 1}

    sub = SimpleNamespace(start_scan=lambda: calls.__setitem__("scan", calls["scan"] + 1) or True,
                          get_scan_status=status)
    slept = []
    assert wait_for_scan(sub, timeout=60, poll=1, sleep_fn=slept.append) is True
    assert calls["scan"] == 1
    assert calls["status"] == 3


def test_wait_for_scan_gives_up_at_timeout():
    sub = SimpleNamespace(start_scan=lambda: True,
                          get_scan_status=lambda: {"scanning": True, "count": 0})
    ticks = iter([0, 10, 20, 30, 40, 50, 60, 70])
    assert wait_for_scan(sub, timeout=30, poll=10, sleep_fn=lambda s: None,
                         clock=lambda: next(ticks)) is False


def test_wait_for_scan_tolerates_client_without_scan_status():
    sub = SimpleNamespace(start_scan=lambda: True)
    slept = []
    assert wait_for_scan(sub, timeout=30, poll=1, sleep_fn=slept.append) is False
    assert slept   # fell back to a fixed sleep


def test_resolve_paths_requires_both_title_and_artist_to_match():
    sub = SimpleNamespace(search_songs=lambda query, count=5: [
        {"id": "wrong", "title": "Track One", "artist": "Someone Else"},
        {"id": "right", "title": "Track One", "artist": "Artist A"},
    ])
    ids, unresolved = resolve_paths(sub, ["/m/1.mp3"],
                                    tag_reader=_tags({"/m/1.mp3": ("Artist A", "Track One")}))
    assert ids == ["right"]
    assert unresolved == []


def test_resolve_paths_rejects_title_only_match():
    sub = SimpleNamespace(search_songs=lambda query, count=5: [
        {"id": "x", "title": "Track One", "artist": "Someone Else"},
    ])
    ids, unresolved = resolve_paths(sub, ["/m/1.mp3"],
                                    tag_reader=_tags({"/m/1.mp3": ("Artist A", "Track One")}))
    assert ids == []
    assert unresolved == ["/m/1.mp3"]


def test_resolve_paths_falls_back_to_basename_path_match():
    sub = SimpleNamespace(search_songs=lambda query, count=5: [
        {"id": "p", "title": "", "artist": "", "path": "sub/dir/1.mp3"},
    ])
    ids, unresolved = resolve_paths(sub, ["/m/1.mp3"], tag_reader=_tags({}))
    assert ids == ["p"]
    assert unresolved == []


def test_resolve_paths_dedupes_and_preserves_order():
    sub = SimpleNamespace(search_songs=lambda query, count=5: [
        {"id": "s", "title": "T", "artist": "A"},
    ])
    ids, unresolved = resolve_paths(sub, ["/m/1.mp3", "/m/2.mp3"],
                                    tag_reader=_tags({"/m/1.mp3": ("A", "T"),
                                                      "/m/2.mp3": ("A", "T")}))
    assert ids == ["s"]


def test_resolve_paths_survives_search_exception():
    def boom(query, count=5):
        raise RuntimeError("nd down")
    sub = SimpleNamespace(search_songs=boom)
    ids, unresolved = resolve_paths(sub, ["/m/1.mp3"],
                                    tag_reader=_tags({"/m/1.mp3": ("A", "T")}))
    assert ids == []
    assert unresolved == ["/m/1.mp3"]
