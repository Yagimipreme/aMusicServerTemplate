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


from discover.playlist_sync import migrate_from_m3u


def _fake_sub(existing_id=None, songs=None, created="NEW"):
    state = {"deleted": [], "created": None}
    songs = songs or []

    def search_songs(query, count=5):
        return songs

    sub = SimpleNamespace(
        find_playlist_id=lambda name: existing_id,
        delete_playlist=lambda pid: state["deleted"].append(pid) or True,
        create_playlist=lambda name, ids: state.__setitem__("created", (name, list(ids))) or created,
        search_songs=search_songs,
        start_scan=lambda: True,
        get_scan_status=lambda: {"scanning": False, "count": 0},
    )
    return sub, state


def test_migrate_renames_m3u_to_bak_and_never_deletes_it(tmp_path):
    m3u = tmp_path / "Weekly Mix.m3u"
    m3u.write_text("#EXTM3U\na.mp3\n", encoding="utf-8")
    sub, state = _fake_sub(existing_id="7",
                           songs=[{"id": "s1", "title": "", "artist": "", "path": "x/a.mp3"}])
    r = migrate_from_m3u(sub, "Weekly Mix", str(tmp_path), tag_reader=lambda p: ("", ""))
    assert r["migrated"] is True
    assert not m3u.exists()
    assert (tmp_path / "Weekly Mix.m3u.bak").read_text(encoding="utf-8").startswith("#EXTM3U")
    assert r["backup"].endswith(".m3u.bak")


def test_migrate_deletes_the_old_api_playlist_first(tmp_path):
    (tmp_path / "Weekly Mix.m3u").write_text("#EXTM3U\na.mp3\n", encoding="utf-8")
    sub, state = _fake_sub(existing_id="7",
                           songs=[{"id": "s1", "title": "", "artist": "", "path": "x/a.mp3"}])
    migrate_from_m3u(sub, "Weekly Mix", str(tmp_path), tag_reader=lambda p: ("", ""))
    assert state["deleted"] == ["7"]


def test_migrate_seeds_new_playlist_and_marks_tracks_owned(tmp_path):
    (tmp_path / "Weekly Mix.m3u").write_text("#EXTM3U\na.mp3\nb.mp3\n", encoding="utf-8")

    def search_songs(query, count=5):
        base = query
        return [{"id": "id-" + base, "title": "", "artist": "", "path": "lib/" + base + ".mp3"}]

    sub, state = _fake_sub(existing_id=None)
    sub.search_songs = search_songs
    r = migrate_from_m3u(sub, "Weekly Mix", str(tmp_path), tag_reader=lambda p: ("", ""))
    assert state["created"][0] == "Weekly Mix"
    assert state["created"][1] == ["id-a", "id-b"]
    assert r["owned"] == ["id-a", "id-b"]
    assert r["playlist_id"] == "NEW"


def test_migrate_is_a_noop_without_an_m3u(tmp_path):
    sub, state = _fake_sub()
    r = migrate_from_m3u(sub, "Weekly Mix", str(tmp_path), tag_reader=lambda p: ("", ""))
    assert r["migrated"] is False
    assert state["deleted"] == [] and state["created"] is None


def test_migrate_sanitizes_the_playlist_name_for_the_filename(tmp_path):
    (tmp_path / "Odd_Name.m3u").write_text("#EXTM3U\na.mp3\n", encoding="utf-8")
    sub, state = _fake_sub(songs=[{"id": "s1", "title": "", "artist": "", "path": "x/a.mp3"}])
    r = migrate_from_m3u(sub, "Odd/Name", str(tmp_path), tag_reader=lambda p: ("", ""))
    assert r["migrated"] is True
    assert (tmp_path / "Odd_Name.m3u.bak").exists()


from discover.playlist_sync import sync_playlist


class FakeSubsonic:
    """Fake Navidrome playlist store, in the SimpleNamespace spirit of the
    existing engine test fakes but stateful enough to assert merges."""

    def __init__(self, playlists=None, songs=None):
        # playlists: {name: {"id": str, "ids": [song ids]}}
        self.playlists = playlists or {}
        self.songs = songs or {}      # query -> [hit dicts]
        self.replaced = []
        self.scans = 0

    def start_scan(self):
        self.scans += 1
        return True

    def get_scan_status(self):
        return {"scanning": False, "count": 0}

    def find_playlist_id(self, name):
        pl = self.playlists.get(name)
        return pl["id"] if pl else None

    def get_playlist_song_ids(self, pid):
        for pl in self.playlists.values():
            if pl["id"] == pid:
                return list(pl["ids"])
        return []

    def create_playlist(self, name, ids):
        pid = "pl-" + name.replace(" ", "-")
        self.playlists[name] = {"id": pid, "ids": list(ids)}
        return pid

    def delete_playlist(self, pid):
        for n, pl in list(self.playlists.items()):
            if pl["id"] == pid:
                del self.playlists[n]
        return True

    def replace_playlist(self, pid, ids):
        self.replaced.append((pid, list(ids)))
        for pl in self.playlists.values():
            if pl["id"] == pid:
                pl["ids"] = list(ids)
        return True

    def search_songs(self, query, count=5):
        return self.songs.get(query.strip(), [])


def _ledger(**kw):
    base = {"playlist_id": "", "owned": [], "pending": [], "migrated": True}
    base.update(kw)
    return base


def test_sync_creates_the_playlist_when_missing(tmp_path):
    sub = FakeSubsonic(songs={"A T": [{"id": "s1", "title": "T", "artist": "A"}]})
    led = _ledger()
    r = sync_playlist(sub, "Weekly Mix", ["/m/1.mp3"], led, cap=10,
                      tag_reader=lambda p: ("A", "T"))
    assert r["status"] == "ok"
    assert led["playlist_id"] == "pl-Weekly-Mix"
    assert led["owned"] == ["s1"]
    assert sub.replaced[-1][1] == ["s1"]


def test_sync_preserves_user_tracks_and_appends_new_ones(tmp_path):
    sub = FakeSubsonic(playlists={"Weekly Mix": {"id": "p1", "ids": ["u1", "e1"]}},
                       songs={"A T2": [{"id": "e2", "title": "T2", "artist": "A"}]})
    led = _ledger(playlist_id="p1", owned=["e1"])
    sync_playlist(sub, "Weekly Mix", ["/m/2.mp3"], led, cap=10,
                  tag_reader=lambda p: ("A", "T2"))
    assert sub.replaced[-1] == ("p1", ["u1", "e1", "e2"])
    assert led["owned"] == ["e1", "e2"]


def test_sync_respects_a_user_deletion(tmp_path):
    sub = FakeSubsonic(playlists={"Weekly Mix": {"id": "p1", "ids": ["e2"]}}, songs={})
    led = _ledger(playlist_id="p1", owned=["e1", "e2"])
    sync_playlist(sub, "Weekly Mix", [], led, cap=10, tag_reader=lambda p: ("", ""))
    assert sub.replaced[-1] == ("p1", ["e2"])
    assert led["owned"] == ["e2"]


def test_sync_evicts_oldest_engine_track_past_cap(tmp_path):
    sub = FakeSubsonic(playlists={"Weekly Mix": {"id": "p1", "ids": ["u1", "e1", "e2"]}},
                       songs={"A T3": [{"id": "e3", "title": "T3", "artist": "A"}]})
    led = _ledger(playlist_id="p1", owned=["e1", "e2"])
    r = sync_playlist(sub, "Weekly Mix", ["/m/3.mp3"], led, cap=2,
                      tag_reader=lambda p: ("A", "T3"))
    assert r["evicted"] == 1
    assert sub.replaced[-1] == ("p1", ["u1", "e2", "e3"])
    assert led["owned"] == ["e2", "e3"]


def test_sync_queues_unresolved_paths_as_pending(tmp_path):
    sub = FakeSubsonic(playlists={"Weekly Mix": {"id": "p1", "ids": []}}, songs={})
    led = _ledger(playlist_id="p1")
    r = sync_playlist(sub, "Weekly Mix", ["/m/miss.mp3"], led, cap=10,
                      tag_reader=lambda p: ("A", "Missing"))
    assert led["pending"] == ["/m/miss.mp3"]
    assert r["pending"] == 1


def test_sync_retries_pending_paths_on_the_next_run(tmp_path):
    sub = FakeSubsonic(playlists={"Weekly Mix": {"id": "p1", "ids": []}},
                       songs={"A Missing": [{"id": "late", "title": "Missing", "artist": "A"}]})
    led = _ledger(playlist_id="p1", pending=["/m/miss.mp3"])
    sync_playlist(sub, "Weekly Mix", [], led, cap=10, tag_reader=lambda p: ("A", "Missing"))
    assert led["pending"] == []
    assert led["owned"] == ["late"]


def test_sync_runs_migration_once_then_never_again(tmp_path):
    (tmp_path / "Weekly Mix.m3u").write_text("#EXTM3U\nold.mp3\n", encoding="utf-8")
    sub = FakeSubsonic(playlists={"Weekly Mix": {"id": "old", "ids": ["x"]}},
                       songs={"old": [{"id": "s-old", "title": "", "artist": "",
                                       "path": "lib/old.mp3"}]})
    led = _ledger(migrated=False)
    sync_playlist(sub, "Weekly Mix", [], led, cap=10, song_dir=str(tmp_path),
                  tag_reader=lambda p: ("", ""))
    assert led["migrated"] is True
    assert led["owned"] == ["s-old"]
    assert not (tmp_path / "Weekly Mix.m3u").exists()

    # Second run must not touch the .bak or re-migrate
    (tmp_path / "Weekly Mix.m3u").write_text("#EXTM3U\nnew.mp3\n", encoding="utf-8")
    sync_playlist(sub, "Weekly Mix", [], led, cap=10, song_dir=str(tmp_path),
                  tag_reader=lambda p: ("", ""))
    assert (tmp_path / "Weekly Mix.m3u").exists()   # untouched on the second run


def test_sync_waits_for_the_scan_before_resolving(tmp_path):
    sub = FakeSubsonic(playlists={"Weekly Mix": {"id": "p1", "ids": []}},
                       songs={"A T": [{"id": "s1", "title": "T", "artist": "A"}]})
    led = _ledger(playlist_id="p1")
    sync_playlist(sub, "Weekly Mix", ["/m/1.mp3"], led, cap=10,
                  tag_reader=lambda p: ("A", "T"))
    assert sub.scans >= 1


def test_sync_reports_error_without_crashing_the_run(tmp_path):
    sub = FakeSubsonic()
    sub.create_playlist = lambda name, ids: (_ for _ in ()).throw(RuntimeError("nd down"))
    led = _ledger()
    r = sync_playlist(sub, "Weekly Mix", [], led, cap=10, tag_reader=lambda p: ("", ""))
    assert r["status"] == "error"
