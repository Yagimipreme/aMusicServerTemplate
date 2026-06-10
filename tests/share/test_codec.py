"""Tests for share/codec.py — encode/decode roundtrips."""
import base64
import json
import pytest


def test_encode_track_returns_url_with_d_param():
    from share.codec import encode_track
    url = encode_track("Burial", "Archangel", "https://soundcloud.com/burial/arch")
    assert "?v=1&d=" in url or "?d=" in url
    assert "share/import" in url


def test_encode_track_without_url():
    from share.codec import encode_track
    url = encode_track("Burial", "Archangel")
    assert "share/import" in url
    # Decode and verify no url field (or url is None/empty)
    d_part = url.split("d=")[1]
    payload = json.loads(base64.urlsafe_b64decode(d_part + "==").decode())
    assert payload["artist"] == "Burial"
    assert payload["title"] == "Archangel"
    assert payload.get("url") in (None, "")


def test_encode_decode_single_track_roundtrip():
    from share.codec import encode_track, decode
    share_url = encode_track("Aphex Twin", "Windowlicker", "https://yt.com/watch?v=abc")
    result = decode(share_url)
    assert result["type"] == "track"
    assert result["tracks"][0]["artist"] == "Aphex Twin"
    assert result["tracks"][0]["title"] == "Windowlicker"
    assert result["tracks"][0]["url"] == "https://yt.com/watch?v=abc"


def test_encode_decode_playlist_roundtrip():
    from share.codec import encode_playlist, decode
    tracks = [
        {"artist": "Burial", "title": "Archangel", "url": "https://sc.com/burial/arch"},
        {"artist": "Coil", "title": "The Anal Staircase"},
    ]
    text = encode_playlist("Chill Evenings", tracks)
    assert text.startswith("PLAYLIST:Chill Evenings\n")
    assert "Burial|Archangel|https://sc.com/burial/arch" in text
    assert "Coil|The Anal Staircase|" in text

    result = decode(text)
    assert result["type"] == "playlist"
    assert result["name"] == "Chill Evenings"
    assert len(result["tracks"]) == 2
    assert result["tracks"][0]["artist"] == "Burial"
    assert result["tracks"][1]["artist"] == "Coil"
    assert result["tracks"][1].get("url", "") == ""


def test_decode_ignores_blank_lines():
    from share.codec import decode
    text = "PLAYLIST:Test\n\nBurial|Archangel|\n\nAphex Twin|Windowlicker|url\n"
    result = decode(text)
    assert len(result["tracks"]) == 2


def test_decode_raises_on_unrecognised_input():
    from share.codec import decode
    with pytest.raises(ValueError):
        decode("random text that is neither a URL nor a playlist block")


def test_decode_full_share_url():
    from share.codec import encode_track, decode
    import urllib.parse
    url = encode_track("Burial", "Archangel")
    # Extract just the ?v=1&d=... query part
    result = decode(url)
    assert result["type"] == "track"


def test_hostname_used_in_share_url(monkeypatch):
    """encode_track must read hostname from config."""
    import share.codec as codec
    monkeypatch.setattr(codec, "_get_hostname", lambda: "myserver.local")
    url = encode_track("A", "T")
    assert "myserver.local" in url


# Import encode_track at module level for monkeypatch test above
from share.codec import encode_track
