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
