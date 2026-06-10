"""Tests for lastfm/client.py — error codes, rate limit, timeout."""

import time
from unittest.mock import MagicMock, patch

import pytest
import requests

from lastfm.client import (
    LastFMClient,
    LastFMError,
    LastFMInvalidKey,
    LastFMNotFound,
    LastFMRateLimitExceeded,
    LastFMServiceOffline,
    LastFMTimeout,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_client(json_response=None, raise_exc=None):
    """Return a LastFMClient with a mocked session."""
    session = MagicMock()
    resp = MagicMock()
    if raise_exc is not None:
        session.get.side_effect = raise_exc
    else:
        resp.json.return_value = json_response or {}
        resp.raise_for_status.return_value = None
        session.get.return_value = resp
    return LastFMClient(api_key="testkey", session=session)


# ── Error code handling ────────────────────────────────────────────────────────

def test_successful_call_returns_data():
    client = _make_client({"artist": {"name": "Aphex Twin"}})
    with patch("lastfm.client._acquire_token"):
        result = client.call("artist.getInfo", artist="Aphex Twin")
    assert result == {"artist": {"name": "Aphex Twin"}}


def test_error_code_6_raises_not_found():
    client = _make_client({"error": 6, "message": "Artist not found"})
    with patch("lastfm.client._acquire_token"):
        with pytest.raises(LastFMNotFound) as exc_info:
            client.call("artist.getInfo", artist="Unknown")
    assert exc_info.value.code == 6


def test_error_code_10_raises_invalid_key():
    client = _make_client({"error": 10, "message": "Invalid API key"})
    with patch("lastfm.client._acquire_token"):
        with pytest.raises(LastFMInvalidKey) as exc_info:
            client.call("artist.getInfo", artist="test")
    assert exc_info.value.code == 10


def test_error_code_11_retries_once_then_raises(monkeypatch):
    call_count = 0

    def fake_raw_call(params):
        nonlocal call_count
        call_count += 1
        return {"error": 11, "message": "Service offline"}

    client = _make_client()
    monkeypatch.setattr(client, "_raw_call", fake_raw_call)
    monkeypatch.setattr("lastfm.client.time.sleep", lambda s: None)

    with pytest.raises(LastFMServiceOffline):
        client.call("artist.getInfo", artist="test")
    assert call_count == 2


def test_error_code_29_retries_once_then_raises(monkeypatch):
    call_count = 0

    def fake_raw_call(params):
        nonlocal call_count
        call_count += 1
        return {"error": 29, "message": "Rate limit exceeded"}

    client = _make_client()
    monkeypatch.setattr(client, "_raw_call", fake_raw_call)
    monkeypatch.setattr("lastfm.client.time.sleep", lambda s: None)

    with pytest.raises(LastFMRateLimitExceeded):
        client.call("artist.getInfo", artist="test")
    assert call_count == 2


def test_error_code_11_succeeds_on_retry(monkeypatch):
    call_count = 0

    def fake_raw_call(params):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {"error": 11, "message": "offline"}
        return {"artist": {"name": "OK"}}

    client = _make_client()
    monkeypatch.setattr(client, "_raw_call", fake_raw_call)
    monkeypatch.setattr("lastfm.client.time.sleep", lambda s: None)

    result = client.call("artist.getInfo", artist="test")
    assert result == {"artist": {"name": "OK"}}
    assert call_count == 2


def test_unknown_error_code_raises_base_error():
    client = _make_client({"error": 99, "message": "unknown"})
    with patch("lastfm.client._acquire_token"):
        with pytest.raises(LastFMError) as exc_info:
            client.call("artist.getInfo", artist="test")
    assert exc_info.value.code == 99


# ── Network timeout handling ───────────────────────────────────────────────────

def test_timeout_exception_raises_lastfm_timeout():
    client = _make_client(raise_exc=requests.exceptions.Timeout())
    with patch("lastfm.client._acquire_token"):
        with pytest.raises(LastFMTimeout):
            client.call("artist.getInfo", artist="test")


def test_connection_error_raises_lastfm_timeout():
    client = _make_client(raise_exc=requests.exceptions.ConnectionError("refused"))
    with patch("lastfm.client._acquire_token"):
        with pytest.raises(LastFMTimeout):
            client.call("artist.getInfo", artist="test")


# ── Rate limit token bucket ────────────────────────────────────────────────────

def test_acquire_token_does_not_block_immediately():
    """First token should be available immediately without sleeping."""
    import lastfm.client as lc
    # Reset bucket
    lc._bucket_tokens = 1.0
    lc._bucket_last = time.monotonic()
    slept = []
    with patch("lastfm.client.time.sleep", side_effect=slept.append):
        lc._acquire_token()
    assert slept == []


def test_acquire_token_sleeps_when_bucket_empty():
    """When token bucket is empty, _acquire_token must sleep."""
    import lastfm.client as lc
    lc._bucket_tokens = 0.0
    lc._bucket_last = time.monotonic()
    slept = []
    with patch("lastfm.client.time.sleep", side_effect=slept.append):
        lc._acquire_token()
    assert len(slept) == 1
    assert slept[0] > 0
