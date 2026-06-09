# Discover Addon — Design Spec

- **Date:** 2026-06-09
- **Status:** Design approved in brainstorming; pending user review of this doc
- **Component:** new `discover` addon inside `aMusicServerTemplate`, served by the existing
  stdlib HTTP server (`sWebExt/py_server/server.py`)

---

## 1. Context & goal

`aMusicServer` is a self-hosting template for people **leaving big streaming services**
(Spotify/Apple/etc.) to own their music library locally. It already downloads tracks from
YouTube / SoundCloud into a folder that **Navidrome** indexes and **Symfonium** (and any
Subsonic client) plays.

The unsolved hard part for that audience is **music exploration**: once you leave the
streaming corp, you lose its recommender. This addon restores exploration **without** depending
on any single corporate recommender — by aggregating the user's *own* cross-platform signals and
turning discoveries into owned, locally-stored music.

### What the user actually wants
1. A **weekly "Weekly Mix" playlist**, generated locally and imported into Navidrome so it
   appears in Symfonium with zero interaction (passive discovery).
2. A **locally-hosted, mobile-first web UI** for active exploration (browse → preview → save),
   reachable from the phone's browser on the home network.
3. **Browse / mirror** public SoundCloud (and, best-effort, Spotify) accounts, their songs, and
   public playlists, and **bulk-import** them into the local library.

### Hard product principles (drive every decision below)
- **Zero extra installs.** The user already has Navidrome + Symfonium + this server. No companion
  Android app, no Tasker requirement, no per-user mandatory cloud login in the core.
- **No hard dependency on any one corporation's API.** Someone exiting Spotify must not be forced
  to keep authing into Spotify forever. External sources are optional/best-effort and isolated.
- **Defensive by default.** The engine must work for a user with *zero* Spotify access.
- **Design for isolation.** Each discovery source is a bounded, independently-testable unit behind
  a common interface; a fragile source breaking must not take down the others.

---

## 2. Verified findings (why the design is shaped this way)

All probed against the user's real services / Spotify's live edge on 2026-06-09. These justify
every "we do / don't use X" decision and should be re-checked if behaviour changes.

### Spotify official Web API — wrong tool, mostly closed for this audience
With a client-credentials token (the no-login path):

| Endpoint | Result | Note |
|---|---|---|
| `search` (artist/track/playlist) | ✅ 200 | works |
| `GET /artists/{id}` | ✅ 200 | works |
| `GET /artists/{id}/albums` | ✅ 200 | works |
| `GET /albums/{id}` + `/albums/{id}/tracks` | ✅ 200 | works |
| `GET /artists/{id}/top-tracks` | ❌ 403 | restricted (dev mode) |
| `GET /recommendations` | ❌ 404 | **deprecated 2024-11-27, dead** |
| `GET /artists/{id}/related-artists` | ❌ 403 | **deprecated, dead** |
| `GET /artists?ids=` (batch), `/browse/new-releases` | ❌ 403 | restricted |
| `GET /users/{id}/playlists` | ❌ 403 | can't list a profile's playlists |
| `GET /playlists/{id}` (metadata) | ✅ 200 | name/cover/owner only |
| `GET /playlists/{id}/tracks` | ❌ 403 | **can't read playlist contents** |
| editorial playlist (e.g. Today's Top Hits) | ❌ 404 | Spotify-owned blocked |
| track `preview_url` | `null` | no 30s previews |

**Conclusion:** the official API cannot give discovery (recs/related dead), cannot read playlists
without user OAuth, and never streams full audio (DRM). A *user OAuth* token would unlock
playlist reads but reintroduces a mandatory login we deliberately reject for this audience, and
still 404s on editorial and can't stream. → **We do not use the official Spotify API.**

### Spotify web surface — has *more* than the API, login-free
The logged-out `open.spotify.com` web player calls an **internal partner API**
(`api-partner.spotify.com` GraphQL): `queryArtistOverview` returns the artist's **Popular/top
tracks AND "Fans also like" related artists** (the exact things the official API forbids), and
`fetchPlaylist` returns public playlist tracklists. → **We harvest the web surface instead.**

But minting the anonymous token now needs a real browser:

| Probe | Result |
|---|---|
| `get_access_token` via naive curl | ❌ 403 "URL Blocked" (CDN edge-block) |
| artist page static HTML | 200 but **no token embedded** (hydrated client-side) |
| `/server-time` (TOTP time-sync) | 404 (moved) |

→ **"Selenium harvests, curl serves"**: a headless browser mints/refreshes the token only;
all data calls are plain `requests`. Identical to the existing SoundCloud `client_id` pattern.

### Navidrome (Subsonic) — discovery graph available for free
Probed against the user's real Navidrome:

- `getArtistInfo2.view?includeNotPresent=true` → **20 similar artists**, all `id:-1`,
  `albumCount:0` → artists the user does **not** own. The Last.fm agent is configured and already
  yields a discovery graph. → **Canonical similarity reuses Navidrome; we build no Last.fm/
  ListenBrainz integration.**
- `getSimilarSongs2.view` → **0 songs**. Navidrome's radio is bounded to owned music and is empty
  for true discovery. → **The acquisition step (resolve → download → assemble) is exactly the gap
  this addon fills; Navidrome structurally cannot do it.**

### SoundCloud — full capability, plumbing already present
The project already harvests a SoundCloud `client_id` via Selenium (auto-refresh on 401). The v2
endpoints (`/resolve`, `/users/{id}/followings`, `/likes`, `/users/{id}/tracks`,
`/users/{id}/playlists`, `/tracks/{id}/related`) are the standard web-client API and provide
follows/reposts/likes/related (discovery) **and** full profile/playlist mirror **with real
streamable audio**. Live-confirmation was blocked only because the stored `client_id` was expired
(401 on a public control resolve) — the expected, auto-recoverable condition.

### Symfonium API — control only, not a UI/data channel
Symfonium 1.7.0+ exposes an Android **broadcast receiver** (commands: play, `MEDIA_SYNC`,
`MEDIA_START`, etc.). It is **phone-app → Symfonium**, fire-and-forget, **no return data** — it
cannot host an exploration UI or fetch suggestions, and our server (not on the phone) cannot send
broadcasts. Using it requires an on-phone sender (Tasker/MacroDroid/companion app) = an extra
install. → **Out of the core; optional power-user polish only.**

---

## 3. Architecture

**One engine. Two interaction modes. One tab per source. Zero extra installs.**

```
 MODE 1 — DISCOVERY (pushed at you)          MODE 2 — BROWSE / MIRROR (you pull a URL)
 ┌─ Canonical   (Navidrome getArtistInfo2)   ┌─ SoundCloud  (paste profile/playlist URL →
 ├─ Spotify-web (queryArtistOverview:         │   tracks + public sets, full playback)
 │   "Fans also like" + top tracks)           ├─ Spotify-web (artist→songs, public playlists,
 ├─ SoundCloud  (follows/reposts/related)     │   via web harvester; no in-browser playback)
 └─ YouTube     (followed channels→uploads)   └─ CSV import (exportify) — existing path
                          │
   every candidate item:  ▶ preview in-browser   ♥ save / bulk-import
                          │                            │
                          │                   existing yt-dlp pipeline ─► song_dir
                          │                            │
                          │                   Navidrome rescan ─► createPlaylist/updatePlaylist
                          │                            │
                          └──────────────────► Weekly Mix playlist ─► Symfonium
```

### Common contract — `DiscoverySource` interface
Every lens/source is an isolated unit implementing one interface:

- **input:** the user's identity/seeds for that platform (owned artists; a SoundCloud profile; a
  channel list; a Spotify URL).
- **output:** a list of `Candidate` records:
  `{ artist, title, source, preview_ref, download_ref, art, why }`
  - `preview_ref` — a streamable URL for in-browser audition (SoundCloud/YouTube); may be null
    (Spotify-web: no playback — display + save only).
  - `download_ref` — what the existing yt-dlp pipeline needs to acquire the track.
  - `why` — provenance string ("Fans also like Aphex Twin", "reposted by …") for UI + debugging.

The web UI renders **one tab per registered source**; the engine treats them uniformly. Adding a
source = implementing the interface + registering it. A source that errors degrades to an empty
tab with a diagnostic, never a crash.

---

## 4. Components (each one bounded, single-purpose, independently testable)

| # | Unit | Input → Output | Depends on | Phase |
|---|---|---|---|---|
| 1 | `seeds` | — → ranked owned artists | Navidrome Subsonic (most-played/starred) | 1 |
| 2 | `expand_canonical` | seeds → scored not-owned artists | Navidrome `getArtistInfo2` | 1 |
| 3 | `resolve` | artist → ranked candidate tracks | source-specific (see below) | 1 |
| 4 | `filter` | candidates → fresh candidates | Subsonic search + `discover_state.json` | 1 |
| 5 | `acquire` | candidate → file on disk | **existing** yt-dlp pipeline | 1 |
| 6 | `assemble` | track IDs → "Weekly Mix" | Subsonic `createPlaylist`/`updatePlaylist` | 1 |
| 7 | `schedule` | trigger 1→6 weekly | existing background timer in `server.py` | 1 |
| 8 | `webui` | engine → mobile PWA (browse/preview/save) | components 1–5 | 2 |
| 9 | `source_soundcloud` | identity/URL → candidates + mirror | existing SC `client_id` machinery | 3 |
| 10 | `source_spotify_web` | identity/URL → candidates + mirror | Selenium token harvest + `requests` | 3* |
| 11 | `source_youtube` | channel list → new-upload candidates | yt-dlp | 4 |
| 12 | `symfonium_notify` (opt) | outbound POST after acquire | user's phone relay (Tasker) | 5 |

\* The Spotify-web source is built in Phase 3 alongside SoundCloud because it carries both a
discovery lens (Fans-also-like) and a mirror source; it can be deferred without blocking 1–2.

### Track resolution (`resolve`) strategy, by source
- **Canonical (Navidrome similar artist, `id:-1`):** the artist isn't owned, so we need their
  popular tracks. Primary = **Spotify-web `queryArtistOverview`** top tracks (login-free, via
  harvester). Fallback (no harvester) = **yt-dlp search** ranking (`"<artist> topic"` / popular).
- **Spotify-web:** tracks come straight from `queryArtistOverview` / `fetchPlaylist`.
- **SoundCloud:** tracks come straight from the v2 endpoints (already have stream + download refs).
- **YouTube:** uploads from followed channels (yt-dlp).

### The Spotify-web harvester (`source_spotify_web`) — fragility isolated
```
[browser, rarely]  Selenium opens open.spotify.com → mints/refreshes anonymous token (intercepted)
[curl, mostly]     requests → api-partner GraphQL:
                     queryArtistOverview → top tracks + "Fans also like" (related artists)
                     fetchPlaylist       → public playlist tracklist
                   on 401 / token expiry → one browser re-harvest  (== SoundCloud 401→Selenium)
```
- Stack: **Selenium** (reuse existing dependency + the SoundCloud harvesting pattern), browser
  used only for the token; `requests` for all data.
- Lives as its own bounded sub-module (mirrors `scripts/Sc2Sp` structure). If Spotify changes the
  token mechanism or GraphQL ops, only this module breaks; Navidrome + SoundCloud + YouTube keep
  working. Clearly labelled "best-effort, may need updates."
- **No in-browser playback of Spotify audio** (DRM) — Spotify items are display + save (download
  the resolved match) only.

---

## 5. Web UI / PWA (Phase 2)

- Served by the existing stdlib server at e.g. `http://homeserver.local:5000/discover`.
- **Mobile-first**, installable as a **PWA** ("Add to Home Screen") so it feels like an app next
  to Symfonium without being an installed app.
- Per source: a tab/page (the user's "separate page/table per source") listing candidate
  artists/tracks/playlists with art, `why`, **▶ in-browser preview** (SoundCloud/YouTube stream;
  Spotify = no preview), and **♥ save** (single) / **bulk-import** (playlist/profile).
- **Listen vs save are decoupled:** preview plays instantly in the tab (no download, no app
  switch); save is a background action whose payoff (a playable, owned file) shows up in Symfonium
  on its next sync. The user never bounces to Symfonium to *evaluate* a track.
- New server routes (additive to `server.py`):
  `GET /discover/preview` (run a source on demand), `POST /discover/run` (trigger engine/weekly),
  `POST /discover/save` (acquire one or many).

---

## 6. Weekly Mix & scheduling (Phase 1)

- The `schedule` unit runs the engine weekly (reusing the server's existing background timer),
  blends candidates across enabled sources, `filter`s out already-owned and previously-suggested
  items, `acquire`s a configurable count, and `assemble`s/updates a Navidrome playlist
  **"Weekly Mix"**. Optional per-source mixes later ("SoundCloud Weekly", etc.).
- Idempotent: `discover_state.json` records suggested items so weekly mixes don't repeat.
- After acquisition, a Navidrome rescan is triggered; Symfonium picks the playlist up on normal
  sync (or pull-to-refresh). No phone-side automation required.

---

## 7. Config & state

New **optional** keys in `config.json` (all absent = feature simply off / degraded gracefully):

```jsonc
{
  "discover": {
    "enabled": true,
    "weekly_count": 30,
    "schedule": "weekly",
    "seed_source": "library",          // library | (future: spotify_bootstrap)
    "sources": ["canonical"],          // + "spotify_web" | "soundcloud" | "youtube"
    "youtube_channels": [],            // user-maintained channel URLs (Phase 4)
    "soundcloud_profile": "",          // for the SoundCloud discovery lens (Phase 3)
    "playlist_name": "Weekly Mix"
  }
}
```

- Reuses existing `navidrome_url/user/pass` and `sc_client_id` (+ its refresh machinery).
- `discover_state.json` (next to `config.json`): suggested-item history for dedupe/no-repeat.
- No Spotify API keys; no OAuth tokens (we abandoned the official API).

---

## 8. Error handling / graceful degradation

- **No Spotify-web harvester / it breaks:** canonical resolve falls back to yt-dlp search; Spotify
  tabs show a diagnostic; everything else works.
- **Navidrome Last.fm agent not configured:** `expand_canonical` detects empty similar-artists and
  surfaces a one-line "enable Navidrome's Last.fm agent" hint instead of failing silently.
- **SoundCloud `client_id` expired (401):** trigger existing Selenium refresh, then retry.
- **yt-dlp acquisition failure:** logged per-track, skipped, doesn't abort the batch.
- Every external call is wrapped; one source's failure never crashes the engine or another source.

---

## 9. Testing strategy

- Each component is unit-tested with **mocked HTTP** (recorded Navidrome / SoundCloud / Spotify-web
  responses) — they're isolated by the `DiscoverySource` interface.
- `filter`/`assemble`/state logic tested against an in-memory fake of the Subsonic + state layers.
- One optional **live smoke test** against the user's real Navidrome.
- **Early spikes (unverifiable in design):** (a) Spotify-web token harvest + current `queryArtist
  Overview`/`fetchPlaylist` op shapes; (b) SoundCloud endpoints with a freshly-harvested
  `client_id`. Both are flagged risks, not assumptions.

---

## 10. Phasing

| Phase | Deliverable | MVP |
|---|---|---|
| **1** | Engine + **Canonical** lens + **Weekly Mix** playlist (headless) | ✅ |
| **2** | Mobile **web UI / PWA** + in-browser preview + save (canonical) | ✅ |
| **3** | **SoundCloud** lens + mirror **and** **Spotify-web** harvester (lens + mirror) | ✅ |
| **4** | **YouTube** lens (followed channels) | — |
| **5** | *(optional)* Symfonium broadcast automation (instant sync / play shortcuts) | — |

**MVP = Phases 1–3.** Phases 4–5 are future work.

---

## 11. Out of scope / YAGNI (explicitly not building now)

- Official Spotify API integration (client-creds or OAuth) — abandoned; web harvester replaces it.
- In-browser playback of Spotify audio (DRM-impossible).
- A companion Android app or required Tasker setup.
- Remote (outside-home) access — `homeserver.local` is home-network only; Tailscale/VPN is a
  later, separate concern.
- ListenBrainz/Last.fm direct integration — canonical similarity comes free via Navidrome.

---

## 12. Naming

Working name: **"Discover"** addon; module `addons/discover/`; default playlist **"Weekly Mix"**.
Names are non-binding and can change during implementation.
