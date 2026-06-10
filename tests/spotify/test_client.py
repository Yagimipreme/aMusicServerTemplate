"""Tests for spotify/client.py — three-layer refresh cascade + 429 backoff."""
from unittest.mock import MagicMock, patch, call
import pytest


def _make_client():
    with patch("spotify.client.cipher"), patch("spotify.client.hashes"):
        from spotify.client import SpotifyClient
        c = SpotifyClient()
        c._token = "tok123"
        c._token_expiry = float("inf")
        return c


def test_graphql_returns_data_on_200():
    client = _make_client()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"data": {"artist": "ok"}}

    with patch("spotify.client.hashes.get_hash", return_value="a" * 64), \
         patch("spotify.client.requests.Session") as mock_sess_cls:
        mock_sess = MagicMock()
        mock_sess.post.return_value = mock_resp
        mock_sess_cls.return_value = mock_sess
        from spotify.client import SpotifyClient
        c = SpotifyClient.__new__(SpotifyClient)
        c._token = "tok"
        c._token_expiry = float("inf")
        c._session = mock_sess
        result = c.graphql("queryArtistOverview", {"uri": "spotify:artist:123"})
    assert result == {"data": {"artist": "ok"}}


def test_graphql_401_remints_token_and_retries():
    from spotify.client import SpotifyClient
    c = SpotifyClient.__new__(SpotifyClient)
    c._token = "tok"
    c._token_expiry = float("inf")

    resp_401 = MagicMock()
    resp_401.status_code = 401
    resp_401.json.return_value = {}

    resp_ok = MagicMock()
    resp_ok.status_code = 200
    resp_ok.json.return_value = {"data": {}}

    c._session = MagicMock()
    c._session.post.side_effect = [resp_401, resp_ok]
    mint_calls = []

    def fake_mint():
        mint_calls.append(1)
        c._token = "newtoken"
        c._token_expiry = float("inf")

    c._mint_token = fake_mint

    with patch("spotify.client.hashes.get_hash", return_value="a" * 64):
        result = c.graphql("queryArtistOverview", {"uri": "spotify:artist:123"})

    assert len(mint_calls) == 1
    assert result == {"data": {}}


def test_graphql_persisted_query_not_found_refreshes_hashes():
    from spotify.client import SpotifyClient
    c = SpotifyClient.__new__(SpotifyClient)
    c._token_expiry = float("inf")
    c._token = "tok"

    resp_pqnf = MagicMock()
    resp_pqnf.status_code = 200
    resp_pqnf.json.return_value = {"errors": [{"message": "PersistedQueryNotFound"}]}

    resp_ok = MagicMock()
    resp_ok.status_code = 200
    resp_ok.json.return_value = {"data": {"result": "fresh"}}

    c._session = MagicMock()
    c._session.post.side_effect = [resp_pqnf, resp_ok]

    refresh_calls = []
    mint_calls = []

    def fake_refresh():
        refresh_calls.append(1)

    def fake_mint():
        mint_calls.append(1)
        c._token = "newtok"
        c._token_expiry = float("inf")

    c._mint_token = fake_mint

    with patch("spotify.client.hashes.get_hash", return_value="a" * 64), \
         patch("spotify.client.hashes.refresh", side_effect=fake_refresh):
        result = c.graphql("queryArtistOverview", {"uri": "x"})

    assert len(refresh_calls) == 1
    assert len(mint_calls) == 1
    assert result == {"data": {"result": "fresh"}}


def test_graphql_429_sleeps_and_retries(monkeypatch):
    from spotify.client import SpotifyClient
    c = SpotifyClient.__new__(SpotifyClient)
    c._token = "tok"
    c._token_expiry = float("inf")

    resp_429 = MagicMock()
    resp_429.status_code = 429
    resp_429.json.return_value = {}

    resp_ok = MagicMock()
    resp_ok.status_code = 200
    resp_ok.json.return_value = {"data": {}}

    c._session = MagicMock()
    c._session.post.side_effect = [resp_429, resp_ok]
    c._mint_token = lambda: None

    slept = []
    monkeypatch.setattr("spotify.client.time.sleep", lambda s: slept.append(s))

    with patch("spotify.client.hashes.get_hash", return_value="a" * 64):
        result = c.graphql("queryArtistOverview", {"uri": "x"})

    assert slept and slept[0] >= 30
    assert result == {"data": {}}
