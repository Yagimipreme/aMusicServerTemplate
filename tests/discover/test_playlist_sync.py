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
