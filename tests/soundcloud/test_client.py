"""Tests for soundcloud/client.py — 401 refresh trigger."""
from unittest.mock import MagicMock, patch
import pytest


def _make_client(client_id="testcid", config_path="/tmp/config.json"):
    from soundcloud.client import SCClient
    return SCClient(client_id, config_path)


def test_get_injects_client_id():
    client = _make_client()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"id": 123}
    mock_resp.raise_for_status.return_value = None
    with patch("soundcloud.client.requests.Session") as mock_sess_cls:
        mock_sess = MagicMock()
        mock_sess.get.return_value = mock_resp
        mock_sess_cls.return_value = mock_sess
        from soundcloud.client import SCClient
        c = SCClient("mycid", "/tmp/cfg.json")
        result = c.get("/resolve", params={"url": "https://soundcloud.com/x"})
    call_kwargs = mock_sess.get.call_args
    assert "mycid" in str(call_kwargs)
    assert result == {"id": 123}


def test_get_401_triggers_selenium_refresh_and_retries():
    """On 401, SCClient fetches a new client_id and retries once."""
    import json, os, tempfile

    cfg = {"sc_client_id": "oldcid"}
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(cfg, f)
        cfg_path = f.name

    try:
        resp_401 = MagicMock()
        resp_401.status_code = 401
        resp_401.raise_for_status.side_effect = Exception("401")

        resp_ok = MagicMock()
        resp_ok.status_code = 200
        resp_ok.json.return_value = {"id": 999}
        resp_ok.raise_for_status.return_value = None

        with patch("soundcloud.client.requests.Session") as mock_sess_cls:
            mock_sess = MagicMock()
            mock_sess.get.side_effect = [resp_401, resp_ok]
            mock_sess_cls.return_value = mock_sess

            with patch("soundcloud.client.fetch_client_id_via_selenium", return_value="newcid"):
                from soundcloud.client import SCClient
                c = SCClient("oldcid", cfg_path)
                result = c.get("/resolve", params={"url": "https://soundcloud.com/x"})

        assert result == {"id": 999}
        # Config file must have been updated
        with open(cfg_path) as f2:
            persisted = json.load(f2)
        assert persisted["sc_client_id"] == "newcid"
    finally:
        os.unlink(cfg_path)


def test_get_404_raises():
    import requests as req
    resp_404 = MagicMock()
    resp_404.status_code = 404
    resp_404.raise_for_status.side_effect = req.HTTPError(response=resp_404)

    with patch("soundcloud.client.requests.Session") as mock_sess_cls:
        mock_sess = MagicMock()
        mock_sess.get.return_value = resp_404
        mock_sess_cls.return_value = mock_sess
        from soundcloud.client import SCClient
        c = SCClient("cid", "/tmp/cfg.json")
        with pytest.raises(req.HTTPError):
            c.get("/missing")
