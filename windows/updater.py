"""
Auto-update for aMusicServer on Windows.

Polls both forges for a newer release tag than the running version. If found,
downloads the Inno Setup installer asset and runs it — Inno Setup with a
stable AppId handles the in-place upgrade.

Used in two modes from tray_app.py:
  - silent (on launch): notification only if an update is found
  - force (manual): always show a result dialog
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
import urllib.request
import urllib.error
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    from version import __version__ as CURRENT_VERSION
except ImportError:
    CURRENT_VERSION = "0.0.0"

CODEBERG_REPO = "Lycka/musicServerTemplate"
GITHUB_REPO   = "Yagimipreme/aMusicServerTemplate"

CODEBERG_API = f"https://codeberg.org/api/v1/repos/{CODEBERG_REPO}/releases/latest"
GITHUB_API   = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

ASSET_PREFIX = "Setup_aMusicServer_"
ASSET_SUFFIX = ".exe"

USER_AGENT = f"aMusicServer-Updater/{CURRENT_VERSION}"

logger = logging.getLogger("updater")


@dataclass
class Release:
    forge: str               # "codeberg" or "github"
    tag: str                 # raw tag e.g. "v1.2.3"
    version: tuple[int, ...] # (1, 2, 3)
    asset_url: Optional[str] # download URL of the .exe asset, if any


def _parse_version(tag: str) -> tuple[int, ...]:
    """Strip leading 'v' and parse 'x.y.z' into a comparable tuple."""
    t = tag.lstrip("vV").split("-", 1)[0]
    parts = []
    for p in t.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def _http_get_json(url: str, timeout: int = 10) -> Optional[dict]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as e:
        logger.warning("update check failed for %s: %s", url, e)
        return None


def _pick_asset(release_json: dict) -> Optional[str]:
    for a in release_json.get("assets", []) or []:
        name = a.get("name", "")
        if name.startswith(ASSET_PREFIX) and name.endswith(ASSET_SUFFIX):
            # Codeberg uses "browser_download_url"; GitHub uses the same.
            return a.get("browser_download_url") or a.get("url")
    return None


def _fetch_release(forge: str, api_url: str) -> Optional[Release]:
    data = _http_get_json(api_url)
    if not data:
        return None
    tag = data.get("tag_name") or data.get("name") or ""
    if not tag:
        return None
    return Release(
        forge=forge,
        tag=tag,
        version=_parse_version(tag),
        asset_url=_pick_asset(data),
    )


def find_latest() -> Optional[Release]:
    """Query both forges; return the newer release that has a downloadable asset."""
    candidates: list[Release] = []
    cb = _fetch_release("codeberg", CODEBERG_API)
    if cb:
        candidates.append(cb)
    gh = _fetch_release("github", GITHUB_API)
    if gh:
        candidates.append(gh)
    if not candidates:
        return None
    candidates.sort(key=lambda r: r.version, reverse=True)
    return next((r for r in candidates if r.asset_url), candidates[0])


def is_newer(release: Release) -> bool:
    return release.version > _parse_version(CURRENT_VERSION)


def download_asset(release: Release) -> Optional[Path]:
    if not release.asset_url:
        return None
    target = Path(tempfile.gettempdir()) / f"{ASSET_PREFIX}{release.tag}{ASSET_SUFFIX}"
    logger.info("Downloading update from %s -> %s", release.asset_url, target)
    try:
        req = urllib.request.Request(release.asset_url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=60) as resp, open(target, "wb") as f:
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                f.write(chunk)
    except (urllib.error.URLError, OSError) as e:
        logger.error("download failed: %s", e)
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    return target


def launch_installer(path: Path) -> None:
    """
    Run the downloaded installer and exit the running app.
    Inno Setup honors /SILENT-ish flags; we leave it interactive so the user
    sees what's happening.
    """
    if sys.platform != "win32":
        logger.info("not on Windows, would launch: %s", path)
        return
    # Detach so the current process can exit before the installer touches files
    DETACHED = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    subprocess.Popen(
        [str(path)],
        creationflags=DETACHED | CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )
    # Caller is responsible for actually exiting the app cleanly
