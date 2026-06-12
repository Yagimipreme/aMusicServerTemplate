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
        flask_app = srv.app
        flask_app.config["TESTING"] = True
        yield flask_app


@pytest.fixture
def client(app):
    return app.test_client()


def test_get_root_returns_ok(client):
    resp = client.get("/")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["status"] == "ok"


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


def test_explore_returns_html(client):
    resp = client.get("/explore")
    assert resp.status_code == 200
    assert b"explore" in resp.data.lower()


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


def test_post_settings_type_mismatch_returns_400(client, tmp_path):
    import json as _json
    cfg = {}
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(_json.dumps(cfg))
    with patch("sWebExt.py_server.server._CONFIG_PATH", str(cfg_file)):
        resp = client.post("/settings", json={"discover.daily.count": "not-an-int"})
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
    cfg = {"discover": {"schedule": "weekly", "daily": {"count": 5}}}
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(_json.dumps(cfg))
    with patch("sWebExt.py_server.server._CONFIG_PATH", str(cfg_file)):
        resp = client.post("/settings", json={"discover.daily.count": 10})
    assert resp.status_code == 200
    saved = _json.loads(cfg_file.read_text())
    # Deep merge: discover.schedule must still be there
    assert saved["discover"]["schedule"] == "weekly"
    assert saved["discover"]["daily"]["count"] == 10


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
        resp = client.post("/settings", json={"discover.run_hour": True})
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
