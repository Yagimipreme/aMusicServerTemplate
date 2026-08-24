# Design: Web First-Run Setup Wizard & Managed Navidrome

**Date:** 2026-08-24
**Status:** Approved

---

## Overview

A guided first-run setup wizard inside the SIGNAL web UI, replacing the terminal-only
onboarding for the primary audience (Windows installer users who never see a shell). Its
centerpiece is closing the project's biggest onboarding cliff: a fresh user has no
Navidrome. The wizard can **auto-install Navidrome on every supported OS** — download,
configure, supervise, and bootstrap an admin account — via a new first-class
`navidrome_mgr/` module. The existing CLI wizard (`setup.py`) stays for headless setups.

Decisions taken during design:

| Decision | Choice |
|---|---|
| Wizard form | Web first-run wizard; CLI wizard retained |
| Navidrome depth | Auto-install on Windows, Linux, and Mac (plus detect/connect paths) |
| Supervision | aMusicServer supervises Navidrome as a managed child process |
| Architecture | Pure-Python `navidrome_mgr/` module (no per-OS scripts, no Docker) |
| Wizard scope | Minimal core: 5 steps; imports/schedules/extension deferred to existing screens |

---

## 1. First-run gating & wizard flow

### Gating

- New route `GET /wizard/state` → `{"needed": bool, "skipped": bool}`.
  `needed` is true when required config is empty: `song_dir`, or all of
  `navidrome_url`/`navidrome_user`/`navidrome_pass`.
- On boot, `app.js` calls it. If `needed && !skipped`, the router forces a new `#wizard`
  screen and hides the bottom nav. Otherwise the app behaves exactly as today.
- **Skip:** every wizard screen has "Skip for now" → `POST /wizard/skip` sets
  `wizard_skipped: true` in config. The wizard never nags again.
- **Re-entry:** the Setup screen gains a "Run setup wizard" link that navigates to
  `#wizard` regardless of gating state (useful for reconfiguration and for the
  Navidrome install path later).

### Steps

```
1. Welcome      — what this app does, one paragraph; Start / Skip
2. Music folder — path input, validation, per-OS default
3. Navidrome    — detect → connect | auto-install | manual URL
4. Last.fm      — optional keys + username, registration link, test button
5. Done         — launchpad: extension install pointer, import-library link,
                  Mixes screen link
```

- Each step commits **only its own config keys** on Continue, via the existing atomic
  config write. Abandoning the wizard mid-way never leaves half-written state.
- Back/Continue navigation; state re-read from config on entry, so re-running the
  wizard shows current values (mirrors the CLI wizard's preserve-on-rerun rule).
- UI follows SIGNAL tokens; DOM built with `createElement`/`textContent` only (no
  `innerHTML` with data — project invariant). Progress dots across the top.

## 2. Music folder step

- Text input, default suggestion per OS (`~/Music`, `%USERPROFILE%\Music`).
- `POST /wizard/validate_dir {path}` → `{ok, created, writable, audio_files}`:
  - Creates the directory if missing (parents included), checks writability.
  - If it already holds audio files, returns the count — UI shows
    "Found 1,381 tracks" for confidence that an existing library was recognized.
- On Continue: writes `song_dir`.

## 3. Navidrome step — three paths

On entry the wizard probes: the configured `navidrome_url` if set, else
`http://localhost:4533` (Subsonic `ping.view`, no auth → server presence; version read
from the response).

- **Found** → "Found Navidrome vX at <url>" → credentials form → validate via
  authenticated ping → write `navidrome_url/user/pass`.
- **Not found** → user chooses:
  - **"Install it for me"** → auto-install flow (below).
  - **"I run it elsewhere"** → URL + credentials form, same validation.

### Auto-install flow

- `POST /wizard/navidrome/install` starts an async job (same thread + status-poll
  pattern as enrich/repair). `GET /wizard/navidrome/install/status` returns
  `{phase, progress_pct, error, done}` with phases:
  `download → verify → unpack → configure → start → create_admin → verify`.
- UI renders the phase list with a progress bar during download.
- On success, the wizard shows the generated admin credentials **once**, with a copy
  button and the note that they also work for logging into Navidrome's own UI. They are
  stored in `navidrome_url/user/pass` — every downstream consumer is unchanged.

## 4. `navidrome_mgr/` module

New top-level package, three units:

### `navidrome_mgr/install.py`

- Platform/arch map: `windows-amd64`, `linux-amd64`, `linux-arm64`, `darwin-amd64`,
  `darwin-arm64`. Anything else → `UnsupportedPlatform` (wizard falls back to the
  manual-connect path with an explanation).
- Downloads the **pinned** Navidrome release (version constant in the module; recorded
  in config on install) from GitHub releases; verifies sha256 against the release
  checksum file; unpacks (zip on Windows, tar.gz elsewhere); `chmod +x` the binary.
- Managed dir: `%LOCALAPPDATA%\aMusicServer\navidrome\` on Windows,
  `<project>/navidrome_data/` elsewhere. Holds the binary, `navidrome.toml`, and
  Navidrome's own data folder (db, cache).
- Generates `navidrome.toml`:
  - `MusicFolder = <song_dir>`
  - `DataFolder = <managed dir>/data`
  - `Address = "127.0.0.1"` (LAN exposure stays aMusicServer's job)
  - `Port` = 4533, or the first free port in 4533–4543 if occupied.

### `navidrome_mgr/supervise.py`

- When `navidrome_managed.enabled`, server startup spawns the binary as a child
  (`subprocess.Popen`), stdout/stderr appended to `logs/navidrome.log`.
- Restart-on-crash with exponential backoff, capped at 60 s. Clean `terminate()` on
  server shutdown (atexit + signal handling).
- `GET /wizard/navidrome/status` → `{running, pid, version, port, last_stderr}`
  (`last_stderr`: tail of the log ring buffer, so the wizard can show why a
  crash-looping child is failing).

### `navidrome_mgr/bootstrap.py`

- Polls the child's HTTP endpoint until up (60 s timeout).
- Creates the initial admin user via Navidrome's first-run admin-creation endpoint.
  **The exact endpoint/payload is the one unpinned external contract — verifying it
  against a real Navidrome release is the first implementation task**, before any
  dependent code is written.
- Username defaults to the OS username; password `secrets.token_urlsafe(12)`.
- Writes `navidrome_url/user/pass` on success.

### Config schema

New block; existing flat keys remain the single source of truth for all consumers:

```json
"navidrome_managed": {
  "enabled": false,
  "version": "",
  "port": 4533,
  "dir": ""
}
```

### Updates — out of scope

Installed version is recorded. A later "Update Navidrome" button on the Setup screen can
re-run `install.py` with a newer pin. Not part of this design.

## 5. Last.fm step

- Inputs: API key, secret, username. Registration link (free) shown inline.
- One "Test" button → `POST /wizard/lastfm/test` → validates the key via an existing
  lastfm client call.
- One plain sentence on what it unlocks (weekly-mix seeding, genre enrichment).
- Fully skippable; skipping writes nothing.

## 6. Error handling

- **Download failure / offline:** job status carries the error; UI offers Retry.
- **Unsupported arch:** automatic fallback to manual-connect with an explanation.
- **Managed dir already initialized** (re-run after crash or partial install): detect
  the existing binary/toml and offer connect-instead-of-reinstall.
- **Port conflicts:** handled by the 4533–4543 probe at configure time.
- **Crash-looping child:** `last_stderr` surfaces in the wizard status view.
- **Admin creation fails** (instance already has users): offer the credentials form
  instead.
- No step ever blocks the rest of the app — Skip is always available, and gating only
  triggers on genuinely empty config.

## 7. Testing

- `install.py`: asset-selection matrix per platform, checksum verification, unpack
  against small fixture archives; `navidrome.toml` generation golden test; port-probe
  logic with mocked sockets.
- `supervise.py`: fake executable (script that exits/sleeps on cue), backoff logic with
  injected clock.
- `bootstrap.py`: mocked HTTP for up-poll and admin creation.
- Wizard routes (`/wizard/*`): Flask test client — gating truth table, validate_dir
  cases (missing/created/unwritable/has-audio), install job lifecycle with `install.py`
  mocked.
- **Manual verification (not automated):** one real download+install+bootstrap run per
  available OS, and the admin-endpoint contract check noted in §4.
- Frontend untested, per project convention.

## 8. Explicit non-goals

- No Navidrome auto-update mechanism (§4).
- No import steps in the wizard (SC likes / Spotify CSV) — the Done step links to the
  Library screen's import cards instead.
- No changes to Windows packaging or the tray app — supervision lives in the server.
- No multi-instance or remote-install support; managed Navidrome is always local.
