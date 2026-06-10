# Design: Setup Wizard Enhancements, Bootstrap Mix & Server Autostart

**Date:** 2026-06-10
**Status:** Approved

---

## Overview

A comprehensive overhaul of `setup.py` that transforms it from a configuration tool into a
full onboarding experience. Covers:

1. **Spotify CSV import** — wizard-driven export prompt, scan, confirm, download, rescan
2. **SoundCloud likes import** — one-step bulk import of the user's SC library at setup
3. **Bootstrap Mix (Starter Mix)** — generate something immediately on first run; switch to
   Last.fm-primary Weekly Mix automatically once enough history exists
4. **Configurable mix schedule** — daily or weekly, user-chosen day and hour
5. **Server autostart** — Windows startup shortcut; Linux systemd unit generation
6. **Quality-of-life gaps** — Last.fm key registration link, failed-track logs, standalone
   import commands, Navidrome rescan polling, SC client_id not-yet-ready state in explore UI

All changes are additive. Existing `config.json` values are preserved on re-run.
Every new wizard step is skippable with Enter.

---

## 1. Complete Wizard Flow (after enhancements)

Steps marked **[NEW]** or **[ENHANCED]** are new or changed from the current wizard.

```
Step 1  — Song directory                      [REQUIRED, unchanged]
Step 2  — Navidrome URL                       [REQUIRED, unchanged]
Step 3  — Navidrome credentials               [REQUIRED, unchanged]
Step 4  — SoundCloud account                  [OPTIONAL, ENHANCED — adds likes import]
Step 5  — Spotify playlist import             [OPTIONAL, ENHANCED — full import flow]
Step 6  — Last.fm API key + secret            [OPTIONAL, ENHANCED — adds registration link]
Step 6b — Last.fm username                    [OPTIONAL, shown if step 6 filled]
Step 7  — Last.fm scrobbling                  [OPTIONAL, shown if step 6 filled, unchanged]
Step 8  — Browser extension                   [OPTIONAL, unchanged]
Step 9  — Dedup scanner                       [OPTIONAL, unchanged]
Step 10 — Title cleanup                       [ON BY DEFAULT, unchanged]
Step 11 — Hostname for share links            [NEW]
Step 12 — Mix schedule                        [NEW]
Step 13 — Favorite artists for Starter Mix    [NEW, OPTIONAL]
Step 14 — Server autostart                    [NEW]

→ Writes config.json
→ Prints summary of changed fields
→ Triggers Starter Mix generation in background (if Navidrome has songs)
```

---

## 2. Step Details

### Step 4 — SoundCloud account [ENHANCED]

Existing: asks for SC username, one-line explanation.

New addition: if a username is provided, offer to import liked tracks immediately.

```
SoundCloud username (or Enter to skip): burial

Fetching SoundCloud client credentials... (may take ~30s)
✓ Connected. Found profile: burial (52k followers, 143 liked tracks)

Import your SoundCloud likes now? (y/N): y

Importing 143 liked tracks...
  ✓ Burial — Archangel
  ✗ Not found: Unknown Artist — Rare Track
  ...
Done: 138 downloaded, 5 not found.
Failed tracks saved to: logs/soundcloud_likes_failed.txt

Triggering Navidrome rescan...
Scanning... (1,381 songs indexed)
✓ Rescan complete. Navidrome now has 1,381 songs.
```

**Implementation notes:**

- The wizard calls `fetch_client_id_via_selenium()` from `scripts/Sc2Sp_src/script_web.py`
  directly (importable) — no server needed. Persists the client_id to config.
- Uses `soundcloud.mirror.get_user_likes(client, user_id, limit=500)` — a new primitive
  added alongside `get_user_tracks` in the `soundcloud/` package.
- Each liked track is downloaded via the existing SC download pipeline.
- Failed tracks written to `logs/soundcloud_likes_failed.txt` — one `artist — title` per line.
- Navidrome rescan triggered after all downloads complete (see Section 4).

### Step 5 — Spotify playlist import [ENHANCED]

Existing: asks for `spotify_playlists_dir`, one-line explanation of Exportify.

New: full guided import flow.

```
Spotify playlists directory: /home/user/Music/spotify-exports

To export your Spotify playlists, go to:
  https://exportify.net
Export each playlist as CSV and save to the directory above.

Opening Exportify in your browser... (or visit the URL above manually)

Press Enter when your CSV files are ready...

Scanning /home/user/Music/spotify-exports...

Found 3 playlist(s):
  • Chill Evenings.csv          (143 tracks)
  • Late Night Drives.csv        (87 tracks)
  • Workout.csv                  (52 tracks)

Proceed with download? (282 tracks total via yt-dlp) (y/N): y

Downloading Chill Evenings (143 tracks)...
  ✓ Aphex Twin — Windowlicker
  ✗ Not found: Artist — Obscure Track
  [============================>    ] 138/143
Done: 138 downloaded, 5 not found → logs/Chill_Evenings_failed.txt

Downloading Late Night Drives (87 tracks)...
  ...

All playlists complete: 267/282 downloaded.
Failed tracks: see logs/ directory.

Triggering Navidrome rescan...
Scanning... (1,643 songs indexed)
✓ Rescan complete. Navidrome now has 1,643 songs across 3 new playlists.
```

**Implementation notes:**

- `webbrowser.open("https://exportify.net")` — same pattern as the existing scrobbling step.
- Directory scan: `glob.glob(os.path.join(spotify_playlists_dir, "*.csv"))`.
- Track count per CSV: read header row, count data rows.
- Download calls the existing `scripts/sTownload/app.py` pipeline per CSV.
- Per-playlist failed log: `logs/{sanitized_playlist_name}_failed.txt`.
- One Navidrome rescan after all CSVs complete (not per-CSV).

### Step 6 — Last.fm API key [ENHANCED]

New addition: before asking for the key, explain where to get one and offer to open the
registration page.

```
Last.fm API key (or Enter to skip):

  Get a free API key at: https://www.last.fm/api/account/create
  Open in browser? (Y/n): y

  After registering, paste your API key and secret below.

Last.fm API key: ________________________________
Last.fm API secret: ________________________________
```

### Step 11 — Hostname for share links [NEW]

```
Hostname for share links [amusicserver.local]:
  (This is embedded in share URLs. Use your Tailscale/WireGuard hostname
  or keep the default — it works on any home network via mDNS.)

  > taichi-music.local

Writing hosts file entry: 127.0.0.1  taichi-music.local
  ✓ Entry written to /etc/hosts
  (mDNS advertisement will start automatically with the server)
```

Hosts file write is idempotent — checks for existing entry before writing.
If write fails (insufficient privilege): prints the manual instruction and continues.

### Step 12 — Mix schedule [NEW]

```
How often should mixes be generated?
  [1] Weekly (default)
  [2] Daily

Choice [1]: 1

Which day should the Weekly Mix generate? [sunday]:
  (sun / mon / tue / wed / thu / fri / sat)
  > sunday

At what hour? (0–23, local time) [22]:
  > 22

✓ Weekly Mix will generate every Sunday at 22:00 local time.
```

Daily mode skips the day question. Hour still configurable.

### Step 13 — Favorite artists for Starter Mix [NEW, OPTIONAL]

```
Your first mix ("Starter Mix") generates immediately using the music you've
just imported, plus Last.fm's similarity graph.

Optionally, name up to 5 favorite artists to improve it:
  (comma-separated, or Enter to skip)

Favorite artists: Aphex Twin, Burial, Coil

✓ Saved 3 seed artists. These will weight your first mix toward your taste.
```

Artists are stored in `config.json` as `discover.manual_seeds`. They are used only during
cold-start mode (before Last.fm readiness gate passes) and removed from active seeding once
the Weekly Mix takes over (the config field is kept but ignored).

If Enter is skipped: Starter Mix uses only CSV artists + library frequency — still generates
a useful result, just with less personal signal.

### Step 14 — Server autostart [NEW]

```
Start the server automatically when you log in? (y/N): y

  ✓ [Windows] Shortcut added to Startup folder.
      The server will start automatically on next login.

  ✓ [Linux]  Systemd user unit written to ~/.config/systemd/user/amusicserver.service
      Run: systemctl --user enable --now amusicserver
      (The wizard has done this for you if systemctl is available.)
```

**Windows implementation:**

```python
import os, winreg
startup = os.path.join(os.environ["APPDATA"],
    "Microsoft", "Windows", "Start Menu", "Programs", "Startup")
# write a .bat shortcut: start "" "path\to\aMusicServer.exe"
```

**Linux implementation:**

Generate `~/.config/systemd/user/amusicserver.service`:

```ini
[Unit]
Description=aMusicServer

[Service]
ExecStart=/path/to/.venv/bin/python /path/to/sWebExt/py_server/server.py
WorkingDirectory=/path/to/project
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

Then attempt `subprocess.run(["systemctl", "--user", "enable", "--now", "amusicserver"])`.
If systemctl unavailable: print the unit file path and the manual enable command.

---

## 3. End of Wizard — Starter Mix Trigger

After writing `config.json`, if Navidrome is reachable and has songs:

```
Setup complete! Starting your first Starter Mix in the background...
(It will appear in Navidrome in a few minutes.)

Your server is ready. Start it with:
  python sWebExt/py_server/server.py        (Linux / macOS)
  aMusicServer.exe                          (Windows — already running if you launched it)

Then open your library at:
  http://taichi-music.local:5000/explore
```

The Starter Mix is triggered as a background `threading.Thread` — the wizard exits
immediately without waiting for downloads to complete.

---

## 4. Navidrome Rescan Helper

Used after both Spotify and SoundCloud imports. Extracted into a shared function:

```python
def trigger_navidrome_rescan(navidrome_url, user, password, poll_interval=3):
    """Trigger a Navidrome library rescan and poll until complete."""
    # POST /rest/startScan.view
    # Poll GET /rest/getScanStatus.view every poll_interval seconds
    # Print "Scanning... (N songs indexed)" while scanning=true
    # Return final song count when scanning=false
```

Called with the already-validated Navidrome credentials from steps 2–3.
Does not require the aMusicServer server to be running — direct HTTP calls to Navidrome.

---

## 5. Bootstrap Mix & Discover Engine Changes

### Readiness gate

```python
# discover/engine.py
def lastfm_is_ready(client, username, cfg):
    threshold = cfg.get("discover", {}).get("lastfm_readiness", {})
    min_scrobbles = threshold.get("min_scrobbles", 100)
    min_artists   = threshold.get("min_unique_artists", 15)

    # user.getRecentTracks(limit=1) → @attr.total gives total scrobble count
    # user.getTopArtists(period=overall, limit=500) → count unique artists
    # both must meet threshold → True
    # on any Last.fm error → False (safe default: use bootstrap)
```

Gate result is cached in `discover_state.json["lastfm_ready"]` once it passes — no re-check
on subsequent runs.

### Bootstrap seeds (`discover/seeds.py`)

Three sources, used in priority order when gate is False:

```python
def get_bootstrap_seeds(cfg, subsonic, lastfm_client=None):
    seeds = []

    # 1. Manual seeds from config (highest priority)
    manual = cfg.get("discover", {}).get("manual_seeds", [])
    seeds.extend(manual)

    # 2. Artists from imported Spotify CSVs
    csv_dir = cfg.get("spotify_playlists_dir", "")
    if csv_dir:
        seeds.extend(get_csv_artists(csv_dir, limit=30))

    # 3. Most-represented artists in Navidrome library
    seeds.extend(get_library_artist_frequency(subsonic, limit=20))

    # Dedup preserving order, return top 20
    seen, result = set(), []
    for s in seeds:
        cf = s.casefold()
        if cf not in seen:
            seen.add(cf)
            result.append(s)
    return result[:20]

def get_csv_artists(csv_dir, limit=30):
    """Read all *.csv files in csv_dir, extract unique artist column values."""
    ...

def get_library_artist_frequency(subsonic, limit=20):
    """Return artists sorted by track count in Navidrome library."""
    # Subsonic getArtists → all artists → sort by album/track count
    ...
```

### Engine routing

```python
# discover/engine.py — run_mix() replaces the existing run_weekly()
def run_mix(deps, cfg):
    disc = cfg.get("discover", {})

    if deps.lastfm_client and lastfm_is_ready(deps.lastfm_client, cfg.get("lastfm_username"), cfg):
        # Normal path — Last.fm scrobble history as seeds
        playlist_name = disc.get("playlist_name", "Weekly Mix")
        seeds = collect_seeds(deps.subsonic, deps.lastfm_client, ...)
    else:
        # Cold-start path — bootstrap seeds
        playlist_name = disc.get("bootstrap_playlist_name", "Starter Mix")
        seeds = get_bootstrap_seeds(cfg, deps.subsonic, deps.lastfm_client)

    # expand → resolve → filter → acquire → assemble (unchanged pipeline)
    ...
```

The existing `run_weekly()` function is kept as a thin wrapper calling `run_mix()` for
backward compatibility.

### Configurable scheduler

The background loop in `server.py` changes from a fixed 7-day sleep to a next-occurrence
calculator:

```python
def _seconds_until_next_run(schedule, run_day, run_hour):
    """Return seconds until the next scheduled run."""
    now = datetime.datetime.now()
    target_hour = datetime.time(run_hour, 0)
    if schedule == "daily":
        # next occurrence of run_hour today or tomorrow
        candidate = now.replace(hour=run_hour, minute=0, second=0, microsecond=0)
        if candidate <= now:
            candidate += datetime.timedelta(days=1)
        return (candidate - now).total_seconds()
    else:  # weekly
        day_map = {"sun": 6, "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5}
        target_weekday = day_map.get(run_day.lower()[:3], 6)
        days_ahead = (target_weekday - now.weekday()) % 7
        candidate = (now + datetime.timedelta(days=days_ahead)).replace(
            hour=run_hour, minute=0, second=0, microsecond=0)
        if candidate <= now:
            candidate += datetime.timedelta(weeks=1)
        return (candidate - now).total_seconds()
```

On server start, the loop immediately runs `run_mix()` once if:
- Navidrome has songs AND
- No mix has ever been generated (`discover_state.json["last_run"]` is absent)

This ensures the Starter Mix triggered at the end of the wizard actually runs when the server starts, even if the wizard background thread didn't complete.

---

## 6. Standalone CLI Commands

`setup.py` gains `argparse` support. Existing no-args behaviour is unchanged.

```
python setup.py                         full wizard (current behaviour)
python setup.py --import-spotify        step 5 only: scan CSVs → download → rescan
python setup.py --import-soundcloud     step 4 SC likes import only
python setup.py --generate-mix          trigger a mix run immediately
python setup.py --reconfigure           alias for no-args (explicit full re-run)
```

`--generate-mix` calls `_run_discover_once()` from the server module directly — same
function the server uses, no server process needed.

---

## 7. Config Schema Changes

```jsonc
{
  // existing fields unchanged

  "discover": {
    // existing
    "enabled": true,
    "weekly_count": 30,
    "playlist_name": "Weekly Mix",
    "lastfm_period": "1month",
    "seed_artist_count": 20,
    "playlist_mix_count": 20,
    "playlist_seed_artist_count": 10,

    // new
    "schedule": "weekly",              // "weekly" | "daily"
    "run_day": "sunday",               // sun|mon|tue|wed|thu|fri|sat (weekly only)
    "run_hour": 22,                    // 0–23, local time
    "bootstrap_playlist_name": "Starter Mix",
    "manual_seeds": [],                // up to 5 artist names; optional cold-start signal
    "lastfm_readiness": {
      "min_scrobbles": 100,
      "min_unique_artists": 15
    }
  }
}
```

`config.example.json` updated to match.

---

## 8. Minor Quality-of-Life Fixes

### SC client_id not-yet-ready state in explore UI

The SC client_id refresh (Selenium) runs at server startup and takes ~30 seconds. During
that window, SC searches return 401 errors that look like failures to the user.

Fix: server tracks client_id readiness in a module-level flag. The `/sc/search/*` and
`/sc/resolve` routes return `{"status": "connecting", "retry_after": 30}` while the
initial refresh is in progress. The explore UI's JavaScript shows a "SoundCloud:
connecting..." badge in the SC tab header and auto-retries after the indicated delay.

### Browser extension silent fail when server is down

The extension's `popup.js` currently shows a generic network error if the server is
unreachable.

Fix: wrap the `fetch()` in a try/catch that specifically detects connection failure and
shows: *"Can't reach server. Is aMusicServer running on port 5000?"*

---

## 9. Testing

| Test target | What is tested |
|---|---|
| `setup.py --import-spotify` | Mock CSV scan, mock download pipeline, verify rescan call |
| `setup.py --import-soundcloud` | Mock SC client_id fetch, mock likes fetch, mock download |
| `discover/engine.py` readiness gate | lastfm_is_ready True/False paths, state persistence |
| `discover/seeds.py` bootstrap | get_csv_artists, get_library_artist_frequency, blending |
| Scheduler | _seconds_until_next_run for daily and weekly modes, edge cases (past hour, correct day) |
| Navidrome rescan helper | Mock startScan + getScanStatus polling |
| Autostart (Linux) | Unit file content verification (no live systemctl calls in tests) |

---

## 10. File Changes Summary

| Action | Path |
|---|---|
| Modified | `setup.py` — full flow enhancement, argparse, all new steps |
| Modified | `discover/engine.py` — run_mix(), lastfm_is_ready(), initial-run trigger |
| Modified | `discover/seeds.py` — get_bootstrap_seeds(), get_csv_artists(), get_library_artist_frequency() |
| Modified | `sWebExt/py_server/server.py` — configurable scheduler, SC readiness flag |
| Modified | `soundcloud/mirror.py` — add get_user_likes() |
| Modified | `sWebExt/popup/popup.js` — connection failure error message |
| Modified | `config.example.json` — new discover.* fields |
| New | `tests/test_setup_wizard.py` — import flows, rescan, argparse commands |
| New | `tests/discover/test_bootstrap.py` — readiness gate, bootstrap seeds |
| New | `tests/discover/test_scheduler.py` — next-run time calculation |

---

## 11. Out of Scope

- **Navidrome installation** — documented in README as a prerequisite; not automated
- **Tailscale / WireGuard setup** — assumed present; not configured by this project
- **Chrome / Chromium installation** — wizard checks and warns if absent; install is manual
- **Multi-user support** — single server, single Last.fm account; deferred
- **README full rewrite** — deferred until project is feature-complete
- **Symfonium / Subsonic client onboarding** — out of scope; user configures their client
  directly against the Navidrome URL
- **`requirements.txt` audit** — trivial maintenance task, done inline during implementation
