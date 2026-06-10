# Design: SoundCloud Mirror, Spotify-Web Harvester, Explore UI & Share

**Date:** 2026-06-10
**Status:** Approved

---

## Overview

Four interconnected features built on top of the existing Last.fm integration:

1. **Flask migration** — replace stdlib HTTP server with Flask; foundation for all new routes and the web UI
2. **`soundcloud/` package** — SC profile/set mirror, tabbed search, discovery lens feeding the Weekly Mix
3. **`spotify/` package** — TOTP-based anonymous token, auto-refreshing cipher + query hashes, `queryArtistOverview` + `fetchPlaylist`
4. **Unified explore + share import UI** — one web page, three tabs (SoundCloud, Spotify, Import Share), shared track list component with in-browser preview and selective/batch download
5. **Share codec + hostname** — fixed `amusicserver.local` hostname via `zeroconf` mDNS + hosts file; self-contained share URLs that work on any receiver's home network

---

## 1. Flask Migration

### Why

The stdlib `HTTPServer` + `BaseHTTPRequestHandler` cannot serve static files or Jinja2 templates cleanly. Flask replaces it with minimal disruption — all existing routes migrate 1:1, no behaviour changes.

### New dependencies

```
flask        — web framework + Jinja2 (bundled)
flask-cors   — CORS for browser extension compatibility (chrome-extension:// origin)
zeroconf     — pure-Python mDNS for amusicserver.local LAN advertisement
```

### Route inventory

**Existing routes (migrated unchanged):**

```
POST  /                        — download dispatcher (extension sends URL here)
GET   /playlists               — Navidrome playlist proxy for extension
POST  /discover/run            — trigger weekly mix
POST  /discover/playlist_mix   — playlist-based mix
POST  /library/dedup/run
POST  /library/dedup/report
POST  /library/enrich
GET   /library/enrich/status
```

**New routes added in this spec:**

```
GET   /explore                     — serves the explore web UI (Jinja2)

GET   /sc/resolve?url=             — resolve SC URL → entity + tracks + sets
GET   /sc/search/users?q=          — SC profile search
GET   /sc/search/tracks?q=         — SC track search
GET   /sc/preview?stream_url=      — return SC stream URL with client_id for <audio>

POST  /spotify/artist              — queryArtistOverview → top tracks + related artists
POST  /spotify/playlist            — fetchPlaylist → playlist tracks
GET   /spotify/search?q=           — search Spotify artists by name

GET   /preview?source=&url=&artist=&title=   — universal preview URL resolver
POST  /import/tracks               — queue tracks for download + M3U creation
GET   /import/status?job_id=       — queue progress polling

GET   /share/link?artist=&title=&url=        — generate single-track share URL
GET   /share/code?playlist_id=               — generate playlist share text block
POST  /share/parse                           — parse share link or text → track list
GET   /share/import?v=1&d=                   — receive shared link → load explore UI
```

### Background threads

All existing background threads (SC client_id refresh, weekly discover loop, dedup scheduler, enrich) are unchanged — they are independent of the HTTP layer and carry over verbatim into the Flask entry point.

### Browser extension compatibility

The extension sends `POST /` and `GET /playlists`. Both routes migrate unchanged. `flask-cors` is applied globally so `chrome-extension://` requests continue to work without modification.

### Project structure after this spec

```
aMusicServerTemplate/
├── lastfm/           (built — Last.fm integration)
├── library/          (built — scanner, dedupe, tagger, enrich)
├── discover/         (built — weekly mix engine)
├── soundcloud/       (new)
├── spotify/          (new)
├── share/            (new)
├── web/
│   ├── templates/
│   │   └── explore.html     (Jinja2)
│   └── static/
│       ├── explore.js
│       └── explore.css
└── sWebExt/py_server/server.py    (Flask rewrite)
```

---

## 2. `soundcloud/` Package

```
soundcloud/
├── __init__.py
├── client.py      — SC v2 API wrapper, client_id injection, 401 → Selenium refresh
├── mirror.py      — resolve URL → entity, fetch profile tracks + sets, fetch set tracks
├── search.py      — search users, search tracks
└── discovery.py   — follows / reposts / related → Weekly Mix candidates
```

### `client.py`

Wraps `https://api-v2.soundcloud.com/`. Injects `client_id` (from config) on every request as a query parameter. On 401: calls the existing `fetch_client_id_via_selenium` from `scripts/Sc2Sp_src/script_web.py`, persists the new ID to `config.json`, retries the original request once. All other modules receive an `SCClient` instance — nothing reads config directly.

### `mirror.py`

**Primitives:**

```python
resolve(client, url) → SCEntity
  # GET /resolve?url=<url>
  # Returns: {kind: "user"|"playlist"|"track", id, name, artwork_url}

get_user_tracks(client, user_id, limit=200) → list[Track]
  # GET /users/{id}/tracks

get_user_sets(client, user_id, limit=50) → list[Playlist]
  # GET /users/{id}/playlists

get_set_tracks(client, playlist_id) → list[Track]
  # GET /playlists/{id}
```

**Composed entry point:**

```python
get_profile(client, url) → {user: User, tracks: list[Track], sets: list[Playlist]}
  # resolve(url):
  #   kind=user     → get_user_tracks + get_user_sets
  #   kind=playlist → get_set_tracks (returned as single set)
  #   kind=track    → single-item tracks list, empty sets
```

**Data shapes:**

```python
Track:    {id, title, artist, stream_url, permalink_url, artwork_url, duration_ms, source: "sc"}
Playlist: {id, title, track_count, artwork_url, tracks: list[Track]}
User:     {id, username, full_name, avatar_url, track_count, followers_count}
```

**Profile scope — tracks + sets only (B):** Reposts and likes are excluded from mirror results. They remain available internally for the discovery lens only.

### `search.py`

```python
search_users(client, query, limit=10)    → list[User]   # GET /search/users?q=
search_tracks(client, query, limit=20)   → list[Track]  # GET /search/tracks?q=
```

### `discovery.py` — Weekly Mix integration

```python
get_followings(client, user_id, limit=100)  → list[User]   # GET /users/{id}/followings
get_reposts(client, user_id, limit=100)     → list[Track]  # GET /users/{id}/reposts
get_related(client, track_id, limit=20)     → list[Track]  # GET /tracks/{id}/related
to_candidates(tracks)                       → list[Candidate]
  # maps SC tracks to the Candidate format the discover engine expects:
  # {artist, title, source: "soundcloud", preview_ref: stream_url, download_ref: permalink_url, why}
```

**Integration with `discover/expand.py`:** Gains an optional `soundcloud_client` parameter. When set (SC username configured in config), calls `get_related` on seed tracks and `get_followings` on the configured SC username — results merged and scored alongside Last.fm candidates before the acquire step.

### Preview

SC stream URLs only need `?client_id=<id>` appended. The `/sc/preview` route returns the composed URL; the browser plays it directly via `<audio src="...">`. No server-side proxying — SC stream URLs do not expire on the timescale of a browsing session.

---

## 3. `spotify/` Package

```
spotify/
├── __init__.py
├── cipher.py     — TOTP cipher fetch, computation, version tracking
├── hashes.py     — JS bundle extraction, persisted query hash cache
├── client.py     — token lifecycle, three-layer refresh cascade, GraphQL calls
└── queries.py    — queryArtistOverview, fetchPlaylist, artist search
```

### Background: what the live probe found (2026-06-10)

All findings probed against live Spotify endpoints. Key facts that drive this design:

- `/get_access_token` is permanently Varnish-blocked (dead since March 2025). **Selenium is not needed.**
- New token endpoint: `GET open.spotify.com/api/token?totp=<6digits>&totpVer=<ver>&ts=<ts>` → opaque bearer token, ~30 min TTL.
- TOTP cipher: ~26 bytes embedded in Spotify's versioned JS bundle; rotates every few days; community-maintained fallback repo exists.
- `queryArtistOverview` and `fetchPlaylist` operation names are stable across 2023–2026; their **persisted query hashes rotate with every JS deploy** — hardcoding them will break.
- `fetchPlaylist` now requires `enableWatchFeedEntrypoint: false` — omitting it returns 400.
- Every token response contains `_notes: "Usage of this endpoint is not permitted..."` — Spotify is aware. At personal/self-hosted scale this has not triggered enforcement; the module is clearly labelled best-effort.
- No preview URLs anywhere in any response — confirmed absent.

### `cipher.py`

The TOTP cipher byte array is embedded in Spotify's versioned JS bundle. Two sources tried in order:

```
Primary:  GET open.spotify.com
          → parse <script src="/cdn/build/web-player/web-player.*.js">
          → fetch JS bundle
          → regex extract cipher bytes array + version number

Fallback: GET raw.githubusercontent.com/xyloflake/spot-secrets-go/main/secrets/secretDict.json
          → latest cipher version + bytes
```

TOTP computation (pure Python — `hmac`, `hashlib`, no external library):

```
1. XOR each cipher byte with (index % 33 + 9)
2. Join transformed bytes as decimal string → "51321..."
3. UTF-8-encode → hex-encode → use raw bytes as HMAC-SHA1 key
4. Counter = floor(server_time / 30)
5. Standard RFC 6238 6-digit truncation
```

Exports: `get_cipher() → (bytes, version_int)`, `compute_totp(cipher_bytes, server_time) → (str, int)`

In-memory cache; `refresh()` re-fetches from primary then fallback.

### `hashes.py`

Persisted query hashes are embedded in the same JS bundle:

```
Pattern: new tM.l("queryArtistOverview","query","<sha256hash>",null)
```

Fetches and caches all operation name → SHA256 hash mappings in one bundle parse. `refresh()` re-fetches the current bundle (triggered on `PersistedQueryNotFound`).

Exports: `get_hash(operation_name) → str`, `refresh()`

### `client.py` — three-layer refresh cascade

```
GraphQL call to api-partner.spotify.com
  → 401                    → re-mint token (TOTP) → retry once
  → PersistedQueryNotFound → hashes.refresh() + re-mint token → retry once
  → cipher failure         → cipher.refresh() (fallback URL) → re-mint → retry once
  → 429 rate limit         → backoff 30s → retry once → surface wait message

Token minting:
  GET /api/server-time      → unix timestamp
  cipher.compute_totp(...)  → (totp_str, version)
  GET /api/token?totp=<str>&totpVer=<ver>&ts=<ts>
  → opaque bearer token, ~30 min TTL, cached until expiry - 60s safety margin
```

All GraphQL calls go to `https://api-partner.spotify.com/pathfinder/v1/query`.

### `queries.py`

```python
get_artist_overview(client, uri_or_url)
  # queryArtistOverview(uri, locale="", includePrerelease=false)
  # Accepts: spotify:artist:4Z8W... OR open.spotify.com/artist/...
  # Returns: {
  #   top_tracks:      list[Track],    # up to 10
  #   related_artists: list[Artist],   # up to 40 ("Fans also like")
  # }

get_playlist(client, uri_or_url, limit=50)
  # fetchPlaylist(uri, offset=0, limit=50, enableWatchFeedEntrypoint=False)
  # enableWatchFeedEntrypoint=False is required — omitting causes 400
  # Returns: {name: str, tracks: list[Track]}

search_artists(client, query, limit=10)
  # Uses search operation from hashes cache
  # Returns: list[Artist]
```

**Data shapes:**

```python
Track:  {id, uri, title, artist, album, artwork_url, duration_ms, playcount, source: "spotify"}
Artist: {id, uri, name, artwork_url, followers, monthly_listeners}
# preview_url is absent from all Spotify responses — confirmed in live probe
# Spotify rows in UI show "display only" — no ▶ button
```

**Fragility label** (written into module docstring and user-facing docs): this module depends on Spotify's undocumented internal API. The TOTP cipher, access token, and query hashes can all change with any Spotify JS deploy. On failure the module logs a diagnostic and returns empty results — it never crashes the server or other sources. Treat as best-effort.

---

## 4. Share Codec

```
share/
├── __init__.py
└── codec.py     — encode + decode both share formats
```

### Formats

**Single track** — a clickable URL the receiver can open:

```
http://amusicserver.local:5000/share/import?v=1&d=<base64url(JSON)>

JSON payload:
{
  "type": "track",
  "artist": "Aphex Twin",
  "title":  "Windowlicker",
  "url":    "https://youtube.com/watch?v=..."   ← optional; falls back to yt-dlp search
}
```

**Playlist** — a text block sent via any messaging channel:

```
PLAYLIST:Chill Evenings
Aphex Twin|Windowlicker|https://youtube.com/watch?v=abc
Burial|Archangel|https://soundcloud.com/burial/archangel
Coil|The Anal Staircase|
```

- First line: `PLAYLIST:<name>`
- Subsequent lines: `artist|title|url` — third field empty means URL-less (falls back to yt-dlp search)
- Blank lines ignored

### `codec.py` exports

```python
encode_track(artist, title, url=None) → str
  # returns full share URL using configured hostname
  # e.g. "http://amusicserver.local:5000/share/import?v=1&d=eyJ0eXBlIjoic..."

encode_playlist(name, tracks) → str
  # tracks: list of {artist, title, url?}
  # returns the pipe-delimited text block

decode(text_or_url) → {
    "type":   "track" | "playlist",
    "name":   str | None,           # playlist name; None for single track
    "tracks": [{"artist", "title", "url"?}, ...]
}
  # auto-detects format:
  #   URL containing ?d= → single track
  #   text starting with PLAYLIST: → playlist
  #   raises ValueError on unrecognised input
```

### Server routes

| Route | Input | Returns |
|---|---|---|
| `GET /share/link?artist=&title=&url=` | query params | `{"url": "http://amusicserver.local:5000/share/import?..."}` |
| `GET /share/code?playlist_id=` | Navidrome playlist ID | `{"text": "PLAYLIST:...\n..."}` |
| `POST /share/parse` | `{"text": "..."}` | `{type, name?, tracks: [...]}` |
| `GET /share/import?v=1&d=` | base64 payload | Redirects to `/explore#import` with payload pre-loaded |

The `/share/import` route redirects to the explore page with the decoded payload in the URL fragment. The explore JS reads the fragment on load and pre-populates the Import Share tab.

---

## 5. Hostname — `amusicserver.local`

### Why a fixed well-known hostname

Share URLs embed a hostname. It must resolve to the **receiver's** server, not the sender's — because the payload is self-contained data and the receiver's server does the import. A fixed well-known name (`amusicserver.local`) that every user maps to their own server achieves this: the URL is identical for everyone, and it always opens on the receiver's own machine.

### How resolution works

Two mechanisms, both automated by the setup wizard:

**1. Hosts file entry (local machine access)**

The wizard writes one line to the system hosts file — idempotent, checks for existing entry first:

```
127.0.0.1    amusicserver.local
```

| Platform | Hosts file path | Privilege |
|---|---|---|
| Linux / macOS | `/etc/hosts` | `sudo` (wizard prompts once) |
| Windows (exe) | `C:\Windows\System32\drivers\etc\hosts` | Run as Administrator (one-time) |

This makes `http://amusicserver.local:5000` work from any browser on the same machine as the server.

**2. mDNS via `zeroconf` (LAN + VPN device access)**

At server startup, the Flask app registers an mDNS service:

```python
from zeroconf import Zeroconf, ServiceInfo
# advertises amusicserver.local → server's LAN IP, port 5000
# any device on the LAN resolves it without configuration
```

This makes `http://amusicserver.local:5000` work from phones, tablets, and other computers on the same network — including devices connected via Tailscale or WireGuard, since both VPN solutions propagate mDNS/DNS through the tunnel transparently.

### Tailscale and WireGuard compatibility

Both are assumed present (self-hosted music streaming requires remote access to the home server). Both work with this design without any VPN-specific code:

- **Tailscale**: MagicDNS propagates mDNS advertisements across the Tailscale network. `amusicserver.local` resolves correctly from any connected device regardless of physical location.
- **WireGuard**: Standard configs route DNS through the tunnel (`DNS =` directive). The home network's Avahi/mDNS resolver handles `.local` names, which flow through the tunnel to connected devices.

No VPN-specific code in the server. The server advertises on the network; the VPN handles the rest.

### Multi-server households

If two aMusicServer instances share a LAN (edge case), mDNS name conflict occurs. Mitigation:

```jsonc
// config.json — optional override
"hostname": "taichi-music.local"   // default: "amusicserver.local"
```

Setup wizard detects existing `amusicserver.local` mDNS advertisements and offers a custom name if conflict is found.

### Config addition

```jsonc
{
  "hostname": "amusicserver.local"   // optional; default used if absent
}
```

The share codec reads this to build share URLs. The setup wizard writes the correct hosts entry and starts the mDNS service with this name.

---

## 6. Unified Explore + Share Import UI

Served at `GET /explore` as a Jinja2 template. Three tabs sharing one track list component.

### Page structure

```
┌─ [SoundCloud]  [Spotify]  [Import Share] ──────────────────────┐
│                                                                 │
│  [ Search or paste URL _______________________ ] [Go]          │
│  (SoundCloud only: sub-tabs [Artist Results] [Track Results])  │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐ │
│  │ ☐  🖼  Artist — Title                [SC]  3:42   [▶]   │ │
│  │ ☐  🖼  Artist — Title                [SP]  4:11   [—]   │ │  ← Spotify: no preview
│  │ ☐  🖼  Artist — Title                [SC]  5:03   [▶]   │ │
│  │ ...                                                       │ │
│  └───────────────────────────────────────────────────────────┘ │
│                                                                 │
│  ▶ ───────────────── 1:24 / 3:42        [persistent player]   │
│                                                                 │
│  Playlist name: [Chill Evenings_______________________________] │
│  3 selected   [Save Selected]   [Save All — 50 at a time ▼]   │
└─────────────────────────────────────────────────────────────────┘
```

### Tab behaviour

**SoundCloud tab:**
- Input accepts: search query OR full SC URL (profile / set / track)
- If URL → calls `GET /sc/resolve` → returns tracks + sets directly
- If search query → two sub-tabs: **Artist Results** (`/sc/search/users`) and **Track Results** (`/sc/search/tracks`)
- Clicking an artist result expands to show their tracks + sets (calls `/sc/resolve` on their profile URL)

**Spotify tab:**
- Input accepts: artist name search OR Spotify artist/playlist URL
- If URL → calls `POST /spotify/artist` or `POST /spotify/playlist` depending on URL shape
- If search query → calls `GET /spotify/search?q=` → shows artist results → click to expand top tracks + related artists
- No audio preview (DRM) — preview column shows `—` for all Spotify rows

**Import Share tab:**
- Large paste field for share link or playlist text block
- Calls `POST /share/parse` → returns track list → renders same track list component
- Pre-populated automatically when arriving via `GET /share/import?d=`

### Track row — shared component

| Field | SoundCloud | Spotify | Share import (SC/YT source) | Share import (unknown) |
|---|---|---|---|---|
| Cover art | ✅ | ✅ | ✅ if URL provided | placeholder |
| Artist — Title | ✅ | ✅ | ✅ | ✅ |
| Source badge | SC | SP | SC / YT | ? |
| Duration | ✅ | ✅ | if available | — |
| Preview ▶ | ✅ | — (display only) | ✅ if SC/YT URL | ✅ after search |
| Checkbox | ✅ | ✅ | ✅ | ✅ |

Status icons replace the checkbox after save: `⏳ queuing` → `⬇ downloading` → `✓ done` → `✗ error`

### Preview flow

All preview calls go through the universal `/preview` route:

```
User clicks ▶ on a track row
  → GET /preview?source=sc&url=<stream_url>
        → server appends client_id → returns {stream_url}
  → GET /preview?source=yt&url=<yt_url>
        → server calls yt-dlp --get-url → returns {stream_url}  (~1-2s)
  → GET /preview?source=unknown&artist=X&title=Y
        → yt-dlp search → first result → returns {stream_url}   (~3-5s)
  → response null (timeout / not found)
        → ▶ button disabled, tooltip "preview unavailable"

Browser sets <audio>.src = stream_url → plays in persistent bottom player
Clicking ▶ on another row replaces current track (no queue)
```

### Download and progress

```
[Save Selected] or [Save All]
  → POST /import/tracks
    body: {
      tracks: [{artist, title, url?, source}],
      playlist_name: "Chill Evenings",
      batch_size: 50       ← Save All sends 50 at a time
    }
  → returns {job_id, queued: N}

UI polls GET /import/status?job_id=<id> every 2s while job active
  → {total, done, errors, tracks: [{title, status}]}
  → each row updates its status icon in place

Save All: when batch N completes, JS automatically POSTs the next 50
          until all tracks are queued. Running count shown in bottom bar.
```

The import backend reuses the existing acquire pipeline: SC `permalink_url` → SC download script; YouTube/unknown URL → yt-dlp; no URL → yt-dlp search by `artist title`. The resulting files are added to a new or existing Navidrome playlist named after the `playlist_name` field.

---

## 7. WOAS Source URL Tag

### What and why

ID3's `WOAS` (Official Audio Source) frame stores the origin URL of the audio file — exactly designed for this purpose. Writing it at download time closes the share-quality loop:

- When sharing a track already in the library, the server reads `WOAS` from the MP3 and puts the **exact** source URL directly into the share payload. No yt-dlp search, no version ambiguity, no studio-vs-live mismatch.
- Solves the SoundCloud-only track problem: if `WOAS` is `soundcloud.com/burial/archangel`, the share codec carries it, the receiver's SC pipeline handles it correctly.

`WOAS` is part of the ID3 spec, visible in most tag editors, and read by Navidrome. It is the right field — not `WXXX` (user-defined) which is more flexible but less standard.

### Where it is written

`library/tagger.py` already touches every file post-download via `eyed3`. One additional call writes `WOAS`:

```python
def write_source_url(path: str, url: str) -> None:
    tag = eyed3.load(path)
    if tag and tag.tag and url:
        tag.tag.frame_set['WOAS'] = eyed3.id3.frames.UrlFrame('WOAS', url)
        tag.tag.save()
```

The three download pipelines already know the source URL at download time — passing it through to `write_source_url` costs nothing:

| Pipeline | Source URL available at |
|---|---|
| `scripts/sTownload/script_web.py` | The YouTube/SC URL passed as argv[1] |
| `scripts/Sc2Sp_src/Sc2Sp/script2.py` | The SoundCloud permalink URL |
| `discover/acquire.py` | `candidate.download_ref` |

### Caveat

YouTube URLs can be taken down or expire. `WOAS` is a best-effort hint. The share codec falls back to yt-dlp search if the stored URL returns a 404 on the receiver's side — the receiver's server attempts the stored URL first, falls back to `"artist title"` search if it fails.

### File changes (additions to Section 9)

| Action | Path |
|---|---|
| Modified | `library/tagger.py` — add `write_source_url(path, url)` |
| Modified | `scripts/sTownload/script_web.py` — call `write_source_url` post-download |
| Modified | `scripts/Sc2Sp_src/Sc2Sp/script2.py` — call `write_source_url` post-download |
| Modified | `discover/acquire.py` — call `write_source_url` post-download |
| Modified | `tests/library/test_tagger.py` — add WOAS write test |

---

## 8. Error Handling



### SoundCloud

| Failure | Behaviour |
|---|---|
| `client_id` expired (401) | Trigger Selenium refresh → retry once → if still failing: empty result + log |
| Entity not found (404) | Return `{"status": "not_found"}` → UI: "not found on SoundCloud" |
| Rate limit (429) | Backoff 10s → retry once → log |
| Network timeout | Empty result + log |

### Spotify

| Failure | Behaviour |
|---|---|
| TOTP cipher stale | Re-fetch from JS bundle → fallback to cipher repo → if both fail: disable Spotify tab with diagnostic |
| `PersistedQueryNotFound` | Re-extract hashes + re-mint token → retry once → if still failing: disable Spotify tab |
| `401` on GraphQL | Re-mint token → retry once |
| `429` rate limit | Backoff 30s → retry once → surface wait message in UI |
| Any unhandled Spotify failure | Empty result, Spotify tab shows "unavailable — check server log" |

### Preview

| Failure | Behaviour |
|---|---|
| `yt-dlp --get-url` timeout | Return `{preview: null}` → ▶ button disabled |
| SC stream URL with stale `client_id` | Trigger client_id refresh → return refreshed URL |

### Import / download

| Failure | Behaviour |
|---|---|
| Per-track download failure | Mark row as `error`, continue remaining tracks, report count in `/import/status` |
| Navidrome playlist creation fails | Log, tracks still downloaded to `song_dir` |

### `zeroconf` / hostname

| Failure | Behaviour |
|---|---|
| mDNS bind conflict | Log warning, skip mDNS — hosts file entry still handles local access |
| Hosts file write fails (insufficient privilege) | Wizard prints manual instruction: "Add `127.0.0.1  amusicserver.local` to your hosts file" |

---

## 9. Testing

### New test suites

```
tests/soundcloud/
├── __init__.py
├── test_client.py      — 401 refresh trigger, retry logic
├── test_mirror.py      — resolve entity type, get_profile composition
├── test_search.py      — user search, track search
└── test_discovery.py   — to_candidates mapping, Weekly Mix integration

tests/spotify/
├── __init__.py
├── test_cipher.py      — TOTP computation, primary + fallback fetch, cache
├── test_hashes.py      — JS bundle regex extraction, refresh on PersistedQueryNotFound
├── test_client.py      — three-layer refresh cascade, 429 backoff
└── test_queries.py     — get_artist_overview, get_playlist (enableWatchFeedEntrypoint enforced)

tests/share/
├── __init__.py
└── test_codec.py       — encode/decode roundtrips, missing URL field, malformed input, playlist header

tests/server/
└── test_routes.py      — Flask test client: all new routes return correct shapes + status codes
```

### Patterns

- SC and Spotify tests use `unittest.mock.patch` on `requests.Session.get/post` with recorded fixture responses — same pattern as `tests/discover/` and `tests/lastfm/`.
- Spotify cipher test fixtures include a `PersistedQueryNotFound` response to verify the three-layer cascade triggers correctly.
- Flask route tests use `app.test_client()` — no live network calls, all dependencies mocked.
- `zeroconf` startup verified by checking it is called at server start (mock, not live mDNS broadcast).

---

## 10. File Changes Summary

| Action | Path |
|---|---|
| New | `soundcloud/__init__.py` |
| New | `soundcloud/client.py` |
| New | `soundcloud/mirror.py` |
| New | `soundcloud/search.py` |
| New | `soundcloud/discovery.py` |
| New | `spotify/__init__.py` |
| New | `spotify/cipher.py` |
| New | `spotify/hashes.py` |
| New | `spotify/client.py` |
| New | `spotify/queries.py` |
| New | `share/__init__.py` |
| New | `share/codec.py` |
| New | `web/templates/explore.html` |
| New | `web/static/explore.js` |
| New | `web/static/explore.css` |
| New | `tests/soundcloud/__init__.py` |
| New | `tests/soundcloud/test_client.py` |
| New | `tests/soundcloud/test_mirror.py` |
| New | `tests/soundcloud/test_search.py` |
| New | `tests/soundcloud/test_discovery.py` |
| New | `tests/spotify/__init__.py` |
| New | `tests/spotify/test_cipher.py` |
| New | `tests/spotify/test_hashes.py` |
| New | `tests/spotify/test_client.py` |
| New | `tests/spotify/test_queries.py` |
| New | `tests/share/__init__.py` |
| New | `tests/share/test_codec.py` |
| New | `tests/server/__init__.py` |
| New | `tests/server/test_routes.py` |
| Modified | `sWebExt/py_server/server.py` — full Flask rewrite, all routes migrated + new routes |
| Modified | `discover/expand.py` — optional `soundcloud_client` parameter for SC discovery lens |
| Modified | `setup.py` — add hosts file write step + mDNS service start + hostname config step |
| Modified | `config.example.json` — add `hostname` field |

---

## 11. Out of Scope

- **In-browser Spotify audio** — DRM, impossible. Spotify rows are display + save only.
- **Spotify user OAuth** — deliberately avoided; anonymous token covers all needed endpoints.
- **SC discovery lens in Weekly Mix Phase 1** — SC discovery (`get_followings`, `get_related`) is wired into `discover/expand.py` in this spec but optional; the engine degrades to Last.fm + Navidrome if SC is unconfigured.
- **Deezer / Apple Music / Tidal** — separate future specs.
- **Remote access setup (Tailscale / WireGuard config)** — assumed present, not configured by this project.
- **QR code display** — deferred; the mDNS + share URL design makes it a pure frontend add-on (one JS library, zero backend changes) if desired later.
- **Per-user share permissions** — single server, single user; multi-user is a future concern.
- **Offline import queue** — tracks queued while server is unreachable; deferred.
