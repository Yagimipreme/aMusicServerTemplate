# Search Polish + SoundCloud Browse Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** YouTube thumbnails in search results, working SoundCloud profile links, and an in-app artist panel with Tracks / Playlists / Likes tabs.

**Architecture:** Flask server (`sWebExt/py_server/server.py`) serves JSON routes to a vanilla-JS SPA (`web/static/app.js`). SoundCloud data flows through `soundcloud/mirror.py` mappers shared by search and resolve. Two new read-only routes wrap existing mirror functions; all UI work lives in the Search screen's render helpers.

**Tech Stack:** Python 3 / Flask, pytest, vanilla JS (no framework, no JS test harness), CSS custom properties (`--panel`, `--line`, `--mut`, `--acid`, `--acid-dim`).

**Spec:** `docs/superpowers/specs/2026-07-11-search-sc-browse-design.md`

## Global Constraints

- Error pattern for `/sc/*` routes is the **existing** one (deviates from the spec's 503/502 wording — the spec said "follow the existing pattern", and the actual pattern is): HTTP 200 with `{"status": "connecting", "reason": ..., "retry_after": N}` while the client id is initializing; HTTP 200 with `{"status": "unavailable", "reason": "sc_client_id not configured"}` when no client; HTTP 500 with `{"status": "error", "error": str(e)}` on upstream exceptions. Match `sc_search_tracks` (server.py:1921-1937) exactly.
- All tests run from repo root: `python -m pytest tests/ -q`. Route tests live in `tests/server/test_routes.py` and use its existing `app`/`client` fixtures (threads are patched; module global `_sc_client_ready` defaults False — set it with `monkeypatch.setattr`).
- No new dependencies. No JS test harness — frontend tasks verify via full pytest (regression) plus the manual checks in Task 6.
- Frontend code style: DOM built with `document.createElement`, no innerHTML with user data, classes reuse existing CSS (`result`, `cover`, `r-meta`, `src-row`, `src`, `warn`).
- Commit after every task with the message given in the task.

---

### Task 1: YouTube thumbnails in `/yt/search`

**Files:**
- Modify: `sWebExt/py_server/server.py:1769-1795` (the `/yt/search` route; add a helper above it)
- Test: `tests/server/test_routes.py` (append to the `/yt/search route` section, after `test_yt_search_subprocess_error_returns_empty_not_500`, ~line 795)

**Interfaces:**
- Produces: each `/yt/search` result dict gains `"artwork_url": str` (may be `""`). Task 4's frontend relies on this key name.

- [ ] **Step 1: Write the failing tests**

Append to `tests/server/test_routes.py`:

```python
def test_yt_search_artwork_from_entry_thumbnails(client):
    """artwork_url comes from the last (largest) entry in yt-dlp's thumbnails list."""
    fake_stdout = json.dumps({
        "entries": [
            {"title": "T", "uploader": "A", "duration": 60, "id": "abc123",
             "url": "https://www.youtube.com/watch?v=abc123",
             "thumbnails": [
                 {"url": "https://i.ytimg.com/vi/abc123/default.jpg"},
                 {"url": "https://i.ytimg.com/vi/abc123/maxresdefault.jpg"},
             ]},
        ]
    })
    import subprocess as _sp
    mock_result = _sp.CompletedProcess(args=[], returncode=0, stdout=fake_stdout, stderr="")
    with patch("subprocess.run", return_value=mock_result):
        resp = client.get("/yt/search?q=test")
    r = json.loads(resp.data)["results"][0]
    assert r["artwork_url"] == "https://i.ytimg.com/vi/abc123/maxresdefault.jpg"


def test_yt_search_artwork_falls_back_to_video_id(client):
    """Entries without thumbnail data derive artwork_url from the video id."""
    fake_stdout = json.dumps({
        "entries": [
            {"title": "T", "uploader": "A", "duration": 60, "id": "abc123",
             "url": "https://www.youtube.com/watch?v=abc123"},
        ]
    })
    import subprocess as _sp
    mock_result = _sp.CompletedProcess(args=[], returncode=0, stdout=fake_stdout, stderr="")
    with patch("subprocess.run", return_value=mock_result):
        resp = client.get("/yt/search?q=test")
    r = json.loads(resp.data)["results"][0]
    assert r["artwork_url"] == "https://i.ytimg.com/vi/abc123/hqdefault.jpg"


def test_yt_search_artwork_empty_when_no_id_or_thumbs(client):
    """No thumbnails and no id → artwork_url is empty string, not a broken URL."""
    fake_stdout = json.dumps({
        "entries": [
            {"title": "T", "uploader": "A", "duration": 60,
             "url": "https://www.youtube.com/watch?v=x"},
        ]
    })
    import subprocess as _sp
    mock_result = _sp.CompletedProcess(args=[], returncode=0, stdout=fake_stdout, stderr="")
    with patch("subprocess.run", return_value=mock_result):
        resp = client.get("/yt/search?q=test")
    r = json.loads(resp.data)["results"][0]
    assert r["artwork_url"] == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/server/test_routes.py -k yt_search_artwork -v`
Expected: 3 FAILED with `KeyError: 'artwork_url'`

- [ ] **Step 3: Implement**

In `sWebExt/py_server/server.py`, insert this helper between the `# ── YouTube search ──…` comment (line 1769) and the `@app.route("/yt/search", …)` decorator:

```python
def _yt_thumbnail(entry: dict) -> str:
    """Best-effort thumbnail URL for a yt-dlp flat-playlist entry."""
    thumbs = entry.get("thumbnails") or []
    if thumbs:
        url = (thumbs[-1] or {}).get("url") or ""
        if url:
            return url
    if entry.get("thumbnail"):
        return entry["thumbnail"]
    vid = entry.get("id") or ""
    return f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg" if vid else ""
```

Then add one line to the results dict inside `yt_search` (after the `"url":` line):

```python
        results = [{
            "source": "yt",
            "title": e.get("title") or "",
            "artist": e.get("uploader") or e.get("channel") or "",
            "duration": e.get("duration"),
            "url": e.get("url") or f"https://www.youtube.com/watch?v={e.get('id','')}",
            "artwork_url": _yt_thumbnail(e),
        } for e in (data.get("entries") or []) if e]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/server/test_routes.py -k yt_search -v`
Expected: all 6 yt_search tests PASS (3 new + 3 existing)

- [ ] **Step 5: Commit**

```bash
git add tests/server/test_routes.py sWebExt/py_server/server.py
git commit -m "feat(server): include artwork_url in /yt/search results"
```

---

### Task 2: Capture SoundCloud permalink in `_user_from_raw`

**Files:**
- Modify: `soundcloud/mirror.py:31-39` (`_user_from_raw`)
- Test: `tests/soundcloud/test_mirror.py` (append)

**Interfaces:**
- Produces: every user dict (from `/sc/search/users` via `search_users`, and from `/sc/resolve` via `get_profile`) gains `"permalink": str` and `"permalink_url": str` (empty string when absent). Task 5's frontend relies on these key names.

- [ ] **Step 1: Write the failing tests**

Append to `tests/soundcloud/test_mirror.py`:

```python
def test_user_from_raw_captures_permalink():
    from soundcloud.mirror import _user_from_raw
    user = _user_from_raw({
        "id": 42, "username": "DJ Foo Bar",
        "permalink": "djfoobar",
        "permalink_url": "https://soundcloud.com/djfoobar",
    })
    assert user["permalink"] == "djfoobar"
    assert user["permalink_url"] == "https://soundcloud.com/djfoobar"


def test_user_from_raw_permalink_defaults_empty():
    from soundcloud.mirror import _user_from_raw
    user = _user_from_raw({"id": 1, "username": "x"})
    assert user["permalink"] == ""
    assert user["permalink_url"] == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/soundcloud/test_mirror.py -k permalink -v`
Expected: 2 FAILED with `KeyError: 'permalink'`

- [ ] **Step 3: Implement**

In `soundcloud/mirror.py`, add two lines to `_user_from_raw`:

```python
def _user_from_raw(raw: dict) -> dict:
    return {
        "id": raw.get("id"),
        "username": raw.get("username", ""),
        "full_name": raw.get("full_name", ""),
        "avatar_url": raw.get("avatar_url") or "",
        "track_count": raw.get("track_count", 0),
        "followers_count": raw.get("followers_count", 0),
        "permalink": raw.get("permalink", ""),
        "permalink_url": raw.get("permalink_url", ""),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/soundcloud/ -v`
Expected: all PASS (including existing mirror/search tests — `search_users` reuses this mapper, so chips get the fields for free)

- [ ] **Step 5: Commit**

```bash
git add tests/soundcloud/test_mirror.py soundcloud/mirror.py
git commit -m "feat(sc): capture permalink and permalink_url on user objects"
```

---

### Task 3: New routes `/sc/set/<id>/tracks` and `/sc/user/<id>/likes`

**Files:**
- Modify: `sWebExt/py_server/server.py` (insert both routes directly after `sc_preview`, i.e. after line 1949, before the `# ── Spotify routes ──` comment)
- Test: `tests/server/test_routes.py` (append)

**Interfaces:**
- Consumes: `soundcloud.mirror.get_set_tracks(client, playlist_id) -> list` and `soundcloud.mirror.get_user_likes(client, user_id, limit=…) -> list` (both exist).
- Produces: `GET /sc/set/<int:set_id>/tracks` → `{"status":"ok","tracks":[…]}`; `GET /sc/user/<int:user_id>/likes?limit=N` (N clamped to 1..200, default 50) → `{"status":"ok","tracks":[…]}`. Track dicts are the `_track_from_raw` shape (`title`, `artist`, `permalink_url`, `artwork_url`, `duration_ms`, `source:"sc"`). Task 5's frontend calls both routes.

- [ ] **Step 1: Write the failing tests**

Append to `tests/server/test_routes.py`:

```python
# ── /sc browse routes (set tracks, user likes) ────────────────────────────────

def _fake_sc_track(title="t"):
    return {"id": 1, "title": title, "artist": "a", "stream_url": "",
            "permalink_url": "https://soundcloud.com/a/t", "artwork_url": "",
            "duration_ms": 1000, "source": "sc"}


def test_sc_set_tracks_happy_path(client, monkeypatch):
    import sWebExt.py_server.server as srv
    monkeypatch.setattr(srv, "_sc_client_ready", True)
    with patch("sWebExt.py_server.server._get_sc_client", return_value=MagicMock()), \
         patch("soundcloud.mirror.get_set_tracks", return_value=[_fake_sc_track()]) as m:
        resp = client.get("/sc/set/123/tracks")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["status"] == "ok"
    assert data["tracks"][0]["title"] == "t"
    assert m.call_args.args[1] == 123


def test_sc_set_tracks_connecting_when_not_ready(client, monkeypatch):
    import sWebExt.py_server.server as srv
    monkeypatch.setattr(srv, "_sc_client_ready", False)
    resp = client.get("/sc/set/123/tracks")
    assert resp.status_code == 200
    assert json.loads(resp.data)["status"] == "connecting"


def test_sc_set_tracks_unavailable_without_client(client, monkeypatch):
    import sWebExt.py_server.server as srv
    monkeypatch.setattr(srv, "_sc_client_ready", True)
    with patch("sWebExt.py_server.server._get_sc_client", return_value=None):
        resp = client.get("/sc/set/123/tracks")
    assert resp.status_code == 200
    assert json.loads(resp.data)["status"] == "unavailable"


def test_sc_set_tracks_upstream_error_returns_500(client, monkeypatch):
    import sWebExt.py_server.server as srv
    monkeypatch.setattr(srv, "_sc_client_ready", True)
    with patch("sWebExt.py_server.server._get_sc_client", return_value=MagicMock()), \
         patch("soundcloud.mirror.get_set_tracks", side_effect=Exception("boom")):
        resp = client.get("/sc/set/123/tracks")
    assert resp.status_code == 500
    assert json.loads(resp.data)["status"] == "error"


def test_sc_user_likes_happy_path_default_limit(client, monkeypatch):
    import sWebExt.py_server.server as srv
    monkeypatch.setattr(srv, "_sc_client_ready", True)
    with patch("sWebExt.py_server.server._get_sc_client", return_value=MagicMock()), \
         patch("soundcloud.mirror.get_user_likes", return_value=[_fake_sc_track("liked")]) as m:
        resp = client.get("/sc/user/42/likes")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["status"] == "ok"
    assert data["tracks"][0]["title"] == "liked"
    assert m.call_args.args[1] == 42
    assert m.call_args.kwargs["limit"] == 50


def test_sc_user_likes_clamps_limit(client, monkeypatch):
    import sWebExt.py_server.server as srv
    monkeypatch.setattr(srv, "_sc_client_ready", True)
    with patch("sWebExt.py_server.server._get_sc_client", return_value=MagicMock()), \
         patch("soundcloud.mirror.get_user_likes", return_value=[]) as m:
        resp = client.get("/sc/user/42/likes?limit=9999")
    assert resp.status_code == 200
    assert m.call_args.kwargs["limit"] == 200


def test_sc_user_likes_connecting_when_not_ready(client, monkeypatch):
    import sWebExt.py_server.server as srv
    monkeypatch.setattr(srv, "_sc_client_ready", False)
    resp = client.get("/sc/user/42/likes")
    assert resp.status_code == 200
    assert json.loads(resp.data)["status"] == "connecting"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/server/test_routes.py -k "sc_set_tracks or sc_user_likes" -v`
Expected: 7 FAILED with 404 (routes don't exist)

- [ ] **Step 3: Implement**

Insert into `sWebExt/py_server/server.py` after `sc_preview` (line 1949), before the `# ── Spotify routes ──` comment:

```python
@app.route("/sc/set/<int:set_id>/tracks", methods=["GET"])
def sc_set_tracks(set_id):
    if not _sc_client_ready:
        return jsonify({"status": "connecting", "reason": "SoundCloud client initializing", "retry_after": 30})
    sc = _get_sc_client()
    if not sc:
        return jsonify({"status": "unavailable", "reason": "sc_client_id not configured"})
    try:
        from soundcloud.mirror import get_set_tracks
        tracks = get_set_tracks(sc, set_id)
        return jsonify({"status": "ok", "tracks": tracks})
    except Exception as e:
        logger.exception("[SC] set tracks failed")
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/sc/user/<int:user_id>/likes", methods=["GET"])
def sc_user_likes(user_id):
    try:
        limit = max(1, min(200, int(request.args.get("limit", 50))))
    except (TypeError, ValueError):
        limit = 50
    if not _sc_client_ready:
        return jsonify({"status": "connecting", "reason": "SoundCloud client initializing", "retry_after": 30})
    sc = _get_sc_client()
    if not sc:
        return jsonify({"status": "unavailable", "reason": "sc_client_id not configured"})
    try:
        from soundcloud.mirror import get_user_likes
        tracks = get_user_likes(sc, user_id, limit=limit)
        return jsonify({"status": "ok", "tracks": tracks})
    except Exception as e:
        logger.exception("[SC] user likes failed")
        return jsonify({"status": "error", "error": str(e)}), 500
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/server/test_routes.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add tests/server/test_routes.py sWebExt/py_server/server.py
git commit -m "feat(server): add /sc/set/<id>/tracks and /sc/user/<id>/likes routes"
```

---

### Task 4: Frontend — YT artwork pass-through + external-link icons

**Files:**
- Modify: `web/static/app.js` (`buildResultRow` at 771-831; YT merge branch at 957-967)
- Modify: `web/static/app.css` (append)

**Interfaces:**
- Consumes: `artwork_url` on `/yt/search` results (Task 1).
- Produces: `buildResultRow(item)` renders an `↗` anchor when `item.url` is set — Task 5 reuses this for all panel rows.

No JS tests exist; verification is pytest regression (nothing should break) + Task 6 manual checks.

- [ ] **Step 1: Fix the source badge fallback in `buildResultRow`**

The `img.onerror` handler at app.js:783 hardcodes `'SC'`. Change it to respect the source (a YT thumbnail that 404s must not display an SC badge):

```js
      img.onerror = () => { img.remove(); cover.textContent = item.source === 'sc' ? 'SC' : 'YT'; };
```

- [ ] **Step 2: Add the external-link anchor in `buildResultRow`**

After `row.appendChild(getBtn);` (app.js:828), before `return {el: row, …}`:

```js
    if (item.url) {
      const ext = document.createElement('a');
      ext.className = 'ext-link';
      ext.href = item.url;
      ext.target = '_blank';
      ext.rel = 'noopener';
      ext.textContent = '↗';
      ext.title = item.source === 'sc' ? 'Open on SoundCloud' : 'Open on YouTube';
      row.appendChild(ext);
    }
```

- [ ] **Step 3: Pass `artwork_url` through in the YT merge branch**

In `doSearch`, change the YT block (app.js:957-967) to:

```js
    // YT
    if (ytRes.status === 'fulfilled' && ytRes.value.results) {
      ytRes.value.results.forEach(r => {
        allResults.push(buildResultRow({
          source: 'yt',
          title: r.title,
          artist: r.artist,
          duration: r.duration,
          url: r.url,
          artwork_url: r.artwork_url || null,
        }));
      });
    }
```

- [ ] **Step 4: Add CSS**

Append to `web/static/app.css` (after the `.get.have` rule, line 91):

```css
.result .ext-link{flex:none;font-family:'JetBrains Mono';font-size:.8rem;color:var(--mut);text-decoration:none;padding:6px;transition:color .15s}
.result .ext-link:hover{color:var(--acid)}
```

- [ ] **Step 5: Regression check**

Run: `python -m pytest tests/ -q`
Expected: all PASS (frontend change; nothing server-side should move)

- [ ] **Step 6: Commit**

```bash
git add web/static/app.js web/static/app.css
git commit -m "feat(web): YT thumbnails in search rows + external open links"
```

---

### Task 5: Frontend — artist tabbed panel (Tracks / Playlists / Likes)

**Files:**
- Modify: `web/static/app.js` (replace `chip.onclick` body in `buildArtistChip` at 863-901; add helpers `scTrackToItem` and `openArtistPanel` inside `renderSearch`, after `buildResultRow`; one-line change in `doSearch`)
- Modify: `web/static/app.css` (append)

**Interfaces:**
- Consumes: `user.permalink_url` / `user.permalink` (Task 2), `GET /sc/set/<id>/tracks` and `GET /sc/user/<id>/likes?limit=50` (Task 3), `buildResultRow` with `↗` support (Task 4), existing `GET /sc/resolve` (returns `{status, user, tracks, sets}` where each set is `{id, title, track_count, artwork_url, tracks}`).
- Produces: user-facing panel only; nothing downstream consumes it.

- [ ] **Step 1: Add `scTrackToItem` helper**

Insert inside `renderSearch`, directly after the `buildResultRow` function (after app.js:831):

```js
  function scTrackToItem(t, fallbackArtist) {
    return {
      source: 'sc',
      title: t.title || '',
      artist: t.artist || fallbackArtist || '',
      duration: t.duration_ms ? Math.round(t.duration_ms / 1000) : null,
      url: t.permalink_url || '',
      artwork_url: t.artwork_url || null,
    };
  }
```

- [ ] **Step 2: Add `openArtistPanel` helper**

Insert directly after `scTrackToItem`:

```js
  function openArtistPanel(user) {
    artistsEl.style.display = 'none';
    srcRow.style.display = 'none';   // source pills don't apply inside the panel
    resultsEl.textContent = '';
    allResults = [];

    const panel = document.createElement('div');
    panel.className = 'artist-panel';

    const head = document.createElement('div');
    head.className = 'ap-head';
    const hname = document.createElement('b');
    hname.textContent = user.full_name || user.username || '';
    head.appendChild(hname);
    if (user.permalink_url) {
      const ext = document.createElement('a');
      ext.className = 'ap-ext';
      ext.href = user.permalink_url;
      ext.target = '_blank';
      ext.rel = 'noopener';
      ext.textContent = '↗ SC';
      head.appendChild(ext);
    }
    panel.appendChild(head);

    const tabRow = document.createElement('div');
    tabRow.className = 'src-row';
    const body = document.createElement('div');
    const state = {profile: null, likes: null};
    const tabs = {};

    function setMsg(text) {
      body.textContent = '';
      const m = document.createElement('div');
      m.className = 'warn';
      m.textContent = text;
      body.appendChild(m);
    }

    async function loadProfile() {
      if (state.profile) return state.profile;
      const url = user.permalink_url ||
        (user.permalink ? 'https://soundcloud.com/' + user.permalink : '');
      if (!url) throw new Error('no profile URL for this artist');
      const data = await API('/sc/resolve?url=' + encodeURIComponent(url));
      if (data.status !== 'ok') throw new Error(data.reason || data.error || 'profile unavailable');
      state.profile = data;
      return data;
    }

    function renderTrackRows(tracks, emptyText) {
      body.textContent = '';
      let shown = 0;
      (tracks || []).forEach(t => {
        const item = scTrackToItem(t, user.full_name || user.username || '');
        if (item.url) { body.appendChild(buildResultRow(item).el); shown++; }
      });
      if (!shown) setMsg(emptyText);
    }

    function renderPlaylists(sets) {
      body.textContent = '';
      if (!sets || !sets.length) { setMsg('No playlists.'); return; }
      sets.forEach(pl => {
        const wrap = document.createElement('div');
        const row = document.createElement('div');
        row.className = 'result pl-row';
        const cover = document.createElement('div');
        cover.className = 'cover';
        if (pl.artwork_url) {
          const img = document.createElement('img');
          img.src = pl.artwork_url;
          img.alt = '';
          img.style.cssText = 'width:100%;height:100%;object-fit:cover;border-radius:3px';
          img.onerror = () => { img.remove(); cover.textContent = 'SC'; };
          cover.appendChild(img);
        } else {
          cover.textContent = 'SC';
        }
        const meta = document.createElement('div');
        meta.className = 'r-meta';
        const title = document.createElement('b');
        title.textContent = pl.title || '(untitled playlist)';
        const sub = document.createElement('span');
        sub.textContent = (pl.track_count || 0) + ' tracks';
        meta.appendChild(title);
        meta.appendChild(sub);
        row.appendChild(cover);
        row.appendChild(meta);

        const inner = document.createElement('div');
        inner.className = 'pl-tracks';
        inner.style.display = 'none';
        let loaded = false;
        row.onclick = async () => {
          if (inner.style.display !== 'none') { inner.style.display = 'none'; return; }
          inner.style.display = '';
          if (loaded) return;
          inner.textContent = 'loading…';
          try {
            const data = await API('/sc/set/' + pl.id + '/tracks');
            if (data.status !== 'ok') throw new Error(data.reason || data.error || 'unavailable');
            inner.textContent = '';
            let shown = 0;
            (data.tracks || []).forEach(t => {
              const item = scTrackToItem(t, user.full_name || user.username || '');
              if (item.url) { inner.appendChild(buildResultRow(item).el); shown++; }
            });
            if (!shown) inner.textContent = 'no playable tracks';
            loaded = true;
          } catch (e) {
            inner.textContent = 'failed to load: ' + (e.message || 'unknown');
          }
        };
        wrap.appendChild(row);
        wrap.appendChild(inner);
        body.appendChild(wrap);
      });
    }

    async function showTab(name) {
      Object.entries(tabs).forEach(([n, el]) => el.classList.toggle('on', n === name));
      setMsg('Loading…');
      try {
        if (name === 'tracks') {
          const p = await loadProfile();
          renderTrackRows(p.tracks, 'No tracks found for this artist.');
        } else if (name === 'playlists') {
          const p = await loadProfile();
          renderPlaylists(p.sets);
        } else {
          if (!state.likes) {
            const data = await API('/sc/user/' + user.id + '/likes?limit=50');
            if (data.status !== 'ok') throw new Error(data.reason || data.error || 'likes unavailable');
            state.likes = data.tracks || [];
          }
          renderTrackRows(state.likes, 'No likes found.');
        }
      } catch (e) {
        setMsg('Failed to load: ' + (e.message || 'unknown'));
      }
    }

    [['tracks', 'Tracks'], ['playlists', 'Playlists'], ['likes', 'Likes']].forEach(([key, label]) => {
      const pill = document.createElement('span');
      pill.className = 'src' + (key === 'tracks' ? ' on' : '');
      pill.textContent = label;
      pill.onclick = () => showTab(key);
      tabs[key] = pill;
      tabRow.appendChild(pill);
    });

    panel.appendChild(tabRow);
    panel.appendChild(body);
    resultsEl.appendChild(panel);
    showTab('tracks');
  }
```

- [ ] **Step 3: Replace the chip click handler**

In `buildArtistChip`, replace the entire `chip.onclick = async () => { … };` block (app.js:863-901, the one that builds `scUrl` from `encodeURIComponent(user.username)`) with:

```js
    chip.onclick = () => openArtistPanel(user);
```

- [ ] **Step 4: Restore the source pills on a new search**

In `doSearch`, after `artistsEl.style.display = 'none';` (app.js:908), add:

```js
    srcRow.style.display = '';
```

- [ ] **Step 5: Add CSS**

Append to `web/static/app.css`:

```css
.artist-panel{margin-bottom:14px}
.ap-head{display:flex;align-items:center;gap:10px;padding:10px 4px;border-bottom:1px solid var(--line);margin-bottom:12px}
.ap-head b{font-size:.9rem}
.ap-ext{flex:none;font-family:'JetBrains Mono';font-size:.62rem;color:var(--mut);text-decoration:none;border:1px solid var(--line);border-radius:20px;padding:4px 10px;transition:color .15s,border-color .15s}
.ap-ext:hover{color:var(--acid);border-color:var(--acid-dim)}
.pl-row{cursor:pointer}
.pl-tracks{margin-left:28px;font-family:'JetBrains Mono';font-size:.64rem;color:var(--mut)}
```

- [ ] **Step 6: Regression check**

Run: `python -m pytest tests/ -q`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add web/static/app.js web/static/app.css
git commit -m "feat(web): artist panel with Tracks/Playlists/Likes tabs, fixed SC profile links"
```

---

### Task 6: End-to-end verification

**Files:** none (verification only)

- [ ] **Step 1: Full test suite**

Run: `python -m pytest tests/ -q`
Expected: all PASS, no new warnings about missing keys

- [ ] **Step 2: Restart the live service and smoke-test routes**

```bash
systemctl --user restart amusicserver
sleep 3
curl -s 'http://localhost:5000/yt/search?q=burial&limit=3' | python3 -m json.tool | grep -m3 artwork_url
curl -s 'http://localhost:5000/sc/search/users?q=burial' | python3 -m json.tool | grep -m3 permalink_url
```

Expected: `artwork_url` lines with `i.ytimg.com` or googleusercontent URLs; `permalink_url` lines with `https://soundcloud.com/<slug>`.
(If the SC route answers `"status": "connecting"`, wait ~30 s for the client-id refresh and retry.)

- [ ] **Step 3: Manual browser checks (user-facing)**

In the web UI at `http://localhost:5000` → SEARCH:
1. Search a term — YouTube rows show real thumbnails, every row has an `↗` icon that opens the right page in a new tab.
2. Click an SC artist chip — panel appears with Tracks / Playlists / Likes tabs and an `↗ SC` header link that opens the artist's actual profile.
3. Playlists tab lists playlists with counts; clicking one expands its tracks; `+` works on an expanded track.
4. Likes tab lists liked tracks with working `+` and `↗`.

- [ ] **Step 4: Report results to the user before claiming completion**
