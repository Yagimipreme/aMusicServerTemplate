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
        srv._mix_last_results = {}
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


# ── /library/dedup/delete ─────────────────────────────────────────────────────

def test_dedup_delete_removes_listed_files(client, tmp_path):
    import sWebExt.py_server.server as srv
    song_dir = tmp_path / "music"
    song_dir.mkdir()
    f1 = song_dir / "dup1.mp3"; f1.write_bytes(b"x")
    f2 = song_dir / "dup2.mp3"; f2.write_bytes(b"x")
    with patch.object(srv, "_get_config", return_value={"song_dir": str(song_dir)}):
        resp = client.post("/library/dedup/delete",
                           json={"paths": [str(f1), str(f2)]})
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert sorted(data["deleted"]) == sorted([str(f1), str(f2)])
    assert data["errors"] == []
    assert not f1.exists() and not f2.exists()


def test_dedup_delete_rejects_path_outside_song_dir(client, tmp_path):
    import sWebExt.py_server.server as srv
    song_dir = tmp_path / "music"; song_dir.mkdir()
    outside = tmp_path / "secret.mp3"; outside.write_bytes(b"x")
    with patch.object(srv, "_get_config", return_value={"song_dir": str(song_dir)}):
        resp = client.post("/library/dedup/delete", json={"paths": [str(outside)]})
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["deleted"] == []
    assert data["errors"][0]["path"] == str(outside)
    assert "outside" in data["errors"][0]["error"]
    assert outside.exists()   # untouched


def test_dedup_delete_rejects_traversal(client, tmp_path):
    import sWebExt.py_server.server as srv
    song_dir = tmp_path / "music"; song_dir.mkdir()
    outside = tmp_path / "secret.mp3"; outside.write_bytes(b"x")
    traversal = str(song_dir / ".." / "secret.mp3")
    with patch.object(srv, "_get_config", return_value={"song_dir": str(song_dir)}):
        resp = client.post("/library/dedup/delete", json={"paths": [traversal]})
    assert json.loads(resp.data)["deleted"] == []
    assert outside.exists()


def test_dedup_delete_rejects_directory(client, tmp_path):
    import sWebExt.py_server.server as srv
    song_dir = tmp_path / "music"; song_dir.mkdir()
    sub = song_dir / "album"; sub.mkdir()
    with patch.object(srv, "_get_config", return_value={"song_dir": str(song_dir)}):
        resp = client.post("/library/dedup/delete", json={"paths": [str(sub)]})
    assert json.loads(resp.data)["deleted"] == []
    assert sub.exists()


def test_dedup_delete_requires_paths(client, tmp_path):
    import sWebExt.py_server.server as srv
    song_dir = tmp_path / "music"; song_dir.mkdir()
    with patch.object(srv, "_get_config", return_value={"song_dir": str(song_dir)}):
        resp = client.post("/library/dedup/delete", json={"paths": []})
    assert resp.status_code == 400


def test_dedup_delete_disabled_without_song_dir(client):
    import sWebExt.py_server.server as srv
    with patch.object(srv, "_get_config", return_value={}):
        resp = client.post("/library/dedup/delete", json={"paths": ["/x.mp3"]})
    assert resp.status_code == 503
    assert json.loads(resp.data)["status"] == "disabled"


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


def test_settings_schema_has_sc_password_secret_field(client, tmp_path):
    """sc_password must be exposed as a secret field under the SoundCloud Mixes group."""
    import json as _json
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(_json.dumps({}))
    with patch("sWebExt.py_server.server._CONFIG_PATH", str(cfg_file)):
        resp = client.get("/settings")
    data = _json.loads(resp.data)
    entries = {e["path"]: e for e in data["schema"]}
    assert "sc_password" in entries
    assert entries["sc_password"]["type"] == "secret"
    assert entries["sc_password"]["group"] == "SoundCloud Mixes"


def test_sc_client_factory_passes_oauth_token(tmp_path):
    import json as _json
    cfg = {"sc_client_id": "cid1", "sc_oauth_token": "OAuth tok1"}
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(_json.dumps(cfg))
    with patch("sWebExt.py_server.server._CONFIG_PATH", str(cfg_file)):
        with patch("soundcloud.client.SCClient") as mock_cls:
            from sWebExt.py_server import server as srv
            srv._get_sc_client()
    mock_cls.assert_called_once_with("cid1", str(cfg_file), oauth_token="OAuth tok1")


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


def test_post_mixes_run_starts_background_and_returns_202(client, tmp_path):
    """POST /mixes/<id>/run spawns a background run and returns 202 started."""
    import json as _json
    profile = _make_valid_profile(id="mymix", name="My Mix")
    cfg = {"mixes": [profile]}
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(_json.dumps(cfg))
    with patch("sWebExt.py_server.server._CONFIG_PATH", str(cfg_file)), \
         patch("sWebExt.py_server.server._discover_running") as lock:
        lock.locked.return_value = False
        resp = client.post("/mixes/mymix/run")
    assert resp.status_code == 202
    data = _json.loads(resp.data)
    assert data["status"] == "started"
    assert data["mix_id"] == "mymix"


def test_post_mixes_run_already_running_returns_200_running(client, tmp_path):
    """POST while a run is in flight → 200 {'status':'running'}, no new thread."""
    import json as _json
    profile = _make_valid_profile(id="mymix", name="My Mix")
    cfg = {"mixes": [profile]}
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(_json.dumps(cfg))
    with patch("sWebExt.py_server.server._CONFIG_PATH", str(cfg_file)), \
         patch("sWebExt.py_server.server._discover_running") as lock:
        lock.locked.return_value = True
        resp = client.post("/mixes/mymix/run")
    assert resp.status_code == 200
    assert _json.loads(resp.data)["status"] == "running"


def test_post_mixes_run_unknown_id_returns_404(client, tmp_path):
    import json as _json
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(_json.dumps({"mixes": []}))
    with patch("sWebExt.py_server.server._CONFIG_PATH", str(cfg_file)):
        resp = client.post("/mixes/nope/run")
    assert resp.status_code == 404


def test_mixes_status_defaults_idle(client):
    import json as _json
    resp = client.get("/mixes/whatever/status")
    assert resp.status_code == 200
    assert _json.loads(resp.data)["status"] == "idle"


def test_run_profile_once_records_status(tmp_path):
    """_run_profile_once stores its result in _mix_last_results keyed by id."""
    from sWebExt.py_server import server as srv
    srv._mix_last_results = {}
    profile = _make_valid_profile(id="mymix", name="My Mix")
    with patch("sWebExt.py_server.server._build_discover_deps", return_value=None):
        srv._run_profile_once(profile)
    assert "mymix" in srv._mix_last_results
    assert srv._mix_last_results["mymix"]["status"] in ("disabled", "ok", "error")


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


def test_yt_search_artwork_from_entry_thumbnails(client):
    """artwork_url comes from the last (largest) entry in yt-dlp's thumbnails list."""
    fake_stdout = json.dumps({
        "entries": [
            {"title": "T", "uploader": "A", "duration": 60, "id": "abc123",
             "url": "https://www.youtube.com/watch?v=abc123",
             "thumbnails": [
                 {"url": "https://i.ytimg.com/vi/abc123/default.jpg"},
                 {"url": "https://i.ytimg.com/vi/abc123/maxresdefault.jpg"},
             ]},
        ]
    })
    import subprocess as _sp
    mock_result = _sp.CompletedProcess(args=[], returncode=0, stdout=fake_stdout, stderr="")
    with patch("subprocess.run", return_value=mock_result):
        resp = client.get("/yt/search?q=test")
    r = json.loads(resp.data)["results"][0]
    assert r["artwork_url"] == "https://i.ytimg.com/vi/abc123/maxresdefault.jpg"


def test_yt_search_artwork_falls_back_to_video_id(client):
    """Entries without thumbnail data derive artwork_url from the video id."""
    fake_stdout = json.dumps({
        "entries": [
            {"title": "T", "uploader": "A", "duration": 60, "id": "abc123",
             "url": "https://www.youtube.com/watch?v=abc123"},
        ]
    })
    import subprocess as _sp
    mock_result = _sp.CompletedProcess(args=[], returncode=0, stdout=fake_stdout, stderr="")
    with patch("subprocess.run", return_value=mock_result):
        resp = client.get("/yt/search?q=test")
    r = json.loads(resp.data)["results"][0]
    assert r["artwork_url"] == "https://i.ytimg.com/vi/abc123/hqdefault.jpg"


def test_yt_search_artwork_empty_when_no_id_or_thumbs(client):
    """No thumbnails and no id → artwork_url is empty string, not a broken URL."""
    fake_stdout = json.dumps({
        "entries": [
            {"title": "T", "uploader": "A", "duration": 60,
             "url": "https://www.youtube.com/watch?v=x"},
        ]
    })
    import subprocess as _sp
    mock_result = _sp.CompletedProcess(args=[], returncode=0, stdout=fake_stdout, stderr="")
    with patch("subprocess.run", return_value=mock_result):
        resp = client.get("/yt/search?q=test")
    r = json.loads(resp.data)["results"][0]
    assert r["artwork_url"] == ""


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


# ── /sc browse routes (set tracks, user likes) ────────────────────────────────

def _fake_sc_track(title="t"):
    return {"id": 1, "title": title, "artist": "a", "stream_url": "",
            "permalink_url": "https://soundcloud.com/a/t", "artwork_url": "",
            "duration_ms": 1000, "source": "sc"}


def test_sc_set_tracks_happy_path(client, monkeypatch):
    import sWebExt.py_server.server as srv
    monkeypatch.setattr(srv, "_sc_client_ready", True)
    with patch("sWebExt.py_server.server._get_sc_client", return_value=MagicMock()), \
         patch("soundcloud.mirror.get_set_tracks", return_value=[_fake_sc_track()]) as m:
        resp = client.get("/sc/set/123/tracks")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["status"] == "ok"
    assert data["tracks"][0]["title"] == "t"
    assert m.call_args.args[1] == 123


def test_sc_set_tracks_connecting_when_not_ready(client, monkeypatch):
    import sWebExt.py_server.server as srv
    monkeypatch.setattr(srv, "_sc_client_ready", False)
    resp = client.get("/sc/set/123/tracks")
    assert resp.status_code == 200
    assert json.loads(resp.data)["status"] == "connecting"


def test_sc_set_tracks_unavailable_without_client(client, monkeypatch):
    import sWebExt.py_server.server as srv
    monkeypatch.setattr(srv, "_sc_client_ready", True)
    with patch("sWebExt.py_server.server._get_sc_client", return_value=None):
        resp = client.get("/sc/set/123/tracks")
    assert resp.status_code == 200
    assert json.loads(resp.data)["status"] == "unavailable"


def test_sc_set_tracks_upstream_error_returns_500(client, monkeypatch):
    import sWebExt.py_server.server as srv
    monkeypatch.setattr(srv, "_sc_client_ready", True)
    with patch("sWebExt.py_server.server._get_sc_client", return_value=MagicMock()), \
         patch("soundcloud.mirror.get_set_tracks", side_effect=Exception("boom")):
        resp = client.get("/sc/set/123/tracks")
    assert resp.status_code == 500
    assert json.loads(resp.data)["status"] == "error"


def test_sc_user_likes_happy_path_default_limit(client, monkeypatch):
    import sWebExt.py_server.server as srv
    monkeypatch.setattr(srv, "_sc_client_ready", True)
    with patch("sWebExt.py_server.server._get_sc_client", return_value=MagicMock()), \
         patch("soundcloud.mirror.get_user_likes", return_value=[_fake_sc_track("liked")]) as m:
        resp = client.get("/sc/user/42/likes")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["status"] == "ok"
    assert data["tracks"][0]["title"] == "liked"
    assert m.call_args.args[1] == 42
    assert m.call_args.kwargs["limit"] == 50


def test_sc_user_likes_clamps_limit(client, monkeypatch):
    import sWebExt.py_server.server as srv
    monkeypatch.setattr(srv, "_sc_client_ready", True)
    with patch("sWebExt.py_server.server._get_sc_client", return_value=MagicMock()), \
         patch("soundcloud.mirror.get_user_likes", return_value=[]) as m:
        resp = client.get("/sc/user/42/likes?limit=9999")
    assert resp.status_code == 200
    assert m.call_args.kwargs["limit"] == 200


def test_sc_user_likes_connecting_when_not_ready(client, monkeypatch):
    import sWebExt.py_server.server as srv
    monkeypatch.setattr(srv, "_sc_client_ready", False)
    resp = client.get("/sc/user/42/likes")
    assert resp.status_code == 200
    assert json.loads(resp.data)["status"] == "connecting"


# ── /genres routes (tag preview + vocab) ──────────────────────────────────────

def _cfg_file_with(tmp_path, cfg: dict):
    import json as _json
    f = tmp_path / "config.json"
    f.write_text(_json.dumps(cfg))
    return str(f)


def test_genres_preview_missing_tag_returns_400(client):
    resp = client.get("/genres/preview")
    assert resp.status_code == 400
    assert json.loads(resp.data)["status"] == "error"


def test_genres_preview_no_api_key_unavailable(client, tmp_path):
    cfg_path = _cfg_file_with(tmp_path, {})
    with patch("sWebExt.py_server.server._CONFIG_PATH", cfg_path):
        resp = client.get("/genres/preview?tag=dub")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["status"] == "unavailable"
    assert "lastfm_api_key" in data["reason"]


def test_genres_preview_happy_path(client, tmp_path, monkeypatch):
    import sWebExt.py_server.server as srv
    srv._genre_preview_cache.clear()
    cfg_path = _cfg_file_with(tmp_path, {"lastfm_api_key": "k"})
    fake = {"tag": "dub", "taggings": 1000, "reach": 500,
            "top_artists": ["King Tubby"], "similar": ["dub techno"]}
    with patch("sWebExt.py_server.server._CONFIG_PATH", cfg_path), \
         patch("lastfm.tag_preview.get_tag_preview", return_value=fake) as m, \
         patch("lastfm.client.LastFMClient"):
        resp = client.get("/genres/preview?tag=dub")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["status"] == "ok"
    assert data["taggings"] == 1000
    assert data["top_artists"] == ["King Tubby"]
    assert m.call_args.args[1] == "dub"


def test_genres_preview_cache_hit_skips_second_call(client, tmp_path):
    import sWebExt.py_server.server as srv
    srv._genre_preview_cache.clear()
    cfg_path = _cfg_file_with(tmp_path, {"lastfm_api_key": "k"})
    fake = {"tag": "dub", "taggings": 1, "reach": 1, "top_artists": [], "similar": []}
    with patch("sWebExt.py_server.server._CONFIG_PATH", cfg_path), \
         patch("lastfm.tag_preview.get_tag_preview", return_value=fake) as m, \
         patch("lastfm.client.LastFMClient"):
        client.get("/genres/preview?tag=Dub")
        resp = client.get("/genres/preview?tag=dUB")  # same tag, different case
    assert resp.status_code == 200
    assert m.call_count == 1


def test_genres_preview_upstream_error_returns_500(client, tmp_path):
    import sWebExt.py_server.server as srv
    srv._genre_preview_cache.clear()
    cfg_path = _cfg_file_with(tmp_path, {"lastfm_api_key": "k"})
    with patch("sWebExt.py_server.server._CONFIG_PATH", cfg_path), \
         patch("lastfm.tag_preview.get_tag_preview", side_effect=RuntimeError("down")), \
         patch("lastfm.client.LastFMClient"):
        resp = client.get("/genres/preview?tag=dub")
    assert resp.status_code == 500
    assert json.loads(resp.data)["status"] == "error"


def _seed_vocab_db(path):
    import json as _json
    from insights import db as idb
    conn = idb.connect(path)
    conn.execute(
        "INSERT INTO artist_tags (artist, tags_json, primary_genre, fetched_at) VALUES (?, ?, ?, ?)",
        ("Deepchord", _json.dumps([{"name": "dub techno", "weight": 100},
                                   {"name": "Ambient", "weight": 60}]), "dub techno", 1))
    conn.execute(
        "INSERT INTO artist_tags (artist, tags_json, primary_genre, fetched_at) VALUES (?, ?, ?, ?)",
        ("King Tubby", _json.dumps([{"name": "dub", "weight": 100}]), "dub", 1))
    conn.commit()
    conn.close()


def test_genres_vocab_merges_insights_and_library(client, monkeypatch, tmp_path):
    import sWebExt.py_server.server as srv
    srv._genre_vocab_cache["tags"] = None
    dbp = str(tmp_path / "i.db")
    _seed_vocab_db(dbp)
    monkeypatch.setattr(srv, "_insights_db_path", lambda: dbp)
    cfg_path = _cfg_file_with(tmp_path, {
        "navidrome_url": "http://x", "navidrome_user": "u", "navidrome_pass": "p"})
    fake_subsonic = MagicMock()
    fake_subsonic.get_genres.return_value = [
        {"name": "Techno", "songCount": 50}, {"name": "dub", "songCount": 10}]
    with patch("sWebExt.py_server.server._CONFIG_PATH", cfg_path), \
         patch("discover.subsonic.Subsonic", return_value=fake_subsonic):
        resp = client.get("/genres/vocab")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["status"] == "ok"
    # casefolded, deduped ("dub" from both sources appears once), sorted
    assert data["tags"] == ["ambient", "dub", "dub techno", "techno"]


def test_genres_vocab_insights_only_when_no_navidrome_creds(client, monkeypatch, tmp_path):
    import sWebExt.py_server.server as srv
    srv._genre_vocab_cache["tags"] = None
    dbp = str(tmp_path / "i.db")
    _seed_vocab_db(dbp)
    monkeypatch.setattr(srv, "_insights_db_path", lambda: dbp)
    cfg_path = _cfg_file_with(tmp_path, {})
    with patch("sWebExt.py_server.server._CONFIG_PATH", cfg_path):
        resp = client.get("/genres/vocab")
    data = json.loads(resp.data)
    assert data["tags"] == ["ambient", "dub", "dub techno"]


def test_genres_vocab_empty_sources_returns_empty_list(client, monkeypatch, tmp_path):
    import sWebExt.py_server.server as srv
    srv._genre_vocab_cache["tags"] = None
    monkeypatch.setattr(srv, "_insights_db_path", lambda: str(tmp_path / "missing.db"))
    cfg_path = _cfg_file_with(tmp_path, {})
    with patch("sWebExt.py_server.server._CONFIG_PATH", cfg_path):
        resp = client.get("/genres/vocab")
    data = json.loads(resp.data)
    assert data["status"] == "ok"
    assert data["tags"] == []


def test_genres_vocab_cached_second_call(client, monkeypatch, tmp_path):
    import sWebExt.py_server.server as srv
    srv._genre_vocab_cache["tags"] = None
    dbp = str(tmp_path / "i.db")
    _seed_vocab_db(dbp)
    calls = []
    monkeypatch.setattr(srv, "_insights_db_path", lambda: (calls.append(1), dbp)[1])
    cfg_path = _cfg_file_with(tmp_path, {})
    with patch("sWebExt.py_server.server._CONFIG_PATH", cfg_path):
        client.get("/genres/vocab")
        client.get("/genres/vocab")
    assert len(calls) == 1


# ── /sc/preview (progressive resolution) ──────────────────────────────────────

def _sc_client_stub(client_id="CID", get_return=None, get_side_effect=None):
    """SCClient stub — sc_preview must route through sc.get(path), which
    injects client_id and handles 401 refresh itself, never a raw
    unauthenticated requests.get() call."""
    stub = MagicMock(client_id=client_id)
    if get_side_effect is not None:
        stub.get.side_effect = get_side_effect
    else:
        stub.get.return_value = get_return if get_return is not None else {}
    return stub


def test_sc_preview_resolves_progressive_to_cdn_url(client):
    sc = _sc_client_stub(get_return={"url": "https://cf-media.sndcdn.com/x.mp3?Policy=abc"})
    with patch("sWebExt.py_server.server._get_sc_client", return_value=sc):
        resp = client.get("/sc/preview?progressive_url=https://api-v2.soundcloud.com/media/1/progressive")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["status"] == "ok"
    assert data["stream_url"] == "https://cf-media.sndcdn.com/x.mp3?Policy=abc"
    # Routed through the authenticated SC client, not a raw requests.get —
    # sc.get() injects client_id and retries on 401 itself.
    assert sc.get.call_args.args[0] == "/media/1/progressive"


def test_sc_preview_unavailable_without_progressive_url(client):
    with patch("sWebExt.py_server.server._get_sc_client", return_value=_sc_client_stub()):
        resp = client.get("/sc/preview?progressive_url=")
    assert resp.status_code == 400


def test_sc_preview_unavailable_when_sc_returns_no_url(client):
    sc = _sc_client_stub(get_return={})
    with patch("sWebExt.py_server.server._get_sc_client", return_value=sc):
        resp = client.get("/sc/preview?progressive_url=https://api-v2.soundcloud.com/media/1/progressive")
    assert resp.status_code == 200
    assert json.loads(resp.data)["status"] == "unavailable"


def test_sc_preview_unavailable_without_client(client):
    with patch("sWebExt.py_server.server._get_sc_client", return_value=None):
        resp = client.get("/sc/preview?progressive_url=https://api-v2.soundcloud.com/media/1/progressive")
    assert resp.status_code == 200
    assert json.loads(resp.data)["status"] == "unavailable"


def test_sc_preview_error_on_upstream_exception(client):
    sc = _sc_client_stub(get_side_effect=Exception("boom"))
    with patch("sWebExt.py_server.server._get_sc_client", return_value=sc):
        resp = client.get("/sc/preview?progressive_url=https://api-v2.soundcloud.com/media/1/progressive")
    assert resp.status_code == 500
    assert json.loads(resp.data)["status"] == "error"


def test_sc_preview_accepts_legacy_stream_url_param(client):
    sc = _sc_client_stub(get_return={"url": "https://cf-media.sndcdn.com/y.mp3"})
    with patch("sWebExt.py_server.server._get_sc_client", return_value=sc):
        resp = client.get("/sc/preview?stream_url=https://api-v2.soundcloud.com/media/1/progressive")
    assert json.loads(resp.data)["stream_url"] == "https://cf-media.sndcdn.com/y.mp3"


# ── /sc/preview SSRF guard ──────────────────────────────────────────────────

def test_sc_preview_rejects_non_soundcloud_host(client):
    sc = _sc_client_stub()
    with patch("sWebExt.py_server.server._get_sc_client", return_value=sc):
        resp = client.get("/sc/preview?progressive_url=http://169.254.169.254/latest/meta-data/")
    assert resp.status_code == 400
    assert sc.get.called is False


def test_sc_preview_rejects_lookalike_host_suffix_bypass(client):
    """A naive startswith()/`in` host check is bypassable by suffixing the
    real host onto an attacker-controlled domain — must be an exact hostname
    match, not a prefix/substring match."""
    sc = _sc_client_stub()
    with patch("sWebExt.py_server.server._get_sc_client", return_value=sc):
        resp = client.get("/sc/preview?progressive_url=https://api-v2.soundcloud.com.evil.com/x")
    assert resp.status_code == 400
    assert sc.get.called is False


def test_sc_preview_rejects_non_https_scheme(client):
    sc = _sc_client_stub()
    with patch("sWebExt.py_server.server._get_sc_client", return_value=sc):
        resp = client.get("/sc/preview?progressive_url=http://api-v2.soundcloud.com/media/1/progressive")
    assert resp.status_code == 400
    assert sc.get.called is False



# ── /share/import redirect ────────────────────────────────────────────────────

def test_share_import_redirects_to_library_with_payload(client):
    resp = client.get("/share/import?v=1&d=eyJhIjoxfQ")
    assert resp.status_code in (301, 302)
    assert resp.headers["Location"] == "/?share=eyJhIjoxfQ#library"


def test_share_import_without_payload_redirects_to_library(client):
    resp = client.get("/share/import")
    assert resp.status_code in (301, 302)
    assert resp.headers["Location"] == "/#library"


def test_share_import_never_redirects_to_explore(client):
    resp = client.get("/share/import?v=1&d=abc")
    assert "/explore" not in resp.headers["Location"]


def test_share_import_quotes_payload(client):
    resp = client.get("/share/import?v=1&d=aGVsbG8%3D")
    assert "explore" not in resp.headers["Location"]
    assert resp.headers["Location"].startswith("/?share=")
    assert "#library" in resp.headers["Location"]


def test_preview_route_uses_resolved_yt_dlp_binary(client):
    """/preview must invoke _YT_DLP, never a hardcoded .venv path."""
    import sWebExt.py_server.server as srv
    import subprocess as _sp
    completed = _sp.CompletedProcess(
        args=[], returncode=0,
        stdout=json.dumps({"url": "https://stream", "title": "T", "uploader": "A"}),
        stderr="")
    with patch("subprocess.run", return_value=completed) as srun:
        resp = client.get("/preview?source=yt&artist=a&title=t")
    assert resp.status_code == 200
    assert srun.call_args.args[0][0] == srv._YT_DLP
    assert ".venv/bin/yt-dlp" not in srun.call_args.args[0][0]


def test_preview_route_prefers_m4a_audio_over_webm(client):
    """YouTube's default bestaudio is WebM/Opus, which phone browsers such as
    iOS Safari cannot play in <audio>. /preview must ask yt-dlp for M4A first
    and fall back to any audio only when M4A is unavailable."""
    import subprocess as _sp
    completed = _sp.CompletedProcess(
        args=[], returncode=0,
        stdout=json.dumps({"url": "https://stream", "title": "T", "uploader": "A"}),
        stderr="")
    with patch("subprocess.run", return_value=completed) as srun:
        resp = client.get("/preview?source=yt&url=https://www.youtube.com/watch?v=x")
    assert resp.status_code == 200
    argv = srun.call_args.args[0]
    fmt = argv[argv.index("-f") + 1]
    assert fmt.split("/")[0] == "bestaudio[ext=m4a]"
    assert "bestaudio" in fmt.split("/")[1:]


# ── /follow/run dispatches in the background ──────────────────────────────────

def test_follow_run_dispatches_in_background_not_synchronously(client):
    """POST /follow/run must not block on _run_follow_once — sync_playlist's
    scan waits alone can take minutes, long enough to hit a browser/proxy
    timeout. Same fire-and-forget pattern POST /follow already uses for its
    immediate backfill kick."""
    import sWebExt.py_server.server as srv
    import time as _time

    def slow_run_once():
        _time.sleep(0.3)
        return {"status": "ok", "acquired": 1, "unavailable": 0}

    with patch.object(srv, "_run_follow_once", side_effect=slow_run_once):
        t0 = _time.monotonic()
        resp = client.post("/follow/run")
        elapsed = _time.monotonic() - t0
        _time.sleep(0.4)   # let the background thread finish before the patch exits
    assert resp.status_code == 200
    assert json.loads(resp.data)["status"] == "started"
    assert elapsed < 0.2   # returned well before slow_run_once's 0.3s sleep completed


# ── import job playlist write: get-or-create + append, never delete-and-recreate ──

def test_get_or_append_playlist_creates_when_missing():
    import sWebExt.py_server.server as srv
    sub = MagicMock()
    sub.find_playlist_id.return_value = None
    sub.create_playlist.return_value = "new-id"

    pid = srv._get_or_append_playlist(sub, "Import playlist", ["s1", "s2"])

    assert pid == "new-id"
    sub.create_playlist.assert_called_once_with("Import playlist", ["s1", "s2"])
    sub.replace_playlist.assert_not_called()
    sub.delete_playlist.assert_not_called()


def test_get_or_append_playlist_appends_to_existing_without_deleting():
    import sWebExt.py_server.server as srv
    sub = MagicMock()
    sub.find_playlist_id.return_value = "p1"
    sub.get_playlist_song_ids.return_value = ["u1", "e1"]   # user track + engine track

    pid = srv._get_or_append_playlist(sub, "Import playlist", ["s1"])

    assert pid == "p1"
    sub.delete_playlist.assert_not_called()
    sub.create_playlist.assert_not_called()
    sub.replace_playlist.assert_called_once_with("p1", ["u1", "e1", "s1"])


def test_get_or_append_playlist_dedupes_ids_already_present():
    import sWebExt.py_server.server as srv
    sub = MagicMock()
    sub.find_playlist_id.return_value = "p1"
    sub.get_playlist_song_ids.return_value = ["e1", "e2"]

    srv._get_or_append_playlist(sub, "Import playlist", ["e2", "s1"])

    sub.replace_playlist.assert_called_once_with("p1", ["e1", "e2", "s1"])


def test_import_tracks_call_site_uses_get_or_append_not_delete_and_recreate():
    """The /import/tracks job's playlist write must go through
    _get_or_append_playlist, not Subsonic.create_or_update_playlist's
    delete-and-recreate (which destroys an existing playlist's contents and
    identity, and on real Navidrome — before the songId encoding fix — even
    recreated it empty)."""
    import inspect
    import sWebExt.py_server.server as srv
    src = inspect.getsource(srv.import_tracks)
    assert "create_or_update_playlist" not in src
    assert "_get_or_append_playlist" in src
