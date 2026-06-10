"""Share codec — encode/decode track and playlist share payloads.

Single track: http://{hostname}:5000/share/import?v=1&d=<base64url(json)>
Playlist: pipe-delimited text block starting with PLAYLIST:<name>
"""
import base64
import json
import logging
import os

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_CONFIG_PATH = os.path.join(_PROJECT_ROOT, "config.json")
_DEFAULT_HOSTNAME = "amusicserver.local"
_DEFAULT_PORT = 5000


def _get_hostname() -> str:
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg.get("hostname", _DEFAULT_HOSTNAME)
    except Exception:
        return _DEFAULT_HOSTNAME


def encode_track(artist: str, title: str, url: str = None) -> str:
    """Return a share URL for a single track."""
    payload = {"type": "track", "artist": artist, "title": title}
    if url:
        payload["url"] = url
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("ascii").rstrip("=")
    hostname = _get_hostname()
    return f"http://{hostname}:{_DEFAULT_PORT}/share/import?v=1&d={encoded}"


def encode_playlist(name: str, tracks: list) -> str:
    """Return a pipe-delimited playlist text block."""
    lines = [f"PLAYLIST:{name}"]
    for t in tracks:
        artist = t.get("artist", "")
        title = t.get("title", "")
        url = t.get("url", "")
        lines.append(f"{artist}|{title}|{url}")
    return "\n".join(lines) + "\n"


def decode(text_or_url: str) -> dict:
    """Auto-detect format and decode.

    Returns: {type, name?, tracks: [{artist, title, url?}]}
    Raises ValueError for unrecognised input.
    """
    text = text_or_url.strip()

    # Single track URL
    if "?d=" in text or "&d=" in text:
        import re
        m = re.search(r"[?&]d=([A-Za-z0-9_=-]+)", text)
        if m:
            padded = m.group(1) + "=="
            try:
                payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
                return {
                    "type": "track",
                    "name": None,
                    "tracks": [{"artist": payload.get("artist", ""),
                                "title": payload.get("title", ""),
                                "url": payload.get("url", "")}],
                }
            except Exception as e:
                raise ValueError(f"Could not decode share URL payload: {e}") from e

    # Playlist text block
    if text.startswith("PLAYLIST:"):
        lines = text.splitlines()
        name = lines[0][len("PLAYLIST:"):].strip()
        tracks = []
        for line in lines[1:]:
            line = line.strip()
            if not line:
                continue
            parts = line.split("|", 2)
            if len(parts) >= 2:
                tracks.append({
                    "artist": parts[0].strip(),
                    "title": parts[1].strip(),
                    "url": parts[2].strip() if len(parts) > 2 else "",
                })
        return {"type": "playlist", "name": name, "tracks": tracks}

    raise ValueError(
        "Unrecognised share format — expected a share URL containing ?d= "
        "or a playlist text block starting with PLAYLIST:"
    )
