from follow import store


def test_list_follows_missing_file_returns_empty(follows_path):
    assert store.list_follows(follows_path) == []


def test_add_follow_then_list(follows_path):
    store.add_follow(follows_path, mbid="abc", name="Massive Attack",
                     disambiguation="Bristol trip-hop")
    got = store.list_follows(follows_path)
    assert len(got) == 1
    assert got[0]["mbid"] == "abc"
    assert got[0]["name"] == "Massive Attack"
    assert got[0]["disambiguation"] == "Bristol trip-hop"
    assert "followed_at" in got[0]


def test_add_follow_is_idempotent_by_mbid(follows_path):
    store.add_follow(follows_path, mbid="abc", name="A", disambiguation="")
    store.add_follow(follows_path, mbid="abc", name="A (dup)", disambiguation="")
    got = store.list_follows(follows_path)
    assert len(got) == 1


def test_remove_follow(follows_path):
    store.add_follow(follows_path, mbid="abc", name="A", disambiguation="")
    store.add_follow(follows_path, mbid="def", name="B", disambiguation="")
    store.remove_follow(follows_path, "abc")
    got = store.list_follows(follows_path)
    assert [g["mbid"] for g in got] == ["def"]
