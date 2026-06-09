from types import SimpleNamespace
from discover.engine import run_weekly
from discover.state import DiscoverState


def build_deps(tmp_path, owned_titles=()):
    owned = set(owned_titles)

    subsonic = SimpleNamespace(
        get_frequent_artists=lambda size=50: [{"id": "a1", "name": "BoC"}],
        get_artist_info2=lambda artist_id, count=20: [
            {"id": "-1", "name": "Zmajor"}, {"id": "-1", "name": "Rushex"},
        ],
        song_exists=lambda artist, title: title in owned,
        start_scan=lambda: True,
    )

    def search_fn(name, n):
        return [{"title": f"{name} hit", "url": f"http://y/{name}"}]

    downloaded = []

    def download_fn(url):
        path = "/music/" + url.rsplit("/", 1)[-1] + ".mp3"
        downloaded.append(path)
        return (None, [path])

    state = DiscoverState(path=str(tmp_path / "state.json"), suggested=set())

    deps = SimpleNamespace(
        subsonic=subsonic,
        search_fn=search_fn,
        download_fn=download_fn,
        state=state,
        song_dir=str(tmp_path),
    )
    return deps, downloaded


def test_run_weekly_builds_playlist_and_records_state(tmp_path):
    deps, downloaded = build_deps(tmp_path)
    result = run_weekly(deps, count=2, seed_limit=5, per_seed=20, per_artist=1)

    # Two not-owned artists -> two candidates -> two downloads.
    assert len(downloaded) == 2
    assert result["acquired"] == 2
    # m3u written with both basenames.
    content = open(result["m3u"], encoding="utf-8").read()
    assert "BoC" not in content  # seed name, not a track filename
    assert content.count(".mp3") == 2
    # State now remembers them, so a second run acquires nothing new.
    deps2, downloaded2 = build_deps(tmp_path)
    deps2.state = deps.state  # carry forward in-memory state
    result2 = run_weekly(deps2, count=2, seed_limit=5, per_seed=20, per_artist=1)
    assert result2["acquired"] == 0


def test_run_weekly_respects_count_cap(tmp_path):
    deps, downloaded = build_deps(tmp_path)
    result = run_weekly(deps, count=1, seed_limit=5, per_seed=20, per_artist=1)
    assert result["acquired"] == 1
    assert len(downloaded) == 1


def test_run_weekly_triggers_scan(tmp_path):
    deps, _ = build_deps(tmp_path)
    scans = []
    deps.subsonic.start_scan = lambda: scans.append(True) or True
    run_weekly(deps, count=2, seed_limit=5, per_seed=20, per_artist=1)
    assert scans == [True]
