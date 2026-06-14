from follow import runner
from follow import fstate


def _follow(mbid, name):
    return {"mbid": mbid, "name": name, "disambiguation": "", "followed_at": ""}


class FakeLB:
    def __init__(self, fresh):
        self._fresh = fresh
    def fresh_releases(self, pivot_date, days=7, past=True, future=False):
        return self._fresh


class FakeMB:
    def __init__(self, tracks):
        self._tracks = tracks
    def get_release_groups(self, mbid, limit=100):
        return []
    def get_release_tracks(self, rg_mbid):
        return self._tracks.get(rg_mbid, [])


def _fresh_single(mbid="m1", rg="rg-1", name="Song"):
    return [{"artist_mbids": [mbid], "release_date": "2026-06-12",
             "release_group_mbid": rg, "release_name": name,
             "primary_type": "Single", "artist_name": "A"}]


def test_happy_path_acquires_and_writes_playlist(state_path):
    st = fstate.load(state_path)
    written = {}

    def fake_resolve(search_fn, artists, per_artist=1):
        a = artists[0]
        return [{"artist": a["name"], "title": a["top_track"], "url": "u"}]

    def fake_acquire(download_fn, candidate):
        return [f"/songs/{candidate['title']}.mp3"]

    def fake_assemble(song_dir, paths, name, cap):
        written["paths"] = list(paths)
        written["name"] = name
        return "/songs/" + name + ".m3u"

    result = runner.run_once(
        mb_client=FakeMB({"rg-1": ["Song"]}),
        lb_client=FakeLB(_fresh_single()),
        follows=[_follow("m1", "A")],
        state=st,
        search_fn=None, download_fn=None, song_dir="/songs",
        cfg={"lookback_days": 7, "default_backfill_days": 30,
             "playlist_name": "NEW RELEASES", "playlist_cap": 100,
             "notify": {"webhook_url": "", "ntfy_topic": ""}},
        resolve_fn=fake_resolve, acquire_fn=fake_acquire,
        assemble_fn=fake_assemble, push_fn=lambda *a, **k: None,
        today="2026-06-14",
    )
    assert result["acquired"] == 1
    assert written["name"] == "NEW RELEASES"
    assert written["paths"] == ["/songs/Song.mp3"]
    assert st.has_acquired("rg-1") is True
    assert st.feed()[0]["status"] == "acquired"


def test_idempotent_second_run_downloads_nothing(state_path):
    st = fstate.load(state_path)
    calls = {"n": 0}

    def fake_resolve(search_fn, artists, per_artist=1):
        return [{"artist": artists[0]["name"], "title": artists[0]["top_track"], "url": "u"}]

    def fake_acquire(download_fn, candidate):
        calls["n"] += 1
        return [f"/songs/{candidate['title']}.mp3"]

    kwargs = dict(
        mb_client=FakeMB({"rg-1": ["Song"]}), lb_client=FakeLB(_fresh_single()),
        follows=[_follow("m1", "A")], state=st, search_fn=None, download_fn=None,
        song_dir="/songs",
        cfg={"lookback_days": 7, "default_backfill_days": 30,
             "playlist_name": "NEW RELEASES", "playlist_cap": 100,
             "notify": {"webhook_url": "", "ntfy_topic": ""}},
        resolve_fn=fake_resolve, acquire_fn=fake_acquire,
        assemble_fn=lambda *a, **k: "x", push_fn=lambda *a, **k: None,
        today="2026-06-14",
    )
    runner.run_once(**kwargs)
    runner.run_once(**kwargs)
    assert calls["n"] == 1   # second run acquires nothing


def test_failure_marks_pending_then_unavailable_after_3(state_path):
    st = fstate.load(state_path)

    def fake_resolve(search_fn, artists, per_artist=1):
        return []   # no source found

    kwargs = dict(
        mb_client=FakeMB({"rg-1": ["Song"]}), lb_client=FakeLB(_fresh_single()),
        follows=[_follow("m1", "A")], state=st, search_fn=None, download_fn=None,
        song_dir="/songs",
        cfg={"lookback_days": 7, "default_backfill_days": 30,
             "playlist_name": "NEW RELEASES", "playlist_cap": 100,
             "notify": {"webhook_url": "", "ntfy_topic": ""}},
        resolve_fn=fake_resolve, acquire_fn=lambda *a, **k: [],
        assemble_fn=lambda *a, **k: "x", push_fn=lambda *a, **k: None,
        today="2026-06-14",
    )
    runner.run_once(**kwargs)
    assert st.pending()[0]["attempts"] == 1
    runner.run_once(**kwargs)
    assert st.pending()[0]["attempts"] == 2
    runner.run_once(**kwargs)
    # third attempt → dropped + recorded unavailable
    assert st.pending() == []
    assert any(e["status"] == "unavailable" for e in st.feed())
