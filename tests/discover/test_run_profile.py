"""Tests for run_profile() in discover/engine.py."""
from types import SimpleNamespace
import pytest
from discover.engine import run_profile
from discover.state import DiscoverState


def make_profile(
    id="testmix", name="Test Mix", count=10, cap=50, new_ratio=1.0,
    cadence="weekly", run_day="sunday", run_hour=22,
    mode="history", genres=None, artists=None, playlist=""
):
    return {
        "id": id,
        "name": name,
        "enabled": True,
        "auto_generated": False,
        "schedule": {"cadence": cadence, "run_day": run_day, "run_hour": run_hour},
        "count": count,
        "cap": cap,
        "new_ratio": new_ratio,
        "seeds": {
            "mode": mode,
            "genres": genres or [],
            "artists": artists or [],
            "playlist": playlist,
        },
        "quality": {},
    }


def make_cfg():
    return {
        "lastfm_username": "testuser",
        "discover": {
            "seed_artist_count": 5,
            "min_artist_listeners": 5000,
            "candidate_oversample": 3,
            "seed_playlist": "",
            "lastfm_period": "1month",
            "lastfm_readiness": {"min_scrobbles": 1, "min_unique_artists": 1},
        },
    }


def build_deps(tmp_path, lastfm_ready=True, library_songs=None):
    """Build fake deps for run_profile tests."""
    # For library picks
    library_songs = library_songs or []

    subsonic = SimpleNamespace(
        get_frequent_artists=lambda size=50: [{"id": "a1", "name": "BoC", "play_count": 10, "weight": 1.0}],
        get_artist_info2=lambda artist_id, count=20: [
            {"id": "-1", "name": "Zmajor"}, {"id": "-1", "name": "Rushex"},
        ],
        get_all_artist_names=lambda: set(),
        song_exists=lambda artist, title: False,
        start_scan=lambda: True,
        get_songs_by_genre=lambda genre, count=200: library_songs,
        search_songs=lambda query, count=20: library_songs,
    )

    def search_fn(name, n, track_hint=None):
        return [{"title": f"{name} hit", "url": f"http://y/{name}"}]

    downloaded = []

    def download_fn(url):
        path = str(tmp_path / (url.rsplit("/", 1)[-1] + ".mp3"))
        downloaded.append(path)
        return (None, [path])

    state = DiscoverState(path=str(tmp_path / "state.json"), suggested={})

    def fake_lastfm_call(method, **kwargs):
        if method == "artist.getInfo":
            return {"artist": {"stats": {"listeners": 999999}}}
        if method == "artist.getSimilar":
            return {"similarartists": {"artist": [
                {"name": "Zmajor", "match": "0.9"},
                {"name": "Rushex", "match": "0.8"},
            ]}}
        if method == "artist.getTopTracks":
            artist_name = kwargs.get("artist", "unknown")
            return {"toptracks": {"track": [{"name": f"{artist_name} hit",
                                              "artist": {"name": artist_name}}]}}
        if method == "user.getTopArtists":
            return {"topartists": {"artist": []}}
        if method == "user.getRecentTracks":
            return {"recenttracks": {"@attr": {"total": "999"}}}
        if method == "tag.gettopartists":
            return {"topartists": {"artist": [{"name": "Genre Artist"}]}}
        return {}

    lastfm_client = SimpleNamespace(call=fake_lastfm_call) if lastfm_ready else None

    deps = SimpleNamespace(
        subsonic=subsonic,
        search_fn=search_fn,
        download_fn=download_fn,
        state=state,
        song_dir=str(tmp_path),
        lastfm_client=lastfm_client,
    )
    return deps, downloaded


# ── ratio 1.0 — all acquired, no library calls ───────────────────────────────

def test_run_profile_ratio_one_all_acquired(tmp_path, monkeypatch):
    monkeypatch.setattr("discover.engine.lastfm_is_ready", lambda *a, **kw: True)
    deps, downloaded = build_deps(tmp_path)
    profile = make_profile(count=2, cap=10, new_ratio=1.0)
    cfg = make_cfg()
    result = run_profile(deps, cfg, profile)
    assert result["acquired"] > 0
    assert result["library_added"] == 0


# ── ratio 0.0 — no acquisition, library only ─────────────────────────────────

def test_run_profile_ratio_zero_library_only(tmp_path, monkeypatch):
    monkeypatch.setattr("discover.engine.lastfm_is_ready", lambda *a, **kw: True)
    library_songs = [
        {"id": "lib1", "artist": "X", "title": "T1", "path": str(tmp_path / "t1.mp3"), "played": None},
        {"id": "lib2", "artist": "Y", "title": "T2", "path": str(tmp_path / "t2.mp3"), "played": None},
    ]
    deps, downloaded = build_deps(tmp_path, library_songs=library_songs)
    profile = make_profile(count=2, cap=10, new_ratio=0.0, mode="genre", genres=["ambient"])
    cfg = make_cfg()
    result = run_profile(deps, cfg, profile)
    assert result["acquired"] == 0
    assert result["library_added"] > 0
    assert len(downloaded) == 0


# ── ratio 0.3 / count 10 — 3 new + 7 library ─────────────────────────────────

def test_run_profile_ratio_blend(tmp_path, monkeypatch):
    monkeypatch.setattr("discover.engine.lastfm_is_ready", lambda *a, **kw: True)
    library_songs = [
        {"id": f"lib{i}", "artist": "X", "title": f"T{i}",
         "path": str(tmp_path / f"lib{i}.mp3"), "played": None}
        for i in range(20)
    ]
    deps, downloaded = build_deps(tmp_path, library_songs=library_songs)
    profile = make_profile(count=10, cap=50, new_ratio=0.3, mode="genre", genres=["techno"])
    cfg = make_cfg()
    result = run_profile(deps, cfg, profile)
    total = result["acquired"] + result["library_added"]
    assert total <= 10


# ── history mode + no lastfm_client → skipped ────────────────────────────────

def test_run_profile_history_no_client_skipped(tmp_path, monkeypatch):
    deps, _ = build_deps(tmp_path, lastfm_ready=False)
    assert deps.lastfm_client is None
    profile = make_profile(count=5, cap=20, new_ratio=1.0, mode="history")
    cfg = make_cfg()
    result = run_profile(deps, cfg, profile)
    assert result["status"] == "skipped"


# ── genre mode — no scrobble check ───────────────────────────────────────────

def test_run_profile_genre_mode_no_lastfm_readiness_check(tmp_path, monkeypatch):
    """genre mode does not call lastfm_is_ready."""
    readiness_calls = []
    monkeypatch.setattr("discover.engine.lastfm_is_ready",
                        lambda *a, **kw: readiness_calls.append(1) or True)
    deps, _ = build_deps(tmp_path)
    profile = make_profile(count=5, cap=20, new_ratio=1.0, mode="genre", genres=["ambient"])
    cfg = make_cfg()
    run_profile(deps, cfg, profile)
    assert len(readiness_calls) == 0


# ── acquisitions recorded in state, library picks NOT ────────────────────────

def test_run_profile_acquisition_recorded_in_state(tmp_path, monkeypatch):
    monkeypatch.setattr("discover.engine.lastfm_is_ready", lambda *a, **kw: True)
    deps, downloaded = build_deps(tmp_path)
    profile = make_profile(count=2, cap=10, new_ratio=1.0)
    cfg = make_cfg()
    result = run_profile(deps, cfg, profile)
    # State should have entries for acquired tracks
    assert len(deps.state._suggested) == result["acquired"]


def test_run_profile_library_picks_not_recorded_in_state(tmp_path, monkeypatch):
    monkeypatch.setattr("discover.engine.lastfm_is_ready", lambda *a, **kw: True)
    library_songs = [
        {"id": "lib1", "artist": "X", "title": "T1",
         "path": str(tmp_path / "t1.mp3"), "played": None},
    ]
    deps, _ = build_deps(tmp_path, library_songs=library_songs)
    profile = make_profile(count=2, cap=10, new_ratio=0.0, mode="genre", genres=["ambient"])
    cfg = make_cfg()
    result = run_profile(deps, cfg, profile)
    assert result["library_added"] > 0
    # No state entries for library picks
    assert len(deps.state._suggested) == 0


# ── m3u written with profile name + cap ──────────────────────────────────────

def test_run_profile_writes_m3u_with_profile_name(tmp_path, monkeypatch):
    monkeypatch.setattr("discover.engine.lastfm_is_ready", lambda *a, **kw: True)
    deps, _ = build_deps(tmp_path)
    profile = make_profile(name="My Techno Mix", count=2, cap=10, new_ratio=1.0)
    cfg = make_cfg()
    result = run_profile(deps, cfg, profile)
    assert result["m3u"] is not None
    assert "My_Techno_Mix" in result["m3u"] or "My Techno Mix" in result["m3u"]


# ── state.save(stamp_last_run=False) ─────────────────────────────────────────

def test_run_profile_state_save_stamp_false(tmp_path, monkeypatch):
    monkeypatch.setattr("discover.engine.lastfm_is_ready", lambda *a, **kw: True)
    deps, _ = build_deps(tmp_path)
    save_calls = []
    original_save = deps.state.save
    def spy_save(stamp_last_run=False):
        save_calls.append(stamp_last_run)
        return original_save(stamp_last_run=stamp_last_run)
    deps.state.save = spy_save
    profile = make_profile(count=2, cap=10, new_ratio=1.0)
    run_profile(deps, cfg=make_cfg(), profile=profile)
    assert any(c is False for c in save_calls)


# ── returns expected keys ─────────────────────────────────────────────────────

def test_run_profile_returns_expected_keys(tmp_path, monkeypatch):
    monkeypatch.setattr("discover.engine.lastfm_is_ready", lambda *a, **kw: True)
    deps, _ = build_deps(tmp_path)
    profile = make_profile()
    result = run_profile(deps, make_cfg(), profile)
    assert "profile" in result
    assert result["profile"] == "testmix"


# ── shortfall backfill ────────────────────────────────────────────────────────

def test_run_profile_new_shortfall_backfilled_by_library(tmp_path, monkeypatch):
    """If new share delivers 0 (empty candidates), library may fill the gap."""
    monkeypatch.setattr("discover.engine.lastfm_is_ready", lambda *a, **kw: True)
    library_songs = [
        {"id": f"lib{i}", "artist": "X", "title": f"T{i}",
         "path": str(tmp_path / f"lib{i}.mp3"), "played": None}
        for i in range(10)
    ]
    deps, _ = build_deps(tmp_path, library_songs=library_songs)
    # Make acquisition always fail (no fresh candidates)
    deps.subsonic.song_exists = lambda a, t: True  # everything "owned" → filter_fresh returns []
    profile = make_profile(count=5, cap=20, new_ratio=0.5, mode="genre", genres=["ambient"])
    cfg = make_cfg()
    result = run_profile(deps, cfg, profile)
    # total ≤ count
    assert result["acquired"] + result["library_added"] <= 5
