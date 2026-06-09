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
