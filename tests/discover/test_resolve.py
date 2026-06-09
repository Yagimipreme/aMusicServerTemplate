from discover.resolve import resolve_tracks


def test_resolve_tracks_calls_search_per_artist_and_flattens():
    calls = []

    def fake_search(name, n):
        calls.append((name, n))
        return [{"title": f"{name} song {i}", "url": f"http://y/{name}/{i}"}
                for i in range(n)]

    artists = [{"name": "Zmajor", "score": 2}, {"name": "Rushex", "score": 1}]
    out = resolve_tracks(fake_search, artists, per_artist=2)

    assert calls == [("Zmajor", 2), ("Rushex", 2)]
    assert len(out) == 4
    assert out[0] == {"artist": "Zmajor", "title": "Zmajor song 0", "url": "http://y/Zmajor/0"}


def test_resolve_tracks_skips_artist_when_search_errors():
    def flaky_search(name, n):
        if name == "Bad":
            raise RuntimeError("boom")
        return [{"title": "ok", "url": "http://y/ok"}]

    artists = [{"name": "Bad", "score": 1}, {"name": "Good", "score": 1}]
    out = resolve_tracks(flaky_search, artists, per_artist=1)
    assert out == [{"artist": "Good", "title": "ok", "url": "http://y/ok"}]


def test_resolve_tracks_drops_results_without_url():
    def search(name, n):
        return [{"title": "no url"}, {"title": "yes", "url": "http://y/1"}]

    out = resolve_tracks(search, [{"name": "A", "score": 1}], per_artist=5)
    assert out == [{"artist": "A", "title": "yes", "url": "http://y/1"}]
