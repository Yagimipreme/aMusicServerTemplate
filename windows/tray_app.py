"""
aMusicServer tray app for Windows.

Wraps server.py in a tray icon with the basic lifecycle controls users actually
need on Windows. Started by the Inno Setup installer on login (optional) and
by the Start Menu shortcut.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

# ── Repo-relative paths (PyInstaller sets sys._MEIPASS for onefile builds) ────

if getattr(sys, "frozen", False):
    APP_ROOT = Path(sys.executable).parent
else:
    APP_ROOT = Path(__file__).resolve().parent.parent

SERVER_SCRIPT  = APP_ROOT / "sWebExt" / "py_server" / "server.py"
EXTENSION_DIR  = APP_ROOT / "sWebExt"
CONFIG_PATH    = APP_ROOT / "config.json"
ICON_PATH      = APP_ROOT / "windows" / "icon.ico"
LOG_DIR        = APP_ROOT / "logs"

LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    filename=str(LOG_DIR / "tray.log"),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("tray")

# ── Lazy imports for runtime-only deps so this file still parses on Linux ────

def _require_tray_libs():
    global pystray, Image, Menu, MenuItem
    import pystray
    from PIL import Image
    from pystray import Menu, MenuItem
    return pystray, Image, Menu, MenuItem


# ── Server subprocess management ──────────────────────────────────────────────

class ServerProcess:
    def __init__(self):
        self.proc: subprocess.Popen | None = None

    def start(self):
        if self.proc and self.proc.poll() is None:
            return
        python = sys.executable if not getattr(sys, "frozen", False) else _bundled_python()
        cmd = [python, str(SERVER_SCRIPT)]
        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        logger.info("starting server: %s", cmd)
        self.proc = subprocess.Popen(
            cmd, cwd=str(APP_ROOT),
            creationflags=creationflags,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    def stop(self):
        if not self.proc:
            return
        if self.proc.poll() is not None:
            return
        logger.info("stopping server (pid=%s)", self.proc.pid)
        try:
            if sys.platform == "win32":
                self.proc.send_signal(subprocess.signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
                try:
                    self.proc.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
            else:
                self.proc.terminate()
                self.proc.wait(timeout=4)
        except Exception:
            logger.exception("error stopping server")

    @property
    def running(self) -> bool:
        return bool(self.proc and self.proc.poll() is None)


def _bundled_python() -> str:
    """Path to the bundled python inside a frozen PyInstaller build."""
    candidate = Path(sys.executable).parent / "python.exe"
    return str(candidate) if candidate.exists() else sys.executable


# ── Chrome detection (informational only — yt-dlp fallback works either way) ─

CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files\Chromium\Application\chrome.exe",
]


def chrome_installed() -> bool:
    return any(os.path.isfile(p) for p in CHROME_PATHS)


# ── "Start with Windows" via HKCU Run key (no extra deps) ─────────────────────

REG_KEY  = r"Software\Microsoft\Windows\CurrentVersion\Run"
REG_NAME = "aMusicServer"


def autostart_enabled() -> bool:
    if sys.platform != "win32":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY) as k:
            winreg.QueryValueEx(k, REG_NAME)
        return True
    except FileNotFoundError:
        return False


def set_autostart(enabled: bool):
    if sys.platform != "win32":
        return
    import winreg
    if enabled:
        exe = sys.executable if getattr(sys, "frozen", False) else f'"{sys.executable}" "{__file__}"'
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, REG_NAME, 0, winreg.REG_SZ, exe)
        logger.info("autostart enabled -> %s", exe)
    else:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY, 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, REG_NAME)
            logger.info("autostart disabled")
        except FileNotFoundError:
            pass


# ── Updater plumbing ──────────────────────────────────────────────────────────

def _update_check(force: bool, icon=None):
    """Run in a background thread; show notifications via the tray icon."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import updater  # noqa: WPS433
    except Exception:
        logger.exception("updater import failed")
        if force and icon:
            icon.notify("Updater unavailable", "aMusicServer")
        return

    rel = updater.find_latest()
    if rel is None:
        logger.info("no release info from either forge")
        if force and icon:
            icon.notify("No release info available right now.", "aMusicServer")
        return

    if not updater.is_newer(rel):
        logger.info("up to date (current=%s, latest=%s)", updater.CURRENT_VERSION, rel.tag)
        if force and icon:
            icon.notify(f"You're up to date (v{updater.CURRENT_VERSION}).", "aMusicServer")
        return

    if icon:
        icon.notify(f"Update available: {rel.tag} ({rel.forge}). Downloading…", "aMusicServer")

    path = updater.download_asset(rel)
    if not path:
        if icon:
            icon.notify("Update download failed; will retry later.", "aMusicServer")
        return

    if icon:
        icon.notify("Update downloaded. Launching installer…", "aMusicServer")
    updater.launch_installer(path)
    # The installer will close us via Inno Setup's CloseApplications


# ── Tray menu wiring ──────────────────────────────────────────────────────────

def _open_folder(path: Path):
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        webbrowser.open(f"file://{path}")


def build_icon(server: ServerProcess):
    pystray, Image, Menu, MenuItem = _require_tray_libs()

    if ICON_PATH.exists():
        image = Image.open(str(ICON_PATH))
    else:
        # Fallback to the existing favicon shipped with the extension
        fallback = APP_ROOT / "sWebExt" / "favicon_io" / "favicon.ico"
        image = Image.open(str(fallback)) if fallback.exists() else Image.new("RGB", (16, 16), "purple")

    def on_check_updates(icon, item):
        threading.Thread(target=_update_check, args=(True, icon), daemon=True).start()

    def on_toggle_autostart(icon, item):
        set_autostart(not autostart_enabled())
        icon.update_menu()

    def on_quit(icon, item):
        server.stop()
        icon.stop()

    menu = Menu(
        MenuItem(lambda item: f"Status: {'Running' if server.running else 'Stopped'}", None, enabled=False),
        MenuItem("Open music folder", lambda icon, item: _open_folder(_music_dir())),
        MenuItem("Open extension folder", lambda icon, item: _open_folder(EXTENSION_DIR)),
        Menu.SEPARATOR,
        MenuItem("Check for updates…", on_check_updates),
        MenuItem("Start with Windows", on_toggle_autostart, checked=lambda item: autostart_enabled()),
        Menu.SEPARATOR,
        MenuItem("Quit", on_quit),
    )
    return pystray.Icon("aMusicServer", image, "aMusicServer", menu)


def _music_dir() -> Path:
    """Read song_dir out of config.json, with a sensible fallback."""
    import json
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        d = cfg.get("song_dir")
        if d:
            return Path(d)
    except (OSError, json.JSONDecodeError):
        pass
    return Path.home() / "Music"


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    logger.info("=" * 60)
    logger.info("aMusicServer tray starting (APP_ROOT=%s)", APP_ROOT)

    server = ServerProcess()
    server.start()

    icon = build_icon(server)

    if sys.platform == "win32" and not chrome_installed():
        threading.Timer(
            5.0,
            lambda: icon.notify(
                "Chrome not detected. SC token auto-refresh disabled — "
                "downloads still work via yt-dlp fallback.",
                "aMusicServer",
            ),
        ).start()

    # Silent update check 30s after start, so it doesn't fight with first launch
    threading.Timer(30.0, lambda: _update_check(force=False, icon=icon)).start()

    icon.run()  # blocks until on_quit


if __name__ == "__main__":
    main()
