from discover.state import DiscoverState, load_state


def test_add_and_has(tmp_path):
    p = tmp_path / "discover_state.json"
    st = load_state(str(p))
    assert st.has("k1") is False
    st.add("k1")
    assert st.has("k1") is True


def test_persists_across_reload(tmp_path):
    p = tmp_path / "discover_state.json"
    st = load_state(str(p))
    st.add("k1")
    st.save()
    st2 = load_state(str(p))
    assert st2.has("k1") is True


def test_load_missing_file_is_empty(tmp_path):
    st = load_state(str(tmp_path / "nope.json"))
    assert st.has("anything") is False


def test_playlist_ledger_starts_blank_and_is_mutable(tmp_path):
    from discover.state import DiscoverState
    st = DiscoverState(path=str(tmp_path / "s.json"), suggested={})
    led = st.playlist_ledger("Weekly Mix")
    assert led == {"playlist_id": "", "owned": [], "pending": [], "migrated": False}
    led["owned"].append("s1")
    assert st.playlist_ledger("Weekly Mix")["owned"] == ["s1"]


def test_playlist_ledger_round_trips_through_save_and_load(tmp_path):
    from discover.state import DiscoverState, load_state
    p = str(tmp_path / "s.json")
    st = DiscoverState(path=p, suggested={})
    led = st.playlist_ledger("Weekly Mix")
    led.update({"playlist_id": "p1", "owned": ["a", "b"], "pending": ["/x.mp3"], "migrated": True})
    st.save()

    st2 = load_state(p)
    assert st2.playlist_ledger("Weekly Mix") == {
        "playlist_id": "p1", "owned": ["a", "b"], "pending": ["/x.mp3"], "migrated": True}


def test_playlist_ledgers_are_per_profile(tmp_path):
    from discover.state import DiscoverState, load_state
    p = str(tmp_path / "s.json")
    st = DiscoverState(path=p, suggested={})
    st.playlist_ledger("A")["owned"] = ["a"]
    st.playlist_ledger("B")["owned"] = ["b"]
    st.save()
    st2 = load_state(p)
    assert st2.playlist_ledger("A")["owned"] == ["a"]
    assert st2.playlist_ledger("B")["owned"] == ["b"]


def test_save_preserves_foreign_keys_alongside_playlists(tmp_path):
    import json
    from discover.state import DiscoverState
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"lastfm_ready": True}), encoding="utf-8")
    st = DiscoverState(path=str(p), suggested={})
    st.playlist_ledger("A")["owned"] = ["a"]
    st.save()
    on_disk = json.loads(p.read_text(encoding="utf-8"))
    assert on_disk["lastfm_ready"] is True
    assert on_disk["playlists"]["A"]["owned"] == ["a"]


def test_load_state_tolerates_a_file_with_no_playlists_key(tmp_path):
    import json
    from discover.state import load_state
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"suggested": {}, "last_run": None}), encoding="utf-8")
    st = load_state(str(p))
    assert st.playlist_ledger("Anything")["owned"] == []
