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


def trigger_navidrome_rescan(navidrome_url, user, password, poll_interval=3):
    """Trigger a Navidrome library rescan and poll until complete. Returns final song count."""
    import time
    base = navidrome_url.rstrip("/")
    params_base = urllib.parse.urlencode({
        "u": user, "p": password, "v": "1.16.1", "c": "amusicserver-setup", "f": "json"
    })
    # Start scan
    try:
        urllib.request.urlopen(f"{base}/rest/startScan.view?{params_base}", timeout=10)
    except Exception as e:
        print(f"  Could not start Navidrome scan: {e}")
        return 0
    # Poll until done
    while True:
        try:
            with urllib.request.urlopen(f"{base}/rest/getScanStatus.view?{params_base}", timeout=10) as r:
                data = json.loads(r.read())
            status = data.get("subsonic-response", {}).get("scanStatus", {})
            count = status.get("count", 0)
            scanning = status.get("scanning", False)
            print(f"\r  Scanning... ({count} songs indexed)", end="", flush=True)
            if not scanning:
                print()
                return count
        except Exception:
            pass
        time.sleep(poll_interval)


def _import_sc_likes(sc_user, cfg, nurl, nuser, npw):
    import sys
    print("  Fetching SoundCloud client credentials... (may take ~30s)")
    try:
        sc_script = os.path.join(_ROOT, "scripts/Sc2Sp_src/script_web.py")
        import importlib.util
        spec = importlib.util.spec_from_file_location("sc_web", sc_script)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        client_id = mod.fetch_client_id_via_selenium()
        cfg["sc_client_id"] = client_id
        print(f"  ✓ Got client_id")
    except Exception as e:
        print(f"  Could not get SC client_id: {e}")
        print("  Skipping SC likes import.")
        return

    try:
        sys.path.insert(0, _ROOT)
        from soundcloud.client import SoundCloudClient
        from soundcloud.mirror import get_user_likes, resolve
        sc = SoundCloudClient(client_id)
        profile_url = f"https://soundcloud.com/{sc_user}"
        entity = resolve(sc, profile_url)
        user_id = entity.get("id")
        if not user_id:
            print("  Could not resolve SC user. Skipping.")
            return
        likes = get_user_likes(sc, user_id, limit=500)
        print(f"  Found {len(likes)} liked tracks.")
    except Exception as e:
        print(f"  Error fetching likes: {e}")
        return

    song_dir = cfg.get("song_dir", "")
    logs_dir = os.path.join(_ROOT, "logs")
    os.makedirs(logs_dir, exist_ok=True)
    failed_log = os.path.join(logs_dir, "soundcloud_likes_failed.txt")
    failed = []

    dl_script = os.path.join(_ROOT, "scripts/Sc2Sp_src/script_web.py")
    import importlib.util
    spec2 = importlib.util.spec_from_file_location("sc_web2", dl_script)
    mod2 = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(mod2)

    for i, track in enumerate(likes):
        artist = track.get("artist", "")
        title = track.get("title", "")
        url = track.get("permalink_url", "")
        print(f"\r  [{i+1}/{len(likes)}] {artist} — {title}", end="", flush=True)
        try:
            mp3 = mod2.download_single_soundcloud(url, song_dir) if hasattr(mod2, "download_single_soundcloud") else None
            if not mp3:
                raise Exception("no output")
        except Exception:
            failed.append(f"{artist} — {title}")
    print()

    if failed:
        with open(failed_log, "w", encoding="utf-8") as f:
            f.write("\n".join(failed) + "\n")
        print(f"  Done: {len(likes)-len(failed)} downloaded, {len(failed)} not found.")
        print(f"  Failed tracks saved to: {failed_log}")
    else:
        print(f"  Done: {len(likes)} downloaded.")

    print("  Triggering Navidrome rescan...")
    count = trigger_navidrome_rescan(nurl, nuser, npw)
    print(f"  ✓ Rescan complete. Navidrome now has {count} songs.")


def _import_spotify_csvs(playlist_info, cfg, nurl, nuser, npw):
    import importlib.util
    import re
    song_dir = cfg.get("song_dir", "")
    logs_dir = os.path.join(_ROOT, "logs")
    os.makedirs(logs_dir, exist_ok=True)
    dl_script = os.path.join(_ROOT, "scripts/sTownload/app.py")

    spec = importlib.util.spec_from_file_location("sTownload_app", dl_script)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as e:
        print(f"  Could not load download script: {e}. Skipping Spotify import.")
        return

    total_done = total_failed = 0
    for name, csv_path, count in playlist_info:
        print(f"\n  Downloading {name} ({count} tracks)...")
        safe_name = re.sub(r"[^\w\-]", "_", name)
        failed_log = os.path.join(logs_dir, f"{safe_name}_failed.txt")
        try:
            result = mod.download_csv(csv_path, song_dir) if hasattr(mod, "download_csv") else {"done": 0, "failed": []}
            done = result.get("done", 0) if isinstance(result, dict) else 0
            failed = result.get("failed", []) if isinstance(result, dict) else []
        except Exception as e:
            print(f"  Error: {e}")
            done, failed = 0, []

        if failed:
            with open(failed_log, "w", encoding="utf-8") as f:
                f.write("\n".join(failed) + "\n")
            print(f"  Done: {done} downloaded, {len(failed)} not found → {failed_log}")
        else:
            print(f"  Done: {done} downloaded.")
        total_done += done
        total_failed += len(failed)

    print(f"\n  All playlists complete: {total_done}/{total_done+total_failed} downloaded.")
    if total_failed:
        print(f"  Failed tracks: see logs/ directory.")
    print("  Triggering Navidrome rescan...")
    count = trigger_navidrome_rescan(nurl, nuser, npw)
    print(f"  ✓ Rescan complete. Navidrome now has {count} songs.")


def _setup_autostart():
    import platform
    plat = platform.system()
    if plat == "Windows":
        try:
            startup = os.path.join(
                os.environ.get("APPDATA", ""),
                "Microsoft", "Windows", "Start Menu", "Programs", "Startup"
            )
            bat = os.path.join(startup, "aMusicServer.bat")
            exe = os.path.join(_ROOT, "aMusicServer.exe")
            with open(bat, "w") as f:
                f.write(f'@echo off\nstart "" "{exe}"\n')
            print(f"  ✓ Startup shortcut written to: {bat}")
        except Exception as e:
            print(f"  Could not write startup shortcut: {e}")
    else:
        try:
            unit_dir = os.path.expanduser("~/.config/systemd/user")
            os.makedirs(unit_dir, exist_ok=True)
            venv_python = os.path.join(_ROOT, ".venv", "bin", "python")
            if not os.path.exists(venv_python):
                venv_python = sys.executable
            server_path = os.path.join(_ROOT, "sWebExt", "py_server", "server.py")
            unit_path = os.path.join(unit_dir, "amusicserver.service")
            unit_content = f"""[Unit]
Description=aMusicServer

[Service]
ExecStart={venv_python} {server_path}
WorkingDirectory={_ROOT}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""
            with open(unit_path, "w") as f:
                f.write(unit_content)
            print(f"  ✓ Systemd unit written to: {unit_path}")
            try:
                subprocess.run(
                    ["systemctl", "--user", "enable", "--now", "amusicserver"],
                    check=True, capture_output=True
                )
                print("  ✓ Service enabled and started.")
            except Exception:
                print("  Run manually to enable:")
                print("    systemctl --user enable --now amusicserver")
        except Exception as e:
            print(f"  Could not set up autostart: {e}")


def _trigger_starter_mix_background(cfg):
    """Spawn a background thread to generate the Starter Mix immediately after setup."""
    import threading
    def _run():
        try:
            import sys
            sys.path.insert(0, _ROOT)
            from discover.engine import run_mix_from_config
            run_mix_from_config(_ROOT)
        except Exception as e:
            pass  # non-fatal; server will retry on next scheduled run
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    print("\nSetup complete! Starting your first Starter Mix in the background...")
    print("(It will appear in Navidrome in a few minutes.)")


def main():
    print("\n=== aMusicServer Setup Wizard ===\n")
    cfg = _load_config()
    changed = []

    # ── Step 1: Song directory ────────────────────────────────────────────────
    print("Step 1/14 — Song directory (where your music files will be saved)")
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
    print("\nStep 2/14 — Navidrome URL (e.g. http://localhost:4533)")
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
    print("\nStep 3/14 — Navidrome credentials")
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
    print("\nStep 4/14 — SoundCloud account (enables one-click SC downloads; press Enter to skip)")
    sc_user = _ask("SoundCloud username", cfg.get("sc_username", ""))
    if cfg.get("sc_username") != sc_user:
        changed.append("sc_username")
    cfg["sc_username"] = sc_user

    if sc_user:
        if _ask_yn("  Import your SoundCloud likes now?", default=False):
            _import_sc_likes(sc_user, cfg, nurl, nuser, npw)

    # ── Step 5: Spotify playlists folder (optional) ──────────────────────────
    print("\nStep 5/14 — Spotify playlist import (optional — press Enter to skip)")
    sp_dir = _ask("Spotify playlists folder", cfg.get("spotify_playlists_dir", ""))
    if sp_dir:
        sp_dir = os.path.expanduser(sp_dir)
        if cfg.get("spotify_playlists_dir") != sp_dir:
            changed.append("spotify_playlists_dir")
        cfg["spotify_playlists_dir"] = sp_dir

        print()
        print("  To export your Spotify playlists, go to:")
        print("    https://exportify.net")
        print("  Export each playlist as CSV and save to the folder above.")
        if _ask_yn("  Open Exportify in browser now?", default=True):
            webbrowser.open("https://exportify.net")
        _ask("  Press Enter when your CSV files are ready")

        import glob as _glob
        csvs = sorted(_glob.glob(os.path.join(sp_dir, "*.csv")))
        if not csvs:
            print("  No CSV files found in that folder. Skipping download.")
        else:
            # Count tracks per CSV
            playlist_info = []
            total_tracks = 0
            for csv_path in csvs:
                name = os.path.splitext(os.path.basename(csv_path))[0]
                try:
                    with open(csv_path, encoding="utf-8") as f:
                        lines = [l for l in f.read().splitlines() if l.strip()]
                    count = max(0, len(lines) - 1)  # minus header
                except Exception:
                    count = 0
                playlist_info.append((name, csv_path, count))
                total_tracks += count

            print(f"\n  Found {len(csvs)} playlist(s):")
            for name, _, count in playlist_info:
                print(f"    • {name}.csv  ({count} tracks)")

            if _ask_yn(f"\n  Proceed with download? ({total_tracks} tracks total via yt-dlp)", default=False):
                _import_spotify_csvs(playlist_info, cfg, nurl, nuser, npw)
    else:
        cfg.setdefault("spotify_playlists_dir", cfg.get("spotify_playlists_dir", ""))

    # ── Step 6: Last.fm API key + secret (optional) ──────────────────────────
    print("\nStep 6/14 — Last.fm API key + secret (enables artist metadata, similar artists, Weekly Mix)")
    if _ask_yn("  Open Last.fm API registration in browser?", default=True):
        webbrowser.open("https://www.last.fm/api/account/create")
    print("  After registering, paste your API key and secret below.")
    lfm_key = _ask("Last.fm API key", cfg.get("lastfm_api_key", ""))
    lfm_sec = _ask("Last.fm API secret", cfg.get("lastfm_api_secret", ""), secret=True)
    if cfg.get("lastfm_api_key") != lfm_key:
        changed.append("lastfm_api_key")
    if cfg.get("lastfm_api_secret") != lfm_sec:
        changed.append("lastfm_api_secret")
    cfg["lastfm_api_key"] = lfm_key
    cfg["lastfm_api_secret"] = lfm_sec

    if lfm_key:
        print("\nStep 6b/14 — Last.fm username (enables Weekly Mix seeds and playlist mix from scrobble history)")
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
        print("\nStep 7/14 — Last.fm scrobbling (tracks your listening history on Last.fm)")
        print("  Authorized per-user in Navidrome's web interface.")
        if _ask_yn("  Open Navidrome settings in browser now?", default=True):
            webbrowser.open(f"{nurl}/settings")
        print("  In Navidrome: Log in → Personal → Last.fm → Connect your account.")
        _ask("  Press Enter when done (or to skip)")
    else:
        print("\nStep 7/14 — Last.fm scrobbling (skipped — no API key provided)")

    # ── Step 8: Browser extension ─────────────────────────────────────────────
    print("\nStep 8/14 — Browser extension (one-click song saving from YouTube / SoundCloud)")
    print("  Firefox:  about:debugging → This Firefox → Load Temporary Add-on → pick any file in sWebExt/")
    print("  Chrome/Edge/Brave:  <browser>://extensions → Developer mode → Load unpacked → sWebExt/")
    if _ask_yn("  Open the sWebExt folder now?", default=True):
        _open_folder(SWEBEXT_PATH)
    _ask("  Press Enter when done (or to skip)")

    # ── Step 9: Dedup scanner ─────────────────────────────────────────────────
    print("\nStep 9/14 — Duplicate scanner (finds songs that exist more than once in your library)")
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
    print("\nStep 10/14 — Title cleanup (strips 'Official Music Video', 'HD', etc. from song titles)")
    print("  Runs automatically after every download. Add your own suffixes to title_suffixes.txt.")
    tc_cfg = cfg.get("title_cleanup") or {}
    tc_enabled = _ask_yn("  Enable title cleanup?", default=tc_cfg.get("enabled", True))
    new_tc = {"enabled": tc_enabled, "extra_suffixes_file": tc_cfg.get("extra_suffixes_file", "title_suffixes.txt")}
    if cfg.get("title_cleanup") != new_tc:
        changed.append("title_cleanup")
    cfg["title_cleanup"] = new_tc

    # ── Step 11: Hostname + hosts file ───────────────────────────────────────
    print("\nStep 11/14 — Share hostname (used in share links so any receiver can open them)")
    print("  Default: amusicserver.local — works if you keep it consistent across devices.")
    hostname = _ask("Hostname for share links", cfg.get("hostname", "amusicserver.local"))
    hostname = hostname.strip().lower() or "amusicserver.local"
    if cfg.get("hostname") != hostname:
        changed.append("hostname")
    cfg["hostname"] = hostname

    # Write hosts file entry
    _write_hosts_entry(hostname)

    # ── Step 12: Mix schedule ─────────────────────────────────────────────────
    print("\nStep 12/14 — Mix schedule (when should your Weekly/Daily Mix be generated?)")
    disc = cfg.get("discover") or {}
    print("  [1] Weekly (default)  [2] Daily")
    sched_choice = _ask("  Choice", "1")
    schedule = "daily" if sched_choice.strip() == "2" else "weekly"
    if schedule == "weekly":
        run_day = _ask("  Day (sun/mon/tue/wed/thu/fri/sat)", disc.get("run_day", "sunday"))
        run_day = run_day.strip().lower() or "sunday"
    else:
        run_day = disc.get("run_day", "sunday")
    run_hour_str = _ask("  Hour (0–23, local time)", str(disc.get("run_hour", 22)))
    try:
        run_hour = max(0, min(23, int(run_hour_str)))
    except ValueError:
        run_hour = 22
    disc["schedule"] = schedule
    disc["run_day"] = run_day
    disc["run_hour"] = run_hour
    cfg["discover"] = disc

    # ── Step 13: Favorite artists for Starter Mix (optional) ─────────────────
    print("\nStep 13/14 — Favorite artists for Starter Mix (optional, press Enter to skip)")
    print("  Up to 5 artists to weight your first generated mix.")
    seeds_str = _ask("  Artists (comma-separated)", ", ".join(disc.get("manual_seeds", [])))
    if seeds_str.strip():
        manual_seeds = [s.strip() for s in seeds_str.split(",") if s.strip()][:5]
        disc["manual_seeds"] = manual_seeds
        print(f"  ✓ Saved {len(manual_seeds)} seed artist(s).")
    else:
        disc.setdefault("manual_seeds", [])
    cfg["discover"] = disc

    # ── Step 14: Server autostart ─────────────────────────────────────────────
    print("\nStep 14/14 — Server autostart (start aMusicServer automatically on login?)")
    if _ask_yn("  Enable autostart?", default=False):
        _setup_autostart()

    # ── Save ──────────────────────────────────────────────────────────────────
    _save_config(cfg)
    print(f"\n  Config saved to {CONFIG_PATH}")
    if changed:
        print(f"  Changed fields: {', '.join(changed)}")
    else:
        print("  No changes made.")

    # Trigger Starter Mix in background if Navidrome has songs
    _trigger_starter_mix_background(cfg)

    hostname_display = cfg.get("hostname", "amusicserver.local")
    print(f"\nSetup complete! Start the server:")
    print("  Linux/Mac:  python sWebExt/py_server/server.py")
    print("  Windows:    aMusicServer.exe")
    print(f"\nThen open: http://{hostname_display}:5000/explore")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="aMusicServer Setup Wizard")
    parser.add_argument("--import-spotify", action="store_true", help="Run Spotify CSV import only")
    parser.add_argument("--import-soundcloud", action="store_true", help="Run SC likes import only")
    parser.add_argument("--generate-mix", action="store_true", help="Generate a mix immediately")
    parser.add_argument("--reconfigure", action="store_true", help="Re-run full wizard")
    args = parser.parse_args()

    if args.import_spotify:
        cfg = _load_config()
        # Run step 5 standalone: scan CSVs, confirm, download
        import glob as _glob
        sp_dir = cfg.get("spotify_playlists_dir", "")
        if not sp_dir:
            print("spotify_playlists_dir not set in config.json")
            sys.exit(1)
        csvs = sorted(_glob.glob(os.path.join(sp_dir, "*.csv")))
        if not csvs:
            print("No CSV files found.")
            sys.exit(0)
        import re
        playlist_info = []
        for csv_path in csvs:
            name = os.path.splitext(os.path.basename(csv_path))[0]
            try:
                with open(csv_path, encoding="utf-8") as f:
                    lines = [l for l in f.read().splitlines() if l.strip()]
                count = max(0, len(lines) - 1)
            except Exception:
                count = 0
            playlist_info.append((name, csv_path, count))
        _import_spotify_csvs(playlist_info, cfg, cfg.get("navidrome_url",""), cfg.get("navidrome_user",""), cfg.get("navidrome_pass",""))
    elif args.import_soundcloud:
        cfg = _load_config()
        sc_user = cfg.get("sc_username","")
        if not sc_user:
            print("sc_username not set in config.json")
            sys.exit(1)
        _import_sc_likes(sc_user, cfg, cfg.get("navidrome_url",""), cfg.get("navidrome_user",""), cfg.get("navidrome_pass",""))
        _save_config(cfg)
    elif args.generate_mix:
        import sys
        sys.path.insert(0, _ROOT)
        try:
            from discover.engine import run_mix_from_config
            result = run_mix_from_config(_ROOT)
            print(f"Mix generated: {result}")
        except Exception as e:
            print(f"Error: {e}")
            sys.exit(1)
    else:
        main()
