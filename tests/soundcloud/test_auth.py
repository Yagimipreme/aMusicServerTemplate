"""Tests for soundcloud/auth.py — headless login + OAuth token capture."""
import json
from unittest.mock import MagicMock, patch


def test_extract_oauth_token_finds_authorization_header():
    from soundcloud.auth import _extract_oauth_token
    entries = [
        {"message": json.dumps({"message": {
            "method": "Network.requestWillBeSent",
            "params": {"request": {"url": "https://api-v2.soundcloud.com/me",
                                    "headers": {"Authorization": "OAuth abc123"}}}
        }})},
    ]
    assert _extract_oauth_token(entries) == "OAuth abc123"


def test_extract_oauth_token_returns_none_when_absent():
    from soundcloud.auth import _extract_oauth_token
    entries = [
        {"message": json.dumps({"message": {
            "method": "Network.requestWillBeSent",
            "params": {"request": {"url": "https://api-v2.soundcloud.com/me", "headers": {}}}
        }})},
    ]
    assert _extract_oauth_token(entries) is None


def test_extract_oauth_token_ignores_non_api_requests():
    from soundcloud.auth import _extract_oauth_token
    entries = [
        {"message": json.dumps({"message": {
            "method": "Network.requestWillBeSent",
            "params": {"request": {"url": "https://soundcloud.com/static/app.js",
                                    "headers": {"Authorization": "OAuth should-be-ignored"}}}
        }})},
    ]
    assert _extract_oauth_token(entries) is None


def test_perform_login_success():
    from soundcloud.auth import _perform_login
    mock_driver = MagicMock()
    mock_pass_field = MagicMock()
    mock_driver.find_element.return_value = mock_pass_field
    mock_user_field = MagicMock()
    with patch("soundcloud.auth.WebDriverWait") as mock_wait_cls:
        mock_wait_cls.return_value.until.return_value = mock_user_field
        result = _perform_login(mock_driver, "user1", "pw1")
    assert result is True
    mock_user_field.send_keys.assert_called_once_with("user1")
    mock_pass_field.send_keys.assert_called_once_with("pw1")
    mock_pass_field.submit.assert_called_once()
    mock_driver.get.assert_called_once_with("https://soundcloud.com/signin")


def test_perform_login_failure_dumps_debug_html(tmp_path):
    import soundcloud.auth as auth_mod
    mock_driver = MagicMock()
    mock_driver.page_source = "<html>broken</html>"
    debug_path = tmp_path / "sc_login_debug.html"
    with patch("soundcloud.auth.WebDriverWait", side_effect=Exception("timeout")), \
         patch.object(auth_mod, "_DEBUG_HTML_PATH", str(debug_path)):
        result = auth_mod._perform_login(mock_driver, "user1", "pw1")
    assert result is False
    assert debug_path.exists()
    assert "broken" in debug_path.read_text()


def test_login_and_capture_token_returns_token_on_success():
    from soundcloud.auth import login_and_capture_token
    mock_driver = MagicMock()
    mock_driver.get_log.return_value = [
        {"message": json.dumps({"message": {
            "method": "Network.requestWillBeSent",
            "params": {"request": {"url": "https://api-v2.soundcloud.com/me",
                                    "headers": {"Authorization": "OAuth xyz789"}}}
        }})},
    ]
    with patch("soundcloud.auth._make_driver", return_value=mock_driver), \
         patch("soundcloud.auth._perform_login", return_value=True):
        token = login_and_capture_token("user1", "pw1")
    assert token == "OAuth xyz789"
    mock_driver.quit.assert_called_once()


def test_login_and_capture_token_returns_none_when_driver_unavailable():
    from soundcloud.auth import login_and_capture_token
    with patch("soundcloud.auth._make_driver", return_value=None):
        token = login_and_capture_token("user1", "pw1")
    assert token is None


def test_login_and_capture_token_returns_none_when_login_fails():
    from soundcloud.auth import login_and_capture_token
    mock_driver = MagicMock()
    with patch("soundcloud.auth._make_driver", return_value=mock_driver), \
         patch("soundcloud.auth._perform_login", return_value=False):
        token = login_and_capture_token("user1", "pw1")
    assert token is None
    mock_driver.quit.assert_called_once()
