#!/usr/bin/env python3
"""Interactive setup wizard for aMusicServer."""
import getpass
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

_ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(_ROOT, "config.json")
CONFIG_EXAMPLE_PATH = os.path.join(_ROOT, "config.example.json")
SWEBEXT_PATH = os.path.join(_ROOT, "sWebExt")


def _load_config():
    for path in (CONFIG_PATH, CONFIG_EXAMPLE_PATH):
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    return json.load(f)
            except json.JSONDecodeError as exc:
                if path == CONFIG_PATH:
                    print(f"Warning: {path} is not valid JSON ({exc}); ignoring it.")
            except Exception:
                pass
    return {}


def _save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def _ask(prompt, default="", secret=False):
    hint = (f" [hidden]" if secret and default else f" [{default}]") if default else ""
    full = f"{prompt}{hint}: "
    try:
        val = getpass.getpass(full) if secret else input(full).strip()
        return val if val else default
    except (KeyboardInterrupt, EOFError):
        print("\nSetup cancelled.")
        sys.exit(0)


def _ask_yn(prompt, default=False):
    hint = "(Y/n)" if default else "(y/N)"
    val = _ask(f"{prompt} {hint}").lower()
    return (val in ("y", "yes")) if val else default


def _ping_navidrome(url, user, pw):
    """Return True if Navidrome /rest/ping responds with status=ok."""
    params = urllib.parse.urlencode({
        "u": user, "p": pw, "v": "1.16.1", "c": "amusicserver-setup", "f": "json"
    })
    try:
        with urllib.request.urlopen(f"{url.rstrip('/')}/rest/ping.view?{params}", timeout=6) as r:
            return json.loads(r.read()).get("subsonic-response", {}).get("status") == "ok"
    except Exception:
        return False


def _open_folder(path):
    import platform
    try:
        plat = platform.system()
        if plat == "Windows":
            os.startfile(path)
        elif plat == "Darwin":
            subprocess.run(["open", path], check=False)
        else:
            subprocess.run(["xdg-open", path], check=False)
    except Exception:
        pass


def _write_hosts_entry(hostname: str):
    """Write 127.0.0.1 {hostname} to the system hosts file if not already present."""
    import platform
    plat = platform.system()
    if plat == "Windows":
        hosts_path = r"C:\Windows\System32\drivers\etc\hosts"
    else:
        hosts_path = "/etc/hosts"

    try:
        with open(hosts_path, "r", encoding="utf-8") as f:
            content = f.read()
        if hostname in content:
            print(f"  hosts file already contains {hostname!r} — skipping.")
            return
    except Exception as e:
        print(f"  Could not read hosts file ({hosts_path}): {e}")
        print(f"  Add this line manually:  127.0.0.1  {hostname}")
        return

    entry = f"\n127.0.0.1  {hostname}\n"
    try:
        with open(hosts_path, "a", encoding="utf-8") as f:
            f.write(entry)
        print(f"  Added to {hosts_path}: 127.0.0.1  {hostname}")
    except PermissionError:
        print(f"  Permission denied writing to {hosts_path}.")
        if plat != "Windows":
            # Try with sudo
            try:
                import subprocess
                subprocess.run(
                    ["sudo", "sh", "-c", f'echo "{entry}" >> {hosts_path}'],
                    check=True,
                )
                print(f"  Added via sudo: 127.0.0.1  {hostname}")
            except Exception:
                print(f"  Could not write with sudo. Add manually:  127.0.0.1  {hostname}")
        else:
            print(f"  Run setup as Administrator, or add manually:  127.0.0.1  {hostname}")
    except Exception as e:
        print(f"  Could not write hosts file: {e}")
        print(f"  Add manually:  127.0.0.1  {hostname}")


def main():
    print("\n=== aMusicServer Setup Wizard ===\n")
    cfg = _load_config()
    changed = []

    # ── Step 1: Song directory ────────────────────────────────────────────────
    print("Step 1/10 — Song directory (where your music files will be saved)")
    while True:
        song_dir = _ask("Music folder path", cfg.get("song_dir", ""))
        if not song_dir:
            print("  Required — cannot be empty.")
            continue
        song_dir = os.path.expanduser(song_dir)
        if not os.path.exists(song_dir):
            if _ask_yn(f"  '{song_dir}' doesn't exist. Create it?", default=True):
                os.makedirs(song_dir, exist_ok=True)
                print(f"  Created {song_dir}")
                break
            continue
        else:
            break
    if cfg.get("song_dir") != song_dir:
        changed.append("song_dir")
    cfg["song_dir"] = song_dir

    # ── Step 2: Navidrome URL ─────────────────────────────────────────────────
    print("\nStep 2/10 — Navidrome URL (e.g. http://localhost:4533)")
    while True:
        nurl = _ask("Navidrome URL", cfg.get("navidrome_url", "http://localhost:4533"))
        nurl = nurl.rstrip("/")
        try:
            urllib.request.urlopen(nurl, timeout=5)
            break
        except urllib.error.HTTPError:
            break  # Got HTTP response — Navidrome is there
        except Exception as e:
            print(f"  Could not reach '{nurl}': {e}")
            if not _ask_yn("  Try a different URL?", default=True):
                break
    if cfg.get("navidrome_url") != nurl:
        changed.append("navidrome_url")
    cfg["navidrome_url"] = nurl

    # ── Step 3: Navidrome credentials ────────────────────────────────────────
    print("\nStep 3/10 — Navidrome credentials")
    while True:
        nuser = _ask("Username", cfg.get("navidrome_user", ""))
        npw = _ask("Password", cfg.get("navidrome_pass", ""), secret=True)
        if _ping_navidrome(nurl, nuser, npw):
            print("  Navidrome connected.")
            break
        print("  Authentication failed — check username and password.")
        if not _ask_yn("  Try again?", default=True):
            break
    if cfg.get("navidrome_user") != nuser:
        changed.append("navidrome_user")
    if cfg.get("navidrome_pass") != npw:
        changed.append("navidrome_pass")
    cfg["navidrome_user"] = nuser
    cfg["navidrome_pass"] = npw

    # ── Step 4: SoundCloud account (optional) ────────────────────────────────
    print("\nStep 4/10 — SoundCloud account (enables one-click SC downloads; press Enter to skip)")
    sc_user = _ask("SoundCloud username", cfg.get("sc_username", ""))
    if cfg.get("sc_username") != sc_user:
        changed.append("sc_username")
    cfg["sc_username"] = sc_user

    # ── Step 5: Spotify playlists folder (optional) ──────────────────────────
    print("\nStep 5/10 — Spotify playlists folder (drop Exportify CSV files here; press Enter to skip)")
    print("  Export at https://exportify.app then copy the .csv files to this folder.")
    sp_dir = _ask("Spotify playlists folder", cfg.get("spotify_playlists_dir", ""))
    if sp_dir:
        sp_dir = os.path.expanduser(sp_dir)
    if cfg.get("spotify_playlists_dir") != sp_dir:
        changed.append("spotify_playlists_dir")
    cfg["spotify_playlists_dir"] = sp_dir

    # ── Step 6: Last.fm API key + secret (optional) ──────────────────────────
    print("\nStep 6/10 — Last.fm API key + secret (enables artist metadata, similar artists, Weekly Mix)")
    print("  Get a free API key at https://www.last.fm/api/account/create")
    lfm_key = _ask("Last.fm API key", cfg.get("lastfm_api_key", ""))
    lfm_sec = _ask("Last.fm API secret", cfg.get("lastfm_api_secret", ""), secret=True)
    if cfg.get("lastfm_api_key") != lfm_key:
        changed.append("lastfm_api_key")
    if cfg.get("lastfm_api_secret") != lfm_sec:
        changed.append("lastfm_api_secret")
    cfg["lastfm_api_key"] = lfm_key
    cfg["lastfm_api_secret"] = lfm_sec

    if lfm_key:
        print("\nStep 6b/10 — Last.fm username (enables Weekly Mix seeds and playlist mix from scrobble history)")
        print("  This is your Last.fm username (not email). Leave blank to skip scrobble-based features.")
        lfm_user = _ask("Last.fm username", cfg.get("lastfm_username", ""))
        if cfg.get("lastfm_username") != lfm_user:
            changed.append("lastfm_username")
        cfg["lastfm_username"] = lfm_user
    else:
        cfg.setdefault("lastfm_username", cfg.get("lastfm_username", ""))

    if lfm_key:
        print("\n  Add these lines to your navidrome.toml and restart Navidrome:")
        print(f"\n  [LastFM]")
        print(f"  ApiKey = \"{lfm_key}\"")
        print(f"  Secret = \"{lfm_sec}\"")
        print()
        print("  Common navidrome.toml locations:")
        print("    Linux:   /var/lib/navidrome/navidrome.toml")
        print("             ~/.config/navidrome/navidrome.toml")
        print("    Windows: %APPDATA%\\navidrome\\navidrome.toml")

    # ── Step 7: Last.fm scrobbling (only if API key set) ─────────────────────
    if lfm_key:
        print("\nStep 7/10 — Last.fm scrobbling (tracks your listening history on Last.fm)")
        print("  Authorized per-user in Navidrome's web interface.")
        if _ask_yn("  Open Navidrome settings in browser now?", default=True):
            webbrowser.open(f"{nurl}/settings")
        print("  In Navidrome: Log in → Personal → Last.fm → Connect your account.")
        _ask("  Press Enter when done (or to skip)")
    else:
        print("\nStep 7/10 — Last.fm scrobbling (skipped — no API key provided)")

    # ── Step 8: Browser extension ─────────────────────────────────────────────
    print("\nStep 8/10 — Browser extension (one-click song saving from YouTube / SoundCloud)")
    print("  Firefox:  about:debugging → This Firefox → Load Temporary Add-on → pick any file in sWebExt/")
    print("  Chrome/Edge/Brave:  <browser>://extensions → Developer mode → Load unpacked → sWebExt/")
    if _ask_yn("  Open the sWebExt folder now?", default=True):
        _open_folder(SWEBEXT_PATH)
    _ask("  Press Enter when done (or to skip)")

    # ── Step 9: Dedup scanner ─────────────────────────────────────────────────
    print("\nStep 9/10 — Duplicate scanner (finds songs that exist more than once in your library)")
    print("  Starts in dry-run mode. To enable actual deletion, set auto_delete: true in config.json")
    print("  or use: curl -X POST http://localhost:5000/library/dedup/report  (to preview first)")
    dedup_cfg = cfg.get("dedup") or {}
    dedup_enabled = _ask_yn("  Enable background duplicate scanner?", default=dedup_cfg.get("enabled", False))
    if dedup_enabled:
        interval_str = _ask("  Check interval in hours", str(dedup_cfg.get("interval_hours", 24)))
        try:
            interval_hours = max(1, int(interval_str))
        except ValueError:
            interval_hours = 24
        new_dedup = {"enabled": True, "interval_hours": interval_hours, "auto_delete": dedup_cfg.get("auto_delete", False)}
    else:
        new_dedup = {"enabled": False, "interval_hours": dedup_cfg.get("interval_hours", 24), "auto_delete": False}
    if cfg.get("dedup") != new_dedup:
        changed.append("dedup")
    cfg["dedup"] = new_dedup

    # ── Step 10: Title cleanup ────────────────────────────────────────────────
    print("\nStep 10/11 — Title cleanup (strips 'Official Music Video', 'HD', etc. from song titles)")
    print("  Runs automatically after every download. Add your own suffixes to title_suffixes.txt.")
    tc_cfg = cfg.get("title_cleanup") or {}
    tc_enabled = _ask_yn("  Enable title cleanup?", default=tc_cfg.get("enabled", True))
    new_tc = {"enabled": tc_enabled, "extra_suffixes_file": tc_cfg.get("extra_suffixes_file", "title_suffixes.txt")}
    if cfg.get("title_cleanup") != new_tc:
        changed.append("title_cleanup")
    cfg["title_cleanup"] = new_tc

    # ── Step 11: Hostname + hosts file ───────────────────────────────────────
    print("\nStep 11/11 — Share hostname (used in share links so any receiver can open them)")
    print("  Default: amusicserver.local — works if you keep it consistent across devices.")
    hostname = _ask("Hostname for share links", cfg.get("hostname", "amusicserver.local"))
    hostname = hostname.strip().lower() or "amusicserver.local"
    if cfg.get("hostname") != hostname:
        changed.append("hostname")
    cfg["hostname"] = hostname

    # Write hosts file entry
    _write_hosts_entry(hostname)

    # ── Save ──────────────────────────────────────────────────────────────────
    _save_config(cfg)
    print(f"\n  Config saved to {CONFIG_PATH}")
    if changed:
        print(f"  Changed fields: {', '.join(changed)}")
    else:
        print("  No changes made.")
    print("\nSetup complete. Start the server:")
    print("  Linux/Mac:  ./start.sh")
    print("  Windows:    use the tray app\n")


if __name__ == "__main__":
    main()
