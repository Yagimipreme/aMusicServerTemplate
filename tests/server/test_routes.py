"""Smoke tests for Flask routes — no live network, all heavy deps mocked."""
import json
from unittest.mock import MagicMock, patch
import pytest


@pytest.fixture
def app():
    """Import app after mocking background threads so they don't start."""
    import sys, os
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if root not in sys.path:
        sys.path.insert(0, root)

    with patch("threading.Thread"):
        from sWebExt.py_server import server as srv
        # Reset global state between test runs
        srv._enrich_last_result = {"status": "idle"}
        srv._insights_last_result = {"status": "idle"}
        srv._insights_features_last_result = {"status": "idle"}
        flask_app = srv.app
        flask_app.config["TESTING"] = True
        yield flask_app


@pytest.fixture
def client(app):
    return app.test_client()


# ── App shell route ───────────────────────────────────────────────────────────

def test_get_root_returns_app_shell(client):
    """GET / returns app.html with nav-mixes and app.css."""
    resp = client.get("/")
    assert resp.status_code == 200
    assert b'id="nav-mixes"' in resp.data
    assert b'app.css' in resp.data


def test_enrich_status_returns_idle(client):
    resp = client.get("/library/enrich/status")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["status"] == "idle"


def test_post_discover_run_when_disabled(client):
    with patch("sWebExt.py_server.server._run_discover_once",
               return_value={"status": "disabled", "reason": "navidrome creds missing"}):
        resp = client.post("/discover/run")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["status"] == "disabled"


def test_post_library_dedup_report(client):
    with patch("sWebExt.py_server.server._run_dedup_once",
               return_value={"status": "ok", "duplicates": 0}):
        resp = client.post("/library/dedup/report")
    assert resp.status_code == 200


def test_post_download_dispatcher_no_url(client):
    resp = client.post("/", json={})
    # No matching script: 404
    assert resp.status_code == 404



def test_sc_preview_missing_param(client):
    resp = client.get("/sc/preview")
    assert resp.status_code == 400


def test_share_link_route(client):
    resp = client.get("/share/link?artist=Burial&title=Archangel")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert "url" in data


def test_share_parse_route(client):
    payload = "PLAYLIST:Test\nBurial|Archangel|\n"
    resp = client.post("/share/parse", json={"text": payload})
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["type"] == "playlist"
    assert len(data["tracks"]) == 1


# ── Daily discover route ──────────────────────────────────────────────────────

def test_post_discover_run_daily_disabled(client):
    with patch("sWebExt.py_server.server._run_discover_daily_once",
               return_value={"status": "disabled", "reason": "navidrome creds missing"}):
        resp = client.post("/discover/run_daily")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["status"] == "disabled"


def test_post_discover_run_daily_skipped(client):
    with patch("sWebExt.py_server.server._run_discover_daily_once",
               return_value={"status": "skipped", "reason": "lastfm not ready"}):
        resp = client.post("/discover/run_daily")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["status"] == "skipped"


# ── Insights sync routes ──────────────────────────────────────────────────────

def test_insights_sync_status_defaults_idle(client):
    resp = client.get("/insights/sync/status")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "idle"


def test_insights_sync_starts_worker(client, monkeypatch):
    import sWebExt.py_server.server as server

    called = {}

    def fake_sync(max_pages=None):
        called["ran"] = True
        called["max_pages"] = max_pages
        return {"status": "ok"}

    class _ImmediateThread:
        def __init__(self, target=None, kwargs=None, daemon=None, **_):
            self._target = target
            self._kwargs = kwargs or {}

        def start(self):
            self._target(**self._kwargs)

    monkeypatch.setattr(server, "_run_insights_sync_once", fake_sync)
    monkeypatch.setattr(server.threading, "Thread", _ImmediateThread)

    resp = client.post("/insights/sync", json={"max_pages": 2})
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "started"
    assert called.get("ran") is True
    assert called.get("max_pages") == 2


# ── Settings routes ───────────────────────────────────────────────────────────

def test_get_settings_returns_schema_and_values(client, tmp_path):
    import json as _json
    cfg = {
        "navidrome_url": "http://localhost:4533",
        "navidrome_pass": "secret123",
        "discover": {"daily": {"count": 7}},
    }
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(_json.dumps(cfg))
    with patch("sWebExt.py_server.server._CONFIG_PATH", str(cfg_file)):
        resp = client.get("/settings")
    assert resp.status_code == 200
    data = _json.loads(resp.data)
    assert "schema" in data
    assert "values" in data
    # Secrets must be masked
    assert data["values"]["navidrome_pass"]["value"] == ""
    assert data["values"]["navidrome_pass"]["set"] is True
    # Groups present
    groups = {e["group"] for e in data["schema"]}
    assert "Discovery" in groups
    assert "Credentials" in groups


def test_get_settings_secret_unset_flag(client, tmp_path):
    import json as _json
    cfg = {"navidrome_pass": ""}
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(_json.dumps(cfg))
    with patch("sWebExt.py_server.server._CONFIG_PATH", str(cfg_file)):
        resp = client.get("/settings")
    data = _json.loads(resp.data)
    assert data["values"]["navidrome_pass"]["set"] is False


def test_post_settings_unknown_key_returns_400(client):
    resp = client.post("/settings", json={"totally_unknown_key": "value"})
    assert resp.status_code == 400
    data = json.loads(resp.data)
    assert "unknown" in data


def test_settings_schema_no_dead_discover_scheduler_rows(client, tmp_path):
    """Issue 15: discover.schedule/run_day/run_hour/weekly_count/playlist_cap
    must NOT appear in SETTINGS_SCHEMA (superseded by Mixes UI)."""
    import json as _json
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(_json.dumps({}))
    with patch("sWebExt.py_server.server._CONFIG_PATH", str(cfg_file)):
        resp = client.get("/settings")
    data = _json.loads(resp.data)
    paths = {e["path"] for e in data["schema"]}
    dead_paths = {
        "discover.schedule", "discover.run_day", "discover.run_hour",
        "discover.weekly_count", "discover.playlist_cap",
    }
    found_dead = dead_paths & paths
    assert not found_dead, f"Dead settings paths still in schema: {found_dead}"


def test_post_settings_type_mismatch_returns_400(client, tmp_path):
    import json as _json
    cfg = {}
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(_json.dumps(cfg))
    with patch("sWebExt.py_server.server._CONFIG_PATH", str(cfg_file)):
        # discover.candidate_oversample is an int field in schema
        resp = client.post("/settings", json={"discover.candidate_oversample": "not-an-int"})
    assert resp.status_code == 400
    data = _json.loads(resp.data)
    assert "fields" in data


def test_post_settings_empty_secret_is_ignored(client, tmp_path):
    import json as _json
    original_pass = "my_secret"
    cfg = {"navidrome_pass": original_pass}
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(_json.dumps(cfg))
    with patch("sWebExt.py_server.server._CONFIG_PATH", str(cfg_file)):
        resp = client.post("/settings", json={"navidrome_pass": ""})
    assert resp.status_code == 200
    # Original password unchanged
    saved = _json.loads(cfg_file.read_text())
    assert saved["navidrome_pass"] == original_pass


def test_post_settings_valid_nested_path_deep_merges(client, tmp_path):
    import json as _json
    cfg = {"discover": {"suggested_ttl_days": 30, "min_artist_listeners": 1000}}
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(_json.dumps(cfg))
    with patch("sWebExt.py_server.server._CONFIG_PATH", str(cfg_file)):
        resp = client.post("/settings", json={"discover.candidate_oversample": 5})
    assert resp.status_code == 200
    saved = _json.loads(cfg_file.read_text())
    # Deep merge: other discover keys must still be present
    assert saved["discover"]["suggested_ttl_days"] == 30
    assert saved["discover"]["candidate_oversample"] == 5


def test_post_settings_atomic_write_leaves_valid_json(client, tmp_path):
    import json as _json
    cfg = {"hostname": "test.local"}
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(_json.dumps(cfg))
    with patch("sWebExt.py_server.server._CONFIG_PATH", str(cfg_file)):
        resp = client.post("/settings", json={"hostname": "new.local"})
    assert resp.status_code == 200
    # File must be valid JSON
    saved = _json.loads(cfg_file.read_text())
    assert saved["hostname"] == "new.local"


# ── Issue 3: Mutual exclusion for discover routes ─────────────────────────────

def test_discover_run_returns_409_when_busy(client):
    """POST /discover/run returns 409 when another discover run is in progress."""
    import sWebExt.py_server.server as srv
    # Hold the lock to simulate a running discover
    with srv._discover_running:
        resp = client.post("/discover/run")
    assert resp.status_code == 409
    data = json.loads(resp.data)
    assert data["status"] == "busy"
    assert "reason" in data


def test_discover_run_daily_returns_409_when_busy(client):
    """POST /discover/run_daily returns 409 when another discover run is in progress."""
    import sWebExt.py_server.server as srv
    with srv._discover_running:
        resp = client.post("/discover/run_daily")
    assert resp.status_code == 409
    data = json.loads(resp.data)
    assert data["status"] == "busy"
    assert "reason" in data


# ── Issue 5: Settings type validation — reject non-string for str/secret ──────

def test_post_settings_dict_value_for_str_returns_400(client, tmp_path):
    """POST /settings with a dict value for a str field must return 400."""
    import json as _json
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(_json.dumps({}))
    with patch("sWebExt.py_server.server._CONFIG_PATH", str(cfg_file)):
        resp = client.post("/settings", json={"song_dir": {"x": 1}})
    assert resp.status_code == 400
    data = _json.loads(resp.data)
    assert "fields" in data


def test_post_settings_null_for_str_returns_400(client, tmp_path):
    """POST /settings with null for a str field must return 400."""
    import json as _json
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(_json.dumps({}))
    with patch("sWebExt.py_server.server._CONFIG_PATH", str(cfg_file)):
        resp = client.post("/settings", json={"hostname": None})
    assert resp.status_code == 400
    data = _json.loads(resp.data)
    assert "fields" in data


def test_post_settings_bool_for_int_returns_400(client, tmp_path):
    """POST /settings with true (bool) for an int field must return 400."""
    import json as _json
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(_json.dumps({}))
    with patch("sWebExt.py_server.server._CONFIG_PATH", str(cfg_file)):
        resp = client.post("/settings", json={"discover.candidate_oversample": True})
    assert resp.status_code == 400
    data = _json.loads(resp.data)
    assert "fields" in data


# ── Issue 6: Empty secrets excluded from "updated" list ──────────────────────

def test_post_settings_empty_secret_not_in_updated(client, tmp_path):
    """POST /settings with empty secret must not appear in the 'updated' response list."""
    import json as _json
    cfg = {"navidrome_pass": "my_secret", "hostname": "test.local"}
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(_json.dumps(cfg))
    with patch("sWebExt.py_server.server._CONFIG_PATH", str(cfg_file)):
        resp = client.post("/settings", json={"navidrome_pass": "", "hostname": "new.local"})
    assert resp.status_code == 200
    data = _json.loads(resp.data)
    assert "navidrome_pass" not in data["updated"], (
        "Empty secret should not appear in updated list"
    )
    assert "hostname" in data["updated"]


# ── /mixes routes ─────────────────────────────────────────────────────────────

def _make_valid_profile(id="testmix", name="Test Mix"):
    return {
        "id": id, "name": name, "enabled": True, "auto_generated": False,
        "schedule": {"cadence": "weekly", "run_day": "sunday", "run_hour": 22},
        "count": 30, "cap": 100, "new_ratio": 1.0,
        "seeds": {"mode": "history", "genres": [], "artists": [], "playlist": ""},
        "quality": {},
    }


def test_get_mixes_returns_mixes_and_next_runs(client, tmp_path):
    """GET /mixes returns mixes list and next_runs dict."""
    import json as _json
    cfg = {"discover": {"playlist_name": "Weekly Mix", "run_day": "sunday", "run_hour": 22,
                        "weekly_count": 30, "playlist_cap": 100}}
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(_json.dumps(cfg))
    state_file = tmp_path / "discover_state.json"
    state_file.write_text(_json.dumps({"next_runs": {"weekly": "2026-06-15T22:00:00"}}))
    with patch("sWebExt.py_server.server._CONFIG_PATH", str(cfg_file)), \
         patch("sWebExt.py_server.server._PROJECT_ROOT", str(tmp_path)):
        resp = client.get("/mixes")
    assert resp.status_code == 200
    data = _json.loads(resp.data)
    assert "mixes" in data
    assert "next_runs" in data
    assert isinstance(data["mixes"], list)


def test_post_mixes_create_valid_profile(client, tmp_path):
    """POST /mixes with a new valid profile → 201 + appears in config."""
    import json as _json
    cfg = {"mixes": []}
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(_json.dumps(cfg))
    profile = _make_valid_profile(id="mynewmix", name="My New Mix")
    with patch("sWebExt.py_server.server._CONFIG_PATH", str(cfg_file)), \
         patch("sWebExt.py_server.server._mix_wake"):
        resp = client.post("/mixes", json=profile)
    assert resp.status_code == 201
    data = _json.loads(resp.data)
    assert data["status"] == "ok"
    # Check it was persisted
    saved = _json.loads(cfg_file.read_text())
    assert any(m["id"] == "mynewmix" for m in saved["mixes"])


def test_post_mixes_update_existing_clears_auto_generated(client, tmp_path):
    """POST /mixes with existing id → 200, auto_generated forced False."""
    import json as _json
    existing = _make_valid_profile(id="mymix", name="My Mix")
    existing["auto_generated"] = True
    cfg = {"mixes": [existing]}
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(_json.dumps(cfg))
    update = _make_valid_profile(id="mymix", name="My Mix Updated")
    update["auto_generated"] = True  # client sends True, server must force False
    with patch("sWebExt.py_server.server._CONFIG_PATH", str(cfg_file)), \
         patch("sWebExt.py_server.server._mix_wake"):
        resp = client.post("/mixes", json=update)
    assert resp.status_code == 200
    data = _json.loads(resp.data)
    assert data["mix"]["auto_generated"] is False


def test_post_mixes_invalid_profile_returns_400(client, tmp_path):
    """POST /mixes with invalid profile → 400 with errors dict."""
    import json as _json
    cfg = {"mixes": []}
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(_json.dumps(cfg))
    bad = {"id": "bad", "name": "Bad", "schedule": {"cadence": "bad"}, "count": 0,
           "cap": 10, "new_ratio": 2.0, "seeds": {"mode": "invalid"}, "quality": {}}
    with patch("sWebExt.py_server.server._CONFIG_PATH", str(cfg_file)):
        resp = client.post("/mixes", json=bad)
    assert resp.status_code == 400
    data = _json.loads(resp.data)
    assert "errors" in data
    assert isinstance(data["errors"], dict)


def test_delete_mixes_removes_profile(client, tmp_path):
    """DELETE /mixes/<id> removes profile and returns 200."""
    import json as _json
    existing = _make_valid_profile(id="removeme", name="Remove Me")
    cfg = {"mixes": [existing]}
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(_json.dumps(cfg))
    with patch("sWebExt.py_server.server._CONFIG_PATH", str(cfg_file)), \
         patch("sWebExt.py_server.server._mix_wake"):
        resp = client.delete("/mixes/removeme")
    assert resp.status_code == 200
    saved = _json.loads(cfg_file.read_text())
    assert not any(m["id"] == "removeme" for m in saved["mixes"])


def test_delete_mixes_unknown_id_returns_404(client, tmp_path):
    """DELETE /mixes/<id> for unknown id → 404."""
    import json as _json
    cfg = {"mixes": []}
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(_json.dumps(cfg))
    with patch("sWebExt.py_server.server._CONFIG_PATH", str(cfg_file)):
        resp = client.delete("/mixes/nonexistent")
    assert resp.status_code == 404


def test_post_mixes_run_triggers_run(client, tmp_path):
    """POST /mixes/<id>/run runs the profile and returns result."""
    import json as _json
    profile = _make_valid_profile(id="mymix", name="My Mix")
    cfg = {"mixes": [profile]}
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(_json.dumps(cfg))
    with patch("sWebExt.py_server.server._CONFIG_PATH", str(cfg_file)), \
         patch("sWebExt.py_server.server._run_profile_once",
               return_value={"profile": "mymix", "acquired": 2, "library_added": 0, "m3u": "/tmp/x.m3u"}):
        resp = client.post("/mixes/mymix/run")
    assert resp.status_code == 200
    data = _json.loads(resp.data)
    assert data["acquired"] == 2


def test_post_mixes_run_busy_returns_409(client, tmp_path):
    """POST /mixes/<id>/run returns 409 when busy."""
    import json as _json
    profile = _make_valid_profile(id="mymix", name="My Mix")
    cfg = {"mixes": [profile]}
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(_json.dumps(cfg))
    with patch("sWebExt.py_server.server._CONFIG_PATH", str(cfg_file)), \
         patch("sWebExt.py_server.server._run_profile_once",
               return_value={"status": "busy", "reason": "another discover run in progress"}):
        resp = client.post("/mixes/mymix/run")
    assert resp.status_code == 409


def test_post_mixes_run_error_returns_500(client, tmp_path):
    """POST /mixes/<id>/run returns 500 when result.status=='error' (Issue 13)."""
    import json as _json
    profile = _make_valid_profile(id="mymix", name="My Mix")
    cfg = {"mixes": [profile]}
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(_json.dumps(cfg))
    with patch("sWebExt.py_server.server._CONFIG_PATH", str(cfg_file)), \
         patch("sWebExt.py_server.server._run_profile_once",
               return_value={"status": "error", "error": "something broke"}):
        resp = client.post("/mixes/mymix/run")
    assert resp.status_code == 500, f"Expected 500 for error status, got {resp.status_code}"


def test_post_mixes_suggest_appends_new_profiles(client, tmp_path):
    """POST /mixes/suggest runs bootstrapper and returns created profiles."""
    import json as _json
    from types import SimpleNamespace
    cfg = {"mixes": [], "navidrome_url": "http://localhost", "navidrome_user": "u", "navidrome_pass": "p"}
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(_json.dumps(cfg))
    suggested_profiles = [_make_valid_profile(id="genre-techno", name="Techno Mix")]
    suggested_profiles[0]["auto_generated"] = True

    def fake_suggest(subsonic, existing, top_n=4):
        return suggested_profiles

    with patch("sWebExt.py_server.server._CONFIG_PATH", str(cfg_file)), \
         patch("sWebExt.py_server.server._mix_wake"), \
         patch("discover.profiles.suggest_genre_profiles", fake_suggest), \
         patch("sWebExt.py_server.server._build_discover_deps",
               return_value=SimpleNamespace(subsonic=SimpleNamespace(get_genres=lambda: []))):
        resp = client.post("/mixes/suggest")
    assert resp.status_code == 200
    data = _json.loads(resp.data)
    assert "created" in data


# ── legacy aliases ────────────────────────────────────────────────────────────

def test_post_discover_run_legacy_alias(client, tmp_path):
    """POST /discover/run still works and routes to weekly profile."""
    import json as _json
    cfg = {"discover": {"playlist_name": "Weekly Mix", "run_day": "sunday",
                        "run_hour": 22, "weekly_count": 30, "playlist_cap": 100}}
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(_json.dumps(cfg))
    with patch("sWebExt.py_server.server._CONFIG_PATH", str(cfg_file)), \
         patch("sWebExt.py_server.server._run_profile_once",
               return_value={"profile": "weekly", "acquired": 0, "library_added": 0, "m3u": None}):
        resp = client.post("/discover/run")
    assert resp.status_code == 200


def test_post_discover_run_daily_legacy_alias(client, tmp_path):
    """POST /discover/run_daily routes to daily profile."""
    import json as _json
    cfg = {"discover": {
        "playlist_name": "Weekly Mix", "run_day": "sunday", "run_hour": 22,
        "weekly_count": 30, "playlist_cap": 100,
        "daily": {"enabled": True, "count": 7, "run_hour": 7,
                  "window_days": 7, "playlist_name": "Daily Mix"},
    }}
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(_json.dumps(cfg))
    with patch("sWebExt.py_server.server._CONFIG_PATH", str(cfg_file)), \
         patch("sWebExt.py_server.server._run_profile_once",
               return_value={"profile": "daily", "acquired": 0, "library_added": 0, "m3u": None}):
        resp = client.post("/discover/run_daily")
    assert resp.status_code == 200


# ── Issue 1: lock reentry — route must NOT hold lock before calling _run_profile_once ─

def test_discover_run_happy_path_200_via_engine_patch(client, tmp_path):
    """POST /discover/run returns 200 on success; patches run_profile engine fn, not _run_profile_once.

    Before the fix, routes acquired _discover_running then called _run_profile_once which
    also tried to acquire the same non-reentrant lock → always returned 409 busy.
    After the fix, only _run_profile_once holds the lock; routes just call _run_discover_once.
    """
    import json as _json
    cfg = {"discover": {"playlist_name": "Weekly Mix", "run_day": "sunday",
                        "run_hour": 22, "weekly_count": 30, "playlist_cap": 100}}
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(_json.dumps(cfg))
    fake_result = {"profile": "weekly", "acquired": 3, "library_added": 0, "m3u": "/tmp/x.m3u"}
    with patch("sWebExt.py_server.server._CONFIG_PATH", str(cfg_file)), \
         patch("discover.engine.run_profile", return_value=fake_result), \
         patch("sWebExt.py_server.server._build_discover_deps") as mock_deps:
        mock_deps.return_value = __import__("types").SimpleNamespace(
            subsonic=None, search_fn=None, download_fn=None,
            state=None, song_dir="/tmp", lastfm_client=None,
        )
        resp = client.post("/discover/run")
    assert resp.status_code == 200, f"Expected 200 got {resp.status_code}: {resp.data}"
    data = _json.loads(resp.data)
    assert data.get("status") == "ok"


def test_discover_run_daily_happy_path_200_via_engine_patch(client, tmp_path):
    """POST /discover/run_daily returns 200 on success; patches run_profile engine fn."""
    import json as _json
    cfg = {"discover": {
        "playlist_name": "Weekly Mix", "run_day": "sunday", "run_hour": 22,
        "weekly_count": 30, "playlist_cap": 100,
        "daily": {"enabled": True, "count": 7, "run_hour": 7,
                  "window_days": 7, "playlist_name": "Daily Mix"},
    }}
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(_json.dumps(cfg))
    fake_result = {"profile": "daily", "acquired": 2, "library_added": 0, "m3u": "/tmp/d.m3u"}
    with patch("sWebExt.py_server.server._CONFIG_PATH", str(cfg_file)), \
         patch("discover.engine.run_profile", return_value=fake_result), \
         patch("sWebExt.py_server.server._build_discover_deps") as mock_deps:
        mock_deps.return_value = __import__("types").SimpleNamespace(
            subsonic=None, search_fn=None, download_fn=None,
            state=None, song_dir="/tmp", lastfm_client=None,
        )
        resp = client.post("/discover/run_daily")
    assert resp.status_code == 200, f"Expected 200 got {resp.status_code}: {resp.data}"
    data = _json.loads(resp.data)
    assert data.get("status") == "ok"


def test_discover_run_busy_maps_to_409_from_run_profile_once(client, tmp_path):
    """When _run_profile_once returns busy (lock held), route returns 409."""
    import json as _json
    import sWebExt.py_server.server as srv
    cfg = {"discover": {"playlist_name": "Weekly Mix", "run_day": "sunday",
                        "run_hour": 22, "weekly_count": 30, "playlist_cap": 100}}
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(_json.dumps(cfg))
    # Hold the lock so _run_profile_once gets "busy"
    with patch("sWebExt.py_server.server._CONFIG_PATH", str(cfg_file)):
        with srv._discover_running:
            resp = client.post("/discover/run")
    assert resp.status_code == 409
    data = _json.loads(resp.data)
    assert data["status"] == "busy"


# ── Issue 4: startup genre bootstrap ─────────────────────────────────────────

def test_bootstrap_genre_profiles_creates_and_persists_when_no_auto_generated(tmp_path):
    """_bootstrap_genre_profiles creates genre profiles when none auto-generated exist."""
    import json as _json
    import sWebExt.py_server.server as srv
    from types import SimpleNamespace

    cfg = {"mixes": [
        {"id": "weekly", "name": "Weekly Mix", "auto_generated": False,
         "seeds": {"mode": "history", "genres": [], "artists": [], "playlist": ""},
         "schedule": {"cadence": "weekly", "run_day": "sunday", "run_hour": 22},
         "count": 30, "cap": 100, "new_ratio": 1.0, "enabled": True, "quality": {}},
    ]}
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(_json.dumps(cfg))

    def fake_suggest(subsonic, existing_mixes, top_n=4):
        return [{"id": "genre-techno", "name": "Techno Mix", "auto_generated": True,
                 "enabled": True,
                 "schedule": {"cadence": "weekly", "run_day": "monday", "run_hour": 7},
                 "count": 15, "cap": 60, "new_ratio": 0.3,
                 "seeds": {"mode": "genre", "genres": ["techno"], "artists": [], "playlist": ""},
                 "quality": {}}]

    fake_subsonic = SimpleNamespace(get_genres=lambda: [{"name": "Techno", "songCount": 50}])

    with patch("sWebExt.py_server.server._CONFIG_PATH", str(cfg_file)), \
         patch("discover.profiles.suggest_genre_profiles", fake_suggest):
        result = srv._bootstrap_genre_profiles(fake_subsonic)

    assert result == 1, f"Expected 1 profile created, got {result}"
    saved = _json.loads(cfg_file.read_text())
    assert any(m["id"] == "genre-techno" for m in saved["mixes"])


def test_bootstrap_genre_profiles_skips_when_auto_generated_exist(tmp_path):
    """_bootstrap_genre_profiles is a no-op when auto_generated profiles already exist."""
    import json as _json
    import sWebExt.py_server.server as srv
    from types import SimpleNamespace

    cfg = {"mixes": [
        {"id": "genre-techno", "name": "Techno Mix", "auto_generated": True,
         "seeds": {"mode": "genre", "genres": ["techno"], "artists": [], "playlist": ""},
         "schedule": {"cadence": "weekly", "run_day": "monday", "run_hour": 7},
         "count": 15, "cap": 60, "new_ratio": 0.3, "enabled": True, "quality": {}},
    ]}
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(_json.dumps(cfg))

    fake_subsonic = SimpleNamespace(get_genres=lambda: [{"name": "Techno", "songCount": 50}])
    suggest_calls = []

    with patch("sWebExt.py_server.server._CONFIG_PATH", str(cfg_file)), \
         patch("discover.profiles.suggest_genre_profiles",
               side_effect=lambda *a, **kw: suggest_calls.append(1) or []):
        result = srv._bootstrap_genre_profiles(fake_subsonic)

    assert result == 0
    assert len(suggest_calls) == 0, "suggest_genre_profiles should not be called when auto-generated exist"


def test_bootstrap_genre_profiles_skips_when_no_genres(tmp_path):
    """_bootstrap_genre_profiles is a no-op when library has no genres."""
    import json as _json
    import sWebExt.py_server.server as srv
    from types import SimpleNamespace

    cfg = {"mixes": []}
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(_json.dumps(cfg))
    fake_subsonic = SimpleNamespace(get_genres=lambda: [])

    with patch("sWebExt.py_server.server._CONFIG_PATH", str(cfg_file)):
        result = srv._bootstrap_genre_profiles(fake_subsonic)

    assert result == 0


# ── Issue 5: initial-run fallback to run_mix bootstrap ───────────────────────

def test_run_discover_once_falls_back_to_run_mix_when_profile_skipped(client, tmp_path):
    """_run_discover_once falls back to run_mix (Starter Mix) when weekly profile
    returns skipped (Last.fm not ready), preserving old bootstrap behavior."""
    import json as _json
    cfg = {"discover": {"playlist_name": "Weekly Mix", "run_day": "sunday",
                        "run_hour": 22, "weekly_count": 30, "playlist_cap": 100}}
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(_json.dumps(cfg))
    run_mix_calls = []

    def fake_run_mix(deps, cfg):
        run_mix_calls.append(1)
        return {"acquired": 5, "m3u": "/tmp/starter.m3u"}

    with patch("sWebExt.py_server.server._CONFIG_PATH", str(cfg_file)), \
         patch("sWebExt.py_server.server._run_profile_once",
               return_value={"profile": "weekly", "status": "skipped",
                             "reason": "lastfm not ready"}), \
         patch("discover.engine.run_mix", fake_run_mix), \
         patch("sWebExt.py_server.server._build_discover_deps") as mock_deps:
        mock_deps.return_value = __import__("types").SimpleNamespace(
            subsonic=None, search_fn=None, download_fn=None,
            state=None, song_dir="/tmp", lastfm_client=None,
        )
        resp = client.post("/discover/run")

    assert resp.status_code == 200, f"Got {resp.status_code}: {resp.data}"
    assert len(run_mix_calls) == 1, "run_mix should be called as fallback"
    data = _json.loads(resp.data)
    assert data.get("status") == "ok"


# ── /yt/search route ─────────────────────────────────────────────────────────

def test_yt_search_missing_q_returns_400(client):
    """GET /yt/search without q → 400."""
    resp = client.get("/yt/search")
    assert resp.status_code == 400
    data = json.loads(resp.data)
    assert data["status"] == "error"
    assert "q" in data["error"]

def test_yt_search_happy_path(client):
    """GET /yt/search with q → 200 with results list."""
    fake_stdout = json.dumps({
        "entries": [
            {"title": "Test Track", "uploader": "Test Artist", "duration": 240,
             "url": "https://www.youtube.com/watch?v=abc123", "id": "abc123"},
        ]
    })
    import subprocess as _sp
    mock_result = _sp.CompletedProcess(args=[], returncode=0, stdout=fake_stdout, stderr="")
    with patch("subprocess.run", return_value=mock_result):
        resp = client.get("/yt/search?q=test")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert "results" in data
    assert len(data["results"]) == 1
    r = data["results"][0]
    assert r["source"] == "yt"
    assert r["title"] == "Test Track"
    assert r["artist"] == "Test Artist"
    assert r["duration"] == 240
    assert "youtube.com" in r["url"]

def test_yt_search_subprocess_error_returns_empty_not_500(client):
    """GET /yt/search when subprocess raises → 200 with empty results + error field."""
    with patch("subprocess.run", side_effect=Exception("yt-dlp not found")):
        resp = client.get("/yt/search?q=test")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["results"] == []


# ── /acquire route ────────────────────────────────────────────────────────────

def test_acquire_no_body_returns_400(client):
    """POST /acquire with no body → 400."""
    resp = client.post("/acquire", json={})
    assert resp.status_code == 400
    data = json.loads(resp.data)
    assert data["status"] == "error"

def test_acquire_ftp_url_returns_400(client):
    """POST /acquire with ftp:// URL → 400."""
    resp = client.post("/acquire", json={"url": "ftp://example.com/file.mp3"})
    assert resp.status_code == 400
    data = json.loads(resp.data)
    assert data["status"] == "error"

def test_acquire_unknown_host_returns_400(client):
    """POST /acquire with unknown host → 400."""
    resp = client.post("/acquire", json={"url": "https://evil.example.com/x"})
    assert resp.status_code == 400
    data = json.loads(resp.data)
    assert data["status"] == "error"

def test_acquire_happy_path(client, tmp_path):
    """POST /acquire with allowed host + mocked download → 200 ok."""
    import sWebExt.py_server.server as srv
    with patch("sWebExt.py_server.server._download_url", return_value="/music/track.mp3"):
        resp = client.post("/acquire", json={"url": "https://www.youtube.com/watch?v=abc123"})
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["status"] == "ok"
    assert data["path"] == "/music/track.mp3"

def test_acquire_inflight_lock_returns_409(client):
    """POST /acquire same URL while in-flight → 409."""
    import sWebExt.py_server.server as srv
    url = "https://www.youtube.com/watch?v=locked"
    with srv._acquire_lock:
        srv._acquire_inflight.add(url)
    try:
        resp = client.post("/acquire", json={"url": url})
        assert resp.status_code == 409
        data = json.loads(resp.data)
        assert data["status"] == "busy"
    finally:
        with srv._acquire_lock:
            srv._acquire_inflight.discard(url)


# ── /library/suffixes route ───────────────────────────────────────────────────

def test_get_suffixes_returns_list(client, tmp_path):
    """GET /library/suffixes reads title_suffixes.txt lines."""
    import json as _json
    suffix_file = tmp_path / "title_suffixes.txt"
    suffix_file.write_text("(Official Video)\n(Lyrics)\n\n  \n")
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(_json.dumps({}))
    with patch("sWebExt.py_server.server._CONFIG_PATH", str(cfg_file)), \
         patch("sWebExt.py_server.server._PROJECT_ROOT", str(tmp_path)):
        resp = client.get("/library/suffixes")
    assert resp.status_code == 200
    data = _json.loads(resp.data)
    assert data["suffixes"] == ["(Official Video)", "(Lyrics)"]


def test_get_suffixes_missing_file_returns_empty(client, tmp_path):
    """GET /library/suffixes when file missing → empty list."""
    import json as _json
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(_json.dumps({}))
    with patch("sWebExt.py_server.server._CONFIG_PATH", str(cfg_file)), \
         patch("sWebExt.py_server.server._PROJECT_ROOT", str(tmp_path)):
        resp = client.get("/library/suffixes")
    assert resp.status_code == 200
    data = _json.loads(resp.data)
    assert data["suffixes"] == []


def test_post_suffixes_writes_and_roundtrips(client, tmp_path):
    """POST /library/suffixes writes atomically and round-trips."""
    import json as _json
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(_json.dumps({}))
    with patch("sWebExt.py_server.server._CONFIG_PATH", str(cfg_file)), \
         patch("sWebExt.py_server.server._PROJECT_ROOT", str(tmp_path)):
        resp = client.post("/library/suffixes", json={"suffixes": ["(Official)", "(Live)"]})
        assert resp.status_code == 200
        # Round-trip via GET
        resp2 = client.get("/library/suffixes")
    data = _json.loads(resp2.data)
    assert data["suffixes"] == ["(Official)", "(Live)"]


def test_post_suffixes_non_list_returns_400(client, tmp_path):
    """POST /library/suffixes with non-list body → 400."""
    import json as _json
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(_json.dumps({}))
    with patch("sWebExt.py_server.server._CONFIG_PATH", str(cfg_file)), \
         patch("sWebExt.py_server.server._PROJECT_ROOT", str(tmp_path)):
        resp = client.post("/library/suffixes", json={"suffixes": "not-a-list"})
    assert resp.status_code == 400


# ── Insights analytics routes ─────────────────────────────────────────────────

def _seed_insights_db(path):
    from insights import db as idb
    conn = idb.connect(path)
    conn.executemany(
        "INSERT INTO scrobbles (ts, artist, track) VALUES (?, ?, ?)",
        [(1700000000, "A", "t1"), (1700000001, "A", "t1"), (1700000002, "B", "t2")])
    conn.execute("INSERT INTO artist_tags (artist, tags_json, primary_genre, fetched_at) "
                 "VALUES ('A', '[]', 'techno', 1)")
    conn.commit()
    conn.close()


def test_insights_overview_endpoint(client, monkeypatch, tmp_path):
    import sWebExt.py_server.server as server
    dbp = str(tmp_path / "i.db")
    _seed_insights_db(dbp)
    monkeypatch.setattr(server, "_insights_db_path", lambda: dbp)
    resp = client.get("/insights/overview?period=all&tz=0")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["total_scrobbles"] == 3
    assert body["top_genre"] == "techno"


def test_insights_temporal_endpoint(client, monkeypatch, tmp_path):
    import sWebExt.py_server.server as server
    dbp = str(tmp_path / "i.db")
    _seed_insights_db(dbp)
    monkeypatch.setattr(server, "_insights_db_path", lambda: dbp)
    resp = client.get("/insights/temporal?tz=0")
    assert resp.status_code == 200
    body = resp.get_json()
    assert len(body["clock"]["hours"]) == 24
    assert len(body["heatmap"]["matrix"]) == 7
    assert "weekday_weekend" in body and "over_time" in body


def test_insights_genres_endpoint(client, monkeypatch, tmp_path):
    import sWebExt.py_server.server as server
    dbp = str(tmp_path / "i.db")
    _seed_insights_db(dbp)
    monkeypatch.setattr(server, "_insights_db_path", lambda: dbp)
    resp = client.get("/insights/genres?tz=0")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["top"][0]["genre"] == "techno"
    assert "by_hour" in body and "evolution" in body and "diversity" in body


# ── Insights features routes ──────────────────────────────────────────────────

def _seed_features_db(path):
    from insights import db as idb
    conn = idb.connect(path)
    conn.executemany(
        "INSERT INTO scrobbles (ts, artist, track) VALUES (?, ?, ?)",
        [(1700000000, "A", "t1"), (1700000001, "A", "t1")])
    conn.execute("INSERT INTO track_features (artist, track, bpm, key, scale, mood, source, analyzed_at) "
                 "VALUES ('A','t1',128.0,'A','minor','happy','acousticbrainz',1)")
    conn.commit(); conn.close()


def test_insights_features_endpoint(client, monkeypatch, tmp_path):
    import sWebExt.py_server.server as server
    dbp = str(tmp_path / "i.db"); _seed_features_db(dbp)
    monkeypatch.setattr(server, "_insights_db_path", lambda: dbp)
    resp = client.get("/insights/features?tz=0")
    assert resp.status_code == 200
    body = resp.get_json()
    for k in ("bpm_distribution", "bpm_curve", "key_distribution",
              "mood_distribution", "mood_by_time", "coverage"):
        assert k in body
    assert body["bpm_curve"]["hours"][22] == 128.0


def test_insights_features_sync_starts_worker(client, monkeypatch):
    import sWebExt.py_server.server as server
    called = {}
    def fake(max_tracks=None):
        called["ran"] = True; called["max"] = max_tracks; return {"status": "ok"}
    class _Imm:
        def __init__(self, target=None, kwargs=None, daemon=None, **_):
            self._t = target; self._k = kwargs or {}
        def start(self): self._t(**self._k)
    monkeypatch.setattr(server, "_run_insights_features_once", fake)
    monkeypatch.setattr(server.threading, "Thread", _Imm)
    resp = client.post("/insights/features/sync", json={"max_tracks": 50})
    assert resp.status_code == 200 and resp.get_json()["status"] == "started"
    assert called.get("ran") and called.get("max") == 50


def test_insights_features_sync_status_idle(client):
    resp = client.get("/insights/features/sync/status")
    assert resp.status_code == 200
    assert resp.get_json()["status"] in ("idle", "ok", "started", "skipped", "disabled", "running")


def test_mb_recording_search_score_filter(monkeypatch):
    import sWebExt.py_server.server as server
    import urllib.request, json, io

    monkeypatch.setattr(server.time, "sleep", lambda *_: None)

    def fake_urlopen(payload):
        def _open(req, timeout=None):
            return io.BytesIO(json.dumps(payload).encode())
        return _open

    # high score → id
    monkeypatch.setattr(urllib.request, "urlopen",
                        fake_urlopen({"recordings": [{"id": "good", "score": 95}]}))
    assert server._mb_recording_search("Artist", "Track") == "good"

    # low score → None
    monkeypatch.setattr(urllib.request, "urlopen",
                        fake_urlopen({"recordings": [{"id": "weak", "score": 50}]}))
    assert server._mb_recording_search("Artist", "Track") is None

    # no recordings → None
    monkeypatch.setattr(urllib.request, "urlopen",
                        fake_urlopen({"recordings": []}))
    assert server._mb_recording_search("Artist", "Track") is None


def test_post_enrich_sets_running_immediately(client):
    # threading.Thread is patched in the `app` fixture, so the worker never runs.
    resp = client.post("/library/enrich")
    assert resp.status_code == 200
    assert json.loads(resp.data)["status"] == "running"

    status = client.get("/library/enrich/status")
    data = json.loads(status.data)
    assert data["status"] == "running"
    assert data["files_total"] == 0
    assert data["files_done"] == 0


def test_run_enrich_once_disabled_when_config_disabled():
    from sWebExt.py_server import server as srv
    srv._enrich_last_result = {"status": "idle"}
    with patch("discover.config.load_config",
               return_value={"enrich": {"enabled": False}, "song_dir": "/x"}):
        result = srv._run_enrich_once()
    assert result["status"] == "disabled"
    assert "disabled" in result["reason"]


def test_run_enrich_once_ok_result_has_ui_fields():
    from sWebExt.py_server import server as srv
    srv._enrich_last_result = {"status": "idle"}
    fake_result = {"processed": 2, "files_total": 2, "enriched": 2,
                   "per_field": {}, "skipped": 0, "errors": 0}
    with patch("discover.config.load_config",
               return_value={"enrich": {"enabled": True}, "song_dir": "/x",
                             "lastfm_api_key": "k"}), \
         patch("library.enrich.run", return_value=dict(fake_result)), \
         patch("follow.musicbrainz.MusicBrainzClient"), \
         patch("lastfm.client.LastFMClient"):
        result = srv._run_enrich_once()
    assert result["status"] == "ok"
    assert result["enriched"] == 2
    assert result["files_done"] == result["files_total"]


def test_enrich_fields_legacy_only_missing_genre():
    from sWebExt.py_server import server as srv
    fields = srv._enrich_fields({"only_missing_genre": False})
    assert fields["genre"] == {"enabled": True, "only_missing": False}
    assert fields["album"] == {"enabled": True, "only_missing": True}


def test_enrich_fields_explicit_block_passthrough():
    from sWebExt.py_server import server as srv
    block = {"fields": {"genre": {"enabled": False, "only_missing": True}}}
    assert srv._enrich_fields(block) == block["fields"]


# ── Insights discovery route ──────────────────────────────────────────────────

def _seed_discovery_db(path):
    from insights import db as idb
    conn = idb.connect(path)
    conn.executemany(
        "INSERT INTO scrobbles (ts, artist, track) VALUES (?, ?, ?)",
        [(1700000000, "A", "t1"), (1700000001, "B", "t2"), (1700000002, "B", "t2")])
    conn.execute("INSERT INTO library_tracks (artist, track) VALUES ('a', 't1')")
    conn.commit(); conn.close()


def test_insights_discovery_endpoint(client, monkeypatch, tmp_path):
    import sWebExt.py_server.server as server
    dbp = str(tmp_path / "i.db"); _seed_discovery_db(dbp)
    monkeypatch.setattr(server, "_insights_db_path", lambda: dbp)
    resp = client.get("/insights/discovery?tz=0")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "overlap" in body and "missing_favorites" in body
    assert body["overlap"]["tracks_in_library"] == 1
    assert body["missing_favorites"][0]["track"] == "t2"
    assert "discovery_rate" in body and "new_vs_repeat" in body
    assert body["new_vs_repeat"]["first"] + body["new_vs_repeat"]["repeat"] == 3
