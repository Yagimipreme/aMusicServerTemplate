"""Tests for spotify/cipher.py — TOTP computation + fetch paths."""
from unittest.mock import MagicMock, patch
import pytest


def test_compute_totp_produces_6_digit_string():
    from spotify.cipher import compute_totp
    # cipher: 26 bytes, server_time such that counter is stable
    cipher_bytes = bytes([81, 53, 45, 41, 49, 45, 105, 88, 94, 31,
                          16, 28, 51, 68, 18, 53, 5, 82, 2, 97,
                          26, 65, 16, 4, 53, 20])
    totp, version = compute_totp(cipher_bytes, 1749567000, version=61)
    assert len(totp) == 6
    assert totp.isdigit()
    assert version == 61


def test_compute_totp_uses_server_time_counter():
    """Two calls with the same 30s window should produce the same TOTP."""
    from spotify.cipher import compute_totp
    cipher_bytes = bytes([81, 53, 45, 41, 49, 45, 105, 88, 94, 31,
                          16, 28, 51, 68, 18, 53, 5, 82, 2, 97,
                          26, 65, 16, 4, 53, 20])
    t1, _ = compute_totp(cipher_bytes, 1749567000, version=61)
    t2, _ = compute_totp(cipher_bytes, 1749567029, version=61)  # same 30s window
    assert t1 == t2


def test_get_cipher_falls_back_to_github_repo():
    """When primary fetch (Spotify HTML) fails, fallback URL is tried."""
    import spotify.cipher as c
    # Clear cache
    c._cipher_cache = None
    c._CIPHER_CACHE = None

    # Patch _fetch_from_bundle to raise and _fetch_from_fallback to succeed
    with patch("spotify.cipher._fetch_from_bundle", side_effect=Exception("connect error")), \
         patch("spotify.cipher._fetch_from_fallback", return_value=(bytes([81, 53, 45, 41, 49]), 61)):
        cipher_bytes, version = c.get_cipher()
    assert version == 61
    assert len(cipher_bytes) > 0


def test_get_cipher_caches_result():
    import spotify.cipher as c
    c._CIPHER_CACHE = None  # ensure the primary cache is cleared
    c._cipher_cache = (b"\x01\x02\x03", 61)
    cipher_bytes, version = c.get_cipher()
    assert cipher_bytes == b"\x01\x02\x03"
    assert version == 61
