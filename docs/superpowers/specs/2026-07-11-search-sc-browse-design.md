# Search Polish + SoundCloud Browse — Design Spec

**Date:** 2026-07-11
**Status:** Approved for planning
**Scope:** Batch A of three (B = genre-tag rethink, C = SC account recommendations — both out of scope here)

## Problem

Four rough edges in the Search screen, found after a month of daily use:

1. YouTube search results show a plain `YT` text badge instead of the video thumbnail, even though the render slot for artwork exists and yt-dlp already returns thumbnail data.
2. Opening a SoundCloud artist profile is broken: the URL is built from the display `username` (`app.js:872`) instead of the permalink slug, which the backend never captures. There is also no actual external link anywhere — the chip only resolves tracks inline.
3. An artist's playlists are fetched by the backend on every resolve (`get_profile` returns `sets`) but the UI silently drops them. Likes have a backend method (`get_user_likes`) with no route and no UI.
4. No search result row can be opened externally (no link to the YouTube video or SoundCloud track page), even though the URLs are already in the item data.

## Design

### 1. YouTube thumbnails

`/yt/search` (`sWebExt/py_server/server.py:1785-1791`) adds `artwork_url` to each returned entry:

- Prefer the best thumbnail from the yt-dlp flat-playlist entry (`thumbnail` field or last element of `thumbnails`).
- Fall back to `https://i.ytimg.com/vi/{id}/hqdefault.jpg` derived from the video id (already available; used at line 1790 for the watch URL).

Frontend: the YT merge branch (`web/static/app.js:959-965`) passes `artwork_url` through. The existing artwork slot in `buildResultRow` (`app.js:778-784`) renders it unchanged.

### 2. SoundCloud permalink capture (profile fix)

`_user_from_raw` (`soundcloud/mirror.py:31-39`) adds two fields from the raw API user object:

- `permalink` — the URL slug
- `permalink_url` — the full profile URL

All profile links use `permalink_url` directly. The broken reconstruction from display name (`app.js:872`) is removed. `/sc/resolve` internal calls keep working as today.

### 3. External-link icons

- Every track result row gets a small `↗` anchor (`target="_blank" rel="noopener"`) next to the `+` acquire button, rendered in `buildResultRow`:
  - SC rows: `permalink_url` (already present in `_track_from_raw`, `mirror.py:24`).
  - YT rows: the watch `url`.
  - Rows with no URL render no icon.
- The artist panel header gets a `↗ SC` anchor built from the user's `permalink_url`.

### 4. Artist tabbed panel (in-app browse)

`buildArtistChip`'s expansion (`app.js:833-904`) becomes a three-tab panel: **Tracks / Playlists / Likes**. Each tab lazy-loads on first open; content is cached in the panel for the session.

```
┌─ DJ Foobar ──────── [↗ SC] ─┐
│ [Tracks] [Playlists] [Likes] │
│ ▸ Deep Cut One      3:41 [+][↗]
│ ▸ Another Track     6:02 [+][↗]
└──────────────────────────────┘
```

- **Tracks** — current behavior, unchanged data path (`/sc/resolve`, reads `data.tracks`).
- **Playlists** — renders the `sets` array that `/sc/resolve` already returns (`get_profile`, `mirror.py:85-94`): title + track count per row. Clicking a playlist expands its tracks in place via a new route:
  - `GET /sc/set/<id>/tracks` → wraps existing `get_set_tracks` (`mirror.py:68`).
- **Likes** — new route:
  - `GET /sc/user/<id>/likes?limit=50` → wraps existing `get_user_likes` (`mirror.py:73-82`). Fixed default limit of 50; no pagination in this batch.
- All tab content rows render through the same `buildResultRow`, so `+` acquire and `↗` behave identically everywhere.

### Error handling

New `/sc/*` routes follow the existing pattern (`server.py:1902-1949`):

- 503 while the SC client id is not ready (`_sc_client_ready` flag).
- 502 on upstream SoundCloud failure.
- Tabs show the panel's existing muted empty/error text (e.g. "no playlists", "couldn't load likes").

### Testing

- `tests/soundcloud/test_mirror.py`: `_user_from_raw` captures `permalink`/`permalink_url`; absent fields degrade to `None`.
- `tests/server/test_routes.py`: `/sc/set/<id>/tracks` and `/sc/user/<id>/likes` — happy path (mocked SC client), not-ready 503, upstream-failure 502, limit clamping.
- `/yt/search` mapping test: thumbnail taken from entry; id-derived fallback used when the entry has none.
- Frontend: no JS test harness exists in this repo; UI verified manually. Out of scope to introduce one here.

## Out of scope

- SC account auth / personalized recommendations (Batch C).
- Any genre/mix changes (Batch B).
- Likes pagination or cross-session caching.
- Dedicated artist screen (rejected in favor of the inline panel).
