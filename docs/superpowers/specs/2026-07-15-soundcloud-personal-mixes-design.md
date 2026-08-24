# SoundCloud Personal Mixes → m3u Design

## Goal

Download the SoundCloud user's own algorithmically-generated personal mixes (e.g. "SoundCloud Weekly" and any other SC-generated recommendation playlists tied to the logged-in account — distinct from this repo's existing Last.fm-based Weekly/Daily discover mixes) and write each one to its own `.m3u` file in `song_dir`, so Navidrome picks it up and it's playable in Symfonium. Runs on a schedule and via a manual "Sync now" button in the webUI.

## Why this needs new infrastructure

The repo's existing SoundCloud integration (`soundcloud/client.py`) only carries an anonymous, scraped `client_id` — enough for public API calls (search, resolve, related tracks) but not for data tied to a specific logged-in account. SoundCloud's personal recommendation playlists are only visible when authenticated as that account, so this feature requires a real login.

## Phase 1: Auth + discovery spike

### Auth

New module `soundcloud/auth.py`, modeled directly on the existing headless-Selenium pattern in `scripts/Sc2Sp_src/script_web.py` (`--headless=new`, Chrome DevTools `Network.enable`, sniffing performance-log entries for network calls — no visible browser, no manual interaction required at run time):

- Launch headless Chrome, navigate to SoundCloud's login page, fill in `sc_username` / `sc_password` from config, submit.
- Sniff the resulting network traffic (same technique already used to find `client_id`) to capture the OAuth token SC's web app attaches to its own `api-v2` calls once logged in.
- Return `(client_id, oauth_token)`. Cache both in config: `sc_client_id` (existing field) and new `sc_oauth_token` / `sc_oauth_token_ts`, refreshed on 401 the same way `client_id` refresh already works in `soundcloud/client.py`.

New config fields:
- `sc_password` — type `"secret"` in `SETTINGS_SCHEMA` (same treatment as `navidrome_pass`).
- `sc_oauth_token`, `sc_oauth_token_ts` — internal/persisted, not exposed in the settings UI (same treatment as `sc_client_id`).

`SCClient` gains an optional `oauth_token` constructor param. When present, every request sends `Authorization: OAuth <token>`. Existing anonymous calls (search, resolve, related tracks) keep working unauthenticated.

### Discovery spike

Rather than a throwaway script, this ships as a permanent low-level function: `soundcloud/auth.discover_personal_mix_endpoints()`. While the headless browser holds an authenticated session, it navigates SoundCloud's stream/library pages and dumps every distinct `api-v2` request URL plus a truncated response sample to `logs/sc_mix_discovery.json`.

This runs once via a temporary CLI entry point. The user runs it against their real account; the dump is inspected manually (by the user and assistant together) and the real endpoint(s) + JSON parsing get hard-coded into Phase 2's `soundcloud/personal_mixes.py` from that point on. No endpoint shape is assumed ahead of time — everything downstream in Phase 2 is built from what the dump actually contains, and Phase 2's implementation details (exact mixes returned, their cadence, naming) may be adjusted once real data is seen.

## Phase 2: Fetch → download → m3u write

### Fetch

`soundcloud/personal_mixes.py` exposes `get_personal_mixes(client) -> list[{id, title, tracks: [...]}]`, using the endpoint(s) found in Phase 1's spike. Tracks are parsed through the existing `_track_from_raw` helper in `soundcloud/mirror.py` so they come back in the same track-dict shape used throughout the rest of the codebase.

### Download

For each mix, each track's `permalink_url` is passed to the existing generic downloader `dl_mod.download_url(url, song_dir)` (the same function the discover engine and `/follow` pipeline already call via `scripts/sTownload/script_web.py`). This already skips re-downloading tracks that exist on disk, so repeated syncs don't re-fetch unchanged tracks.

### m3u write

New function `write_mix_snapshot(song_dir, mp3_paths, name)` in `discover/assemble.py`. Unlike `write_weekly_mix` (sliding window: accumulates and rotates out oldest past a cap), this **fully replaces** the `.m3u` file's contents on every run — these are SC's live rotating snapshot, not an accumulating history. Filename sanitization reuses the same logic `write_weekly_mix` already applies. Each mix returned by `get_personal_mixes` gets its own `<Title>.m3u` in `song_dir`.

## Phase 2: Scheduling & webUI

- New state file `sc_mixes_state.json`, same shape as `discover_state.json` / `follow_state.json`, tracking `last_run` per mix id.
- New `_sc_mixes_due_now()` scheduler check alongside the existing `_profiles_due_now` loop in `sWebExt/py_server/server.py`. Default cadence: weekly for anything SC itself labels as a weekly mix, daily otherwise. Exact cadence rules are finalized once Phase 1 shows what mixes actually exist and how SC labels them.
- New route `POST /sc/mixes/sync` triggers an immediate run (mirrors the existing `/discover/run`), returns `{status, synced: [...]}`.
- A "Sync SC Mixes now" button is added to the existing settings UI, calling that route.
- New config block `sc_mixes: {enabled, run_hour}` plus the `sc_password` field, added to `SETTINGS_SCHEMA` under a new `"SoundCloud Mixes"` group.

## Error handling

- Login failure (bad password, CAPTCHA, SC layout change breaking the sniff) logs a warning and disables the feature for that run only — never crashes the scheduler loop, matching how `client_id` refresh failures are handled today.
- OAuth token expiry: same 401-retry-once pattern already in `SCClient.get()`, extended so the retry re-runs the headless login (instead of just `fetch_client_id_via_selenium`) when an `oauth_token` is configured.

## Testing

- All `soundcloud/auth.py` and `personal_mixes.py` tests mock Selenium and network calls entirely (per existing `tests/soundcloud/` conventions) — no live login in CI.
- `write_mix_snapshot` gets a straightforward unit test (full-replace behavior, filename sanitization) alongside the existing `write_weekly_mix` tests in `tests/discover/test_assemble.py`.
- `personal_mixes.get_personal_mixes` gets unit tests once its real response shape is known from the Phase 1 dump.

## Open questions (resolved during Phase 1, not before)

- Exact number and identity of SC's personal mixes (this design assumes "at least a weekly mix," but the real count/cadence is unknown until the spike runs).
- Exact endpoint path(s) and JSON shape for those mixes.
- Whether SC's login flow can be automated headlessly without hitting CAPTCHA/2FA in practice — if it can't, Phase 1's auth approach needs revisiting before Phase 2 proceeds.

---

## Amendment 2026-08-24 — three-layer token provisioning (approved)

The last open question above is now answered: **headless login hits a CAPTCHA.** On
2026-08-24 (after fixing the AppArmor profile that had blocked headless Chrome
entirely), the Phase 1 spike ran against the real account and SoundCloud served a
captcha challenge on the sign-in page; the endpoint dump came back empty. Automated
CAPTCHA solving is out of scope by principle, so the original "refresh token via
headless re-login on 401" mechanism is demoted from primary to opportunistic fallback.

The token is therefore treated as a **provisioned secret**: long-lived (weeks–months,
dies on logout-everywhere / password change / SC-side expiry → 401), provisioned and
re-provisioned through three layers, tried in this order:

1. **Extension token bridge (primary).** The browser extension runs where the user is
   already logged into SoundCloud. Additions to `sWebExt/`: `cookies` permission +
   `*.soundcloud.com` host permission in the manifest; a **"Connect SoundCloud
   account"** button in the popup that reads the `oauth_token` cookie and POSTs it to
   the server; and a background service-worker `chrome.cookies.onChanged` listener that
   re-pushes the cookie whenever SC rotates it — the server copy stays fresh
   indefinitely for anyone who keeps using soundcloud.com in that browser. No CAPTCHA,
   no devtools, one click.
2. **Headless login with stored credentials (opportunistic fallback).** The merged
   Phase 1 code (`soundcloud/auth.py` + `sc_username`/`sc_password`), attempted on 401.
   CAPTCHA challenges are risk-based, so this sometimes silently self-heals the token;
   a challenge simply means the attempt fails quietly, matching the existing
   error-handling rule (warn + skip that run, never crash the scheduler).
3. **Manual paste (universal fallback).** A token field in the "SoundCloud Mixes"
   settings group with a short walkthrough (any api-v2 request's `Authorization`
   header, or the `oauth_token` cookie). Needed anyway for extension-less setups.

New server route: `POST /sc/oauth_token {token}` — validates the token with one
authenticated api-v2 call (e.g. `/me`), persists `sc_oauth_token` +
`sc_oauth_token_ts` on success, returns the resolved account username so the extension
popup and settings UI can show "Connected as <user>". Used by layers 1 and 3.

Status surface: when all layers are exhausted (401 and no fresh token), the SoundCloud
Mixes settings group and the personal-mix sync status show
"token expired — click Connect SoundCloud in the extension (or paste a token)".
Rationale for not storing only the password: the token is a revocable session
credential, while the config password is the whole account in plaintext on a server
with no route auth — the password stays optional, the token is the working secret.

Setup-wizard interaction: none. Per the 2026-08-24 wizard design's minimal-core scope,
SC personal mixes are not a wizard step; the wizard's Done step points at installing
the extension, and the extension's Connect button is the SC onboarding.

### Phase 1 spike results (2026-08-24, manual-token run — spike COMPLETE)

Run with a manually provisioned token (layer 3) against the real account; full dump in
`logs/sc_mix_discovery.json`. The open questions above are answered:

**The mixes.** `GET /mixed-selections` returns selection groups; the personal ones:

| Selection id | Contents | Cadence signal |
|---|---|---|
| `soundcloud:selections:made-for-you` | **Weekly Wave** (`…system-playlists:weekly:<user_id>`), **Daily Drops** (`…new-for-you:<user_id>`) | weekly / daily |
| `soundcloud:selections:your-moods` | **Your Mix 1–6** (`…your-moods:<user_id>:1..6`) | unlabeled — treat as daily |

Other selections (curated-global, trending-by-genre, artist-stations, liked-by,
buzzing…) are not account-personal mixes and are out of scope for Phase 2.

**The fetch contract** (three calls, all `api-v2` with `Authorization: OAuth` +
`client_id`):

1. `GET /mixed-selections` → filter to the two selection ids above; each item carries
   a stable `urn`.
2. `GET /system-playlists/<urlencoded urn>` → `{title, last_updated, tracks: [...]}` —
   Weekly Wave had 30 tracks, `last_updated` matched the weekly refresh. **Tracks come
   back as id-only stubs.**
3. `GET /tracks?ids=<comma-separated>` → hydrated track objects (title,
   `permalink_url`, `media.transcodings`) — batch ~20 ids per call. Verified working.

Hydrated tracks flow through the existing `_track_from_raw` → `permalink_url` →
existing downloader, exactly as this spec's Phase 2 assumed.

**Phase 2 amendment:** playlist writing uses the merge-aware
`discover/playlist_sync.py` mechanism (2026-08-24 usable-status batch) instead of the
originally planned `write_mix_snapshot` m3u full-replace: each SC mix syncs as an
engine-owned track set (full engine-side replace per SC refresh), so user additions
and deletions in the Navidrome playlist survive, consistent with every other generated
playlist. `write_mix_snapshot` will not be built.
