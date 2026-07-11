# Genre Tag Preview & Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show what a Last.fm genre tag actually means (top artists, usage counts, similar tags) in the mix editor before it is saved, with local-vocabulary autocomplete and warn-but-allow validation.

**Architecture:** A new `lastfm/tag_preview.py` wraps three Last.fm calls into one preview dict. Two new Flask routes (`/genres/preview`, `/genres/vocab`) expose it with in-memory TTL caches; vocab merges insights-DB artist tags with Navidrome library genres. The mix editor's `prompt('Genre:')` is replaced by an inline input + suggestion chips + preview card, and genre chips get an amber warn state.

**Tech Stack:** Python 3 / Flask, `lastfm.client.LastFMClient` (rate-limited, typed exceptions), SQLite insights DB, pytest; vanilla JS SPA (no framework, no JS tests).

**Spec:** `docs/superpowers/specs/2026-07-11-genre-tag-preview-design.md`

## Global Constraints

- Warn threshold (exact values from spec): a tag is "weak" when `taggings < 100` OR `top_artists` is empty. Warning is display-only; **saving is never blocked**.
- Cache TTLs from spec: preview 3600 s (keyed by casefolded tag), vocab 600 s.
- Degraded statuses follow the codebase pattern: HTTP 200 `{"status":"unavailable","reason":"lastfm_api_key not configured"}` when no key; HTTP 400 for missing param; HTTP 500 `{"status":"error","error":str(e)}` on upstream failure.
- `get_tag_preview`: individual sub-call failures degrade to empty values; raise only if **all three** sub-calls fail.
- All tests run from repo root: `.venv/bin/python -m pytest tests/ -q` (540 passing at plan time). Route tests use the existing `app`/`client` fixtures in `tests/server/test_routes.py`.
- No new dependencies. No JS test harness. Frontend style: `document.createElement` + `textContent` only, 2-space indent, single quotes, CSS custom properties (`--line`, `--mut`, `--acid`).
- No changes to `discover/seeds.py`, the genre gate, or `/mixes` save behavior.
- Commit after every task with the message given in the task.

---

### Task 1: `get_tag_preview` in `lastfm/tag_preview.py`

**Files:**
- Create: `lastfm/tag_preview.py`
- Test: `tests/lastfm/test_tag_preview.py` (new file)

**Interfaces:**
- Consumes: `client.call(method, **params) -> dict` (any object with that method; production passes `lastfm.client.LastFMClient`).
- Produces: `get_tag_preview(client, tag: str) -> dict` returning `{"tag": str, "taggings": int, "reach": int, "top_artists": [str], "similar": [str]}`. Task 2's route spreads this dict into its JSON payload.

Last.fm response shapes (for the mocks and the parser):
- `tag.getInfo` → `{"tag": {"name": ..., "total": N, "reach": N, ...}}` (unknown tags: zeros).
- `tag.getTopArtists` → `{"topartists": {"artist": [{"name": ...}, ...]}}` (a single dict instead of a list when there is exactly one result).
- `tag.getSimilar` → `{"similartags": {"tag": [{"name": ...}, ...]}}` (frequently empty).

- [ ] **Step 1: Write the failing tests**

Create `tests/lastfm/test_tag_preview.py`:

```python
"""Tests for lastfm/tag_preview.py."""
from unittest.mock import MagicMock

import pytest


def _client(responses: dict, errors: dict | None = None):
    """Mock client: responses/errors keyed by API method name."""
    client = MagicMock()

    def call(method, **params):
        if errors and method in errors:
            raise errors[method]
        return responses.get(method, {})

    client.call.side_effect = call
    return client


def test_preview_happy_path():
    from lastfm.tag_preview import get_tag_preview
    client = _client({
        "tag.getInfo": {"tag": {"name": "dub techno", "total": 51234, "reach": 9876}},
        "tag.getTopArtists": {"topartists": {"artist": [
            {"name": "Deepchord"}, {"name": "Basic Channel"}]}},
        "tag.getSimilar": {"similartags": {"tag": [{"name": "dub"}, {"name": "minimal techno"}]}},
    })
    p = get_tag_preview(client, "  dub techno ")
    assert p == {
        "tag": "dub techno",
        "taggings": 51234,
        "reach": 9876,
        "top_artists": ["Deepchord", "Basic Channel"],
        "similar": ["dub", "minimal techno"],
    }


def test_preview_unknown_tag_returns_zeros():
    from lastfm.tag_preview import get_tag_preview
    client = _client({
        "tag.getInfo": {"tag": {"name": "dub tecno", "total": 0, "reach": 0}},
        "tag.getTopArtists": {"topartists": {"artist": []}},
        "tag.getSimilar": {"similartags": {"tag": []}},
    })
    p = get_tag_preview(client, "dub tecno")
    assert p["taggings"] == 0
    assert p["top_artists"] == []
    assert p["similar"] == []


def test_preview_single_artist_dict_normalized():
    from lastfm.tag_preview import get_tag_preview
    client = _client({
        "tag.getInfo": {"tag": {"total": 5, "reach": 3}},
        "tag.getTopArtists": {"topartists": {"artist": {"name": "Only One"}}},
        "tag.getSimilar": {"similartags": {}},
    })
    p = get_tag_preview(client, "obscure")
    assert p["top_artists"] == ["Only One"]


def test_preview_partial_failure_degrades():
    from lastfm.tag_preview import get_tag_preview
    client = _client(
        {"tag.getInfo": {"tag": {"total": 100, "reach": 50}},
         "tag.getTopArtists": {"topartists": {"artist": [{"name": "A"}]}}},
        errors={"tag.getSimilar": RuntimeError("boom")},
    )
    p = get_tag_preview(client, "dub techno")
    assert p["taggings"] == 100
    assert p["top_artists"] == ["A"]
    assert p["similar"] == []


def test_preview_total_failure_raises():
    from lastfm.tag_preview import get_tag_preview
    err = RuntimeError("lastfm down")
    client = _client({}, errors={
        "tag.getInfo": err, "tag.getTopArtists": err, "tag.getSimilar": err})
    with pytest.raises(RuntimeError):
        get_tag_preview(client, "dub techno")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/lastfm/test_tag_preview.py -v`
Expected: 5 FAILED/ERROR with `ModuleNotFoundError: No module named 'lastfm.tag_preview'`

- [ ] **Step 3: Implement**

Create `lastfm/tag_preview.py`:

```python
"""Last.fm tag preview: what a tag actually means before it seeds a mix."""
import logging

logger = logging.getLogger(__name__)

_TOP_ARTISTS_LIMIT = 10


def _as_list(raw):
    if isinstance(raw, dict):
        return [raw]
    return raw or []


def get_tag_preview(client, tag: str) -> dict:
    """Return {'tag', 'taggings', 'reach', 'top_artists', 'similar'} for a tag.

    Sub-call failures degrade to empty values; raises only when every
    sub-call fails (total Last.fm outage).
    """
    name = (tag or "").strip()
    failures = 0
    last_exc = None

    taggings = reach = 0
    try:
        info = client.call("tag.getInfo", tag=name)
        t = info.get("tag") or {}
        taggings = int(t.get("total") or 0)
        reach = int(t.get("reach") or 0)
    except Exception as exc:
        failures += 1
        last_exc = exc
        logger.warning("tag_preview: tag.getInfo failed for %r", name)

    top_artists = []
    try:
        data = client.call("tag.getTopArtists", tag=name, limit=_TOP_ARTISTS_LIMIT)
        raw = _as_list((data.get("topartists") or {}).get("artist"))
        top_artists = [a.get("name", "") for a in raw if a.get("name")]
    except Exception as exc:
        failures += 1
        last_exc = exc
        logger.warning("tag_preview: tag.getTopArtists failed for %r", name)

    similar = []
    try:
        data = client.call("tag.getSimilar", tag=name)
        raw = _as_list((data.get("similartags") or {}).get("tag"))
        similar = [t.get("name", "") for t in raw if t.get("name")]
    except Exception as exc:
        failures += 1
        last_exc = exc
        logger.warning("tag_preview: tag.getSimilar failed for %r", name)

    if failures == 3 and last_exc is not None:
        raise last_exc

    return {
        "tag": name,
        "taggings": taggings,
        "reach": reach,
        "top_artists": top_artists,
        "similar": similar,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/lastfm/ -v`
Expected: all PASS (5 new + existing lastfm tests)

- [ ] **Step 5: Commit**

```bash
git add lastfm/tag_preview.py tests/lastfm/test_tag_preview.py
git commit -m "feat(lastfm): add get_tag_preview (tag info + top artists + similar)"
```

---

### Task 2: `/genres/preview` route with TTL cache

**Files:**
- Modify: `sWebExt/py_server/server.py` — insert a new section directly after the `library_suffixes_post` function, before the `# ── Explore UI ──` comment
- Test: `tests/server/test_routes.py` (append)

**Interfaces:**
- Consumes: `lastfm.tag_preview.get_tag_preview(client, tag) -> dict` (Task 1), `lastfm.client.LastFMClient(api_key)`, existing `_get_config()`.
- Produces: `GET /genres/preview?tag=X` → `{"status":"ok","tag","taggings","reach","top_artists","similar"}`; module globals `_genre_preview_cache` (dict) and `_GENRE_PREVIEW_TTL = 3600`. Task 4's frontend calls this route.

Note: `server.py` already imports `time` and `json` at module level (used by the schedulers) — verify with `grep -n "^import time\|^import json" sWebExt/py_server/server.py` and do not re-import.

- [ ] **Step 1: Write the failing tests**

Append to `tests/server/test_routes.py`:

```python
# ── /genres routes (tag preview + vocab) ──────────────────────────────────────

def _cfg_file_with(tmp_path, cfg: dict):
    import json as _json
    f = tmp_path / "config.json"
    f.write_text(_json.dumps(cfg))
    return str(f)


def test_genres_preview_missing_tag_returns_400(client):
    resp = client.get("/genres/preview")
    assert resp.status_code == 400
    assert json.loads(resp.data)["status"] == "error"


def test_genres_preview_no_api_key_unavailable(client, tmp_path):
    cfg_path = _cfg_file_with(tmp_path, {})
    with patch("sWebExt.py_server.server._CONFIG_PATH", cfg_path):
        resp = client.get("/genres/preview?tag=dub")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["status"] == "unavailable"
    assert "lastfm_api_key" in data["reason"]


def test_genres_preview_happy_path(client, tmp_path, monkeypatch):
    import sWebExt.py_server.server as srv
    srv._genre_preview_cache.clear()
    cfg_path = _cfg_file_with(tmp_path, {"lastfm_api_key": "k"})
    fake = {"tag": "dub", "taggings": 1000, "reach": 500,
            "top_artists": ["King Tubby"], "similar": ["dub techno"]}
    with patch("sWebExt.py_server.server._CONFIG_PATH", cfg_path), \
         patch("lastfm.tag_preview.get_tag_preview", return_value=fake) as m, \
         patch("lastfm.client.LastFMClient"):
        resp = client.get("/genres/preview?tag=dub")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["status"] == "ok"
    assert data["taggings"] == 1000
    assert data["top_artists"] == ["King Tubby"]
    assert m.call_args.args[1] == "dub"


def test_genres_preview_cache_hit_skips_second_call(client, tmp_path):
    import sWebExt.py_server.server as srv
    srv._genre_preview_cache.clear()
    cfg_path = _cfg_file_with(tmp_path, {"lastfm_api_key": "k"})
    fake = {"tag": "dub", "taggings": 1, "reach": 1, "top_artists": [], "similar": []}
    with patch("sWebExt.py_server.server._CONFIG_PATH", cfg_path), \
         patch("lastfm.tag_preview.get_tag_preview", return_value=fake) as m, \
         patch("lastfm.client.LastFMClient"):
        client.get("/genres/preview?tag=Dub")
        resp = client.get("/genres/preview?tag=dUB")  # same tag, different case
    assert resp.status_code == 200
    assert m.call_count == 1


def test_genres_preview_upstream_error_returns_500(client, tmp_path):
    import sWebExt.py_server.server as srv
    srv._genre_preview_cache.clear()
    cfg_path = _cfg_file_with(tmp_path, {"lastfm_api_key": "k"})
    with patch("sWebExt.py_server.server._CONFIG_PATH", cfg_path), \
         patch("lastfm.tag_preview.get_tag_preview", side_effect=RuntimeError("down")), \
         patch("lastfm.client.LastFMClient"):
        resp = client.get("/genres/preview?tag=dub")
    assert resp.status_code == 500
    assert json.loads(resp.data)["status"] == "error"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/server/test_routes.py -k genres_preview -v`
Expected: 5 FAILED (404 route missing / AttributeError on `_genre_preview_cache`)

- [ ] **Step 3: Implement**

Insert into `sWebExt/py_server/server.py` directly after `library_suffixes_post`, before the `# ── Explore UI ──` comment:

```python
# ── Genre tag preview / vocabulary ────────────────────────────────────────────

_genre_preview_cache: dict = {}   # casefolded tag -> (fetched_at, payload)
_GENRE_PREVIEW_TTL = 3600         # seconds


@app.route("/genres/preview", methods=["GET"])
def genres_preview():
    tag = (request.args.get("tag") or "").strip()
    if not tag:
        return jsonify({"status": "error", "error": "tag required"}), 400
    cfg = _get_config()
    api_key = cfg.get("lastfm_api_key", "")
    if not api_key:
        return jsonify({"status": "unavailable", "reason": "lastfm_api_key not configured"})
    key = tag.casefold()
    now = time.time()
    cached = _genre_preview_cache.get(key)
    if cached and now - cached[0] < _GENRE_PREVIEW_TTL:
        return jsonify(cached[1])
    try:
        from lastfm.client import LastFMClient
        from lastfm.tag_preview import get_tag_preview
        payload = {"status": "ok", **get_tag_preview(LastFMClient(api_key), tag)}
    except Exception as e:
        logger.exception("[GENRES] preview failed")
        return jsonify({"status": "error", "error": str(e)}), 500
    _genre_preview_cache[key] = (now, payload)
    return jsonify(payload)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/server/test_routes.py -k genres_preview -v`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add tests/server/test_routes.py sWebExt/py_server/server.py
git commit -m "feat(server): add /genres/preview route with TTL cache"
```

---

### Task 3: `/genres/vocab` route (insights + library merge)

**Files:**
- Modify: `sWebExt/py_server/server.py` — append to the `# ── Genre tag preview / vocabulary ──` section created in Task 2
- Test: `tests/server/test_routes.py` (append)

**Interfaces:**
- Consumes: `_insights_db_path()` (exists, server.py), `insights.db.connect(path)` (creates schema if missing), `artist_tags.tags_json` = JSON list of `{"name": str, "weight": int}`, `discover.subsonic.Subsonic(host, user, pw).get_genres() -> [{"name": str, "songCount": int}]`, `_get_config()`.
- Produces: `GET /genres/vocab` → `{"status":"ok","tags":[str, …]}` (casefolded, deduped, sorted); module global `_genre_vocab_cache = {"ts": 0.0, "tags": None}` and `_GENRE_VOCAB_TTL = 600`. Task 4's frontend calls this route.

- [ ] **Step 1: Write the failing tests**

Append to `tests/server/test_routes.py`:

```python
def _seed_vocab_db(path):
    import json as _json
    from insights import db as idb
    conn = idb.connect(path)
    conn.execute(
        "INSERT INTO artist_tags (artist, tags_json, primary_genre, fetched_at) VALUES (?, ?, ?, ?)",
        ("Deepchord", _json.dumps([{"name": "dub techno", "weight": 100},
                                   {"name": "Ambient", "weight": 60}]), "dub techno", 1))
    conn.execute(
        "INSERT INTO artist_tags (artist, tags_json, primary_genre, fetched_at) VALUES (?, ?, ?, ?)",
        ("King Tubby", _json.dumps([{"name": "dub", "weight": 100}]), "dub", 1))
    conn.commit()
    conn.close()


def test_genres_vocab_merges_insights_and_library(client, monkeypatch, tmp_path):
    import sWebExt.py_server.server as srv
    srv._genre_vocab_cache["tags"] = None
    dbp = str(tmp_path / "i.db")
    _seed_vocab_db(dbp)
    monkeypatch.setattr(srv, "_insights_db_path", lambda: dbp)
    cfg_path = _cfg_file_with(tmp_path, {
        "navidrome_url": "http://x", "navidrome_user": "u", "navidrome_pass": "p"})
    fake_subsonic = MagicMock()
    fake_subsonic.get_genres.return_value = [
        {"name": "Techno", "songCount": 50}, {"name": "dub", "songCount": 10}]
    with patch("sWebExt.py_server.server._CONFIG_PATH", cfg_path), \
         patch("discover.subsonic.Subsonic", return_value=fake_subsonic):
        resp = client.get("/genres/vocab")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["status"] == "ok"
    # casefolded, deduped ("dub" from both sources appears once), sorted
    assert data["tags"] == ["ambient", "dub", "dub techno", "techno"]


def test_genres_vocab_insights_only_when_no_navidrome_creds(client, monkeypatch, tmp_path):
    import sWebExt.py_server.server as srv
    srv._genre_vocab_cache["tags"] = None
    dbp = str(tmp_path / "i.db")
    _seed_vocab_db(dbp)
    monkeypatch.setattr(srv, "_insights_db_path", lambda: dbp)
    cfg_path = _cfg_file_with(tmp_path, {})
    with patch("sWebExt.py_server.server._CONFIG_PATH", cfg_path):
        resp = client.get("/genres/vocab")
    data = json.loads(resp.data)
    assert data["tags"] == ["ambient", "dub", "dub techno"]


def test_genres_vocab_empty_sources_returns_empty_list(client, monkeypatch, tmp_path):
    import sWebExt.py_server.server as srv
    srv._genre_vocab_cache["tags"] = None
    monkeypatch.setattr(srv, "_insights_db_path", lambda: str(tmp_path / "missing.db"))
    cfg_path = _cfg_file_with(tmp_path, {})
    with patch("sWebExt.py_server.server._CONFIG_PATH", cfg_path):
        resp = client.get("/genres/vocab")
    data = json.loads(resp.data)
    assert data["status"] == "ok"
    assert data["tags"] == []


def test_genres_vocab_cached_second_call(client, monkeypatch, tmp_path):
    import sWebExt.py_server.server as srv
    srv._genre_vocab_cache["tags"] = None
    dbp = str(tmp_path / "i.db")
    _seed_vocab_db(dbp)
    calls = []
    monkeypatch.setattr(srv, "_insights_db_path", lambda: (calls.append(1), dbp)[1])
    cfg_path = _cfg_file_with(tmp_path, {})
    with patch("sWebExt.py_server.server._CONFIG_PATH", cfg_path):
        client.get("/genres/vocab")
        client.get("/genres/vocab")
    assert len(calls) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/server/test_routes.py -k genres_vocab -v`
Expected: 4 FAILED (404 / AttributeError on `_genre_vocab_cache`)

- [ ] **Step 3: Implement**

Append to the `# ── Genre tag preview / vocabulary ──` section in `sWebExt/py_server/server.py`:

```python
_genre_vocab_cache: dict = {"ts": 0.0, "tags": None}
_GENRE_VOCAB_TTL = 600  # seconds


def _vocab_from_insights() -> set:
    """Tag names from the insights artist_tags cache (casefolded)."""
    tags = set()
    try:
        from insights import db as insights_db
        conn = insights_db.connect(_insights_db_path())
        try:
            for (tags_json,) in conn.execute("SELECT tags_json FROM artist_tags"):
                for t in (json.loads(tags_json or "[]") or []):
                    name = (t.get("name") or "").strip()
                    if name:
                        tags.add(name.casefold())
        finally:
            conn.close()
    except Exception:
        logger.warning("[GENRES] vocab: insights source unavailable", exc_info=True)
    return tags


def _vocab_from_library() -> set:
    """Genre names from Navidrome (casefolded); empty when creds missing/down."""
    tags = set()
    try:
        cfg = _get_config()
        host = cfg.get("navidrome_url", "")
        user = cfg.get("navidrome_user", "")
        pw = cfg.get("navidrome_pass", "")
        if host and user and pw:
            from discover.subsonic import Subsonic
            for g in Subsonic(host, user, pw).get_genres():
                name = (g.get("name") or "").strip()
                if name:
                    tags.add(name.casefold())
    except Exception:
        logger.warning("[GENRES] vocab: library source unavailable", exc_info=True)
    return tags


@app.route("/genres/vocab", methods=["GET"])
def genres_vocab():
    now = time.time()
    if _genre_vocab_cache["tags"] is not None and now - _genre_vocab_cache["ts"] < _GENRE_VOCAB_TTL:
        return jsonify({"status": "ok", "tags": _genre_vocab_cache["tags"]})
    tags = sorted(_vocab_from_insights() | _vocab_from_library())
    _genre_vocab_cache["ts"] = now
    _genre_vocab_cache["tags"] = tags
    return jsonify({"status": "ok", "tags": tags})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/server/test_routes.py -k genres_ -v` then the full suite `.venv/bin/python -m pytest tests/ -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add tests/server/test_routes.py sWebExt/py_server/server.py
git commit -m "feat(server): add /genres/vocab route (insights + library tag merge)"
```

---

### Task 4: Mix editor — inline tag input, preview card, warn chips

**Files:**
- Modify: `web/static/app.js:283-312` (the genre chips block inside the mix editor; anchors: `// Genre chips row (only when mode=genre)` down to `inner.appendChild(genreRow);`)
- Modify: `web/static/app.css` (append)

**Interfaces:**
- Consumes: `GET /genres/preview?tag=X` (Task 2) and `GET /genres/vocab` (Task 3); existing `API()` helper; existing CSS classes `chip`, `chip add`, `txt`, `btn`, `frow`.
- Produces: user-facing editor UI only.

No JS harness — verification is the pytest suite as regression, a `node -e "new Function(require('fs').readFileSync('web/static/app.js','utf8'))"` syntax check, and Task 5's manual checks.

- [ ] **Step 1: Add module-level preview/vocab helpers**

Insert near the top of `web/static/app.js`, after the `API` helper function (app.js:2 region — place directly after the API function's closing brace):

```js
// ── Genre tag preview helpers ─────────────────────────────────────────────────
const _tagPreviewCache = {};  // casefolded tag -> Promise<payload>
function fetchTagPreview(tag) {
  const key = tag.trim().toLowerCase();
  if (!_tagPreviewCache[key]) {
    _tagPreviewCache[key] = API('/genres/preview?tag=' + encodeURIComponent(tag.trim()))
      .catch(e => { delete _tagPreviewCache[key]; throw e; });
  }
  return _tagPreviewCache[key];
}
function isWeakTag(p) {
  return p.status === 'ok' && (p.taggings < 100 || !(p.top_artists || []).length);
}
let _genreVocabPromise = null;
function fetchGenreVocab() {
  if (!_genreVocabPromise) {
    _genreVocabPromise = API('/genres/vocab')
      .then(d => d.tags || [])
      .catch(e => { _genreVocabPromise = null; return []; });
  }
  return _genreVocabPromise;
}
```

- [ ] **Step 2: Replace the chip-add flow**

Replace the `renderChips` function only (app.js:291-308, from `function renderChips() {` through its closing `}` — the existing `renderChips();` call on line 309 stays where it is) with:

```js
  function renderChips() {
    chipsDiv.textContent = '';
    genres.forEach((g, i) => {
      const chip = document.createElement('span');
      chip.className = 'chip';
      chip.textContent = g + ' ✕';
      chip.onclick = () => { genres.splice(i, 1); renderChips(); };
      chipsDiv.appendChild(chip);
      fetchTagPreview(g).then(p => {
        if (isWeakTag(p)) {
          chip.classList.add('warn');
          chip.textContent = '⚠ ' + g + ' ✕';
          chip.title = 'barely used on Last.fm — check the preview';
        }
      }).catch(() => {});
    });
    const addChip = document.createElement('span');
    addChip.className = 'chip add';
    addChip.textContent = '+ add';
    addChip.onclick = () => {
      addChip.style.display = 'none';
      tagEditor.style.display = '';
      tagInput.focus();
      fetchGenreVocab();
    };
    chipsDiv.appendChild(addChip);
  }
```

- [ ] **Step 3: Build the tag editor (input + suggestions + preview card)**

Insert directly after `genreRow.appendChild(chipsDiv);` (before `inner.appendChild(genreRow);`):

```js
  // Inline tag editor: input + vocab suggestions + Last.fm preview card
  const tagEditor = document.createElement('div');
  tagEditor.className = 'tag-editor';
  tagEditor.style.display = 'none';
  const tagInput = document.createElement('input');
  tagInput.className = 'txt';
  tagInput.type = 'text';
  tagInput.placeholder = 'genre tag… (Enter to preview)';
  const sugDiv = document.createElement('div');
  sugDiv.className = 'tag-suggest';
  const cardDiv = document.createElement('div');
  cardDiv.className = 'tag-card';
  cardDiv.style.display = 'none';
  const btnRow = document.createElement('div');
  btnRow.className = 'tag-btns';
  const addBtn = document.createElement('button');
  addBtn.className = 'btn run';
  addBtn.textContent = 'ADD';
  addBtn.disabled = true;
  const cancelBtn = document.createElement('button');
  cancelBtn.className = 'btn';
  cancelBtn.textContent = 'CANCEL';
  btnRow.appendChild(addBtn);
  btnRow.appendChild(cancelBtn);
  tagEditor.appendChild(tagInput);
  tagEditor.appendChild(sugDiv);
  tagEditor.appendChild(cardDiv);
  tagEditor.appendChild(btnRow);
  genreRow.appendChild(tagEditor);

  tagInput.oninput = async () => {
    addBtn.disabled = !tagInput.value.trim();
    const q = tagInput.value.trim().toLowerCase();
    sugDiv.textContent = '';
    if (q.length < 2) return;
    const vocab = await fetchGenreVocab();
    if (tagInput.value.trim().toLowerCase() !== q) return;  // stale
    vocab.filter(t => t.includes(q)).slice(0, 8).forEach(t => {
      const s = document.createElement('span');
      s.className = 'chip';
      s.textContent = t;
      s.onclick = () => { tagInput.value = t; sugDiv.textContent = ''; addBtn.disabled = false; previewTag(t); };
      sugDiv.appendChild(s);
    });
  };
  tagInput.onkeydown = (e) => {
    if (e.key === 'Enter') { e.preventDefault(); previewTag(tagInput.value); }
  };

  async function previewTag(tag) {
    tag = (tag || '').trim();
    if (!tag) return;
    cardDiv.style.display = '';
    cardDiv.textContent = 'loading preview…';
    try {
      const p = await fetchTagPreview(tag);
      if (tagInput.value.trim().toLowerCase() !== tag.toLowerCase()) return;  // stale
      cardDiv.textContent = '';
      if (p.status !== 'ok') { cardDiv.textContent = 'preview unavailable'; return; }
      const stats = document.createElement('div');
      stats.className = 'tc-stats';
      stats.textContent = p.taggings.toLocaleString() + ' taggings on Last.fm';
      cardDiv.appendChild(stats);
      const arts = document.createElement('div');
      arts.textContent = (p.top_artists || []).length
        ? 'top: ' + p.top_artists.join(', ')
        : 'no artists found for this tag';
      cardDiv.appendChild(arts);
      if ((p.similar || []).length) {
        const simRow = document.createElement('div');
        simRow.className = 'tc-similar';
        p.similar.slice(0, 8).forEach(t => {
          const s = document.createElement('span');
          s.className = 'chip';
          s.textContent = t;
          s.onclick = () => { tagInput.value = t; addBtn.disabled = false; previewTag(t); };
          simRow.appendChild(s);
        });
        cardDiv.appendChild(simRow);
      }
      if (isWeakTag(p)) {
        const w = document.createElement('div');
        w.className = 'tc-warn';
        w.textContent = '⚠ barely used on Last.fm — check the preview';
        cardDiv.appendChild(w);
      }
    } catch (e) {
      cardDiv.textContent = 'preview unavailable';
    }
  }

  function closeTagEditor() {
    tagEditor.style.display = 'none';
    tagInput.value = '';
    sugDiv.textContent = '';
    cardDiv.style.display = 'none';
    cardDiv.textContent = '';
    addBtn.disabled = true;
    renderChips();
  }
  addBtn.onclick = () => {
    const g = tagInput.value.trim();
    if (g) { genres.push(g); closeTagEditor(); }
  };
  cancelBtn.onclick = closeTagEditor;
```

Placement note: `renderChips` (Step 2) references `tagEditor`/`tagInput` only inside the `addChip.onclick` closure, which cannot fire until after this block has executed — so the pre-existing `renderChips();` call on line 309 running before these `const` declarations is safe (no TDZ evaluation at call time). No reordering of existing lines is needed.

- [ ] **Step 4: Add CSS**

Append to `web/static/app.css`:

```css
.chip.warn{border-color:#7a5a1a;color:#ffb04d}
.tag-editor{margin-top:8px;display:flex;flex-direction:column;gap:8px;width:100%}
.tag-suggest{display:flex;gap:6px;flex-wrap:wrap}
.tag-btns{display:flex;gap:8px}
.tag-card{border:1px solid var(--line);border-radius:4px;padding:10px;font-family:'JetBrains Mono',monospace;font-size:.64rem;color:var(--mut)}
.tag-card .tc-stats{color:var(--acid);margin-bottom:6px}
.tag-card .tc-similar{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
.tag-card .tc-warn{color:#ffb04d;margin-top:8px}
```

- [ ] **Step 5: Verify**

Run: `.venv/bin/python -m pytest tests/ -q` (regression — expect same pass count as Task 3's run)
Run: `node -e "new Function(require('fs').readFileSync('web/static/app.js','utf8'))"` (clean exit)

- [ ] **Step 6: Commit**

```bash
git add web/static/app.js web/static/app.css
git commit -m "feat(web): genre tag editor with vocab autocomplete, Last.fm preview card, warn chips"
```

---

### Task 5: End-to-end verification

**Files:** none (verification only)

- [ ] **Step 1: Full test suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: all PASS

- [ ] **Step 2: Restart the live service and smoke-test the routes**

```bash
systemctl --user restart amusicserver
sleep 5
systemctl --user is-active amusicserver
curl -s 'http://localhost:5000/genres/preview?tag=dub' | python3 -m json.tool | head -20
curl -s 'http://localhost:5000/genres/preview?tag=dub%20techno' | python3 -m json.tool | head -20
curl -s 'http://localhost:5000/genres/vocab' | python3 -c "import json,sys; t=json.load(sys.stdin)['tags']; print(len(t), 'tags;', [x for x in t if 'dub' in x][:5])"
```

Expected: `tag=dub` preview lists reggae-lineage artists (King Tubby / Lee "Scratch" Perry / Scientist territory) — the proof the preview would have caught the original mistake; `tag=dub techno` lists Deepchord/Basic Channel territory; vocab returns a non-trivial list including dub-related tags from the user's history.
(If port 5000 is squatted after restart — `is-active` stuck on `activating` with "Address already in use" in `journalctl --user -u amusicserver` — find the orphan with `ss -tlnp | grep :5000` and kill it; the unit auto-recovers within ~5 s.)

- [ ] **Step 3: Manual browser checks (user-facing)**

In the web UI → MIXES → open a genre-mode mix (e.g. the dubtechno weekly):
1. Existing chips show state: a weak/wrong tag renders amber with ⚠, healthy ones stay neutral.
2. "+ add" opens the inline editor; typing "dub" suggests tags from your own history (e.g. "dub techno").
3. Enter/selection shows the preview card: taggings count, top-10 artists, similar-tag chips that swap on click.
4. A weak tag shows the ⚠ note in the card but ADD still works; saving the mix is unaffected.

- [ ] **Step 4: Report results to the user before claiming completion**
