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


def test_wait_for_scan_does_not_accept_immediate_idle_without_a_grace_period():
    """A scan that never reports scanning:true (too fast to observe, or the
    async scanner hasn't flipped the flag yet by the time of the first poll)
    must not be accepted as 'done' on the very first status check — that read
    could just as easily mean 'hasn't started yet', in which case downloads
    from this run would resolve to nothing since nothing was actually
    (re)indexed. Give the scanner a bounded grace window to prove it started."""
    calls = {"status": 0}

    def status():
        calls["status"] += 1
        return {"scanning": False, "count": 0}

    sub = SimpleNamespace(start_scan=lambda: True, get_scan_status=status)
    ticks = iter([0, 1, 2, 3, 4, 5, 6])
    result = wait_for_scan(sub, timeout=60, poll=1, sleep_fn=lambda s: None,
                           clock=lambda: next(ticks), start_grace=5)
    assert result is True
    assert calls["status"] > 1   # not accepted on the very first poll


def test_wait_for_scan_accepts_idle_immediately_once_grace_elapses(monkeypatch):
    """After the grace window passes with scanning never observed true, the
    scan is assumed to have already finished (or had nothing to do) and idle
    is accepted — this must not spin for the full timeout in that case."""
    calls = {"status": 0}

    def status():
        calls["status"] += 1
        return {"scanning": False, "count": 0}

    sub = SimpleNamespace(start_scan=lambda: True, get_scan_status=status)
    ticks = iter([0, 6, 12])   # first poll already past a 5s grace window
    result = wait_for_scan(sub, timeout=60, poll=1, sleep_fn=lambda s: None,
                           clock=lambda: next(ticks), start_grace=5)
    assert result is True
    assert calls["status"] == 1


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

def _instant_wait(subsonic):
    """Fast stand-in for wait_for_scan in tests that don't care about its
    specific timing (the race-condition fix is covered directly by the
    wait_for_scan tests above) — still triggers start_scan() so
    scan-triggering assertions keep working, but returns immediately instead
    of sleeping through the real grace period wait_for_scan now has."""
    try:
        subsonic.start_scan()
    except Exception:
        pass
    return True



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
    r = migrate_from_m3u(sub, "Weekly Mix", str(tmp_path), tag_reader=lambda p: ("", ""), wait_fn=_instant_wait)
    assert r["migrated"] is True
    assert not m3u.exists()
    assert (tmp_path / "Weekly Mix.m3u.bak").read_text(encoding="utf-8").startswith("#EXTM3U")
    assert r["backup"].endswith(".m3u.bak")


def test_migrate_deletes_the_old_api_playlist_first(tmp_path):
    (tmp_path / "Weekly Mix.m3u").write_text("#EXTM3U\na.mp3\n", encoding="utf-8")
    sub, state = _fake_sub(existing_id="7",
                           songs=[{"id": "s1", "title": "", "artist": "", "path": "x/a.mp3"}])
    migrate_from_m3u(sub, "Weekly Mix", str(tmp_path), tag_reader=lambda p: ("", ""), wait_fn=_instant_wait)
    assert state["deleted"] == ["7"]


def test_migrate_seeds_new_playlist_and_marks_tracks_owned(tmp_path):
    (tmp_path / "Weekly Mix.m3u").write_text("#EXTM3U\na.mp3\nb.mp3\n", encoding="utf-8")

    def search_songs(query, count=5):
        base = query
        return [{"id": "id-" + base, "title": "", "artist": "", "path": "lib/" + base + ".mp3"}]

    sub, state = _fake_sub(existing_id=None)
    sub.search_songs = search_songs
    r = migrate_from_m3u(sub, "Weekly Mix", str(tmp_path), tag_reader=lambda p: ("", ""), wait_fn=_instant_wait)
    assert state["created"][0] == "Weekly Mix"
    assert state["created"][1] == ["id-a", "id-b"]
    assert r["owned"] == ["id-a", "id-b"]
    assert r["playlist_id"] == "NEW"


def test_migrate_is_a_noop_without_an_m3u(tmp_path):
    sub, state = _fake_sub()
    r = migrate_from_m3u(sub, "Weekly Mix", str(tmp_path), tag_reader=lambda p: ("", ""), wait_fn=_instant_wait)
    assert r["migrated"] is False
    assert state["deleted"] == [] and state["created"] is None


def test_migrate_sanitizes_the_playlist_name_for_the_filename(tmp_path):
    (tmp_path / "Odd_Name.m3u").write_text("#EXTM3U\na.mp3\n", encoding="utf-8")
    sub, state = _fake_sub(songs=[{"id": "s1", "title": "", "artist": "", "path": "x/a.mp3"}])
    r = migrate_from_m3u(sub, "Odd/Name", str(tmp_path), tag_reader=lambda p: ("", ""), wait_fn=_instant_wait)
    assert r["migrated"] is True
    assert (tmp_path / "Odd_Name.m3u.bak").exists()


def test_migrate_aborts_without_touching_old_playlist_or_m3u_if_create_fails(tmp_path):
    """Nothing destructive may happen until the new playlist is durably
    created. If create_playlist fails, the old API playlist must still exist
    and the .m3u must still be at its original path (unrenamed)."""
    m3u = tmp_path / "Weekly Mix.m3u"
    m3u.write_text("#EXTM3U\na.mp3\n", encoding="utf-8")
    sub, state = _fake_sub(existing_id="7",
                           songs=[{"id": "s1", "title": "", "artist": "", "path": "x/a.mp3"}])

    def boom(name, ids):
        raise RuntimeError("nd down")
    sub.create_playlist = boom

    r = migrate_from_m3u(sub, "Weekly Mix", str(tmp_path), tag_reader=lambda p: ("", ""), wait_fn=_instant_wait)
    assert r["migrated"] is False
    assert m3u.exists()             # never renamed
    assert not (tmp_path / "Weekly Mix.m3u.bak").exists()
    assert state["deleted"] == []   # old playlist never touched


def test_migrate_looks_up_old_playlist_before_creating_the_new_one(tmp_path):
    """find_playlist_id(name) must be called before create_playlist(name, ...)
    — the new playlist shares the same name, so looking it up afterwards could
    match the freshly-created playlist instead of the one being retired."""
    (tmp_path / "Weekly Mix.m3u").write_text("#EXTM3U\na.mp3\n", encoding="utf-8")
    sub, state = _fake_sub(existing_id="7",
                           songs=[{"id": "s1", "title": "", "artist": "", "path": "x/a.mp3"}])
    calls = []
    orig_find = sub.find_playlist_id
    orig_create = sub.create_playlist
    sub.find_playlist_id = lambda name: (calls.append("find"), orig_find(name))[1]
    sub.create_playlist = lambda name, ids: (calls.append("create"), orig_create(name, ids))[1]

    migrate_from_m3u(sub, "Weekly Mix", str(tmp_path), tag_reader=lambda p: ("", ""), wait_fn=_instant_wait)
    assert calls.index("find") < calls.index("create")
    assert state["deleted"] == ["7"]   # the OLD id, not the new one


def test_migrate_queues_unresolved_tracks_as_pending(tmp_path):
    (tmp_path / "Weekly Mix.m3u").write_text("#EXTM3U\na.mp3\nmissing.mp3\n", encoding="utf-8")

    def search_songs(query, count=5):
        if query == "a":
            return [{"id": "id-a", "title": "", "artist": "", "path": "lib/a.mp3"}]
        return []

    sub, state = _fake_sub(existing_id=None)
    sub.search_songs = search_songs
    r = migrate_from_m3u(sub, "Weekly Mix", str(tmp_path), tag_reader=lambda p: ("", ""), wait_fn=_instant_wait)
    assert r["migrated"] is True
    assert r["owned"] == ["id-a"]
    assert r["pending"] == [str(tmp_path / "missing.mp3")]


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

    def get_playlist(self, pid):
        for pl in self.playlists.values():
            if pl["id"] == pid:
                return {"id": pid, "entry": [{"id": s} for s in pl["ids"]]}
        return {}

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
                      tag_reader=lambda p: ("A", "T"), wait_fn=_instant_wait)
    assert r["status"] == "ok"
    assert led["playlist_id"] == "pl-Weekly-Mix"
    assert led["owned"] == ["s1"]
    assert sub.replaced[-1][1] == ["s1"]


def test_sync_preserves_user_tracks_and_appends_new_ones(tmp_path):
    sub = FakeSubsonic(playlists={"Weekly Mix": {"id": "p1", "ids": ["u1", "e1"]}},
                       songs={"A T2": [{"id": "e2", "title": "T2", "artist": "A"}]})
    led = _ledger(playlist_id="p1", owned=["e1"])
    sync_playlist(sub, "Weekly Mix", ["/m/2.mp3"], led, cap=10,
                  tag_reader=lambda p: ("A", "T2"), wait_fn=_instant_wait)
    assert sub.replaced[-1] == ("p1", ["u1", "e1", "e2"])
    assert led["owned"] == ["e1", "e2"]


def test_sync_respects_a_user_deletion(tmp_path):
    sub = FakeSubsonic(playlists={"Weekly Mix": {"id": "p1", "ids": ["e2"]}}, songs={})
    led = _ledger(playlist_id="p1", owned=["e1", "e2"])
    sync_playlist(sub, "Weekly Mix", [], led, cap=10, tag_reader=lambda p: ("", ""), wait_fn=_instant_wait)
    assert sub.replaced[-1] == ("p1", ["e2"])
    assert led["owned"] == ["e2"]


def test_sync_evicts_oldest_engine_track_past_cap(tmp_path):
    sub = FakeSubsonic(playlists={"Weekly Mix": {"id": "p1", "ids": ["u1", "e1", "e2"]}},
                       songs={"A T3": [{"id": "e3", "title": "T3", "artist": "A"}]})
    led = _ledger(playlist_id="p1", owned=["e1", "e2"])
    r = sync_playlist(sub, "Weekly Mix", ["/m/3.mp3"], led, cap=2,
                      tag_reader=lambda p: ("A", "T3"), wait_fn=_instant_wait)
    assert r["evicted"] == 1
    assert sub.replaced[-1] == ("p1", ["u1", "e2", "e3"])
    assert led["owned"] == ["e2", "e3"]


def test_sync_queues_unresolved_paths_as_pending(tmp_path):
    sub = FakeSubsonic(playlists={"Weekly Mix": {"id": "p1", "ids": []}}, songs={})
    led = _ledger(playlist_id="p1")
    r = sync_playlist(sub, "Weekly Mix", ["/m/miss.mp3"], led, cap=10,
                      tag_reader=lambda p: ("A", "Missing"), wait_fn=_instant_wait)
    assert led["pending"] == ["/m/miss.mp3"]
    assert r["pending"] == 1


def test_sync_retries_pending_paths_on_the_next_run(tmp_path):
    sub = FakeSubsonic(playlists={"Weekly Mix": {"id": "p1", "ids": []}},
                       songs={"A Missing": [{"id": "late", "title": "Missing", "artist": "A"}]})
    led = _ledger(playlist_id="p1", pending=["/m/miss.mp3"])
    sync_playlist(sub, "Weekly Mix", [], led, cap=10, tag_reader=lambda p: ("A", "Missing"), wait_fn=_instant_wait)
    assert led["pending"] == []
    assert led["owned"] == ["late"]


def test_sync_runs_migration_once_then_never_again(tmp_path):
    (tmp_path / "Weekly Mix.m3u").write_text("#EXTM3U\nold.mp3\n", encoding="utf-8")
    sub = FakeSubsonic(playlists={"Weekly Mix": {"id": "old", "ids": ["x"]}},
                       songs={"old": [{"id": "s-old", "title": "", "artist": "",
                                       "path": "lib/old.mp3"}]})
    led = _ledger(migrated=False)
    sync_playlist(sub, "Weekly Mix", [], led, cap=10, song_dir=str(tmp_path),
                  tag_reader=lambda p: ("", ""), wait_fn=_instant_wait)
    assert led["migrated"] is True
    assert led["owned"] == ["s-old"]
    assert not (tmp_path / "Weekly Mix.m3u").exists()

    # Second run must not touch the .bak or re-migrate
    (tmp_path / "Weekly Mix.m3u").write_text("#EXTM3U\nnew.mp3\n", encoding="utf-8")
    sync_playlist(sub, "Weekly Mix", [], led, cap=10, song_dir=str(tmp_path),
                  tag_reader=lambda p: ("", ""), wait_fn=_instant_wait)
    assert (tmp_path / "Weekly Mix.m3u").exists()   # untouched on the second run


def test_sync_does_not_mark_migrated_when_migration_fails(tmp_path):
    """A failed migration must retry next run, not be permanently skipped —
    and the old playlist/m3u must survive the failed attempt untouched."""
    (tmp_path / "Weekly Mix.m3u").write_text("#EXTM3U\na.mp3\n", encoding="utf-8")
    sub = FakeSubsonic(playlists={"Weekly Mix": {"id": "old", "ids": ["x"]}}, songs={})

    def boom(name, ids):
        raise RuntimeError("nd down")
    sub.create_playlist = boom

    led = _ledger(migrated=False)
    r = sync_playlist(sub, "Weekly Mix", [], led, cap=10, song_dir=str(tmp_path),
                      tag_reader=lambda p: ("", ""), wait_fn=_instant_wait)
    assert led["migrated"] is False              # retried next run, not skipped forever
    assert (tmp_path / "Weekly Mix.m3u").exists()  # old m3u untouched
    assert "Weekly Mix" in sub.playlists           # old playlist untouched


def test_sync_moves_unresolved_migration_tracks_to_pending(tmp_path):
    (tmp_path / "Weekly Mix.m3u").write_text("#EXTM3U\nmissing.mp3\n", encoding="utf-8")
    sub = FakeSubsonic(songs={})   # nothing resolves
    led = _ledger(migrated=False)
    sync_playlist(sub, "Weekly Mix", [], led, cap=10, song_dir=str(tmp_path),
                  tag_reader=lambda p: ("", ""), wait_fn=_instant_wait)
    assert led["migrated"] is True
    assert str(tmp_path / "missing.mp3") in led["pending"]


def test_sync_waits_for_the_scan_before_resolving(tmp_path):
    sub = FakeSubsonic(playlists={"Weekly Mix": {"id": "p1", "ids": []}},
                       songs={"A T": [{"id": "s1", "title": "T", "artist": "A"}]})
    led = _ledger(playlist_id="p1")
    sync_playlist(sub, "Weekly Mix", ["/m/1.mp3"], led, cap=10,
                  tag_reader=lambda p: ("A", "T"), wait_fn=_instant_wait)
    assert sub.scans >= 1


def test_sync_reports_error_without_crashing_the_run(tmp_path):
    sub = FakeSubsonic()
    sub.create_playlist = lambda name, ids: (_ for _ in ()).throw(RuntimeError("nd down"))
    led = _ledger()
    r = sync_playlist(sub, "Weekly Mix", [], led, cap=10, tag_reader=lambda p: ("", ""), wait_fn=_instant_wait)
    assert r["status"] == "error"


def test_sync_does_not_lose_resolved_tracks_from_pending_on_write_failure(tmp_path):
    """If replace_playlist raises after paths were successfully resolved this
    run, nothing may be dropped from the retry queue: the previously-pending
    path AND this run's newly-resolved-but-unwritten path must both survive,
    or the suggested-TTL dedupe will silently block rediscovery for ~90 days."""
    sub = FakeSubsonic(playlists={"Weekly Mix": {"id": "p1", "ids": []}},
                       songs={"A T": [{"id": "s1", "title": "T", "artist": "A"}]})

    def boom(pid, ids):
        raise RuntimeError("nd down")
    sub.replace_playlist = boom

    led = _ledger(playlist_id="p1", pending=["/m/old-pending.mp3"])
    r = sync_playlist(sub, "Weekly Mix", ["/m/1.mp3"], led, cap=10,
                      tag_reader=lambda p: ("A", "T"), wait_fn=_instant_wait)
    assert r["status"] == "error"
    assert "/m/old-pending.mp3" in led["pending"]
    assert "/m/1.mp3" in led["pending"]
    assert led["owned"] == []   # nothing committed — write never actually succeeded


def test_sync_does_not_commit_pending_before_playlist_lookup_succeeds(tmp_path):
    """A failure in find_playlist_id/create_playlist/get_playlist_song_ids
    (before replace_playlist is even reached) must also leave pending
    untouched, not just a failure in replace_playlist itself."""
    sub = FakeSubsonic(songs={"A T": [{"id": "s1", "title": "T", "artist": "A"}]})

    def boom(name):
        raise RuntimeError("nd down")
    sub.find_playlist_id = boom

    led = _ledger(pending=["/m/old-pending.mp3"])
    r = sync_playlist(sub, "Weekly Mix", ["/m/1.mp3"], led, cap=10,
                      tag_reader=lambda p: ("A", "T"), wait_fn=_instant_wait)
    assert r["status"] == "error"
    assert "/m/old-pending.mp3" in led["pending"]
    assert "/m/1.mp3" in led["pending"]


def test_sync_recreates_when_ledger_playlist_id_no_longer_exists(tmp_path):
    """If the user deletes the playlist directly in Navidrome, the ledger's
    stale id must be detected and cleared rather than trusted forever — a
    no-op 'ok' against a vanished playlist silently corrupts the ledger."""
    sub = FakeSubsonic(songs={"A T": [{"id": "s1", "title": "T", "artist": "A"}]})
    led = _ledger(playlist_id="gone-id", owned=["old1", "old2"])
    r = sync_playlist(sub, "Weekly Mix", ["/m/1.mp3"], led, cap=10,
                      tag_reader=lambda p: ("A", "T"), wait_fn=_instant_wait)
    assert r["status"] == "ok"
    assert led["playlist_id"] != "gone-id"
    assert led["playlist_id"] == "pl-Weekly-Mix"   # freshly created
    assert led["owned"] == ["s1"]                  # stale owned ids dropped, fresh start


def test_sync_treats_false_replace_playlist_return_as_an_error(tmp_path):
    """replace_playlist's boolean return was never checked — a False (Navidrome
    reported failure without raising) must not be reported as status ok."""
    sub = FakeSubsonic(playlists={"Weekly Mix": {"id": "p1", "ids": []}},
                       songs={"A T": [{"id": "s1", "title": "T", "artist": "A"}]})
    sub.replace_playlist = lambda pid, ids: False

    led = _ledger(playlist_id="p1")
    r = sync_playlist(sub, "Weekly Mix", ["/m/1.mp3"], led, cap=10,
                      tag_reader=lambda p: ("A", "T"), wait_fn=_instant_wait)
    assert r["status"] == "error"
    assert led["owned"] == []
