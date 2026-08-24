# Usable-Status Batch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the "implemented backend, no UI" and "destructive with no review" gaps listed in `docs/ROADMAP.md`, and retire the m3u-overwrite playlist writer in favour of merge-aware Navidrome playlists that never clobber user edits.

**Architecture:** Flask server (`sWebExt/py_server/server.py`, 2454 lines) serves JSON routes to a vanilla-JS SPA (`web/static/app.js`, 2198 lines). Discovery runs in `discover/` with a `deps` `SimpleNamespace` (`subsonic`, `search_fn`, `download_fn`, `state`, `song_dir`, `lastfm_client`) built by `_build_discover_deps()` (server.py:148-188). Follow runs in `follow/` with its own state file. Both currently write playlists as `.m3u` files via `discover/assemble.py:write_weekly_mix`.

**Tech Stack:** Python 3 / Flask, pytest, vanilla JS (no framework, no JS test harness), CSS custom properties (`--panel`, `--line`, `--mut`, `--acid`, `--acid-dim`, `--danger`).

**Design source:** approved design brief of 2026-08-24 (items 0-5, 7). Item numbers from that brief are noted in each group heading.

---

## Global Constraints

- **All tests run from repo root as `venv/bin/python -m pytest tests/ -q`.** Baseline at the time of writing: **570 passed, 1 skipped**. Every task must leave the suite green.
- **Strict TDD.** Every backend task writes failing tests first, confirms the failure, then implements. Frontend tasks are the documented exception: `web/static/app.js` has no test harness by project convention, so frontend tasks verify via full-suite regression plus explicit manual browser checks.
- **Frontend security invariant:** DOM built with `document.createElement` / `textContent` only. **Never `innerHTML` with data.** Reuse existing CSS classes (`card`, `frow`, `txt`, `btn`, `chip`, `chips`, `result`, `cover`, `r-meta`, `src-row`, `src`, `warn`, `bar`).
- Route error pattern for `/sc/*` routes is the existing one: HTTP 200 `{"status":"connecting", "reason":…, "retry_after":30}` while the client id initializes; HTTP 200 `{"status":"unavailable","reason":…}` when no client; HTTP 500 `{"status":"error","error":str(e)}` on upstream exceptions. Match `sc_search_tracks` (server.py:2024-2037).
- Route tests live in `tests/server/test_routes.py` and use its existing `app`/`client` fixtures (test_routes.py:1-29; `threading.Thread` is patched at import, module global `_sc_client_ready` defaults False — set it with `monkeypatch.setattr`).
- No new Python dependencies. `requests` is already a dependency (used by `soundcloud/client.py`), `eyed3` is already a dependency (used by `library/scanner.py`).
- **`config.json` contains a live plaintext `navidrome_pass`, and `Subsonic._url` puts it in every query string.** Never print a built URL, never paste config values into a commit, never log a full `_url()` result.
- Commit after every task with the message given in the task.

---

## Implementation order

Groups run in this order (design-brief numbering in parentheses):

| Order | Group | Design item | Tasks |
|---|---|---|---|
| 1 | Dedup dry-run review | 1 | 1-3 |
| 2 | Mixes editor completion | 3 | 4-5 |
| 3 | Audio preview in Search | 2 (+ 0a folded in) | 6-8 |
| 4 | Share UI | 4 (+ 0b fixed properly) | 9-10 |
| 5 | Batch import card | 5 | 11 |
| 6 | Merge-aware playlists | 7 | 12-20 |
| 7 | Close-out | — | 21 |

---

# GROUP 1 — Dedup dry-run review (design item 1)

Today `renderLibrary`'s dedup card (app.js:761-775) calls `POST /library/dedup/run`, which honours `cfg["dedup"]["auto_delete"]` and can delete files with no review step. `library/dedupe.py:run()` already computes keep/remove per group inside its loop (dedupe.py:31-52) but only **logs** it — the return value is `{"groups": <int count>, "would_delete": [paths], "deleted": [paths]}`. We surface the per-group detail and add an explicit, path-validated delete endpoint.

### Task 1: `library/dedupe.py` returns `groups_detail`

**Files:**
- Modify: `library/dedupe.py:25-52` (`run()`)
- Test: `tests/library/test_dedupe.py` (append; it imports `_pick_keep, find_groups, run` at line 6 and patches `library.dedupe.scan` — reuse that pattern)

**Interfaces:**
- Produces: `run()`'s dict gains `"groups_detail": [{"key": str, "keep": {"path","artist","title"}, "remove": [{"path","artist","title"}, …]}, …]`. Task 3's frontend depends on these exact key names. Existing keys `groups`, `would_delete`, `deleted` are unchanged (server.py:922 spreads the dict into the route response, and `tests/server/test_routes.py:58-63` asserts only the status code).
- Record shape from `library/scanner.py:_make_record` is `{"path","key","artist","title","has_tags"}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/library/test_dedupe.py`:

```python
def test_run_returns_groups_detail_shape(tmp_path):
    """groups_detail carries the keep record and the removable records per group."""
    a = tmp_path / "a.mp3"; a.write_bytes(b"x")
    b = tmp_path / "b.mp3"; b.write_bytes(b"x")
    import os, time
    os.utime(a, (1000, 1000))
    os.utime(b, (2000, 2000))
    records = [
        {"path": str(a), "key": "same title", "artist": "A", "title": "Same Title", "has_tags": True},
        {"path": str(b), "key": "same title", "artist": "",  "title": "Same Title", "has_tags": False},
    ]
    with patch("library.dedupe.scan", return_value=records):
        result = run(str(tmp_path), auto_delete=False)

    assert "groups_detail" in result
    assert len(result["groups_detail"]) == 1
    g = result["groups_detail"][0]
    assert g["key"] == "same title"
    assert g["keep"] == {"path": str(a), "artist": "A", "title": "Same Title"}
    assert g["remove"] == [{"path": str(b), "artist": "", "title": "Same Title"}]


def test_run_groups_detail_omits_singleton_groups(tmp_path):
    """Files with a unique key produce no group at all."""
    a = tmp_path / "a.mp3"; a.write_bytes(b"x")
    records = [{"path": str(a), "key": "solo", "artist": "A", "title": "Solo", "has_tags": True}]
    with patch("library.dedupe.scan", return_value=records):
        result = run(str(tmp_path), auto_delete=False)
    assert result["groups_detail"] == []
    assert result["groups"] == 0


def test_run_groups_detail_matches_would_delete(tmp_path):
    """Every path in groups_detail[*].remove appears in would_delete, and vice versa."""
    paths = []
    for n in ("a", "b", "c"):
        p = tmp_path / f"{n}.mp3"; p.write_bytes(b"x")
        paths.append(str(p))
    import os
    for i, p in enumerate(paths):
        os.utime(p, (1000 + i, 1000 + i))
    records = [{"path": p, "key": "k", "artist": "A", "title": "T", "has_tags": True} for p in paths]
    with patch("library.dedupe.scan", return_value=records):
        result = run(str(tmp_path), auto_delete=False)
    flat = [r["path"] for g in result["groups_detail"] for r in g["remove"]]
    assert sorted(flat) == sorted(result["would_delete"])
```

(`patch` is already imported at the top of `tests/library/test_dedupe.py`; if it is not, add `from unittest.mock import patch`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest tests/library/test_dedupe.py -k groups_detail -v`
Expected: 3 FAILED with `KeyError: 'groups_detail'` / `assert 'groups_detail' in result`.

- [ ] **Step 3: Implement**

In `library/dedupe.py`, rewrite the body of `run()` (lines 31-52) so the existing loop also accumulates detail. Add a tiny record projector above `run()`:

```python
def _brief(record):
    """Projection of a scan record safe to hand to the UI."""
    return {"path": record["path"], "artist": record.get("artist", ""),
            "title": record.get("title", "")}
```

Then inside the loop, alongside the existing `to_remove` computation:

```python
    groups_detail = []

    for key, group in groups.items():
        keep = _pick_keep(group)
        removable = [r for r in group if r["path"] != keep["path"]]
        to_remove = [r["path"] for r in removable]
        would_delete.extend(to_remove)
        groups_detail.append({
            "key": key,
            "keep": _brief(keep),
            "remove": [_brief(r) for r in removable],
        })
        logger.info("dedup: group %r — keep=%s, remove=%s", key, keep["path"], to_remove)
        ...
```

and extend the return:

```python
    return {"groups": len(groups), "would_delete": would_delete,
            "deleted": deleted, "groups_detail": groups_detail}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/python -m pytest tests/library/ tests/server/test_routes.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add library/dedupe.py tests/library/test_dedupe.py
git commit -m "feat(library): dedupe run() returns per-group keep/remove detail"
```

---

### Task 2: `POST /library/dedup/delete` with song_dir containment check

**Files:**
- Modify: `sWebExt/py_server/server.py` — add a module-level helper next to `_run_dedup_once` (server.py:908-926), and the route directly after `dedup_report` (server.py:1286-1290)
- Test: `tests/server/test_routes.py` (append after the existing `test_post_library_dedup_report`, ~line 63)

**Interfaces:**
- Consumes: `POST /library/dedup/delete` body `{"paths": ["/abs/path.mp3", …]}`.
- Produces: `{"status":"ok","deleted":[paths],"errors":[{"path":…,"error":…}]}`. HTTP 400 when `paths` is missing/empty; HTTP 503 `{"status":"disabled","reason":"song_dir not set"}` when `song_dir` is unset. Task 3's frontend depends on `deleted` and `errors`.
- `/library/dedup/run` is **kept unchanged** — the scheduler (`_dedup_scheduled_loop`, server.py:929-936) still uses it.

**Note:** there is currently **no** realpath containment helper anywhere in `server.py` (grep for `realpath`/`commonpath` returns only `_SERVER_DIR`/`_PROJECT_ROOT` at lines 24-25). This task adds the first one.

- [ ] **Step 1: Write the failing tests**

Append to `tests/server/test_routes.py`:

```python
# ── /library/dedup/delete ─────────────────────────────────────────────────────

def test_dedup_delete_removes_listed_files(client, tmp_path):
    import sWebExt.py_server.server as srv
    song_dir = tmp_path / "music"
    song_dir.mkdir()
    f1 = song_dir / "dup1.mp3"; f1.write_bytes(b"x")
    f2 = song_dir / "dup2.mp3"; f2.write_bytes(b"x")
    with patch.object(srv, "_get_config", return_value={"song_dir": str(song_dir)}):
        resp = client.post("/library/dedup/delete",
                           json={"paths": [str(f1), str(f2)]})
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert sorted(data["deleted"]) == sorted([str(f1), str(f2)])
    assert data["errors"] == []
    assert not f1.exists() and not f2.exists()


def test_dedup_delete_rejects_path_outside_song_dir(client, tmp_path):
    import sWebExt.py_server.server as srv
    song_dir = tmp_path / "music"; song_dir.mkdir()
    outside = tmp_path / "secret.mp3"; outside.write_bytes(b"x")
    with patch.object(srv, "_get_config", return_value={"song_dir": str(song_dir)}):
        resp = client.post("/library/dedup/delete", json={"paths": [str(outside)]})
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["deleted"] == []
    assert data["errors"][0]["path"] == str(outside)
    assert "outside" in data["errors"][0]["error"]
    assert outside.exists()   # untouched


def test_dedup_delete_rejects_traversal(client, tmp_path):
    import sWebExt.py_server.server as srv
    song_dir = tmp_path / "music"; song_dir.mkdir()
    outside = tmp_path / "secret.mp3"; outside.write_bytes(b"x")
    traversal = str(song_dir / ".." / "secret.mp3")
    with patch.object(srv, "_get_config", return_value={"song_dir": str(song_dir)}):
        resp = client.post("/library/dedup/delete", json={"paths": [traversal]})
    assert json.loads(resp.data)["deleted"] == []
    assert outside.exists()


def test_dedup_delete_rejects_directory(client, tmp_path):
    import sWebExt.py_server.server as srv
    song_dir = tmp_path / "music"; song_dir.mkdir()
    sub = song_dir / "album"; sub.mkdir()
    with patch.object(srv, "_get_config", return_value={"song_dir": str(song_dir)}):
        resp = client.post("/library/dedup/delete", json={"paths": [str(sub)]})
    assert json.loads(resp.data)["deleted"] == []
    assert sub.exists()


def test_dedup_delete_requires_paths(client, tmp_path):
    import sWebExt.py_server.server as srv
    song_dir = tmp_path / "music"; song_dir.mkdir()
    with patch.object(srv, "_get_config", return_value={"song_dir": str(song_dir)}):
        resp = client.post("/library/dedup/delete", json={"paths": []})
    assert resp.status_code == 400


def test_dedup_delete_disabled_without_song_dir(client):
    import sWebExt.py_server.server as srv
    with patch.object(srv, "_get_config", return_value={}):
        resp = client.post("/library/dedup/delete", json={"paths": ["/x.mp3"]})
    assert resp.status_code == 503
    assert json.loads(resp.data)["status"] == "disabled"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest tests/server/test_routes.py -k dedup_delete -v`
Expected: 6 FAILED with 405/404 (route does not exist).

- [ ] **Step 3: Implement**

Add the containment helper in `sWebExt/py_server/server.py` immediately above `_run_dedup_once` (before line 908):

```python
def _inside_song_dir(path: str, song_dir: str) -> bool:
    """True only when `path` resolves to a location strictly inside `song_dir`.

    Uses realpath on both sides so symlinks and ../ traversal cannot escape.
    """
    try:
        root = os.path.realpath(song_dir)
        target = os.path.realpath(path)
    except Exception:
        return False
    if not root:
        return False
    return os.path.commonpath([root, target]) == root and target != root
```

Add the route directly after `dedup_report` (after line 1290):

```python
@app.route("/library/dedup/delete", methods=["POST"])
def dedup_delete():
    """Delete an explicit list of duplicate files, validated against song_dir."""
    body = request.get_json(force=True, silent=True) or {}
    paths = body.get("paths") or []
    if not isinstance(paths, list) or not paths:
        return jsonify({"status": "error", "error": "paths required"}), 400

    cfg = _get_config()
    song_dir = cfg.get("song_dir", "")
    if not song_dir:
        return jsonify({"status": "disabled", "reason": "song_dir not set"}), 503

    deleted, errors = [], []
    for p in paths:
        if not isinstance(p, str) or not p:
            errors.append({"path": str(p), "error": "not a path"})
            continue
        if not _inside_song_dir(p, song_dir):
            errors.append({"path": p, "error": "outside song_dir"})
            continue
        if not os.path.isfile(p):
            errors.append({"path": p, "error": "not a file"})
            continue
        try:
            os.remove(p)
            deleted.append(p)
            logger.info("[DEDUP] deleted %s", p)
        except Exception as e:
            errors.append({"path": p, "error": str(e)})
    return jsonify({"status": "ok", "deleted": deleted, "errors": errors})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/python -m pytest tests/server/test_routes.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add sWebExt/py_server/server.py tests/server/test_routes.py
git commit -m "feat(server): POST /library/dedup/delete with song_dir containment check"
```

---

### Task 3: Frontend — two-step dedup review card

**Files:**
- Modify: `web/static/app.js:761-775` (the dedup card inside `renderLibrary`, which spans 633-843)
- Modify: `web/static/app.css` (append)

**Interfaces:**
- Consumes: `POST /library/dedup/report` → `{status, groups, would_delete, deleted, groups_detail}` (Task 1); `POST /library/dedup/delete` → `{status, deleted, errors}` (Task 2).
- The `makeToolCard(title, desc, goLabel, goHandler)` builder is at app.js:640-661 and exposes `card._statusSpan` (658) and `card._btn` (659). Keep using it for the Scan action; append the review panel to the same card element.

No JS tests exist; verify via full pytest regression plus the manual checks below.

- [ ] **Step 1: Replace the dedup card**

Replace app.js:761-775 in full with:

```javascript
  // ── 3. De-duplicate ───────────────────────────────────────────────────────────
  const dedupPanel = document.createElement('div');
  dedupPanel.className = 'dedup-panel';
  dedupPanel.style.display = 'none';

  const dedupCard = makeToolCard('De-duplicate', 'review duplicate titles before deleting', 'scan', async () => {
    dedupCard._btn.disabled = true;
    dedupCard._statusSpan.textContent = 'scanning…';
    dedupPanel.textContent = '';
    dedupPanel.style.display = 'none';
    try {
      const r = await API('/library/dedup/report', {method: 'POST'});
      const groups = r.groups_detail || [];
      const dupCount = groups.reduce((n, g) => n + (g.remove || []).length, 0);
      dedupCard._statusSpan.textContent = dupCount
        ? (dupCount + ' duplicates in ' + groups.length + ' groups')
        : 'no duplicates';
      if (dupCount) renderDedupGroups(groups);
    } catch(e) {
      dedupCard._statusSpan.textContent = 'Error: ' + (e.message || 'unknown');
    } finally {
      dedupCard._btn.disabled = false;
    }
  });
  dedupCard.appendChild(dedupPanel);
  el.appendChild(dedupCard);

  function renderDedupGroups(groups) {
    dedupPanel.textContent = '';
    dedupPanel.style.display = '';
    const boxes = [];

    groups.forEach(g => {
      const gEl = document.createElement('div');
      gEl.className = 'dedup-group';

      const keepEl = document.createElement('div');
      keepEl.className = 'dedup-row keep';
      const keepTag = document.createElement('span');
      keepTag.className = 'dedup-tag';
      keepTag.textContent = 'KEEP';
      const keepTxt = document.createElement('span');
      keepTxt.className = 'dedup-path';
      keepTxt.textContent = dedupLabel(g.keep);
      keepTxt.title = (g.keep && g.keep.path) || '';
      keepEl.appendChild(keepTag);
      keepEl.appendChild(keepTxt);
      gEl.appendChild(keepEl);

      (g.remove || []).forEach(r => {
        const row = document.createElement('div');
        row.className = 'dedup-row';
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.checked = true;
        cb.dataset.path = r.path;
        const txt = document.createElement('span');
        txt.className = 'dedup-path';
        txt.textContent = dedupLabel(r);
        txt.title = r.path || '';
        row.appendChild(cb);
        row.appendChild(txt);
        gEl.appendChild(row);
        boxes.push(cb);
      });

      dedupPanel.appendChild(gEl);
    });

    const footer = document.createElement('div');
    footer.className = 'dedup-footer';
    const delBtn = document.createElement('button');
    delBtn.className = 'btn del';
    const result = document.createElement('span');
    result.className = 'dedup-result';

    function refreshLabel() {
      const n = boxes.filter(b => b.checked).length;
      delBtn.textContent = 'Delete ' + n + ' file' + (n === 1 ? '' : 's');
      delBtn.disabled = n === 0;
    }
    boxes.forEach(b => { b.onchange = refreshLabel; });
    refreshLabel();

    delBtn.onclick = async () => {
      const paths = boxes.filter(b => b.checked).map(b => b.dataset.path);
      if (!paths.length) return;
      delBtn.disabled = true;
      result.textContent = 'deleting…';
      try {
        const r = await API('/library/dedup/delete', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({paths}),
        });
        const nDel = (r.deleted || []).length;
        const nErr = (r.errors || []).length;
        result.textContent = nDel + ' deleted' + (nErr ? (', ' + nErr + ' failed') : '');
        dedupCard._statusSpan.textContent = 'rescan to refresh';
        const done = new Set(r.deleted || []);
        boxes.forEach(b => {
          if (done.has(b.dataset.path)) {
            b.checked = false;
            b.disabled = true;
            b.parentElement.classList.add('gone');
          }
        });
        refreshLabel();
      } catch(e) {
        result.textContent = 'Error: ' + (e.message || 'unknown');
        delBtn.disabled = false;
      }
    };

    footer.appendChild(delBtn);
    footer.appendChild(result);
    dedupPanel.appendChild(footer);
  }

  function dedupLabel(rec) {
    if (!rec) return '';
    const name = (rec.path || '').split('/').pop();
    const who = [rec.artist, rec.title].filter(Boolean).join(' — ');
    return who ? (who + '  ·  ' + name) : name;
  }
```

- [ ] **Step 2: Add CSS**

Append to `web/static/app.css`:

```css
/* ---- DEDUP REVIEW ---- */
.dedup-panel{margin-top:12px;border-top:1px solid var(--line);padding-top:10px}
.dedup-group{border:1px solid var(--line);border-radius:3px;padding:8px;margin-bottom:8px}
.dedup-row{display:flex;align-items:center;gap:8px;padding:3px 0;font-family:'JetBrains Mono';font-size:.62rem;color:var(--mut)}
.dedup-row.keep{color:var(--acid)}
.dedup-row.gone{opacity:.35;text-decoration:line-through}
.dedup-tag{flex:none;font-size:.52rem;letter-spacing:.1em;border:1px solid var(--acid-dim);border-radius:8px;padding:1px 6px}
.dedup-path{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.dedup-footer{display:flex;align-items:center;gap:10px;margin-top:6px}
.dedup-result{font-family:'JetBrains Mono';font-size:.62rem;color:var(--mut)}
```

- [ ] **Step 3: Regression check**

Run: `venv/bin/python -m pytest tests/ -q`
Expected: 570+ passed (frontend-only change; nothing server-side moves).

- [ ] **Step 4: Manual browser check**

`systemctl --user restart amusicserver && sleep 3`, then open `http://localhost:5000#library`:
1. "De-duplicate" card now says "scan"; clicking it lists groups with a green KEEP row and checked duplicates.
2. Unchecking boxes updates the "Delete N files" label; unchecking all disables the button.
3. Clicking Delete strikes through the deleted rows and reports a count. Confirm on disk that only the checked files are gone and every KEEP file still exists.

- [ ] **Step 5: Commit**

```bash
git add web/static/app.js web/static/app.css
git commit -m "feat(web): dedup review — scan, per-group checkboxes, explicit delete"
```

---

# GROUP 2 — Mixes editor completion (design item 3)

Frontend only. No backend change: `validate_profile` already accepts `seeds.artists` and `quality`, the save payload already sends `seeds.artists` (app.js:575) and `quality` (app.js:578) verbatim from the loaded mix, and `discover/engine.py:208` overlays `profile["quality"]` on top of `cfg["discover"]`.

### Task 4: Manual seed artist chips

**Files:**
- Modify: `web/static/app.js` — inside `buildMixCard` (starts at 131). Insert the artists row after the genre row is appended (app.js:450, `inner.appendChild(genreRow)`) and before the playlist row block; extend `updateModeVisibility` (app.js:467-472); change the save payload line 575.

**Interfaces:**
- Consumes: `mix.seeds.artists` (array of strings, already loaded and persisted).
- Produces: `seeds.artists` in the POST `/mixes` body now reflects the edited chip list when `mode === 'manual'`.
- Pattern to clone: the genre chip row — `chipsDiv`/`renderChips` at app.js:306-343 (chip element, `chip.className='chip'`, `chip.textContent = g + ' ✕'`, `chip.onclick` splices then re-renders, plus a trailing `chip add` element). Do **not** clone the Last.fm tag-preview machinery (app.js:344-449) — artists have no tag preview.

- [ ] **Step 1: Add the artists row**

Insert in `buildMixCard` immediately after `inner.appendChild(genreRow);` (app.js:450):

```javascript
  // Manual artist chips row (only when mode=manual)
  const artistRow = document.createElement('div');
  artistRow.className = 'frow';
  const artistLabel = document.createElement('label');
  artistLabel.textContent = 'artists';
  const artistChips = document.createElement('div');
  artistChips.className = 'chips';
  const artists = Array.isArray((mix.seeds || {}).artists) ? [...mix.seeds.artists] : [];

  const artistInput = document.createElement('input');
  artistInput.className = 'txt';
  artistInput.type = 'text';
  artistInput.placeholder = 'artist name… (Enter to add)';
  artistInput.style.display = 'none';

  function addArtist() {
    const v = artistInput.value.trim();
    if (v && !artists.some(a => a.toLowerCase() === v.toLowerCase())) artists.push(v);
    artistInput.value = '';
    artistInput.style.display = 'none';
    renderArtistChips();
  }
  artistInput.onkeydown = (ev) => {
    if (ev.key === 'Enter') { ev.preventDefault(); addArtist(); }
    else if (ev.key === 'Escape') { artistInput.value = ''; artistInput.style.display = 'none'; renderArtistChips(); }
  };
  artistInput.onblur = () => { if (artistInput.value.trim()) addArtist(); else { artistInput.style.display = 'none'; renderArtistChips(); } };

  function renderArtistChips() {
    artistChips.textContent = '';
    artists.forEach((a, i) => {
      const chip = document.createElement('span');
      chip.className = 'chip';
      chip.textContent = a + ' ✕';
      chip.onclick = () => { artists.splice(i, 1); renderArtistChips(); };
      artistChips.appendChild(chip);
    });
    const addChip = document.createElement('span');
    addChip.className = 'chip add';
    addChip.textContent = '+ add';
    addChip.onclick = () => {
      addChip.style.display = 'none';
      artistInput.style.display = '';
      artistInput.focus();
    };
    artistChips.appendChild(addChip);
  }
  renderArtistChips();
  artistRow.appendChild(artistLabel);
  artistRow.appendChild(artistChips);
  inner.appendChild(artistRow);
  inner.appendChild(artistInput);
```

- [ ] **Step 2: Teach `updateModeVisibility` the third row**

Replace app.js:467-470 (the body of `updateModeVisibility`) with:

```javascript
  function updateModeVisibility() {
    genreRow.style.display    = modeSel.value === 'genre'    ? '' : 'none';
    artistRow.style.display   = modeSel.value === 'manual'   ? '' : 'none';
    playlistRow.style.display = modeSel.value === 'playlist' ? '' : 'none';
    if (modeSel.value !== 'manual') artistInput.style.display = 'none';
  }
```

Leave `modeSel.onchange = updateModeVisibility;` and the immediate `updateModeVisibility();` call (471-472) as they are.

- [ ] **Step 3: Wire the save payload**

Change app.js:575 from

```javascript
        artists:  (mix.seeds || {}).artists    || [],
```

to

```javascript
        artists:  modeSel.value === 'manual'   ? [...artists]      : ((mix.seeds || {}).artists || []),
```

(The non-manual branch preserves any previously stored list rather than wiping it, matching how `genres` and `playlist` behave only in their own mode.)

- [ ] **Step 4: Regression check**

Run: `venv/bin/python -m pytest tests/ -q`
Expected: all PASS.

- [ ] **Step 5: Manual browser check**

`http://localhost:5000#mixes` → expand a mix → set mode to `manual`: an "artists" chip row appears (genre/playlist rows hide). Add two artists, save, reload the page, re-expand — the chips are still there. Switch to `genre` — the artists row hides.

- [ ] **Step 6: Commit**

```bash
git add web/static/app.js
git commit -m "feat(web): manual seed artist chips in the mix editor"
```

---

### Task 5: Per-mix quality "Advanced" section

**Files:**
- Modify: `web/static/app.js` — `buildMixCard`: add the Advanced block after the size/cap row (`inner.appendChild(sizeRow)`, app.js:492) and before the actions row; change the save payload line 578.
- Modify: `web/static/app.css` (append)

**Interfaces:**
- Produces: `profile.quality` in the POST `/mixes` body, containing **only** the fields the user filled in.
- **Field names verified against `discover/engine.py`** (`quality = {**cfg["discover"], **profile["quality"]}` at line 208):

| UI field | engine read | line | default |
|---|---|---|---|
| `min_artist_listeners` (number) | `int(quality.get("min_artist_listeners", 5000))` | 262 | 5000 |
| `candidate_oversample` (number) | `int(quality.get("candidate_oversample", 3))` | 255 | 3 |
| `seed_artist_count` (number) | `int(quality.get("seed_artist_count", 20))` | 231, 245, 259 | 20 |
| `lastfm_period` (select) | `quality.get("lastfm_period", "1month")` | 233 | `1month` |

  `lastfm_periods` (plural, line 234) also exists but is a list-valued advanced key with no single-value UI — leave it out and do not clobber it (see Step 3).

- [ ] **Step 1: Add the Advanced block**

Insert in `buildMixCard` immediately after `inner.appendChild(sizeRow);` (app.js:492):

```javascript
  // Advanced (per-mix quality overrides — blank field = inherit global default)
  const advWrap = document.createElement('div');
  advWrap.className = 'adv-wrap';
  const advToggle = document.createElement('div');
  advToggle.className = 'adv-toggle';
  advToggle.textContent = '▸ advanced';
  const advBody = document.createElement('div');
  advBody.className = 'adv-body';
  advBody.style.display = 'none';
  advToggle.onclick = () => {
    const open = advBody.style.display === 'none';
    advBody.style.display = open ? '' : 'none';
    advToggle.textContent = (open ? '▾' : '▸') + ' advanced';
  };

  const q = mix.quality || {};
  const qInputs = {};

  function advNumRow(key, label, placeholder) {
    const row = document.createElement('div');
    row.className = 'frow';
    const lab = document.createElement('label');
    lab.textContent = label;
    const inp = document.createElement('input');
    inp.className = 'txt';
    inp.type = 'number';
    inp.placeholder = placeholder;
    inp.value = (q[key] === undefined || q[key] === null) ? '' : String(q[key]);
    row.appendChild(lab);
    row.appendChild(inp);
    advBody.appendChild(row);
    qInputs[key] = inp;
  }

  advNumRow('min_artist_listeners', 'min listeners', 'inherit (5000)');
  advNumRow('candidate_oversample', 'oversample',    'inherit (3)');
  advNumRow('seed_artist_count',    'seed artists',  'inherit (20)');

  const perRow = document.createElement('div');
  perRow.className = 'frow';
  const perLab = document.createElement('label');
  perLab.textContent = 'lastfm period';
  const perSel = document.createElement('select');
  [['', 'inherit'], ['7day', '7day'], ['1month', '1month'], ['3month', '3month'],
   ['6month', '6month'], ['12month', '12month'], ['overall', 'overall']].forEach(([v, t]) => {
    const o = document.createElement('option');
    o.value = v;
    o.textContent = t;
    if ((q.lastfm_period || '') === v) o.selected = true;
    perSel.appendChild(o);
  });
  perRow.appendChild(perLab);
  perRow.appendChild(perSel);
  advBody.appendChild(perRow);

  advWrap.appendChild(advToggle);
  advWrap.appendChild(advBody);
  inner.appendChild(advWrap);
```

- [ ] **Step 2: Build the quality object on save**

Replace app.js:578 (`quality: mix.quality || {},`) with a computed object. Above the `const profile = {` literal (app.js:559), insert:

```javascript
    // Advanced: only include fields the user actually filled in; anything else
    // is omitted so the engine falls through to the global cfg["discover"] default.
    const quality = {};
    Object.keys(mix.quality || {}).forEach(k => {
      if (!(k in qInputs) && k !== 'lastfm_period') quality[k] = mix.quality[k];  // preserve keys with no UI (e.g. lastfm_periods)
    });
    ['min_artist_listeners', 'candidate_oversample', 'seed_artist_count'].forEach(k => {
      const raw = (qInputs[k].value || '').trim();
      if (raw === '') return;
      const n = parseInt(raw, 10);
      if (!Number.isNaN(n)) quality[k] = n;
    });
    if (perSel.value) quality.lastfm_period = perSel.value;
```

and change the payload line to:

```javascript
      quality: quality,
```

- [ ] **Step 3: Add CSS**

Append to `web/static/app.css`:

```css
/* ---- MIX ADVANCED ---- */
.adv-wrap{margin-top:6px;border-top:1px solid var(--line);padding-top:8px}
.adv-toggle{font-family:'JetBrains Mono';font-size:.6rem;letter-spacing:.1em;text-transform:uppercase;color:var(--mut);cursor:pointer;padding:4px 0}
.adv-toggle:hover{color:var(--acid)}
.adv-body{padding-top:4px}
```

- [ ] **Step 4: Regression check**

Run: `venv/bin/python -m pytest tests/ -q`
Expected: all PASS.

- [ ] **Step 5: Manual browser check**

`http://localhost:5000#mixes` → expand a mix → click "▸ advanced". Leave all fields blank and save; confirm `config.json`'s profile has `"quality": {}` (nothing written). Then set `min listeners` to `20000` and period to `3month`, save, and confirm `"quality": {"min_artist_listeners": 20000, "lastfm_period": "3month"}` — the other two keys absent. Reload; the fields repopulate.

- [ ] **Step 6: Commit**

```bash
git add web/static/app.js web/static/app.css
git commit -m "feat(web): per-mix advanced quality overrides in the mix editor"
```

---

# GROUP 3 — Audio preview in Search (design item 2, with fix 0a folded in)

`/sc/preview` (server.py:2040-2049) today just appends `client_id` to whatever `stream_url` it is given. Since `soundcloud/mirror.py:_track_from_raw` (lines 17-28) sets `stream_url` from `_first_hls_url`, that means the route hands the browser an **HLS** manifest URL that `<audio>` cannot play. We capture the progressive transcoding instead and resolve it server-side to a plain CDN mp3.

### Task 6: Capture `progressive_url` in `_track_from_raw`

**Files:**
- Modify: `soundcloud/mirror.py:6-28`
- Test: `tests/soundcloud/test_mirror.py` (append)

**Interfaces:**
- Produces: every track dict — from `/sc/search/tracks` (`soundcloud/search.py:search_tracks`), `/sc/resolve` (`get_profile`), `/sc/set/<id>/tracks`, `/sc/user/<id>/likes` — gains `"progressive_url": str` (empty string when the track has no progressive transcoding). Tasks 7 and 8 depend on this key name. `stream_url` (HLS) is left untouched.

- [ ] **Step 1: Write the failing tests**

Append to `tests/soundcloud/test_mirror.py`:

```python
def test_track_from_raw_captures_progressive_url():
    from soundcloud.mirror import _track_from_raw
    t = _track_from_raw({
        "id": 1, "title": "T", "user": {"username": "A"},
        "media": {"transcodings": [
            {"url": "https://api-v2.soundcloud.com/media/1/hls",
             "format": {"protocol": "hls"}},
            {"url": "https://api-v2.soundcloud.com/media/1/progressive",
             "format": {"protocol": "progressive"}},
        ]},
    })
    assert t["progressive_url"] == "https://api-v2.soundcloud.com/media/1/progressive"
    assert t["stream_url"] == "https://api-v2.soundcloud.com/media/1/hls"


def test_track_from_raw_progressive_url_empty_when_hls_only():
    from soundcloud.mirror import _track_from_raw
    t = _track_from_raw({
        "id": 1, "title": "T", "user": {"username": "A"},
        "media": {"transcodings": [
            {"url": "https://api-v2.soundcloud.com/media/1/hls",
             "format": {"protocol": "hls"}},
        ]},
    })
    assert t["progressive_url"] == ""


def test_track_from_raw_progressive_url_empty_when_no_media():
    from soundcloud.mirror import _track_from_raw
    t = _track_from_raw({"id": 1, "title": "T", "user": {"username": "A"}})
    assert t["progressive_url"] == ""


def test_track_from_raw_takes_first_progressive_transcoding():
    from soundcloud.mirror import _track_from_raw
    t = _track_from_raw({
        "id": 1, "title": "T", "user": {"username": "A"},
        "media": {"transcodings": [
            {"url": "https://p1", "format": {"protocol": "progressive"}},
            {"url": "https://p2", "format": {"protocol": "progressive"}},
        ]},
    })
    assert t["progressive_url"] == "https://p1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest tests/soundcloud/test_mirror.py -k progressive -v`
Expected: 4 FAILED with `KeyError: 'progressive_url'`.

- [ ] **Step 3: Implement**

In `soundcloud/mirror.py`, generalise the protocol picker and add the field:

```python
_HLS_PROTOCOL = "hls"
_PROGRESSIVE_PROTOCOL = "progressive"


def _first_url_for_protocol(transcodings: list, protocol: str) -> str:
    for t in (transcodings or []):
        if (t.get("format") or {}).get("protocol") == protocol:
            return t.get("url", "") or ""
    return ""


def _first_hls_url(transcodings: list) -> str:
    """Pick the first HLS transcoding URL."""
    return _first_url_for_protocol(transcodings, _HLS_PROTOCOL)
```

and in `_track_from_raw`, after the `"stream_url"` entry:

```python
        "stream_url": _first_hls_url(transcodings),
        "progressive_url": _first_url_for_protocol(transcodings, _PROGRESSIVE_PROTOCOL),
```

(`_first_hls_url` is kept as a named function because `tests/soundcloud/test_mirror.py` and `soundcloud/discovery.py` may reference it.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/python -m pytest tests/soundcloud/ -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add soundcloud/mirror.py tests/soundcloud/test_mirror.py
git commit -m "feat(sc): capture progressive transcoding URL on track objects"
```

---

### Task 7: Rewrite `/sc/preview`; fix `/preview`'s hardcoded yt-dlp path (item 0a)

**Files:**
- Modify: `sWebExt/py_server/server.py:2040-2049` (`sc_preview`) — full rewrite
- Modify: `sWebExt/py_server/server.py:2176` (the hardcoded `".venv/bin/yt-dlp"` inside `preview()`)
- Test: `tests/server/test_routes.py` (append)

**Interfaces:**
- Consumes: `GET /sc/preview?progressive_url=<url>` (new param name; the old `stream_url` param is accepted as a fallback so nothing that still sends it breaks).
- Produces: `{"status":"ok","stream_url":"<CDN mp3 url>"}` on success; `{"status":"unavailable","reason":…}` when the track has no progressive transcoding, when no SC client is configured, or when SC returns no `url`; HTTP 400 when neither param is given; HTTP 500 `{"status":"error","error":…}` on upstream exception. **No HLS proxying.**
- Task 8's frontend calls this route.
- `_YT_DLP` is defined at server.py:48 (`shutil.which("yt-dlp") or os.path.join(os.path.dirname(sys.executable), "yt-dlp")`) and already used correctly by `/yt/search` (server.py:1797).

- [ ] **Step 1: Write the failing tests**

Append to `tests/server/test_routes.py`:

```python
# ── /sc/preview (progressive resolution) ──────────────────────────────────────

def _sc_client_stub(client_id="CID"):
    return MagicMock(client_id=client_id)


def test_sc_preview_resolves_progressive_to_cdn_url(client):
    resp_stub = MagicMock()
    resp_stub.json.return_value = {"url": "https://cf-media.sndcdn.com/x.mp3?Policy=abc"}
    resp_stub.raise_for_status.return_value = None
    with patch("sWebExt.py_server.server._get_sc_client", return_value=_sc_client_stub()), \
         patch("requests.get", return_value=resp_stub) as rget:
        resp = client.get("/sc/preview?progressive_url=https://api-v2.soundcloud.com/media/1/progressive")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["status"] == "ok"
    assert data["stream_url"] == "https://cf-media.sndcdn.com/x.mp3?Policy=abc"
    assert rget.call_args.kwargs["params"]["client_id"] == "CID"


def test_sc_preview_unavailable_without_progressive_url(client):
    with patch("sWebExt.py_server.server._get_sc_client", return_value=_sc_client_stub()):
        resp = client.get("/sc/preview?progressive_url=")
    assert resp.status_code == 400


def test_sc_preview_unavailable_when_sc_returns_no_url(client):
    resp_stub = MagicMock()
    resp_stub.json.return_value = {}
    resp_stub.raise_for_status.return_value = None
    with patch("sWebExt.py_server.server._get_sc_client", return_value=_sc_client_stub()), \
         patch("requests.get", return_value=resp_stub):
        resp = client.get("/sc/preview?progressive_url=https://api-v2.soundcloud.com/media/1/progressive")
    assert resp.status_code == 200
    assert json.loads(resp.data)["status"] == "unavailable"


def test_sc_preview_unavailable_without_client(client):
    with patch("sWebExt.py_server.server._get_sc_client", return_value=None):
        resp = client.get("/sc/preview?progressive_url=https://api-v2.soundcloud.com/media/1/progressive")
    assert resp.status_code == 200
    assert json.loads(resp.data)["status"] == "unavailable"


def test_sc_preview_error_on_upstream_exception(client):
    with patch("sWebExt.py_server.server._get_sc_client", return_value=_sc_client_stub()), \
         patch("requests.get", side_effect=Exception("boom")):
        resp = client.get("/sc/preview?progressive_url=https://api-v2.soundcloud.com/media/1/progressive")
    assert resp.status_code == 500
    assert json.loads(resp.data)["status"] == "error"


def test_sc_preview_accepts_legacy_stream_url_param(client):
    resp_stub = MagicMock()
    resp_stub.json.return_value = {"url": "https://cf-media.sndcdn.com/y.mp3"}
    resp_stub.raise_for_status.return_value = None
    with patch("sWebExt.py_server.server._get_sc_client", return_value=_sc_client_stub()), \
         patch("requests.get", return_value=resp_stub):
        resp = client.get("/sc/preview?stream_url=https://api-v2.soundcloud.com/media/1/progressive")
    assert json.loads(resp.data)["stream_url"] == "https://cf-media.sndcdn.com/y.mp3"


def test_preview_route_uses_resolved_yt_dlp_binary(client):
    """/preview must invoke _YT_DLP, never a hardcoded .venv path."""
    import sWebExt.py_server.server as srv
    import subprocess as _sp
    completed = _sp.CompletedProcess(
        args=[], returncode=0,
        stdout=json.dumps({"url": "https://stream", "title": "T", "uploader": "A"}),
        stderr="")
    with patch("subprocess.run", return_value=completed) as srun:
        resp = client.get("/preview?source=yt&artist=a&title=t")
    assert resp.status_code == 200
    assert srun.call_args.args[0][0] == srv._YT_DLP
    assert ".venv/bin/yt-dlp" not in srun.call_args.args[0][0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest tests/server/test_routes.py -k "sc_preview or preview_route_uses" -v`
Expected: 7 FAILED (the old `sc_preview` ignores `progressive_url` and never calls `requests.get`; the `/preview` assert fails on `.venv/bin/yt-dlp`).

- [ ] **Step 3: Implement**

Replace `sWebExt/py_server/server.py:2040-2049` in full:

```python
@app.route("/sc/preview", methods=["GET"])
def sc_preview():
    """Resolve a SoundCloud progressive transcoding to a directly playable CDN mp3.

    SoundCloud's progressive transcoding endpoint answers {"url": "<signed CDN mp3>"}
    when queried with a valid client_id. HLS manifests are never proxied — a track
    with no progressive transcoding is simply reported unavailable.
    """
    progressive_url = (request.args.get("progressive_url")
                       or request.args.get("stream_url") or "").strip()
    if not progressive_url:
        return jsonify({"status": "error", "error": "progressive_url required"}), 400
    sc = _get_sc_client()
    if not sc:
        return jsonify({"status": "unavailable", "reason": "sc_client_id not configured"})
    try:
        import requests
        resp = requests.get(progressive_url,
                            params={"client_id": sc.client_id},
                            timeout=10)
        resp.raise_for_status()
        cdn_url = (resp.json() or {}).get("url") or ""
        if not cdn_url:
            return jsonify({"status": "unavailable", "reason": "no progressive stream"})
        return jsonify({"status": "ok", "stream_url": cdn_url})
    except Exception as e:
        logger.exception("[SC] preview resolve failed")
        return jsonify({"status": "error", "error": str(e)}), 500
```

Then fix item 0a — change server.py:2176 from

```python
                    [".venv/bin/yt-dlp", "--dump-json", "-f", "bestaudio/best",
```

to

```python
                    [_YT_DLP, "--dump-json", "-f", "bestaudio/best",
```

leaving the rest of `preview()` (including `cwd=_PROJECT_ROOT`) unchanged.

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/python -m pytest tests/server/test_routes.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add sWebExt/py_server/server.py tests/server/test_routes.py
git commit -m "feat(server): /sc/preview resolves progressive stream server-side; /preview uses _YT_DLP"
```

---

### Task 8: Frontend — preview buttons + shared mini-player

**Files:**
- Modify: `web/templates/app.html` (add the mini-player element between the last `<section>` and `<nav>`)
- Modify: `web/static/app.js` — a module-scope player singleton near the top (after the `screens`/`_currentScreen` declarations at lines 31-32); a `▶` button in `buildResultRow` (app.js:909-980, inserted before `getBtn` is appended at 966); pass `progressive_url` through in `doSearch`'s SC branch (app.js:1241-1253) and in `scTrackToItem` (app.js inside `renderSearch`, the helper used by the artist panel)
- Modify: `web/static/app.css` (append)

**Interfaces:**
- Consumes: `progressive_url` on SC track dicts (Task 6); `GET /sc/preview?progressive_url=…` (Task 7); `GET /preview?source=yt&url=…` (existing, server.py:2156-2198).
- One shared `Audio` element: starting a new preview stops the previous one.
- Button states mirror the existing acquire-button pattern (app.js:945-964): `…` while resolving, `!` flash for 2 s on error.

- [ ] **Step 1: Add the mini-player to the shell**

In `web/templates/app.html`, insert immediately before `<nav>`:

```html
<div id="miniplayer" class="miniplayer" style="display:none">
  <button id="mp-toggle" class="mp-btn">▶</button>
  <span id="mp-title" class="mp-title"></span>
  <button id="mp-close" class="mp-btn mp-close">✕</button>
</div>
```

- [ ] **Step 2: Add the player singleton in app.js**

Insert after `let _currentScreen = null;` (app.js:32):

```javascript
// ── Shared audio preview (one element for the whole app) ─────────────────────
const Player = (() => {
  let audio = null, curKey = null, onStop = null;

  function els() {
    return {
      bar:    document.getElementById('miniplayer'),
      toggle: document.getElementById('mp-toggle'),
      title:  document.getElementById('mp-title'),
      close:  document.getElementById('mp-close'),
    };
  }

  function ensure() {
    if (audio) return audio;
    audio = new Audio();
    audio.preload = 'none';
    const e = els();
    audio.onplay  = () => { e.toggle.textContent = '❚❚'; };
    audio.onpause = () => { e.toggle.textContent = '▶'; };
    audio.onended = () => stop();
    e.toggle.onclick = () => { if (audio.paused) audio.play().catch(() => {}); else audio.pause(); };
    e.close.onclick = () => stop();
    return audio;
  }

  function stop() {
    if (audio) { audio.pause(); audio.removeAttribute('src'); audio.load(); }
    const e = els();
    if (e.bar) e.bar.style.display = 'none';
    if (e.title) e.title.textContent = '';
    curKey = null;
    if (onStop) { const f = onStop; onStop = null; f(); }
  }

  function play(key, url, label, stopCallback) {
    const a = ensure();
    if (onStop) { const f = onStop; onStop = null; f(); }
    onStop = stopCallback || null;
    curKey = key;
    const e = els();
    e.title.textContent = label || '';
    e.bar.style.display = '';
    a.src = url;
    a.play().catch(() => {});
  }

  return {play, stop, current: () => curKey};
})();
```

- [ ] **Step 3: Add the `▶` button in `buildResultRow`**

Insert in `buildResultRow` immediately before `row.appendChild(getBtn);` (app.js:966):

```javascript
    const playBtn = document.createElement('button');
    playBtn.className = 'play';
    playBtn.textContent = '▶';
    const playKey = (item.source || '') + '|' + (item.url || '') + '|' + (item.title || '');
    function resetPlay() { playBtn.textContent = '▶'; playBtn.classList.remove('on'); }
    playBtn.onclick = async () => {
      if (Player.current() === playKey) { Player.stop(); return; }
      playBtn.disabled = true;
      playBtn.textContent = '…';
      try {
        let data;
        if (item.source === 'sc') {
          if (!item.progressive_url) throw new Error('no progressive stream');
          data = await API('/sc/preview?progressive_url=' + encodeURIComponent(item.progressive_url));
        } else {
          data = await API('/preview?source=yt&url=' + encodeURIComponent(item.url || '') +
                           '&artist=' + encodeURIComponent(item.artist || '') +
                           '&title=' + encodeURIComponent(item.title || ''));
        }
        if (data.status !== 'ok' || !data.stream_url) throw new Error(data.reason || 'unavailable');
        playBtn.textContent = '❚❚';
        playBtn.classList.add('on');
        Player.play(playKey, data.stream_url,
                    [item.artist, item.title].filter(Boolean).join(' — '), resetPlay);
      } catch(e) {
        playBtn.textContent = '!';
        setTimeout(resetPlay, 2000);
      } finally {
        playBtn.disabled = false;
      }
    };
    row.appendChild(playBtn);
```

- [ ] **Step 4: Pass `progressive_url` through**

In `doSearch`'s SC branch (app.js:1241-1253), add one key to the item literal:

```javascript
          artwork_url: t.artwork_url || null,
          progressive_url: t.progressive_url || '',
```

and do the same in the `scTrackToItem` helper (the artist-panel mapper defined inside `renderSearch`), so panel rows preview too:

```javascript
      artwork_url: t.artwork_url || null,
      progressive_url: t.progressive_url || '',
```

- [ ] **Step 5: Add CSS**

Append to `web/static/app.css`:

```css
/* ---- PREVIEW ---- */
.result .play{flex:none;background:none;border:1px solid var(--line);border-radius:50%;width:26px;height:26px;
  color:var(--mut);font-size:.66rem;cursor:pointer;transition:color .15s,border-color .15s}
.result .play:hover{color:var(--acid);border-color:var(--acid-dim)}
.result .play.on{color:var(--acid);border-color:var(--acid)}
.miniplayer{position:fixed;bottom:calc(56px + env(safe-area-inset-bottom));left:50%;transform:translateX(-50%);
  width:100%;max-width:430px;display:flex;align-items:center;gap:10px;z-index:39;
  background:rgba(10,10,12,.96);backdrop-filter:blur(12px);border-top:1px solid var(--line);padding:8px 14px}
.mp-title{flex:1;min-width:0;font-family:'JetBrains Mono';font-size:.62rem;color:var(--mut);
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.mp-btn{flex:none;background:none;border:0;color:var(--acid);font-size:.8rem;cursor:pointer;padding:4px 6px}
.mp-close{color:var(--mut)}
.mp-close:hover{color:var(--danger)}
```

- [ ] **Step 6: Regression check**

Run: `venv/bin/python -m pytest tests/ -q`
Expected: all PASS.

- [ ] **Step 7: Manual browser check**

`systemctl --user restart amusicserver && sleep 3`, then `http://localhost:5000#search`:
1. Search a term. Every row has a `▶` next to `+`.
2. Click `▶` on a SoundCloud row — button shows `…`, then audio starts and the mini-player bar appears above the nav with the track title.
3. Click `▶` on a different row — the first stops (its button resets to `▶`) and the new one plays. Only one audio stream at a time.
4. Mini-player play/pause toggles; `✕` stops and hides the bar.
5. Click `▶` on a YouTube row — resolves via `/preview` and plays.
6. A track with no progressive transcoding flashes `!` and resets after 2 s.

- [ ] **Step 8: Commit**

```bash
git add web/templates/app.html web/static/app.js web/static/app.css
git commit -m "feat(web): audio preview buttons in search + shared mini-player bar"
```

---

# GROUP 4 — Share UI (design item 4, fixing 0b properly)

`/share/import` (server.py:2394-2400) redirects to `/explore`, a route that **does not exist** in `server.py` — any incoming share link 404s today.

### Task 9: `/share/import` redirects into the Library screen

**Files:**
- Modify: `sWebExt/py_server/server.py:2394-2400`
- Test: `tests/server/test_routes.py` (append)

**Interfaces:**
- Produces: `GET /share/import?v=1&d=<payload>` → 302 to `/?share=<payload>#library`. With no `d`, → 302 to `/#library`.
- `<payload>` is the raw base64url `d` value produced by `share/codec.py:encode_track` (which builds `http://{hostname}:5000/share/import?v=1&d={encoded}`). It must be URL-quoted in the redirect target so `+`/`=` survive; `share.codec.decode` matches `[?&]d=([A-Za-z0-9_=-]+)`, and Task 10's frontend reassembles a full `?d=…` string before POSTing to `/share/parse`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/server/test_routes.py`:

```python
# ── /share/import redirect ────────────────────────────────────────────────────

def test_share_import_redirects_to_library_with_payload(client):
    resp = client.get("/share/import?v=1&d=eyJhIjoxfQ")
    assert resp.status_code in (301, 302)
    assert resp.headers["Location"] == "/?share=eyJhIjoxfQ#library"


def test_share_import_without_payload_redirects_to_library(client):
    resp = client.get("/share/import")
    assert resp.status_code in (301, 302)
    assert resp.headers["Location"] == "/#library"


def test_share_import_never_redirects_to_explore(client):
    resp = client.get("/share/import?v=1&d=abc")
    assert "/explore" not in resp.headers["Location"]


def test_share_import_quotes_payload(client):
    resp = client.get("/share/import?v=1&d=aGVsbG8%3D")
    assert "explore" not in resp.headers["Location"]
    assert resp.headers["Location"].startswith("/?share=")
    assert "#library" in resp.headers["Location"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest tests/server/test_routes.py -k share_import -v`
Expected: 4 FAILED — `Location` is `/explore#import:…`.

- [ ] **Step 3: Implement**

Replace server.py:2394-2400:

```python
@app.route("/share/import", methods=["GET"])
def share_import():
    """Receive a shared link — hand the payload to the Library screen's Share card."""
    import urllib.parse as _up
    d = request.args.get("d", "")
    if d:
        return redirect(f"/?share={_up.quote(d, safe='')}#library")
    return redirect("/#library")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/python -m pytest tests/server/test_routes.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add sWebExt/py_server/server.py tests/server/test_routes.py
git commit -m "fix(server): /share/import redirects to the Library share card, not deleted /explore"
```

---

### Task 10: Frontend — Share card (send + receive) and the shared import-progress helper

**Files:**
- Modify: `web/static/app.js` — new card inside `renderLibrary` (633-843), appended after the Title-cleanup wrapper (`el.appendChild(suffixWrapper)`, app.js:818); a module-scope `runImportJob` helper (place it next to `makeBar`'s consumers, above `renderLibrary` at line 633); the `?share=` read in the boot block (app.js:2195-2197)
- Modify: `web/static/app.css` (append)

**Interfaces:**
- Consumes: `GET /playlists` → `{status, playlists:[{name,id,songCount}]}` (server.py:1145-1177); `GET /share/code?playlist_id=<id>` → `{status:"ok", text:"PLAYLIST:…"}` (server.py:2347-2375 — returns `text`, **not** a URL); `POST /share/parse` `{text}` → `{status:"ok", type, name, tracks:[{artist,title,url}]}` (server.py:2378-2391, `share/codec.py:decode`); `POST /import/tracks` `{tracks, playlist_name}` → `{status:"ok", job_id, queued}` (server.py:2203-2318); `GET /import/status?job_id=` → `{status:"ok", total, done, errors, tracks:[…]}` (server.py:2321-2327).
- Produces: `runImportJob(tracks, playlistName, barEl, fillEl, statusEl)` — a shared helper Task 11 reuses.
- Note: `/import/tracks` only creates a Navidrome playlist when `playlist_name` is set **and is not the literal string `"Import"`** (server.py:2285). The UI must therefore default the name field to something else and never send `"Import"` silently.

- [ ] **Step 1: Add the shared import-progress helper**

Insert above `async function renderLibrary()` (before app.js:633):

```javascript
// ── Shared batch-import driver (used by the Share and Import cards) ──────────
async function runImportJob(tracks, playlistName, fillEl, statusEl) {
  statusEl.textContent = 'queuing ' + tracks.length + ' tracks…';
  const start = await API('/import/tracks', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({tracks, playlist_name: playlistName}),
  });
  if (start.status !== 'ok' || !start.job_id) throw new Error(start.error || 'import failed to start');

  return new Promise((resolve, reject) => {
    const timer = setInterval(async () => {
      try {
        const s = await API('/import/status?job_id=' + encodeURIComponent(start.job_id));
        const total = s.total || 0;
        const done = s.done || 0;
        const errs = s.errors || 0;
        fillEl.style.width = total ? Math.round((done / total) * 100) + '%' : '0%';
        statusEl.textContent = done + '/' + total + ' done' + (errs ? (' · ' + errs + ' failed') : '');
        if (total && done >= total) {
          clearInterval(timer);
          resolve({total, done, errors: errs});
        }
      } catch(e) {
        clearInterval(timer);
        reject(e);
      }
    }, 2000);
  });
}
```

- [ ] **Step 2: Add the Share card**

Insert in `renderLibrary` after `el.appendChild(suffixWrapper);` (app.js:818):

```javascript
  // ── 5. Share ─────────────────────────────────────────────────────────────────
  const shareCard = document.createElement('div');
  shareCard.className = 'card';
  const shareHead = document.createElement('div');
  shareHead.className = 'tool-head';
  const shareTitle = document.createElement('b');
  shareTitle.textContent = 'Share';
  const shareDesc = document.createElement('span');
  shareDesc.textContent = 'send a playlist to another server, or receive one';
  shareHead.appendChild(shareTitle);
  shareHead.appendChild(shareDesc);
  shareCard.appendChild(shareHead);

  // — Send half —
  const sendWrap = document.createElement('div');
  sendWrap.className = 'share-half';
  const sendLabel = document.createElement('div');
  sendLabel.className = 'share-sub';
  sendLabel.textContent = 'send';
  const plSel = document.createElement('select');
  plSel.className = 'txt';
  const codeBtn = document.createElement('button');
  codeBtn.className = 'btn ghost';
  codeBtn.textContent = 'get code';
  const codeArea = document.createElement('textarea');
  codeArea.className = 'txt share-code';
  codeArea.rows = 5;
  codeArea.readOnly = true;
  codeArea.style.display = 'none';
  const copyBtn = document.createElement('button');
  copyBtn.className = 'btn ghost';
  copyBtn.textContent = 'copy';
  copyBtn.style.display = 'none';
  const sendStatus = document.createElement('span');
  sendStatus.className = 'share-status';

  const sendRow = document.createElement('div');
  sendRow.className = 'frow';
  sendRow.appendChild(plSel);
  sendRow.appendChild(codeBtn);
  sendWrap.appendChild(sendLabel);
  sendWrap.appendChild(sendRow);
  sendWrap.appendChild(codeArea);
  const sendBtnRow = document.createElement('div');
  sendBtnRow.className = 'frow';
  sendBtnRow.appendChild(copyBtn);
  sendBtnRow.appendChild(sendStatus);
  sendWrap.appendChild(sendBtnRow);
  shareCard.appendChild(sendWrap);

  codeBtn.onclick = async () => {
    if (!plSel.value) return;
    codeBtn.disabled = true;
    sendStatus.textContent = 'building…';
    try {
      const r = await API('/share/code?playlist_id=' + encodeURIComponent(plSel.value));
      if (r.status !== 'ok') throw new Error(r.error || 'failed');
      codeArea.value = r.text || '';
      codeArea.style.display = '';
      copyBtn.style.display = '';
      sendStatus.textContent = '';
    } catch(e) {
      sendStatus.textContent = 'Error: ' + (e.message || 'unknown');
    } finally {
      codeBtn.disabled = false;
    }
  };
  copyBtn.onclick = async () => {
    try {
      await navigator.clipboard.writeText(codeArea.value);
      sendStatus.textContent = 'copied';
    } catch(e) {
      codeArea.select();
      sendStatus.textContent = 'select + copy manually';
    }
    setTimeout(() => { sendStatus.textContent = ''; }, 2000);
  };

  // — Receive half —
  const recvWrap = document.createElement('div');
  recvWrap.className = 'share-half';
  const recvLabel = document.createElement('div');
  recvLabel.className = 'share-sub';
  recvLabel.textContent = 'receive';
  const recvArea = document.createElement('textarea');
  recvArea.className = 'txt';
  recvArea.rows = 4;
  recvArea.placeholder = 'paste a share link or a PLAYLIST: block…';
  const parseBtn = document.createElement('button');
  parseBtn.className = 'btn ghost';
  parseBtn.textContent = 'preview';
  const recvPreview = document.createElement('div');
  recvPreview.className = 'share-preview';
  const recvName = document.createElement('input');
  recvName.className = 'txt';
  recvName.type = 'text';
  recvName.placeholder = 'playlist name';
  recvName.style.display = 'none';
  const importBtn = document.createElement('button');
  importBtn.className = 'btn run';
  importBtn.textContent = 'import';
  importBtn.style.display = 'none';
  const recvBar = makeBar();
  recvBar.wrap.style.display = 'none';
  const recvStatus = document.createElement('span');
  recvStatus.className = 'share-status';

  recvWrap.appendChild(recvLabel);
  recvWrap.appendChild(recvArea);
  const recvBtnRow = document.createElement('div');
  recvBtnRow.className = 'frow';
  recvBtnRow.appendChild(parseBtn);
  recvBtnRow.appendChild(recvStatus);
  recvWrap.appendChild(recvBtnRow);
  recvWrap.appendChild(recvPreview);
  const recvGo = document.createElement('div');
  recvGo.className = 'frow';
  recvGo.appendChild(recvName);
  recvGo.appendChild(importBtn);
  recvWrap.appendChild(recvGo);
  recvWrap.appendChild(recvBar.wrap);
  shareCard.appendChild(recvWrap);
  el.appendChild(shareCard);

  let recvTracks = [];
  parseBtn.onclick = async () => {
    const text = recvArea.value.trim();
    if (!text) return;
    parseBtn.disabled = true;
    recvStatus.textContent = 'parsing…';
    recvPreview.textContent = '';
    try {
      const r = await API('/share/parse', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({text}),
      });
      if (r.status !== 'ok') throw new Error(r.error || 'could not parse');
      recvTracks = r.tracks || [];
      renderTrackPreview(recvPreview, recvTracks);
      recvName.value = r.name || 'Shared';
      recvName.style.display = '';
      importBtn.style.display = '';
      recvStatus.textContent = recvTracks.length + ' tracks';
    } catch(e) {
      recvStatus.textContent = 'Error: ' + (e.message || 'unknown');
    } finally {
      parseBtn.disabled = false;
    }
  };

  importBtn.onclick = async () => {
    if (!recvTracks.length) return;
    importBtn.disabled = true;
    recvBar.wrap.style.display = '';
    try {
      const name = (recvName.value || '').trim() || 'Shared';
      await runImportJob(recvTracks, name, recvBar.fill, recvStatus);
    } catch(e) {
      recvStatus.textContent = 'Error: ' + (e.message || 'unknown');
    } finally {
      importBtn.disabled = false;
    }
  };

  // Populate the playlist dropdown (after the synchronous appends, per the
  // no-double-card convention documented in ROADMAP)
  try {
    const pls = await API('/playlists');
    (pls.playlists || []).forEach(p => {
      const o = document.createElement('option');
      o.value = p.id;
      o.textContent = p.name + ' (' + (p.songCount || 0) + ')';
      plSel.appendChild(o);
    });
    if (!(pls.playlists || []).length) sendStatus.textContent = 'no playlists found';
  } catch(e) {
    sendStatus.textContent = 'playlists unavailable';
    codeBtn.disabled = true;
  }

  // Inbound share payload from /share/import
  if (window.__pendingShare) {
    recvArea.value = window.__pendingShare;
    window.__pendingShare = null;
    parseBtn.click();
  }
```

Also add the shared preview renderer above `renderLibrary` (next to `runImportJob`):

```javascript
function renderTrackPreview(container, tracks, limit) {
  container.textContent = '';
  const max = limit || 50;
  tracks.slice(0, max).forEach(t => {
    const row = document.createElement('div');
    row.className = 'imp-row';
    row.textContent = [t.artist, t.title].filter(Boolean).join(' — ') || '(untitled)';
    container.appendChild(row);
  });
  if (tracks.length > max) {
    const more = document.createElement('div');
    more.className = 'imp-row imp-more';
    more.textContent = '…and ' + (tracks.length - max) + ' more';
    container.appendChild(more);
  }
}
```

`makeBar()` is defined inside `renderLibrary` (app.js:663-672) and returns the wrap/fill pair used by the enrich and repair cards — reuse it unchanged.

- [ ] **Step 3: Read `?share=` at boot**

Replace app.js:2195-2197 (the "Initial screen" block at the end of the `DOMContentLoaded` handler) with:

```javascript
  // Inbound share link: /share/import redirects here as /?share=<payload>#library
  const params = new URLSearchParams(location.search);
  const sharePayload = params.get('share');
  let forced = null;
  if (sharePayload) {
    window.__pendingShare = '?d=' + sharePayload;   // decode() matches [?&]d=
    forced = 'library';
    params.delete('share');
    const qs = params.toString();
    history.replaceState({}, '', location.pathname + (qs ? '?' + qs : '') + location.hash);
  }

  // Initial screen
  const initial = forced || location.hash.slice(1);
  show(screens[initial] ? initial : 'mixes');
```

- [ ] **Step 4: Add CSS**

Append to `web/static/app.css`:

```css
/* ---- SHARE / IMPORT CARDS ---- */
.share-half{border-top:1px solid var(--line);margin-top:10px;padding-top:10px}
.share-sub{font-family:'JetBrains Mono';font-size:.58rem;letter-spacing:.12em;text-transform:uppercase;color:var(--mut);margin-bottom:6px}
.share-code{font-family:'JetBrains Mono';font-size:.6rem;width:100%;resize:vertical}
.share-status{font-family:'JetBrains Mono';font-size:.62rem;color:var(--mut)}
.share-preview,.imp-preview{max-height:180px;overflow-y:auto;margin:8px 0}
.imp-row{font-family:'JetBrains Mono';font-size:.62rem;color:var(--mut);padding:2px 0;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.imp-more{color:var(--acid)}
```

- [ ] **Step 5: Regression check**

Run: `venv/bin/python -m pytest tests/ -q`
Expected: all PASS.

- [ ] **Step 6: Manual browser check**

`systemctl --user restart amusicserver && sleep 3`:
1. `http://localhost:5000#library` → "Share" card lists Navidrome playlists in the dropdown; "get code" fills the textarea with a `PLAYLIST:` block; "copy" reports "copied".
2. Paste that block into the receive box → "preview" lists the tracks and pre-fills the playlist name → "import" shows a progress bar counting to `N/N done`.
3. Visit `http://localhost:5000/share/import?v=1&d=<any base64url payload>` — the browser lands on the Library screen with the receive box pre-filled and already previewed, and the URL bar shows `http://localhost:5000/#library` (no `?share=` left over).

- [ ] **Step 7: Commit**

```bash
git add web/static/app.js web/static/app.css
git commit -m "feat(web): share card — send playlist codes, receive and import shared playlists"
```

---

# GROUP 5 — Batch import card (design item 5)

### Task 11: Frontend — "Import playlist" card (absorbs the Spotify surface)

**Files:**
- Modify: `web/static/app.js` — new card inside `renderLibrary`, appended after the Share card
- Modify: `web/static/app.css` (already covered by Task 10's `.imp-*` rules; add only what is missing)

**Interfaces:**
- Consumes: `POST /spotify/playlist` `{url}` → `{status:"ok", name, tracks:[{id,uri,title,artist,album,artwork_url,duration_ms,playcount}]}` (server.py:2118-2134 → `spotify/queries.py:get_playlist`). When the Spotify cipher fetch fails the route answers `{"status":"unavailable","reason":"cipher fetch failed"}` at HTTP 200 — surface that reason in the card.
- Consumes: `runImportJob` and `renderTrackPreview` from Task 10.
- Exportify CSV is parsed **in the browser** — there is no upload endpoint. Columns are `Track Name` and `Artist Name(s)`.
- Tracks are posted **without** a `url`: the import worker already falls through to `download_url(f"ytsearch:{artist} {title}", song_dir)` (server.py:2229-2269) when no url is present.
- **No other Spotify UI is added.** The Spotify routes stay backend-only apart from this input.
- Server-side tests: none. All three routes already exist and are covered.

- [ ] **Step 1: Add the card**

Insert in `renderLibrary` after `el.appendChild(shareCard);`:

```javascript
  // ── 6. Import playlist ───────────────────────────────────────────────────────
  const impCard = document.createElement('div');
  impCard.className = 'card';
  const impHead = document.createElement('div');
  impHead.className = 'tool-head';
  const impTitle = document.createElement('b');
  impTitle.textContent = 'Import playlist';
  const impDesc = document.createElement('span');
  impDesc.textContent = 'Spotify link, Exportify CSV, or "Artist - Title" lines';
  impHead.appendChild(impTitle);
  impHead.appendChild(impDesc);
  impCard.appendChild(impHead);

  const impArea = document.createElement('textarea');
  impArea.className = 'txt';
  impArea.rows = 4;
  impArea.placeholder = 'https://open.spotify.com/playlist/…  — or one "Artist - Title" per line';

  const impFile = document.createElement('input');
  impFile.type = 'file';
  impFile.accept = '.csv,text/csv';
  impFile.className = 'txt';

  const loadBtn = document.createElement('button');
  loadBtn.className = 'btn ghost';
  loadBtn.textContent = 'load';
  const impStatus = document.createElement('span');
  impStatus.className = 'share-status';
  const impPreview = document.createElement('div');
  impPreview.className = 'imp-preview';
  const impName = document.createElement('input');
  impName.className = 'txt';
  impName.type = 'text';
  impName.placeholder = 'playlist name';
  impName.style.display = 'none';
  const impGo = document.createElement('button');
  impGo.className = 'btn run';
  impGo.textContent = 'import';
  impGo.style.display = 'none';
  const impBar = makeBar();
  impBar.wrap.style.display = 'none';

  impCard.appendChild(impArea);
  const impFileRow = document.createElement('div');
  impFileRow.className = 'frow';
  impFileRow.appendChild(impFile);
  impCard.appendChild(impFileRow);
  const impBtnRow = document.createElement('div');
  impBtnRow.className = 'frow';
  impBtnRow.appendChild(loadBtn);
  impBtnRow.appendChild(impStatus);
  impCard.appendChild(impBtnRow);
  impCard.appendChild(impPreview);
  const impGoRow = document.createElement('div');
  impGoRow.className = 'frow';
  impGoRow.appendChild(impName);
  impGoRow.appendChild(impGo);
  impCard.appendChild(impGoRow);
  impCard.appendChild(impBar.wrap);
  el.appendChild(impCard);

  let impTracks = [];

  function showImpTracks(tracks, name) {
    impTracks = tracks;
    renderTrackPreview(impPreview, impTracks);
    impName.value = name || 'Import playlist';
    impName.style.display = '';
    impGo.style.display = '';
    impStatus.textContent = impTracks.length + ' tracks';
  }

  function parseTextLines(text) {
    return text.split('\n').map(l => l.trim()).filter(Boolean).map(line => {
      const i = line.indexOf(' - ');
      if (i === -1) return {artist: '', title: line};
      return {artist: line.slice(0, i).trim(), title: line.slice(i + 3).trim()};
    }).filter(t => t.title);
  }

  // Minimal RFC4180-ish CSV splitter (Exportify quotes fields containing commas)
  function csvSplit(line) {
    const out = [];
    let cur = '', inQ = false;
    for (let i = 0; i < line.length; i++) {
      const c = line[i];
      if (inQ) {
        if (c === '"' && line[i + 1] === '"') { cur += '"'; i++; }
        else if (c === '"') inQ = false;
        else cur += c;
      } else if (c === '"') inQ = true;
      else if (c === ',') { out.push(cur); cur = ''; }
      else cur += c;
    }
    out.push(cur);
    return out;
  }

  function parseExportifyCsv(text) {
    const lines = text.split(/\r?\n/).filter(l => l.trim());
    if (!lines.length) return [];
    const header = csvSplit(lines[0]).map(h => h.trim());
    const ti = header.indexOf('Track Name');
    const ai = header.indexOf('Artist Name(s)');
    if (ti === -1) throw new Error('CSV has no "Track Name" column');
    return lines.slice(1).map(l => {
      const cols = csvSplit(l);
      return {
        artist: ai === -1 ? '' : (cols[ai] || '').trim(),
        title: (cols[ti] || '').trim(),
      };
    }).filter(t => t.title);
  }

  impFile.onchange = () => {
    const f = impFile.files && impFile.files[0];
    if (!f) return;
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const tracks = parseExportifyCsv(String(reader.result || ''));
        if (!tracks.length) throw new Error('no tracks found in CSV');
        showImpTracks(tracks, f.name.replace(/\.csv$/i, ''));
      } catch(e) {
        impStatus.textContent = 'Error: ' + (e.message || 'bad CSV');
      }
    };
    reader.onerror = () => { impStatus.textContent = 'could not read file'; };
    reader.readAsText(f);
  };

  loadBtn.onclick = async () => {
    const raw = impArea.value.trim();
    if (!raw) { impStatus.textContent = 'paste a link or some lines first'; return; }
    loadBtn.disabled = true;
    impStatus.textContent = 'loading…';
    impPreview.textContent = '';
    try {
      if (/open\.spotify\.com|spotify:playlist:/i.test(raw)) {
        const r = await API('/spotify/playlist', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({url: raw, limit: 200}),
        });
        if (r.status === 'unavailable') throw new Error('Spotify unavailable: ' + (r.reason || 'unknown'));
        if (r.status !== 'ok') throw new Error(r.error || 'spotify fetch failed');
        const tracks = (r.tracks || []).map(t => ({artist: t.artist || '', title: t.title || ''}))
                                       .filter(t => t.title);
        if (!tracks.length) throw new Error('playlist returned no tracks');
        showImpTracks(tracks, r.name || 'Spotify import');
      } else {
        const tracks = parseTextLines(raw);
        if (!tracks.length) throw new Error('no "Artist - Title" lines found');
        showImpTracks(tracks, 'Import playlist');
      }
    } catch(e) {
      impStatus.textContent = 'Error: ' + (e.message || 'unknown');
    } finally {
      loadBtn.disabled = false;
    }
  };

  impGo.onclick = async () => {
    if (!impTracks.length) return;
    impGo.disabled = true;
    impBar.wrap.style.display = '';
    try {
      const name = (impName.value || '').trim() || 'Import playlist';
      await runImportJob(impTracks, name, impBar.fill, impStatus);
    } catch(e) {
      impStatus.textContent = 'Error: ' + (e.message || 'unknown');
    } finally {
      impGo.disabled = false;
    }
  };
```

- [ ] **Step 2: Regression check**

Run: `venv/bin/python -m pytest tests/ -q`
Expected: all PASS.

- [ ] **Step 3: Manual browser check**

`systemctl --user restart amusicserver && sleep 3`, then `http://localhost:5000#library`:
1. Paste a public Spotify playlist URL → "load" → track list preview with the playlist's real name. (If Spotify's cipher fetch is failing, the card shows `Spotify unavailable: cipher fetch failed` rather than a bare error.)
2. Pick an Exportify CSV → the list populates without any network call; the playlist name defaults to the filename.
3. Paste three `Artist - Title` lines → "load" → three rows.
4. Click "import" → progress bar counts up; when it finishes, the named playlist exists in Navidrome and `logs/import_<name>_failed.txt` lists any failures.

- [ ] **Step 4: Commit**

```bash
git add web/static/app.js web/static/app.css
git commit -m "feat(web): batch import card — Spotify link, Exportify CSV, plain text lines"
```

---

# GROUP 6 — Merge-aware playlists via the Subsonic API (design item 7)

Today both playlist writers overwrite a `.m3u` file wholesale:

- `discover/engine.py:303-306` — `write_weekly_mix(deps.song_dir, acquired_paths + lib_paths, name=profile["name"], cap=cap)`
- `follow/runner.py:91-92` — `assemble_fn(song_dir, paths, playlist_name, playlist_cap)` (default `assemble_fn` bound at runner.py:23-24)

Anything the user adds to or deletes from the playlist inside Navidrome is destroyed on the next run (Navidrome re-imports the m3u), and `discover/subsonic.py:create_or_update_playlist` (143-170) makes it worse for API playlists by deleting and recreating, which loses the playlist ID, owner, public flag and comment.

`discover/engine.py` also **reads** the m3u — `_existing_playlist_basenames` (engine.py:200-202) called at line 282 to keep library picks from repeating. That read-side coupling moves to the API too.

### Task 12: LIVE verification spike — API delete + m3u rename leaves no ghost

**This task must complete and pass before any code touches a real playlist.** The migration deletes an API playlist that Navidrome originally created from an `.m3u` file. If Navidrome re-imports the file (or keeps a ghost row) after the delete, the whole migration strategy is wrong and the plan must stop here for a redesign.

**Files:** none committed. Use a throwaway script under the session scratch directory.

- [ ] **Step 1: Write the spike script**

Write to `/tmp/claude-1000/.../scratchpad/spike_playlist_ghost.py` (scratch, not the repo):

```python
"""Live Navidrome spike: does deleting an m3u-imported playlist leave a ghost?"""
import json, os, sys, time
sys.path.insert(0, os.path.abspath("."))
from discover.subsonic import Subsonic

cfg = json.load(open("config.json", encoding="utf-8"))
sub = Subsonic(cfg["navidrome_url"], cfg["navidrome_user"], cfg["navidrome_pass"])
song_dir = cfg["song_dir"]
NAME = "ZZ_GHOST_SPIKE"
m3u = os.path.join(song_dir, NAME + ".m3u")


def playlists():
    sr = sub._call("getPlaylists.view")
    return (sr.get("playlists", {}) or {}).get("playlist", []) or []


def find(name):
    return [p for p in playlists() if (p.get("name") or "") == name]


def wait_scan(timeout=180):
    sub.start_scan()
    deadline = time.time() + timeout
    while time.time() < deadline:
        sr = sub._call("getScanStatus.view")
        st = sr.get("scanStatus", {}) or {}
        if st and not st.get("scanning"):
            return True
        time.sleep(3)
    return False


# 1. seed an m3u from three real files already in the library
mp3s = []
for root, _, files in os.walk(song_dir):
    for f in sorted(files):
        if f.lower().endswith(".mp3"):
            mp3s.append(f)
    if len(mp3s) >= 3:
        break
assert len(mp3s) >= 3, "need at least 3 mp3s in song_dir"
open(m3u, "w", encoding="utf-8").write("\n".join(["#EXTM3U"] + mp3s[:3]) + "\n")
print("step 1: wrote m3u with 3 entries")

print("step 2: scan ->", wait_scan())
hits = find(NAME)
print("step 2: playlist present after import:", len(hits), "entries:",
      [p.get("songCount") for p in hits])
assert hits, "Navidrome did not import the m3u — check song_dir and scan config"
pid = hits[0]["id"]

# 3. delete via API, THEN rename the m3u to .bak
sub._call("deletePlaylist.view", id=pid)
os.rename(m3u, m3u + ".bak")
print("step 3: deleted via API and renamed m3u -> .m3u.bak")

print("step 4: rescan ->", wait_scan())
after = find(NAME)
print("step 4: playlists named", NAME, "after rescan:", len(after))

# 5. cleanup
for p in after:
    sub._call("deletePlaylist.view", id=p["id"])
if os.path.exists(m3u + ".bak"):
    os.remove(m3u + ".bak")
print("RESULT:", "PASS — no ghost" if not after else "FAIL — ghost playlist survived")
```

- [ ] **Step 2: Run it**

```bash
venv/bin/python /tmp/claude-1000/.../scratchpad/spike_playlist_ghost.py
```

Expected: `RESULT: PASS — no ghost`, and step 4 reports `0`.

Also record from the run output: whether `getScanStatus.view` returned a usable `scanStatus.scanning` boolean, and roughly how long a full scan took — Task 15 depends on both.

- [ ] **Step 3: Decide**

- **PASS** → continue to Task 13.
- **FAIL (ghost survives)** → **stop**. Report to the user: the delete-then-rename migration is unsafe on this Navidrome build; the alternative is to rename the m3u *first*, rescan, then create the API playlist. Do not proceed to Task 16 with the current design.

- [ ] **Step 4: Report the spike result to the user before continuing.** No commit (nothing in the repo changed).

---

### Task 13: `discover/subsonic.py` — playlist and scan-status methods

**Files:**
- Modify: `discover/subsonic.py` (add methods; leave `create_or_update_playlist` in place — server.py:638 and server.py:2301 still call it)
- Test: `tests/discover/test_subsonic.py` (append; reuse `make_client(responses)` at lines 4-11 and the `_make_playlist_client` routing at 88-102 — note the routing order trap: `"getPlaylists"` must be matched **before** `"getPlaylist"`, since the latter is a prefix of the former)

**Interfaces (all new):**
- `get_playlists() -> list` — raw playlist dicts from `getPlaylists.view`
- `find_playlist_id(name) -> str | None` — **casefold** name match (unlike the case-sensitive comparison in `create_or_update_playlist:154`)
- `get_playlist_song_ids(playlist_id) -> list[str]` — ordered ids from `getPlaylist.view`'s `entry` list
- `delete_playlist(playlist_id) -> bool`
- `replace_playlist(playlist_id, song_ids) -> bool` — `createPlaylist.view` with `playlistId=<id>` plus the full song list, which is the Subsonic-spec way to overwrite an existing playlist's contents **while keeping its id, owner, public flag and comment**
- `create_playlist(name, song_ids) -> str` — `createPlaylist.view` with `name`, returns the new id
- `get_scan_status() -> dict` — `{"scanning": bool, "count": int}` from `getScanStatus.view`

Keep the existing bracket-indexed `songId[i]` param convention (`create_or_update_playlist:167`) — `urlencode` on a dict cannot emit repeated keys, and the spike in Task 12 confirms Navidrome accepts this shape.

- [ ] **Step 1: Write the failing tests**

Append to `tests/discover/test_subsonic.py`:

```python
def test_get_playlists_returns_raw_list():
    c = make_client({"getPlaylists": {"subsonic-response": {"playlists": {"playlist": [
        {"id": "1", "name": "Weekly Mix", "songCount": 3},
    ]}}}})
    assert c.get_playlists()[0]["name"] == "Weekly Mix"


def test_find_playlist_id_is_case_insensitive():
    c = make_client({"getPlaylists": {"subsonic-response": {"playlists": {"playlist": [
        {"id": "7", "name": "Weekly Mix"},
    ]}}}})
    assert c.find_playlist_id("weekly mix") == "7"
    assert c.find_playlist_id("Weekly Mix") == "7"


def test_find_playlist_id_returns_none_when_absent():
    c = make_client({"getPlaylists": {"subsonic-response": {"playlists": {}}}})
    assert c.find_playlist_id("nope") is None


def test_get_playlist_song_ids_preserves_order():
    c = make_client({"getPlaylist": {"subsonic-response": {"playlist": {"entry": [
        {"id": "b"}, {"id": "a"}, {"id": "c"},
    ]}}}})
    assert c.get_playlist_song_ids("1") == ["b", "a", "c"]


def test_get_playlist_song_ids_empty_when_no_entries():
    c = make_client({"getPlaylist": {"subsonic-response": {"playlist": {}}}})
    assert c.get_playlist_song_ids("1") == []


def test_replace_playlist_sends_playlist_id_and_all_songs():
    seen = {}

    def fake_fetch(url):
        seen["url"] = url
        return {"subsonic-response": {"status": "ok"}}

    from discover.subsonic import Subsonic
    c = Subsonic("http://nd:4533", "u", "p", fetch_json=fake_fetch)
    assert c.replace_playlist("42", ["s1", "s2"]) is True
    assert "playlistId=42" in seen["url"]
    assert "songId%5B0%5D=s1" in seen["url"]
    assert "songId%5B1%5D=s2" in seen["url"]
    assert "name=" not in seen["url"]


def test_create_playlist_returns_new_id():
    c = make_client({"createPlaylist": {"subsonic-response": {
        "status": "ok", "playlist": {"id": "99"}}}})
    assert c.create_playlist("New", ["a"]) == "99"


def test_delete_playlist_reports_ok():
    c = make_client({"deletePlaylist": {"subsonic-response": {"status": "ok"}}})
    assert c.delete_playlist("5") is True


def test_get_scan_status_parses_scanning_flag():
    c = make_client({"getScanStatus": {"subsonic-response": {
        "status": "ok", "scanStatus": {"scanning": False, "count": 1234}}}})
    st = c.get_scan_status()
    assert st == {"scanning": False, "count": 1234}


def test_get_scan_status_defaults_when_missing():
    c = make_client({"getScanStatus": {"subsonic-response": {"status": "ok"}}})
    assert c.get_scan_status() == {"scanning": False, "count": 0}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest tests/discover/test_subsonic.py -q`
Expected: 10 FAILED with `AttributeError: 'Subsonic' object has no attribute …`.

- [ ] **Step 3: Implement**

Add to `discover/subsonic.py`, after `get_playlist` (line 138-141):

```python
    def get_playlists(self) -> list:
        """All playlists as raw Subsonic dicts."""
        sr = self._call("getPlaylists.view")
        raw = (sr.get("playlists", {}) or {}).get("playlist", []) or []
        if isinstance(raw, dict):
            raw = [raw]
        return raw

    def find_playlist_id(self, name: str):
        """Case-insensitive lookup of a playlist id by name, or None."""
        cf = (name or "").casefold()
        for pl in self.get_playlists():
            if (pl.get("name") or "").casefold() == cf:
                return pl.get("id")
        return None

    def get_playlist_song_ids(self, playlist_id: str) -> list:
        """Ordered song ids currently in the playlist."""
        pl = self.get_playlist(playlist_id)
        entries = pl.get("entry", []) or []
        if isinstance(entries, dict):
            entries = [entries]
        return [e.get("id") for e in entries if e.get("id")]

    def delete_playlist(self, playlist_id: str) -> bool:
        sr = self._call("deletePlaylist.view", id=playlist_id)
        return sr.get("status") == "ok"

    def _playlist_song_params(self, song_ids) -> dict:
        return {f"songId[{i}]": sid for i, sid in enumerate(song_ids)}

    def replace_playlist(self, playlist_id: str, song_ids: list) -> bool:
        """Overwrite an existing playlist's contents, keeping its identity.

        createPlaylist.view with playlistId replaces the song list in place —
        the playlist id, owner, public flag and comment all survive.
        """
        params = {"playlistId": playlist_id}
        params.update(self._playlist_song_params(song_ids))
        sr = self._call("createPlaylist.view", **params)
        return sr.get("status") == "ok" or bool(sr.get("playlist"))

    def create_playlist(self, name: str, song_ids: list) -> str:
        params = {"name": name}
        params.update(self._playlist_song_params(song_ids))
        sr = self._call("createPlaylist.view", **params)
        return (sr.get("playlist", {}) or {}).get("id", "")

    def get_scan_status(self) -> dict:
        sr = self._call("getScanStatus.view")
        st = sr.get("scanStatus", {}) or {}
        return {"scanning": bool(st.get("scanning", False)),
                "count": int(st.get("count") or 0)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/python -m pytest tests/discover/ -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add discover/subsonic.py tests/discover/test_subsonic.py
git commit -m "feat(subsonic): playlist id lookup, in-place replace, delete, and scan status"
```

---

### Task 14: `discover/playlist_sync.py` — the pure merge function

**Files:**
- Create: `discover/playlist_sync.py`
- Create: `tests/discover/test_playlist_sync.py`

**Interfaces:**
- `merge_playlist(current_ids, owned_ids, new_ids, cap) -> {"final": [...], "owned": [...], "evicted": [...], "user": [...]}` — **pure**, no I/O, no clock.
- Semantics (exactly as approved):
  - `user = [i for i in current_ids if i not in owned_ids]` — user-added tracks, in their existing order, **never touched, never evicted, never counted against `cap`**.
  - `surviving = [i for i in owned_ids if i in current_ids]` — engine tracks the user has not deleted. Anything the user deleted is **not re-added**.
  - `engine = surviving + [i for i in new_ids if not already present]` — new discoveries append at the end.
  - `cap` applies to `engine` only; when over, the **oldest** engine entries are evicted from the front.
  - `final = user + engine`.
- The ledger is a plain dict so the merge stays pure; persistence is Task 18's job.

- [ ] **Step 1: Write the failing tests**

Create `tests/discover/test_playlist_sync.py`:

```python
"""Unit tests for the merge-aware playlist sync (discover/playlist_sync.py)."""
import pytest
from discover.playlist_sync import merge_playlist


def test_new_ids_append_to_an_empty_playlist():
    r = merge_playlist(current_ids=[], owned_ids=[], new_ids=["a", "b"], cap=10)
    assert r["final"] == ["a", "b"]
    assert r["owned"] == ["a", "b"]
    assert r["evicted"] == []
    assert r["user"] == []


def test_user_added_tracks_are_preserved_and_kept_first():
    r = merge_playlist(current_ids=["u1", "e1", "u2"], owned_ids=["e1"],
                       new_ids=["e2"], cap=10)
    assert r["user"] == ["u1", "u2"]
    assert r["final"] == ["u1", "u2", "e1", "e2"]
    assert r["owned"] == ["e1", "e2"]


def test_user_deletion_of_an_engine_track_is_respected():
    # e1 was engine-owned but the user removed it from the playlist.
    r = merge_playlist(current_ids=["e2"], owned_ids=["e1", "e2"],
                       new_ids=[], cap=10)
    assert "e1" not in r["final"]
    assert r["owned"] == ["e2"]


def test_deleted_engine_track_is_not_readded_even_if_rediscovered():
    r = merge_playlist(current_ids=["e2"], owned_ids=["e1", "e2"],
                       new_ids=["e2"], cap=10)
    assert r["final"] == ["e2"]
    assert r["owned"] == ["e2"]


def test_cap_evicts_oldest_engine_tracks_only():
    r = merge_playlist(current_ids=["e1", "e2", "e3"], owned_ids=["e1", "e2", "e3"],
                       new_ids=["e4"], cap=3)
    assert r["evicted"] == ["e1"]
    assert r["final"] == ["e2", "e3", "e4"]
    assert r["owned"] == ["e2", "e3", "e4"]


def test_cap_never_counts_or_evicts_user_tracks():
    r = merge_playlist(current_ids=["u1", "u2", "u3", "e1"], owned_ids=["e1"],
                       new_ids=["e2", "e3"], cap=2)
    assert r["user"] == ["u1", "u2", "u3"]
    assert r["evicted"] == ["e1"]
    assert r["final"] == ["u1", "u2", "u3", "e2", "e3"]
    assert len(r["owned"]) == 2


def test_cap_zero_or_none_means_unbounded():
    r = merge_playlist(current_ids=["e1"], owned_ids=["e1"], new_ids=["e2", "e3"], cap=0)
    assert r["evicted"] == []
    assert r["owned"] == ["e1", "e2", "e3"]


def test_new_ids_are_deduped_against_user_tracks():
    # The user already added this track by hand — do not duplicate it.
    r = merge_playlist(current_ids=["x"], owned_ids=[], new_ids=["x", "y"], cap=10)
    assert r["final"] == ["x", "y"]
    assert r["owned"] == ["y"]


def test_new_ids_are_deduped_against_each_other():
    r = merge_playlist(current_ids=[], owned_ids=[], new_ids=["a", "a", "b"], cap=10)
    assert r["owned"] == ["a", "b"]


def test_ordering_of_owned_ids_drives_eviction_not_current_order():
    # owned_ids is the ledger's insertion order = discovery order.
    r = merge_playlist(current_ids=["e3", "e1", "e2"], owned_ids=["e1", "e2", "e3"],
                       new_ids=[], cap=2)
    assert r["evicted"] == ["e1"]
    assert r["owned"] == ["e2", "e3"]


def test_merge_does_not_mutate_its_inputs():
    current, owned, new = ["e1"], ["e1"], ["e2"]
    merge_playlist(current, owned, new, cap=1)
    assert current == ["e1"] and owned == ["e1"] and new == ["e2"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest tests/discover/test_playlist_sync.py -q`
Expected: 11 FAILED with `ModuleNotFoundError: No module named 'discover.playlist_sync'`.

- [ ] **Step 3: Implement**

Create `discover/playlist_sync.py`:

```python
"""Merge-aware Navidrome playlist sync.

Replaces the m3u overwrite in discover/engine.py and follow/runner.py. The
engine owns a subset of a playlist's tracks (recorded in a per-playlist
ledger); everything else in the playlist belongs to the user and is never
touched. User deletions of engine tracks stick — the suggested-TTL dedupe in
discover/state.py keeps them from being immediately rediscovered.
"""
import logging

logger = logging.getLogger(__name__)


def merge_playlist(current_ids, owned_ids, new_ids, cap):
    """Pure merge. Returns {"final", "owned", "evicted", "user"}.

    current_ids -- song ids currently in the Navidrome playlist, in order
    owned_ids   -- song ids the engine believes it owns, in discovery order
    new_ids     -- song ids resolved from this run's downloads
    cap         -- max engine-owned tracks (0/None = unbounded); user tracks
                   are never counted against it and never evicted
    """
    current = list(current_ids or [])
    owned = list(owned_ids or [])
    incoming = list(new_ids or [])

    owned_set = set(owned)
    current_set = set(current)

    user = [i for i in current if i not in owned_set]
    user_set = set(user)

    # User deletions are respected: an owned id absent from the playlist is dropped.
    engine = [i for i in owned if i in current_set]
    engine_set = set(engine)

    for sid in incoming:
        if not sid or sid in engine_set or sid in user_set:
            continue
        engine.append(sid)
        engine_set.add(sid)

    evicted = []
    if cap and len(engine) > cap:
        cut = len(engine) - cap
        evicted = engine[:cut]
        engine = engine[cut:]

    return {"final": user + engine, "owned": engine,
            "evicted": evicted, "user": user}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/python -m pytest tests/discover/test_playlist_sync.py -q`
Expected: 11 PASS.

- [ ] **Step 5: Commit**

```bash
git add discover/playlist_sync.py tests/discover/test_playlist_sync.py
git commit -m "feat(discover): pure merge_playlist() — user tracks preserved, deletions respected"
```

---

### Task 15: Path → song-id resolution with a bounded scan wait

**Files:**
- Modify: `discover/playlist_sync.py`
- Modify: `tests/discover/test_playlist_sync.py`

**Interfaces:**
- `wait_for_scan(subsonic, timeout=120, poll=3.0, sleep_fn=time.sleep, clock=time.monotonic) -> bool` — fires `start_scan()`, then polls `get_scan_status()` until `scanning` is False or the timeout expires. Returns whether the scan settled. Never raises: a client without `get_scan_status` (or an exception from it) falls back to a single fixed sleep and returns False.
- `resolve_paths(subsonic, paths, tag_reader=None) -> (ids, unresolved)` — for each path, read `(artist, title)` from ID3 (via `eyed3`, as `library/scanner.py` does) and match through `subsonic.search_songs(f"{artist} {title}", count=5)`, accepting only a hit whose **title AND artist both** casefold-match. Falls back to matching the file's basename against the hit's `path` when tags are missing. Unresolved paths are returned so the caller can queue them as pending.
- `tag_reader` is injected for tests: `tag_reader(path) -> (artist, title)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/discover/test_playlist_sync.py`:

```python
from types import SimpleNamespace
from discover.playlist_sync import wait_for_scan, resolve_paths


def _tags(mapping):
    return lambda path: mapping.get(path, ("", ""))


def test_wait_for_scan_triggers_scan_and_polls_until_idle():
    calls = {"scan": 0, "status": 0}

    def status():
        calls["status"] += 1
        return {"scanning": calls["status"] < 3, "count": 1}

    sub = SimpleNamespace(start_scan=lambda: calls.__setitem__("scan", calls["scan"] + 1) or True,
                          get_scan_status=status)
    slept = []
    assert wait_for_scan(sub, timeout=60, poll=1, sleep_fn=slept.append) is True
    assert calls["scan"] == 1
    assert calls["status"] == 3


def test_wait_for_scan_gives_up_at_timeout():
    sub = SimpleNamespace(start_scan=lambda: True,
                          get_scan_status=lambda: {"scanning": True, "count": 0})
    ticks = iter([0, 10, 20, 30, 40, 50, 60, 70])
    assert wait_for_scan(sub, timeout=30, poll=10, sleep_fn=lambda s: None,
                         clock=lambda: next(ticks)) is False


def test_wait_for_scan_tolerates_client_without_scan_status():
    sub = SimpleNamespace(start_scan=lambda: True)
    slept = []
    assert wait_for_scan(sub, timeout=30, poll=1, sleep_fn=slept.append) is False
    assert slept   # fell back to a fixed sleep


def test_resolve_paths_requires_both_title_and_artist_to_match():
    sub = SimpleNamespace(search_songs=lambda query, count=5: [
        {"id": "wrong", "title": "Track One", "artist": "Someone Else"},
        {"id": "right", "title": "Track One", "artist": "Artist A"},
    ])
    ids, unresolved = resolve_paths(sub, ["/m/1.mp3"],
                                    tag_reader=_tags({"/m/1.mp3": ("Artist A", "Track One")}))
    assert ids == ["right"]
    assert unresolved == []


def test_resolve_paths_rejects_title_only_match():
    sub = SimpleNamespace(search_songs=lambda query, count=5: [
        {"id": "x", "title": "Track One", "artist": "Someone Else"},
    ])
    ids, unresolved = resolve_paths(sub, ["/m/1.mp3"],
                                    tag_reader=_tags({"/m/1.mp3": ("Artist A", "Track One")}))
    assert ids == []
    assert unresolved == ["/m/1.mp3"]


def test_resolve_paths_falls_back_to_basename_path_match():
    sub = SimpleNamespace(search_songs=lambda query, count=5: [
        {"id": "p", "title": "", "artist": "", "path": "sub/dir/1.mp3"},
    ])
    ids, unresolved = resolve_paths(sub, ["/m/1.mp3"], tag_reader=_tags({}))
    assert ids == ["p"]
    assert unresolved == []


def test_resolve_paths_dedupes_and_preserves_order():
    sub = SimpleNamespace(search_songs=lambda query, count=5: [
        {"id": "s", "title": "T", "artist": "A"},
    ])
    ids, unresolved = resolve_paths(sub, ["/m/1.mp3", "/m/2.mp3"],
                                    tag_reader=_tags({"/m/1.mp3": ("A", "T"),
                                                      "/m/2.mp3": ("A", "T")}))
    assert ids == ["s"]


def test_resolve_paths_survives_search_exception():
    def boom(query, count=5):
        raise RuntimeError("nd down")
    sub = SimpleNamespace(search_songs=boom)
    ids, unresolved = resolve_paths(sub, ["/m/1.mp3"],
                                    tag_reader=_tags({"/m/1.mp3": ("A", "T")}))
    assert ids == []
    assert unresolved == ["/m/1.mp3"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest tests/discover/test_playlist_sync.py -q`
Expected: 8 FAILED with `ImportError: cannot import name 'wait_for_scan'`.

- [ ] **Step 3: Implement**

Append to `discover/playlist_sync.py`:

```python
import os
import time

_SCAN_FALLBACK_SLEEP = 15.0


def _norm(s) -> str:
    return (s or "").strip().casefold()


def _read_tags(path):
    """(artist, title) from ID3, best effort. Mirrors library/scanner.py."""
    try:
        import eyed3
        af = eyed3.load(path)
        if af is None or af.tag is None:
            return ("", "")
        return (af.tag.artist or "", af.tag.title or "")
    except Exception:
        return ("", "")


def wait_for_scan(subsonic, timeout=120, poll=3.0, sleep_fn=None,
                  clock=None) -> bool:
    """Trigger a library scan and wait (bounded) for it to settle.

    Returns True when the scan reported idle inside the timeout. A client with
    no get_scan_status falls back to one fixed sleep and returns False.
    """
    sleep_fn = sleep_fn or time.sleep
    clock = clock or time.monotonic
    try:
        subsonic.start_scan()
    except Exception:
        logger.exception("playlist_sync: start_scan failed")

    status_fn = getattr(subsonic, "get_scan_status", None)
    if status_fn is None:
        sleep_fn(_SCAN_FALLBACK_SLEEP)
        return False

    deadline = clock() + timeout
    while clock() < deadline:
        try:
            st = status_fn() or {}
        except Exception:
            logger.exception("playlist_sync: get_scan_status failed")
            sleep_fn(_SCAN_FALLBACK_SLEEP)
            return False
        if not st.get("scanning"):
            return True
        sleep_fn(poll)
    logger.warning("playlist_sync: scan did not settle within %ss", timeout)
    return False


def resolve_paths(subsonic, paths, tag_reader=None):
    """Resolve downloaded file paths to Navidrome song ids.

    Returns (ids, unresolved_paths). A hit counts only when title AND artist
    both match; when the file has no usable tags we fall back to matching the
    basename against the hit's reported path.
    """
    tag_reader = tag_reader or _read_tags
    ids, unresolved, seen = [], [], set()

    for path in (paths or []):
        artist, title = tag_reader(path)
        base = os.path.basename(path)
        query = f"{artist} {title}".strip() or os.path.splitext(base)[0]
        try:
            hits = subsonic.search_songs(query, count=5) or []
        except Exception:
            logger.exception("playlist_sync: search failed for %s", path)
            unresolved.append(path)
            continue

        match = None
        if artist and title:
            for h in hits:
                if _norm(h.get("title")) == _norm(title) and _norm(h.get("artist")) == _norm(artist):
                    match = h
                    break
        if match is None:
            for h in hits:
                if base and os.path.basename(h.get("path") or "") == base:
                    match = h
                    break

        sid = (match or {}).get("id")
        if not sid:
            unresolved.append(path)
            continue
        if sid not in seen:
            seen.add(sid)
            ids.append(sid)

    return ids, unresolved
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/python -m pytest tests/discover/test_playlist_sync.py -q`
Expected: 19 PASS.

- [ ] **Step 5: Commit**

```bash
git add discover/playlist_sync.py tests/discover/test_playlist_sync.py
git commit -m "feat(discover): bounded scan wait + title-and-artist song id resolution"
```

---

### Task 16: One-time m3u → API playlist migration

**Files:**
- Modify: `discover/playlist_sync.py`
- Modify: `tests/discover/test_playlist_sync.py`

**Interfaces:**
- `migrate_from_m3u(subsonic, name, song_dir, tag_reader=None, wait_fn=None) -> {"migrated": bool, "playlist_id": str, "owned": [ids], "backup": str}`
- Sequence, per the approved design and the Task 12 spike:
  1. Read the existing `.m3u` basenames via `discover.assemble.read_playlist_basenames` (assemble.py:5; entries are **bare basenames, not paths**).
  2. Delete the API playlist of that name, if one exists.
  3. **Rename** `<name>.m3u` → `<name>.m3u.bak`. **Never delete it.**
  4. Trigger a scan and wait (Task 15's `wait_for_scan`) so Navidrome forgets the imported playlist.
  5. Resolve the recorded basenames to song ids and `create_playlist(name, ids)`.
  6. Mark every seeded track engine-owned in the returned `owned` list.
- If there is no `.m3u` file, the function is a no-op returning `{"migrated": False, …}`.
- The m3u name is sanitized the same way `write_weekly_mix` does: `re.sub(r'[\\/:*?"<>|]', "_", name)` (assemble.py:31).

- [ ] **Step 1: Write the failing tests**

Append to `tests/discover/test_playlist_sync.py`:

```python
from discover.playlist_sync import migrate_from_m3u


def _fake_sub(existing_id=None, songs=None, created="NEW"):
    state = {"deleted": [], "created": None}
    songs = songs or []

    def search_songs(query, count=5):
        return songs

    sub = SimpleNamespace(
        find_playlist_id=lambda name: existing_id,
        delete_playlist=lambda pid: state["deleted"].append(pid) or True,
        create_playlist=lambda name, ids: state.__setitem__("created", (name, list(ids))) or created,
        search_songs=search_songs,
        start_scan=lambda: True,
        get_scan_status=lambda: {"scanning": False, "count": 0},
    )
    return sub, state


def test_migrate_renames_m3u_to_bak_and_never_deletes_it(tmp_path):
    m3u = tmp_path / "Weekly Mix.m3u"
    m3u.write_text("#EXTM3U\na.mp3\n", encoding="utf-8")
    sub, state = _fake_sub(existing_id="7",
                           songs=[{"id": "s1", "title": "", "artist": "", "path": "x/a.mp3"}])
    r = migrate_from_m3u(sub, "Weekly Mix", str(tmp_path), tag_reader=lambda p: ("", ""))
    assert r["migrated"] is True
    assert not m3u.exists()
    assert (tmp_path / "Weekly Mix.m3u.bak").read_text(encoding="utf-8").startswith("#EXTM3U")
    assert r["backup"].endswith(".m3u.bak")


def test_migrate_deletes_the_old_api_playlist_first(tmp_path):
    (tmp_path / "Weekly Mix.m3u").write_text("#EXTM3U\na.mp3\n", encoding="utf-8")
    sub, state = _fake_sub(existing_id="7",
                           songs=[{"id": "s1", "title": "", "artist": "", "path": "x/a.mp3"}])
    migrate_from_m3u(sub, "Weekly Mix", str(tmp_path), tag_reader=lambda p: ("", ""))
    assert state["deleted"] == ["7"]


def test_migrate_seeds_new_playlist_and_marks_tracks_owned(tmp_path):
    (tmp_path / "Weekly Mix.m3u").write_text("#EXTM3U\na.mp3\nb.mp3\n", encoding="utf-8")

    def search_songs(query, count=5):
        base = query
        return [{"id": "id-" + base, "title": "", "artist": "", "path": "lib/" + base + ".mp3"}]

    sub, state = _fake_sub(existing_id=None)
    sub.search_songs = search_songs
    r = migrate_from_m3u(sub, "Weekly Mix", str(tmp_path), tag_reader=lambda p: ("", ""))
    assert state["created"][0] == "Weekly Mix"
    assert state["created"][1] == ["id-a", "id-b"]
    assert r["owned"] == ["id-a", "id-b"]
    assert r["playlist_id"] == "NEW"


def test_migrate_is_a_noop_without_an_m3u(tmp_path):
    sub, state = _fake_sub()
    r = migrate_from_m3u(sub, "Weekly Mix", str(tmp_path), tag_reader=lambda p: ("", ""))
    assert r["migrated"] is False
    assert state["deleted"] == [] and state["created"] is None


def test_migrate_sanitizes_the_playlist_name_for_the_filename(tmp_path):
    (tmp_path / "Odd_Name.m3u").write_text("#EXTM3U\na.mp3\n", encoding="utf-8")
    sub, state = _fake_sub(songs=[{"id": "s1", "title": "", "artist": "", "path": "x/a.mp3"}])
    r = migrate_from_m3u(sub, "Odd/Name", str(tmp_path), tag_reader=lambda p: ("", ""))
    assert r["migrated"] is True
    assert (tmp_path / "Odd_Name.m3u.bak").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest tests/discover/test_playlist_sync.py -k migrate -q`
Expected: 5 FAILED with `ImportError: cannot import name 'migrate_from_m3u'`.

- [ ] **Step 3: Implement**

Append to `discover/playlist_sync.py`:

```python
import re


def _m3u_path(song_dir: str, name: str) -> str:
    safe = re.sub(r'[\\/:*?"<>|]', "_", name)
    return os.path.join(song_dir, safe + ".m3u")


def migrate_from_m3u(subsonic, name, song_dir, tag_reader=None, wait_fn=None):
    """One-time migration of an m3u-backed playlist to an API playlist.

    Deletes the imported playlist via the API, renames the .m3u to .m3u.bak
    (never deletes it), rescans so Navidrome forgets it, then recreates the
    playlist through the API seeded with the old contents.
    """
    wait_fn = wait_fn or wait_for_scan
    result = {"migrated": False, "playlist_id": "", "owned": [], "backup": ""}

    m3u = _m3u_path(song_dir, name)
    if not os.path.exists(m3u):
        return result

    from discover.assemble import read_playlist_basenames
    basenames = read_playlist_basenames(song_dir, name)

    try:
        old_id = subsonic.find_playlist_id(name)
    except Exception:
        logger.exception("playlist_sync: could not look up %r before migration", name)
        old_id = None
    if old_id:
        try:
            subsonic.delete_playlist(old_id)
        except Exception:
            logger.exception("playlist_sync: could not delete old playlist %s", old_id)

    backup = m3u + ".bak"
    try:
        os.replace(m3u, backup)
        result["backup"] = backup
    except Exception:
        logger.exception("playlist_sync: could not rename %s — aborting migration", m3u)
        return result

    wait_fn(subsonic)

    ids, unresolved = resolve_paths(subsonic,
                                    [os.path.join(song_dir, b) for b in basenames],
                                    tag_reader=tag_reader)
    if unresolved:
        logger.warning("playlist_sync: %d of %d migrated tracks unresolved for %r",
                       len(unresolved), len(basenames), name)
    try:
        new_id = subsonic.create_playlist(name, ids)
    except Exception:
        logger.exception("playlist_sync: could not create migrated playlist %r", name)
        return result

    result.update({"migrated": True, "playlist_id": new_id or "", "owned": ids})
    logger.info("playlist_sync: migrated %r from m3u — %d tracks seeded, backup at %s",
                name, len(ids), backup)
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/python -m pytest tests/discover/test_playlist_sync.py -q`
Expected: 24 PASS.

- [ ] **Step 5: Commit**

```bash
git add discover/playlist_sync.py tests/discover/test_playlist_sync.py
git commit -m "feat(discover): one-time m3u to API playlist migration (m3u kept as .bak)"
```

---

### Task 17: `sync_playlist()` orchestration

**Files:**
- Modify: `discover/playlist_sync.py`
- Modify: `tests/discover/test_playlist_sync.py`

**Interfaces:**
- `sync_playlist(subsonic, name, new_paths, ledger, cap, song_dir=None, tag_reader=None, wait_fn=None) -> dict`
- `ledger` is a mutable dict, the shape persisted by Task 18:
  ```python
  {"playlist_id": str, "owned": [song_id, ...], "pending": [path, ...], "migrated": bool}
  ```
- Sequence:
  1. If `not ledger.get("migrated")` and `song_dir` is given → `migrate_from_m3u`; on success seed `ledger["playlist_id"]`/`["owned"]`, and set `ledger["migrated"] = True` **either way** so migration is attempted exactly once per playlist.
  2. `wait_fn(subsonic)` after downloads, so new files are indexed.
  3. `resolve_paths(subsonic, ledger["pending"] + new_paths)` — pending paths from previous runs are retried first; anything still unresolved goes back into `ledger["pending"]`.
  4. Look up `current_ids` via `ledger["playlist_id"]` (falling back to `find_playlist_id(name)`); if there is still no playlist, `create_playlist(name, [])` and record the id.
  5. `merge_playlist(current_ids, ledger["owned"], new_ids, cap)`.
  6. `replace_playlist(playlist_id, merged["final"])`.
  7. `ledger["owned"] = merged["owned"]`.
- Returns `{"status": "ok"|"error", "playlist_id", "added", "evicted", "pending", "migrated", "final_count"}`.
- **The caller persists the ledger** — `sync_playlist` mutates the dict but does no file I/O.

- [ ] **Step 1: Write the failing tests**

Append to `tests/discover/test_playlist_sync.py`:

```python
from discover.playlist_sync import sync_playlist


class FakeSubsonic:
    """Fake Navidrome playlist store, in the SimpleNamespace spirit of the
    existing engine test fakes but stateful enough to assert merges."""

    def __init__(self, playlists=None, songs=None):
        # playlists: {name: {"id": str, "ids": [song ids]}}
        self.playlists = playlists or {}
        self.songs = songs or {}      # query -> [hit dicts]
        self.replaced = []
        self.scans = 0

    def start_scan(self):
        self.scans += 1
        return True

    def get_scan_status(self):
        return {"scanning": False, "count": 0}

    def find_playlist_id(self, name):
        pl = self.playlists.get(name)
        return pl["id"] if pl else None

    def get_playlist_song_ids(self, pid):
        for pl in self.playlists.values():
            if pl["id"] == pid:
                return list(pl["ids"])
        return []

    def create_playlist(self, name, ids):
        pid = "pl-" + name.replace(" ", "-")
        self.playlists[name] = {"id": pid, "ids": list(ids)}
        return pid

    def delete_playlist(self, pid):
        for n, pl in list(self.playlists.items()):
            if pl["id"] == pid:
                del self.playlists[n]
        return True

    def replace_playlist(self, pid, ids):
        self.replaced.append((pid, list(ids)))
        for pl in self.playlists.values():
            if pl["id"] == pid:
                pl["ids"] = list(ids)
        return True

    def search_songs(self, query, count=5):
        return self.songs.get(query.strip(), [])


def _ledger(**kw):
    base = {"playlist_id": "", "owned": [], "pending": [], "migrated": True}
    base.update(kw)
    return base


def test_sync_creates_the_playlist_when_missing(tmp_path):
    sub = FakeSubsonic(songs={"A T": [{"id": "s1", "title": "T", "artist": "A"}]})
    led = _ledger()
    r = sync_playlist(sub, "Weekly Mix", ["/m/1.mp3"], led, cap=10,
                      tag_reader=lambda p: ("A", "T"))
    assert r["status"] == "ok"
    assert led["playlist_id"] == "pl-Weekly-Mix"
    assert led["owned"] == ["s1"]
    assert sub.replaced[-1][1] == ["s1"]


def test_sync_preserves_user_tracks_and_appends_new_ones(tmp_path):
    sub = FakeSubsonic(playlists={"Weekly Mix": {"id": "p1", "ids": ["u1", "e1"]}},
                       songs={"A T2": [{"id": "e2", "title": "T2", "artist": "A"}]})
    led = _ledger(playlist_id="p1", owned=["e1"])
    sync_playlist(sub, "Weekly Mix", ["/m/2.mp3"], led, cap=10,
                  tag_reader=lambda p: ("A", "T2"))
    assert sub.replaced[-1] == ("p1", ["u1", "e1", "e2"])
    assert led["owned"] == ["e1", "e2"]


def test_sync_respects_a_user_deletion(tmp_path):
    sub = FakeSubsonic(playlists={"Weekly Mix": {"id": "p1", "ids": ["e2"]}}, songs={})
    led = _ledger(playlist_id="p1", owned=["e1", "e2"])
    sync_playlist(sub, "Weekly Mix", [], led, cap=10, tag_reader=lambda p: ("", ""))
    assert sub.replaced[-1] == ("p1", ["e2"])
    assert led["owned"] == ["e2"]


def test_sync_evicts_oldest_engine_track_past_cap(tmp_path):
    sub = FakeSubsonic(playlists={"Weekly Mix": {"id": "p1", "ids": ["u1", "e1", "e2"]}},
                       songs={"A T3": [{"id": "e3", "title": "T3", "artist": "A"}]})
    led = _ledger(playlist_id="p1", owned=["e1", "e2"])
    r = sync_playlist(sub, "Weekly Mix", ["/m/3.mp3"], led, cap=2,
                      tag_reader=lambda p: ("A", "T3"))
    assert r["evicted"] == 1
    assert sub.replaced[-1] == ("p1", ["u1", "e2", "e3"])
    assert led["owned"] == ["e2", "e3"]


def test_sync_queues_unresolved_paths_as_pending(tmp_path):
    sub = FakeSubsonic(playlists={"Weekly Mix": {"id": "p1", "ids": []}}, songs={})
    led = _ledger(playlist_id="p1")
    r = sync_playlist(sub, "Weekly Mix", ["/m/miss.mp3"], led, cap=10,
                      tag_reader=lambda p: ("A", "Missing"))
    assert led["pending"] == ["/m/miss.mp3"]
    assert r["pending"] == 1


def test_sync_retries_pending_paths_on_the_next_run(tmp_path):
    sub = FakeSubsonic(playlists={"Weekly Mix": {"id": "p1", "ids": []}},
                       songs={"A Missing": [{"id": "late", "title": "Missing", "artist": "A"}]})
    led = _ledger(playlist_id="p1", pending=["/m/miss.mp3"])
    sync_playlist(sub, "Weekly Mix", [], led, cap=10, tag_reader=lambda p: ("A", "Missing"))
    assert led["pending"] == []
    assert led["owned"] == ["late"]


def test_sync_runs_migration_once_then_never_again(tmp_path):
    (tmp_path / "Weekly Mix.m3u").write_text("#EXTM3U\nold.mp3\n", encoding="utf-8")
    sub = FakeSubsonic(playlists={"Weekly Mix": {"id": "old", "ids": ["x"]}},
                       songs={"old": [{"id": "s-old", "title": "", "artist": "",
                                       "path": "lib/old.mp3"}]})
    led = _ledger(migrated=False)
    sync_playlist(sub, "Weekly Mix", [], led, cap=10, song_dir=str(tmp_path),
                  tag_reader=lambda p: ("", ""))
    assert led["migrated"] is True
    assert led["owned"] == ["s-old"]
    assert not (tmp_path / "Weekly Mix.m3u").exists()

    # Second run must not touch the .bak or re-migrate
    (tmp_path / "Weekly Mix.m3u").write_text("#EXTM3U\nnew.mp3\n", encoding="utf-8")
    sync_playlist(sub, "Weekly Mix", [], led, cap=10, song_dir=str(tmp_path),
                  tag_reader=lambda p: ("", ""))
    assert (tmp_path / "Weekly Mix.m3u").exists()   # untouched on the second run


def test_sync_waits_for_the_scan_before_resolving(tmp_path):
    sub = FakeSubsonic(playlists={"Weekly Mix": {"id": "p1", "ids": []}},
                       songs={"A T": [{"id": "s1", "title": "T", "artist": "A"}]})
    led = _ledger(playlist_id="p1")
    sync_playlist(sub, "Weekly Mix", ["/m/1.mp3"], led, cap=10,
                  tag_reader=lambda p: ("A", "T"))
    assert sub.scans >= 1


def test_sync_reports_error_without_crashing_the_run(tmp_path):
    sub = FakeSubsonic()
    sub.create_playlist = lambda name, ids: (_ for _ in ()).throw(RuntimeError("nd down"))
    led = _ledger()
    r = sync_playlist(sub, "Weekly Mix", [], led, cap=10, tag_reader=lambda p: ("", ""))
    assert r["status"] == "error"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest tests/discover/test_playlist_sync.py -k sync_ -q`
Expected: 9 FAILED with `ImportError: cannot import name 'sync_playlist'`.

- [ ] **Step 3: Implement**

Append to `discover/playlist_sync.py`:

```python
def _blank_ledger() -> dict:
    return {"playlist_id": "", "owned": [], "pending": [], "migrated": False}


def sync_playlist(subsonic, name, new_paths, ledger, cap,
                  song_dir=None, tag_reader=None, wait_fn=None):
    """Merge this run's new tracks into the named Navidrome playlist.

    Never touches user-added tracks, never re-adds tracks the user deleted, and
    caps only the engine-owned share. Mutates `ledger` in place; the caller
    persists it.
    """
    wait_fn = wait_fn or wait_for_scan
    for k, v in _blank_ledger().items():
        ledger.setdefault(k, v)

    try:
        # 1. one-time migration off the m3u writer
        if not ledger.get("migrated") and song_dir:
            mig = migrate_from_m3u(subsonic, name, song_dir,
                                   tag_reader=tag_reader, wait_fn=wait_fn)
            if mig["migrated"]:
                ledger["playlist_id"] = mig["playlist_id"]
                ledger["owned"] = list(mig["owned"])
            ledger["migrated"] = True   # attempted once, never retried

        # 2. make sure this run's downloads are indexed
        wait_fn(subsonic)

        # 3. resolve pending retries first, then this run's new files
        candidates = list(ledger.get("pending") or []) + list(new_paths or [])
        new_ids, unresolved = resolve_paths(subsonic, candidates, tag_reader=tag_reader)
        ledger["pending"] = unresolved

        # 4. locate (or create) the playlist
        pid = ledger.get("playlist_id") or subsonic.find_playlist_id(name)
        if not pid:
            pid = subsonic.create_playlist(name, [])
        ledger["playlist_id"] = pid
        current_ids = subsonic.get_playlist_song_ids(pid) if pid else []

        # 5. merge and write back
        merged = merge_playlist(current_ids, ledger.get("owned") or [], new_ids, cap)
        subsonic.replace_playlist(pid, merged["final"])
        ledger["owned"] = merged["owned"]

        logger.info("playlist_sync: %r — %d user, %d engine (+%d new, -%d evicted, %d pending)",
                    name, len(merged["user"]), len(merged["owned"]),
                    len(new_ids), len(merged["evicted"]), len(unresolved))
        return {"status": "ok", "playlist_id": pid,
                "added": len(new_ids), "evicted": len(merged["evicted"]),
                "pending": len(unresolved), "migrated": ledger["migrated"],
                "final_count": len(merged["final"])}
    except Exception as e:
        logger.exception("playlist_sync: sync failed for %r", name)
        return {"status": "error", "error": str(e), "playlist_id": ledger.get("playlist_id", ""),
                "added": 0, "evicted": 0, "pending": len(ledger.get("pending") or []),
                "migrated": ledger.get("migrated", False), "final_count": 0}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/python -m pytest tests/discover/ -q`
Expected: all PASS (33 in `test_playlist_sync.py`).

- [ ] **Step 5: Commit**

```bash
git add discover/playlist_sync.py tests/discover/test_playlist_sync.py
git commit -m "feat(discover): sync_playlist() — migrate, resolve, merge, write back"
```

---

### Task 18: Ledger persistence in `discover_state.json` and `follow_state.json`

**Files:**
- Modify: `discover/state.py` (`DiscoverState`)
- Modify: `follow/fstate.py` (`FollowState`)
- Test: `tests/discover/test_state.py` (append), `tests/follow/test_fstate.py` (append)

**Interfaces:**
- `DiscoverState.playlist_ledger(profile_name) -> dict` — returns the mutable ledger for that playlist, creating a blank one on first access. Backed by a new top-level `"playlists"` key in `discover_state.json`:
  ```json
  {"suggested": {...}, "last_run": "...", "lastfm_ready": true,
   "playlists": {"Weekly Mix": {"playlist_id": "p1", "owned": ["s1"], "pending": [], "migrated": true}}}
  ```
  `DiscoverState.save()` already read-merges the on-disk file and preserves foreign top-level keys (state.py:45-62) — extend it so it also writes back the in-memory `playlists` map rather than relying on a foreign-key passthrough.
- `FollowState.playlist_ledger() -> dict` — the single NEW RELEASES ledger, stored under a new `"playlist"` key in `follow_state.json`. `FollowState.load()` merges key-by-key against `_empty()` (fstate.py:102-112), so adding the key to `_empty()` is enough for backwards compatibility.
- Existing state files without these keys must load cleanly and produce a blank ledger.

- [ ] **Step 1: Write the failing tests**

Append to `tests/discover/test_state.py`:

```python
def test_playlist_ledger_starts_blank_and_is_mutable(tmp_path):
    from discover.state import DiscoverState
    st = DiscoverState(path=str(tmp_path / "s.json"), suggested={})
    led = st.playlist_ledger("Weekly Mix")
    assert led == {"playlist_id": "", "owned": [], "pending": [], "migrated": False}
    led["owned"].append("s1")
    assert st.playlist_ledger("Weekly Mix")["owned"] == ["s1"]


def test_playlist_ledger_round_trips_through_save_and_load(tmp_path):
    from discover.state import DiscoverState, load_state
    p = str(tmp_path / "s.json")
    st = DiscoverState(path=p, suggested={})
    led = st.playlist_ledger("Weekly Mix")
    led.update({"playlist_id": "p1", "owned": ["a", "b"], "pending": ["/x.mp3"], "migrated": True})
    st.save()

    st2 = load_state(p)
    assert st2.playlist_ledger("Weekly Mix") == {
        "playlist_id": "p1", "owned": ["a", "b"], "pending": ["/x.mp3"], "migrated": True}


def test_playlist_ledgers_are_per_profile(tmp_path):
    from discover.state import DiscoverState, load_state
    p = str(tmp_path / "s.json")
    st = DiscoverState(path=p, suggested={})
    st.playlist_ledger("A")["owned"] = ["a"]
    st.playlist_ledger("B")["owned"] = ["b"]
    st.save()
    st2 = load_state(p)
    assert st2.playlist_ledger("A")["owned"] == ["a"]
    assert st2.playlist_ledger("B")["owned"] == ["b"]


def test_save_preserves_foreign_keys_alongside_playlists(tmp_path):
    import json
    from discover.state import DiscoverState
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"lastfm_ready": True}), encoding="utf-8")
    st = DiscoverState(path=str(p), suggested={})
    st.playlist_ledger("A")["owned"] = ["a"]
    st.save()
    on_disk = json.loads(p.read_text(encoding="utf-8"))
    assert on_disk["lastfm_ready"] is True
    assert on_disk["playlists"]["A"]["owned"] == ["a"]


def test_load_state_tolerates_a_file_with_no_playlists_key(tmp_path):
    import json
    from discover.state import load_state
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"suggested": {}, "last_run": None}), encoding="utf-8")
    st = load_state(str(p))
    assert st.playlist_ledger("Anything")["owned"] == []
```

Append to `tests/follow/test_fstate.py`:

```python
def test_follow_playlist_ledger_starts_blank(tmp_path):
    from follow import fstate
    st = fstate.load(str(tmp_path / "f.json"))
    assert st.playlist_ledger() == {"playlist_id": "", "owned": [], "pending": [], "migrated": False}


def test_follow_playlist_ledger_round_trips(tmp_path):
    from follow import fstate
    p = str(tmp_path / "f.json")
    st = fstate.load(p)
    led = st.playlist_ledger()
    led.update({"playlist_id": "p9", "owned": ["x"], "pending": [], "migrated": True})
    st.save()
    assert fstate.load(p).playlist_ledger() == {
        "playlist_id": "p9", "owned": ["x"], "pending": [], "migrated": True}


def test_follow_load_tolerates_state_file_without_playlist_key(tmp_path):
    import json
    from follow import fstate
    p = tmp_path / "f.json"
    p.write_text(json.dumps({"acquired_release_groups": {}, "feed": []}), encoding="utf-8")
    assert fstate.load(str(p)).playlist_ledger()["owned"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest tests/discover/test_state.py tests/follow/test_fstate.py -q`
Expected: 8 FAILED with `AttributeError: … has no attribute 'playlist_ledger'`.

- [ ] **Step 3: Implement**

In `discover/state.py` — add a shared blank-ledger helper at module level:

```python
def blank_ledger() -> dict:
    return {"playlist_id": "", "owned": [], "pending": [], "migrated": False}
```

extend `DiscoverState.__init__` (state.py:12-18) with a `playlists` argument defaulting to `None`:

```python
    def __init__(self, path: str, suggested: dict, last_run=None,
                 ttl_days: int = _DEFAULT_TTL_DAYS, playlists: dict = None):
        ...
        self._playlists = playlists if isinstance(playlists, dict) else {}
```

add the accessor next to `add()`:

```python
    def playlist_ledger(self, name: str) -> dict:
        """Mutable per-playlist merge ledger. Created blank on first access."""
        led = self._playlists.get(name)
        if not isinstance(led, dict):
            led = blank_ledger()
            self._playlists[name] = led
        for k, v in blank_ledger().items():
            led.setdefault(k, v)
        return led
```

and in `save()` (state.py:36-66) extend the `existing.update(...)` call at line 62:

```python
        existing.update({"suggested": merged_suggested, "last_run": self._last_run,
                         "playlists": {**(existing.get("playlists") or {}), **self._playlists}})
```

Finally, in `load_state` (state.py:77-95) read the key and pass it through:

```python
            playlists = d.get("playlists") or {}
            ...
    return DiscoverState(path, suggested, last_run=last_run, ttl_days=ttl_days,
                         playlists=playlists if isinstance(playlists, dict) else {})
```

(Initialise `playlists = {}` before the `try` so the failure path still constructs cleanly.)

In `follow/fstate.py` — add `"playlist": {}` to `_empty()` (fstate.py:90-99) and the accessor next to `set_runs`:

```python
    def playlist_ledger(self) -> dict:
        """Mutable merge ledger for the NEW RELEASES playlist."""
        from discover.state import blank_ledger
        led = self._d.get("playlist")
        if not isinstance(led, dict):
            led = blank_ledger()
            self._d["playlist"] = led
        for k, v in blank_ledger().items():
            led.setdefault(k, v)
        return led
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/python -m pytest tests/ -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add discover/state.py follow/fstate.py tests/discover/test_state.py tests/follow/test_fstate.py
git commit -m "feat(state): per-playlist merge ledgers in discover_state.json and follow_state.json"
```

---

### Task 19: Wire `discover/engine.py:run_profile` to `sync_playlist`

**Files:**
- Modify: `discover/engine.py:200-202` (`_existing_playlist_basenames`) and `discover/engine.py:303-313` (the write block at the end of `run_profile`)
- Modify: `tests/discover/test_run_profile.py`, `tests/discover/test_engine.py`, `tests/discover/test_daily.py` — the three `build_deps` helpers construct `subsonic` as a `SimpleNamespace`, so **any new method `run_profile` calls will raise `AttributeError` in all three** unless added there. Add the playlist methods to each fake.
- Test: `tests/discover/test_run_profile.py` (append)

**Interfaces:**
- `run_profile` picks the API path when Navidrome credentials produced a real client (i.e. `deps.subsonic` exposes `replace_playlist`), and falls back to `write_weekly_mix` **with a warning log** otherwise. The design's fallback condition is "Navidrome credentials absent" — in practice `_build_discover_deps()` (server.py:148-188) returns `None` when creds are missing, so the observable in-engine signal is the capability of the injected client. Use `hasattr(deps.subsonic, "replace_playlist")`.
- `_existing_playlist_basenames` (used at engine.py:282 to keep library picks from repeating) becomes `_existing_playlist_keys(deps, name)`: prefer `getPlaylist` entries (basename of each entry's `path`, plus `"artist - title"`), falling back to the m3u reader.
- Return value keeps `"m3u"` for compatibility with existing tests and adds `"playlist"`: the `sync_playlist` result dict.
- **Legacy `run_weekly` (engine.py:63-70) and `run_mix` (engine.py:188-195) are intentionally left on the m3u writer** — they are the deprecated bootstrap paths listed under "Technical debt" in `docs/ROADMAP.md` and are not scheduled.

- [ ] **Step 1: Extend the three fake subsonic builders**

In `tests/discover/test_run_profile.py:67-77`, `tests/discover/test_engine.py:9-15`, and `tests/discover/test_daily.py:11-58`, add to each `SimpleNamespace(...)` subsonic fake:

```python
        find_playlist_id=lambda name: None,
        get_playlist_song_ids=lambda pid: [],
        create_playlist=lambda name, ids: "pl-fake",
        replace_playlist=lambda pid, ids: True,
        delete_playlist=lambda pid: True,
        get_scan_status=lambda: {"scanning": False, "count": 0},
        search_songs=lambda query, count=5: [],
```

(`test_run_profile.py` already supplies `search_songs=lambda query, count=20: library_songs` — keep that one and give it a `count=5`-compatible default: `lambda query, count=5: library_songs`.)

- [ ] **Step 2: Write the failing tests**

Append to `tests/discover/test_run_profile.py`:

```python
def test_run_profile_uses_sync_playlist_when_api_available(tmp_path, monkeypatch):
    deps, downloaded = build_deps(tmp_path)
    seen = {}

    def fake_sync(subsonic, name, new_paths, ledger, cap, **kw):
        seen.update({"name": name, "paths": list(new_paths), "cap": cap})
        ledger["owned"] = list(new_paths)
        return {"status": "ok", "playlist_id": "p1", "added": len(new_paths),
                "evicted": 0, "pending": 0, "migrated": True,
                "final_count": len(new_paths)}

    monkeypatch.setattr("discover.playlist_sync.sync_playlist", fake_sync)
    result = run_profile(deps, make_cfg(), make_profile(count=2, cap=7))
    assert seen["name"] == "Test Mix"
    assert seen["cap"] == 7
    assert result["playlist"]["status"] == "ok"
    assert result["m3u"] is None


def test_run_profile_falls_back_to_m3u_without_api_client(tmp_path, monkeypatch):
    deps, downloaded = build_deps(tmp_path)
    # Simulate a client built before the API methods existed / no creds path.
    for attr in ("replace_playlist", "create_playlist", "find_playlist_id"):
        if hasattr(deps.subsonic, attr):
            delattr(deps.subsonic, attr)
    result = run_profile(deps, make_cfg(), make_profile(count=2))
    assert result["m3u"] and result["m3u"].endswith(".m3u")
    assert result.get("playlist") is None


def test_run_profile_persists_the_playlist_ledger(tmp_path, monkeypatch):
    deps, downloaded = build_deps(tmp_path)

    def fake_sync(subsonic, name, new_paths, ledger, cap, **kw):
        ledger["playlist_id"] = "p1"
        ledger["owned"] = ["s1"]
        ledger["migrated"] = True
        return {"status": "ok", "playlist_id": "p1", "added": 1, "evicted": 0,
                "pending": 0, "migrated": True, "final_count": 1}

    monkeypatch.setattr("discover.playlist_sync.sync_playlist", fake_sync)
    run_profile(deps, make_cfg(), make_profile(count=2))
    assert deps.state.playlist_ledger("Test Mix")["owned"] == ["s1"]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `venv/bin/python -m pytest tests/discover/test_run_profile.py -k "sync_playlist or falls_back or ledger" -v`
Expected: 3 FAILED (`result` has no `"playlist"` key; `sync_playlist` never called).

- [ ] **Step 4: Implement**

Replace `discover/engine.py:200-202`:

```python
def _existing_playlist_keys(deps, name: str) -> set:
    """Keys already in the named playlist, so library picks don't repeat.

    Prefers the live Navidrome playlist; falls back to the legacy .m3u file.
    """
    keys = set()
    sub = getattr(deps, "subsonic", None)
    if sub is not None and hasattr(sub, "find_playlist_id"):
        try:
            pid = sub.find_playlist_id(name)
            if pid:
                pl = sub.get_playlist(pid) or {}
                entries = pl.get("entry", []) or []
                if isinstance(entries, dict):
                    entries = [entries]
                for e in entries:
                    p = e.get("path") or ""
                    if p:
                        keys.add(os.path.basename(p))
                return keys
        except Exception:
            logger.exception("discover: could not read playlist %r via API", name)
    return set(read_playlist_basenames(deps.song_dir, name))


# Kept for the legacy run_weekly / run_mix paths.
def _existing_playlist_basenames(song_dir: str, name: str) -> set:
    """Return set of basenames already in the named playlist's m3u."""
    return set(read_playlist_basenames(song_dir, name))
```

(`import os` is already present at the top of `engine.py`; add it if not.)

Change the call at engine.py:282 from

```python
        existing = _existing_playlist_basenames(deps.song_dir, profile["name"])
```

to

```python
        existing = _existing_playlist_keys(deps, profile["name"])
```

Replace the write block at engine.py:303-313:

```python
    m3u = None
    playlist_result = None
    all_paths = acquired_paths + lib_paths
    if all_paths:
        if hasattr(deps.subsonic, "replace_playlist"):
            from discover.playlist_sync import sync_playlist
            ledger = deps.state.playlist_ledger(profile["name"])
            playlist_result = sync_playlist(
                deps.subsonic, profile["name"], acquired_paths, ledger, cap,
                song_dir=deps.song_dir)
        else:
            logger.warning("discover: no Navidrome playlist API available — "
                           "falling back to the m3u writer for %r", profile["name"])
            m3u = write_weekly_mix(deps.song_dir, all_paths,
                                   name=profile["name"], cap=cap)
            try:
                deps.subsonic.start_scan()
            except Exception:
                logger.exception("discover: scan trigger failed")
    deps.state.save(stamp_last_run=False)
    return {"profile": profile["id"], "acquired": len(acquired_paths),
            "library_added": len(lib_paths), "m3u": m3u,
            "playlist": playlist_result}
```

Note `sync_playlist` receives **`acquired_paths` only**: library picks are already Navidrome songs and are already in the playlist or deliberately not owned by the engine. `sync_playlist` performs its own `start_scan` + wait via `wait_for_scan`, so the separate `start_scan()` call is only needed on the m3u fallback path.

- [ ] **Step 5: Run tests to verify they pass**

Run: `venv/bin/python -m pytest tests/ -q`
Expected: all PASS. If `tests/discover/test_run_profile.py` or `test_daily.py` assert on `result["m3u"]` being a path, update those assertions to the API path (`result["playlist"]["status"] == "ok"`) — the change of writer is intentional and the assertion is what is now wrong.

- [ ] **Step 6: Live smoke test**

```bash
systemctl --user restart amusicserver && sleep 3
curl -s -X POST http://localhost:5000/mixes/<some-mix-id>/run | python3 -m json.tool
```

Then, in Navidrome: the mix playlist still exists with its original id; a track you manually added beforehand is still present and first; the newly discovered tracks are appended at the end; `<name>.m3u.bak` exists in `song_dir` and `<name>.m3u` does not.

- [ ] **Step 7: Commit**

```bash
git add discover/engine.py tests/discover/
git commit -m "feat(discover): run_profile writes merge-aware API playlists instead of m3u"
```

---

### Task 20: Wire `follow/runner.py` + the server's follow entry point

**Files:**
- Modify: `follow/runner.py:16-32` (signature + config reads) and `follow/runner.py:91-92` (the write call site)
- Modify: `sWebExt/py_server/server.py:210-213` (the `runner.run_once(...)` call inside `_run_follow_once`)
- Test: `tests/follow/test_runner.py` (modify + append). Note the existing fake at test_runner.py:41-45 is `def fake_assemble(song_dir, paths, name, cap)` — a **positional four-arg** contract that must keep working for the fallback path.

**Interfaces:**
- `run_once(...)` gains a keyword-only `subsonic=None` parameter. When `subsonic` is provided and exposes `replace_playlist`, the NEW RELEASES playlist is synced via `sync_playlist` using `state.playlist_ledger()`; otherwise the existing `assemble_fn(song_dir, paths, playlist_name, playlist_cap)` fallback runs unchanged.
- `server.py` passes `subsonic=deps.subsonic` (`deps` is already built at server.py:203).
- `follow/runner.py` currently never calls `start_scan` — `sync_playlist` supplies that for free on the API path.

- [ ] **Step 1: Write the failing tests**

Append to `tests/follow/test_runner.py`:

```python
def test_run_once_syncs_via_api_when_subsonic_is_given(monkeypatch, tmp_path):
    seen = {}

    def fake_sync(subsonic, name, new_paths, ledger, cap, **kw):
        seen.update({"name": name, "paths": list(new_paths), "cap": cap})
        ledger["owned"] = list(new_paths)
        return {"status": "ok", "playlist_id": "p1", "added": len(new_paths),
                "evicted": 0, "pending": 0, "migrated": True, "final_count": 1}

    monkeypatch.setattr("discover.playlist_sync.sync_playlist", fake_sync)
    # Build the same fixtures the existing happy-path test uses, plus a fake client
    # that advertises the playlist API.
    from types import SimpleNamespace
    sub = SimpleNamespace(replace_playlist=lambda pid, ids: True)
    result = _run_happy_path(tmp_path, subsonic=sub)     # see Step 2
    assert seen["name"] == "NEW RELEASES"
    assert result["acquired"] >= 1


def test_run_once_still_writes_m3u_without_a_subsonic_client(tmp_path):
    written = {}

    def fake_assemble(song_dir, paths, name, cap):
        written["name"] = name
        written["paths"] = list(paths)
        return "/songs/" + name + ".m3u"

    result = _run_happy_path(tmp_path, assemble_fn=fake_assemble)
    assert written["name"] == "NEW RELEASES"
    assert written["paths"]
```

- [ ] **Step 2: Factor the existing happy-path fixture**

`tests/follow/test_runner.py` already builds a full happy-path run (mb/lb stubs, `search_fn`, `download_fn`, `fake_assemble` at lines 41-45). Extract that setup into a module-level `_run_happy_path(tmp_path, **overrides)` helper that forwards `subsonic`/`assemble_fn` into `runner.run_once`, and have the existing tests call it, so both new tests and the old ones share one fixture.

- [ ] **Step 3: Run tests to verify they fail**

Run: `venv/bin/python -m pytest tests/follow/test_runner.py -q`
Expected: the two new tests FAIL — `run_once()` got an unexpected keyword argument `subsonic`.

- [ ] **Step 4: Implement**

In `follow/runner.py`, change the signature (lines 16-18):

```python
def run_once(mb_client, lb_client, follows, state, search_fn, download_fn,
             song_dir, cfg, resolve_fn=None, acquire_fn=None, assemble_fn=None,
             push_fn=None, today=None, subsonic=None) -> dict:
```

and replace the write block (lines 91-92):

```python
    playlist_result = None
    if paths:
        if subsonic is not None and hasattr(subsonic, "replace_playlist"):
            from discover.playlist_sync import sync_playlist
            ledger = state.playlist_ledger()
            playlist_result = sync_playlist(subsonic, playlist_name, paths, ledger,
                                            playlist_cap, song_dir=song_dir)
        else:
            logger.warning("[FOLLOW] no Navidrome playlist API available — "
                           "falling back to the m3u writer for %r", playlist_name)
            assemble_fn(song_dir, paths, playlist_name, playlist_cap)
```

(add `import logging` / `logger = logging.getLogger(__name__)` at the top of `follow/runner.py` if not already present), and extend the return at line 100:

```python
    return {"acquired": acquired, "unavailable": unavailable,
            "playlist": playlist_result}
```

In `sWebExt/py_server/server.py`, change the call at 210-213:

```python
        result = runner.run_once(
            mb_client=mb, lb_client=lb, follows=follows, state=state,
            search_fn=deps.search_fn, download_fn=deps.download_fn,
            song_dir=deps.song_dir, cfg=fc, subsonic=deps.subsonic)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `venv/bin/python -m pytest tests/ -q`
Expected: all PASS.

- [ ] **Step 6: Live smoke test**

```bash
systemctl --user restart amusicserver && sleep 3
curl -s -X POST http://localhost:5000/follow/run | python3 -m json.tool
```

In Navidrome, "NEW RELEASES" keeps its playlist id; `song_dir/NEW RELEASES.m3u.bak` exists and `NEW RELEASES.m3u` does not. Add a track by hand in Navidrome, run again, and confirm it survives.

- [ ] **Step 7: Commit**

```bash
git add follow/runner.py sWebExt/py_server/server.py tests/follow/test_runner.py
git commit -m "feat(follow): NEW RELEASES uses merge-aware API playlist sync"
```

---

# GROUP 7 — Close-out

### Task 21: Full suite + ROADMAP gap-list update

**Files:**
- Modify: `docs/ROADMAP.md`

- [ ] **Step 1: Full test suite**

Run: `venv/bin/python -m pytest tests/ -q`
Expected: all PASS. Record the final count (baseline was 570 passed, 1 skipped).

- [ ] **Step 2: Live end-to-end sanity pass**

```bash
systemctl --user restart amusicserver && sleep 3
systemctl --user is-active amusicserver
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:5000/
```

Then walk the UI once: Library (dedup review, Share, Import playlist), Search (preview + mini-player), Mixes (manual artists + advanced).

- [ ] **Step 3: Update `docs/ROADMAP.md`**

Under **Known gaps and bugs → Broken**, remove all three entries — `/share/import` redirects to `/explore` (Task 9), `/preview` uses hardcoded `.venv/bin/yt-dlp` (Task 7), and "No UI for `/preview`" (Task 8).

Under **Not surfaced in UI**, remove: Spotify routes (Task 11), `/import/tracks` + `/import/status` (Tasks 10-11), `/share/link` + `/share/code` + `/share/parse` (Task 10), `/sc/preview` (Tasks 6-8), `/library/dedup/report` (Task 3).

Under **UI gaps**, remove: "No audio preview" (Task 8), "No share UI" (Task 10), "No batch import UI" (Task 11), "Mixes screen: manual seed mode has no artist input" (Task 4), "Quality settings not editable per-mix" (Task 5), "Dedup: no review step" (Tasks 1-3). Leave "Repair / Enrich: no result details" and "Setup screen: no `discover.*` quality keys" — neither is addressed by this batch.

Under **Backlog**, remove the rows for Audio preview, Share codes UI, Batch Spotify CSV import UI, Per-mix quality editor, Dedup dry-run review, and Manual seed artist input.

Under **Technical debt**, remove the `/preview` hardcoded-cwd entry (Task 7 fixed the binary; note the `cwd=_PROJECT_ROOT` remains, harmless). Add one new entry:

> - **Legacy `run_weekly` / `run_mix` still write m3u** — `run_profile` and the follow runner now use `discover/playlist_sync.py` against the Subsonic API, but the deprecated bootstrap paths in `discover/engine.py` (lines ~63-70 and ~188-195) still call `write_weekly_mix`. `discover/assemble.py` is retained as the credential-less fallback.

Then update these sections to match reality:
- **Route inventory**: add `POST /library/dedup/delete`; change `/sc/preview` to "Resolve SC progressive transcoding → playable CDN mp3"; change `/share/import` to "Redirect to `/?share=<d>#library`".
- **Web UI → LIBRARY screen**: replace the De-duplicate bullet with the two-step review; add Share and Import-playlist card bullets.
- **Web UI → SEARCH screen**: add the `▶` preview button and mini-player bar.
- **Web UI → MIXES screen**: add manual-artist chips and the Advanced quality section.
- **Discovery engine** and **Key file index**: add `discover/playlist_sync.py` — "merge-aware Navidrome playlist sync (`merge_playlist`, `resolve_paths`, `migrate_from_m3u`, `sync_playlist`)"; note that `discover_state.json` now carries a `playlists` ledger and `follow_state.json` a `playlist` ledger.
- **Test suite**: update the test count to the number from Step 1 and add `tests/discover/test_playlist_sync.py`.
- Update the header line: `> Accurate as of 2026-08-24, branch bare_bones.`

- [ ] **Step 4: Verify the doc against the code**

```bash
grep -n "explore" docs/ROADMAP.md            # expect: no hits
grep -n "\.venv/bin/yt-dlp" docs/ROADMAP.md  # expect: no hits
grep -rn "\.venv/bin/yt-dlp" sWebExt/        # expect: no hits
```

- [ ] **Step 5: Report to the user before claiming completion.** Include the final test count, the Task 12 spike result, and anything the live smoke tests surfaced.

- [ ] **Step 6: Commit**

```bash
git add docs/ROADMAP.md
git commit -m "docs(roadmap): close usable-status gaps; document merge-aware playlist sync"
```
