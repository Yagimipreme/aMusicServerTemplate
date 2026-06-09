from discover.dedupe import track_key, filter_fresh
from discover.state import DiscoverState


def test_track_key_is_normalized():
    assert track_key("Boards of Canada", "Roygbiv") == track_key("boards of canada", "  roygbiv ")


def test_filter_fresh_drops_owned_and_already_suggested():
    state = DiscoverState(path="/x", suggested={track_key("A", "owned-before")})
    owned = {("B", "in-library")}

    def is_owned(artist, title):
        return (artist, title) in owned

    candidates = [
        {"artist": "A", "title": "owned-before", "url": "u1"},  # in state -> drop
        {"artist": "B", "title": "in-library", "url": "u2"},    # owned -> drop
        {"artist": "C", "title": "brand-new", "url": "u3"},     # keep
    ]
    fresh = filter_fresh(is_owned, state, candidates)
    assert fresh == [{"artist": "C", "title": "brand-new", "url": "u3"}]


def test_filter_fresh_dedupes_within_batch():
    state = DiscoverState(path="/x", suggested=set())

    def is_owned(artist, title):
        return False

    candidates = [
        {"artist": "C", "title": "dup", "url": "u1"},
        {"artist": "c", "title": "  DUP ", "url": "u2"},  # same key, different casing/space
    ]
    fresh = filter_fresh(is_owned, state, candidates)
    assert len(fresh) == 1
