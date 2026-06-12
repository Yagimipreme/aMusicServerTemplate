# WebUI Redesign — SIGNAL (club dark) — Design

**Date:** 2026-06-12
**Status:** Approved direction (user picked SIGNAL from three live mockups)
**Depends on:** 2026-06-12-mix-profiles-genre-mixes-design.md (Mixes screen consumes /mixes)
**Reference:** `web/mockups/signal.html` — the approved interactive mockup is the
visual source of truth (also served at /static/mockups/ for review).

## Goal

Replace the broad multi-tab explore page with a mobile-first app shell in the
SIGNAL aesthetic: four screens behind a fixed bottom nav, mixes as the home
screen. Full redesign of every existing tab's functionality — reorganized, not
dropped.

## Design language (from the approved mockup)

- Tokens (CSS variables): `--bg #08080a`, `--panel #101013`, `--panel2 #16161a`,
  `--line #222228`, `--acid #c3ff3d` (single hot accent), `--txt #e8e8e6`,
  `--mut #6f6f78`, `--danger #ff4d5e`.
- Type: Unbounded (display, uppercase, letterspaced), Archivo (body),
  JetBrains Mono (data/meta). **Self-host woff2 files in `web/static/fonts/`** —
  the UI must not depend on Google Fonts/internet (LAN appliance).
- Texture: fixed SVG-noise grain overlay at low opacity; acid glow on fresh
  indicators and active controls; cards are bordered panels with 4px radius.
- Layout: max-width 430px center column; bottom nav with safe-area inset;
  accordion expansion (max-height transition) for editable items.

## App shell

New template `web/templates/app.html` + `web/static/app.js` + `app.css`,
served at `/` (root GET). The legacy explore page stays reachable at
`/explore` unchanged until parity is confirmed, then is removed in a follow-up.
Root POST (URL submit) keeps working — the Search screen replaces that form.

Bottom nav: **MIXES · LIBRARY · SEARCH · SETUP** (screens below). Screen state
in `location.hash` (`#mixes`, `#library`, …) so refresh/back behave.

## Screens → endpoints

### 1. Mixes (home)
Accordion list per the mix-profiles spec UI section, in SIGNAL styling exactly
as mocked: status dot (acid = fresh, i.e. last run < its cadence ago), name +
meta line (count · schedule · new%), `fresh`/`suggested` badges, expand →
blend slider, schedule selects, genre chips, size/cap, run/save/delete.
Data: `GET /mixes` (+ `next_runs`); mutations per mix-profiles routes.
"+ NEW MIX" appends an editable blank card.

### 2. Library
Action cards wired to EXISTING routes, with live status where a status route
exists:
- Enrich → `POST /library/enrich`, poll `GET /library/enrich/status` (progress bar)
- Repair → `POST /library/repair`, poll `GET /library/repair/status`
- Dedup → `POST /library/dedup/run` / `POST /library/dedup/report` (count badge)
- Title cleanup → NEW `GET/POST /library/suffixes` (read/write
  `title_suffixes.txt`, list-of-lines semantics, atomic write)
- Import → triggers the existing SoundCloud-likes / Spotify-playlists sync
  (existing root POST flow exposed as a button; no new backend)

### 3. Search (acquire)
One input, three behaviors:
- **Pasted URL** (yt/sc link detected by pattern) → existing download flow.
- **Text query** → merged results from `GET /sc/search/tracks` (exists) and
  NEW `GET /yt/search?q=&limit=` (yt-dlp `ytsearchN:` flat extraction:
  title/uploader/duration/id only — no download).
- Source filter pills: all / soundcloud / youtube.
Each result row: source tag, title/artist/duration, `+` acquire button →
NEW `POST /acquire {"url": ...}` which routes into the existing download +
title-cleanup + scan pipeline and returns the acquired path. Rows whose
artist+title already exist in Navidrome (`song_exists`) render the ✓ "have"
state instead.

### 4. Setup
The schema-driven settings renderer (already implemented this cycle) restyled
into SIGNAL collapsible groups; group save semantics unchanged
(changed-fields-only POST to `/settings`). Credentials warning banner kept.

## Backend additions (small)

- `GET /yt/search` — yt-dlp flat search, 10s timeout, returns
  `{results: [{source:"yt", title, artist, duration, url}]}`; errors → empty
  list + `"error"` field, never 500 for upstream failures.
- `POST /acquire` — body `{url}`; validates scheme/host (http/https only,
  yt/sc hosts), reuses the existing download function used by root POST;
  returns `{status, path}`; 409 via lock if an acquire for the same URL is
  already running.
- `GET/POST /library/suffixes` — text lines ↔ JSON list, atomic write.
- Everything else consumes existing routes.

## Migration & rollout

1. Ship `app.html` at `/` with all four screens; `/explore` untouched.
2. Manual parity checklist (each legacy tab's function reachable in new UI).
3. Follow-up commit removes explore.html/js/css and the `/explore` route.
The mockup files under `web/mockups/` and `web/static/mockups/` are design
artifacts — kept in git, excluded from any parity requirements.

## Testing

- Route tests: `/yt/search` (mocked yt-dlp; upstream error → empty results),
  `/acquire` (URL validation matrix: non-http scheme rejected, unknown host
  rejected, happy path mocked download), `/library/suffixes` round-trip +
  atomic write.
- JS is still framework-free; UI verified by the parity checklist (no JS test
  harness exists in this repo and adding one is out of scope).

## Out of scope

- Auth (unchanged stance).
- PWA/offline manifest.
- Spotify metadata search in the Search screen (URL import for Spotify stays
  in Library → Import; revisit after mix profiles land).
- Removing the legacy explore page (separate follow-up once parity confirmed).
