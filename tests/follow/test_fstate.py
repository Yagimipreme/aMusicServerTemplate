from follow import fstate


def test_load_missing_returns_empty(state_path):
    st = fstate.load(state_path)
    assert st.has_acquired("rg-1") is False
    assert st.is_backfilled("mbid-1") is False
    assert st.summary()["unseen_count"] == 0


def test_mark_acquired_persists(state_path):
    st = fstate.load(state_path)
    st.mark_acquired("rg-1")
    st.save()
    st2 = fstate.load(state_path)
    assert st2.has_acquired("rg-1") is True


def test_backfill_marker(state_path):
    st = fstate.load(state_path)
    st.mark_backfilled("mbid-1")
    st.save()
    assert fstate.load(state_path).is_backfilled("mbid-1") is True


def test_append_feed_bumps_unseen(state_path):
    st = fstate.load(state_path)
    st.append_feed({"artist": "A", "title": "T", "release_name": "R",
                    "release_date": "2026-06-12", "primary_type": "Single",
                    "status": "acquired"})
    assert st.summary()["unseen_count"] == 1
    assert st.feed()[0]["artist"] == "A"
    assert "ts" in st.feed()[0]


def test_mark_seen_resets_unseen(state_path):
    st = fstate.load(state_path)
    st.append_feed({"artist": "A", "title": "T", "release_name": "R",
                    "release_date": "", "primary_type": "Single", "status": "acquired"})
    st.mark_seen()
    assert st.summary()["unseen_count"] == 0


def test_feed_capped_at_200(state_path):
    st = fstate.load(state_path)
    for i in range(205):
        st.append_feed({"artist": f"A{i}", "title": "T", "release_name": "R",
                        "release_date": "", "primary_type": "Single",
                        "status": "acquired"})
    assert len(st.feed()) == 200
    assert st.feed()[0]["artist"] == "A5"   # oldest 5 dropped


def test_pending_add_bump_drop(state_path):
    st = fstate.load(state_path)
    st.add_pending("rg-1", "A", "T")
    assert st.pending()[0]["attempts"] == 1
    st.bump_pending("rg-1")
    assert st.pending()[0]["attempts"] == 2
    st.drop_pending("rg-1")
    assert st.pending() == []
