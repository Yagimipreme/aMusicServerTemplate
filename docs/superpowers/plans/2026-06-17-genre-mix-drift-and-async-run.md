# Genre Mix Drift + Async Run Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make genre mixes download on-genre tracks (no more phonk→rap drift) and make the mix-run button non-blocking with live status, so the webUI stops showing errors during long runs.

**Architecture:** (1) Genre mode in `run_profile` gets a seed-direct pipeline (genre top artists, no similar-artist expansion, no listener floor) plus a Last.fm tag gate that drops artists not carrying the target genre. (2) The `POST /mixes/<id>/run` route spawns a background daemon thread and returns immediately; a per-mix in-memory status store is polled by the frontend — mirroring the existing enrich background-job pattern.

**Tech Stack:** Python 3.13, Flask, vanilla JS (`web/static/app.js`), pytest. Last.fm via `lastfm/` package.

## Global Constraints

- Reuse `lastfm.tags.get_artist_tags(client, artist)` — returns `[{"name": <lowercased>, "weight": int}]`, `[]` on error. Do not add new Last.fm client methods.
- Reuse the existing single-runner lock `_discover_running` (`server.py:121`). Only one discover/mix run at a time.
- Mirror the enrich async pattern exactly: module-global status (`server.py:87 _enrich_last_result`), `locked()` guard (`server.py:1283`), `threading.Thread(..., daemon=True)` (`server.py:1288`), `/status` GET route (`server.py:1293`), `pollEnrich` (`app.js:532`).
- Do NOT touch the follow (`app.js:1454`) or dedup (`app.js:609`) run buttons — out of scope.
- TDD: failing test first, minimal impl, commit per task.

---

### Task 1: `filter_artists_by_genre` gate helper

**Files:**
- Modify: `discover/seeds.py` (add function after `genre_seed_artists`, ~line 25)
- Test: `tests/discover/test_seeds.py` (add tests; file exists)

**Interfaces:**
- Consumes: `lastfm.tags.get_artist_tags(client, name) -> list[{"name","weight"}]`
- Produces: `filter_artists_by_genre(lastfm_client, artists: list[dict], genres: list[str]) -> list[dict]` — keeps artists whose Last.fm top tags contain a target genre token (substring, casefolded). Drops artists with no matching tag (fail-closed). Returns input unchanged when `genres` is empty or `lastfm_client is None`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/discover/test_seeds.py`:

```python
from types import SimpleNamespace
from discover.seeds import filter_artists_by_genre


def _client_with_tags(tag_map):
    """tag_map: {artist_name: [tag_str, ...]} -> fake lastfm client."""
    def call(method, **kwargs):
        if method == "artist.getTopTags":
            name = kwargs.get("artist")
            tags = tag_map.get(name, [])
            return {"toptags": {"tag": [{"name": t, "count": 100} for t in tags]}}
        return {}
    return SimpleNamespace(call=call)


def test_gate_keeps_exact_genre_match():
    client = _client_with_tags({"V21": ["phonk", "electronic"]})
    artists = [{"id": "-1", "name": "V21"}]
    assert filter_artists_by_genre(client, artists, ["phonk"]) == artists


def test_gate_keeps_genre_variant_substring():
    # "phonk" token is a substring of "drift phonk"
    client = _client_with_tags({"OBLXKQ": ["drift phonk", "memphis"]})
    artists = [{"id": "-1", "name": "OBLXKQ"}]
    assert len(filter_artists_by_genre(client, artists, ["phonk"])) == 1


def test_gate_drops_rap_only_artist():
    client = _client_with_tags({"SomeRapper": ["rap", "hip-hop", "trap"]})
    artists = [{"id": "-1", "name": "SomeRapper"}]
    assert filter_artists_by_genre(client, artists, ["phonk"]) == []


def test_gate_is_case_insensitive_and_dedupes_genres():
    client = _client_with_tags({"V21": ["Phonk"]})  # get_artist_tags lowercases tags
    artists = [{"id": "-1", "name": "V21"}]
    # genres list has mixed case duplicates
    assert len(filter_artists_by_genre(client, artists, ["Phonk", "phonk"])) == 1


def test_gate_drops_artist_when_tag_fetch_fails():
    def call(method, **kwargs):
        raise RuntimeError("lastfm down")
    client = SimpleNamespace(call=call)
    artists = [{"id": "-1", "name": "Whoever"}]
    assert filter_artists_by_genre(client, artists, ["phonk"]) == []


def test_gate_passthrough_when_no_genres():
    client = _client_with_tags({})
    artists = [{"id": "-1", "name": "A"}, {"id": "-1", "name": "B"}]
    assert filter_artists_by_genre(client, artists, []) == artists


def test_gate_passthrough_when_no_client():
    artists = [{"id": "-1", "name": "A"}]
    assert filter_artists_by_genre(None, artists, ["phonk"]) == artists
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/discover/test_seeds.py -k gate -v`
Expected: FAIL with `ImportError: cannot import name 'filter_artists_by_genre'`

- [ ] **Step 3: Write minimal implementation**

Add to `discover/seeds.py` (after `genre_seed_artists`, before `collect_seeds`):

```python
def filter_artists_by_genre(lastfm_client, artists, genres):
    """Keep only artists whose Last.fm top tags include a target genre token.

    Token match is casefold substring, so "phonk" keeps an artist tagged
    "drift phonk" or "phonk, rap" but drops one tagged only "rap, hip-hop".
    Fail-closed: artists whose tag fetch errors or returns no match are dropped.
    Returns the input unchanged when genres is empty or lastfm_client is None.
    """
    targets = {g.strip().casefold() for g in (genres or []) if g and g.strip()}
    if not targets or lastfm_client is None:
        return list(artists)
    from lastfm.tags import get_artist_tags
    kept = []
    for a in artists:
        name = a.get("name")
        if not name:
            continue
        tag_names = [t["name"] for t in get_artist_tags(lastfm_client, name)]
        if any(tok in tname for tok in targets for tname in tag_names):
            kept.append(a)
    return kept
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/discover/test_seeds.py -k gate -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add discover/seeds.py tests/discover/test_seeds.py
git commit -m "feat(discover): add filter_artists_by_genre tag gate for genre mixes"
```

---

### Task 2: Genre-mode seed-direct pipeline in `run_profile`

**Files:**
- Modify: `discover/engine.py:238` (genre seed line) and `discover/engine.py:247-255` (candidate pipeline)
- Test: `tests/discover/test_run_profile.py` (migrate 2 tests, add 2 new)

**Interfaces:**
- Consumes: `filter_artists_by_genre` (Task 1); existing `genre_seed_artists(client, genres, limit_per_genre=60)`, `resolve_tracks`, `expand_similar`, `enrich_artist_info`.
- Produces: genre mode resolves tracks from gated genre top artists only — `expand_similar` and `enrich_artist_info` are NOT called when `mode == "genre"`.

- [ ] **Step 1: Migrate the two existing genre tests that depend on similar-artist expansion**

These tests used `mode="genre"` only to bypass readiness; they assert acquisitions that come from `expand_similar`. After this task genre mode no longer expands, so switch them to `mode="manual"` (same downstream pipeline, with seed artists).

In `tests/discover/test_run_profile.py`, edit `test_run_profile_ratio_blend_exact_split` (line ~290):

```python
    profile = make_profile(count=10, cap=50, new_ratio=0.3, mode="manual", artists=["SeedArtist"])
```

And `test_run_profile_library_shortfall_backfilled_by_acquisition` (line ~309):

```python
    profile = make_profile(count=10, cap=50, new_ratio=0.3, mode="manual", artists=["SeedArtist"])
```

- [ ] **Step 2: Add new failing tests for genre seed-direct + gate**

Append to `tests/discover/test_run_profile.py`:

```python
# ── genre mode: seed-direct + tag gate (no similar expansion) ─────────────────

def _genre_deps(tmp_path, tag_map, top_artists):
    """Deps whose tag.gettopartists returns top_artists and
    artist.getTopTags returns tag_map[name]."""
    deps, downloaded = build_deps(tmp_path)

    def fake_call(method, **kwargs):
        if method == "tag.gettopartists":
            return {"topartists": {"artist": [{"name": n} for n in top_artists]}}
        if method == "artist.getTopTags":
            name = kwargs.get("artist")
            return {"toptags": {"tag": [{"name": t, "count": 100}
                                        for t in tag_map.get(name, [])]}}
        if method == "artist.getSimilar":
            raise AssertionError("genre mode must not expand to similar artists")
        if method == "artist.getInfo":
            raise AssertionError("genre mode must not apply listener floor")
        return {}

    deps.lastfm_client.call = fake_call
    return deps, downloaded


def test_genre_mode_downloads_only_gated_artists(tmp_path, monkeypatch):
    monkeypatch.setattr("discover.engine.lastfm_is_ready", lambda *a, **kw: True)
    tag_map = {"PhonkGuy": ["phonk"], "RapGuy": ["rap", "hip-hop"]}
    deps, downloaded = _genre_deps(tmp_path, tag_map, ["PhonkGuy", "RapGuy"])
    profile = make_profile(count=5, cap=20, new_ratio=1.0, mode="genre", genres=["phonk"])
    run_profile(deps, make_cfg(), profile)
    # Only PhonkGuy passes the gate; RapGuy dropped. search_fn url is http://y/<name>
    assert any("PhonkGuy" in p for p in downloaded)
    assert not any("RapGuy" in p for p in downloaded)


def test_genre_mode_does_not_call_similar_or_listener_floor(tmp_path, monkeypatch):
    monkeypatch.setattr("discover.engine.lastfm_is_ready", lambda *a, **kw: True)
    # fake_call raises if artist.getSimilar / artist.getInfo are hit
    deps, _ = _genre_deps(tmp_path, {"PhonkGuy": ["phonk"]}, ["PhonkGuy"])
    profile = make_profile(count=2, cap=10, new_ratio=1.0, mode="genre", genres=["phonk"])
    run_profile(deps, make_cfg(), profile)  # must not raise AssertionError
```

- [ ] **Step 3: Run new tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/discover/test_run_profile.py -k genre_mode -v`
Expected: FAIL — `artist.getSimilar must not expand` AssertionError (current code expands).

- [ ] **Step 4: Implement the genre seed-direct pipeline**

In `discover/engine.py`, change the genre seed line (currently line 237-238):

```python
        elif mode == "genre":
            seeds = (genre_seed_artists(lastfm_client, seeds_cfg.get("genres") or [],
                                        limit_per_genre=60)
                     if lastfm_client else [])
```

Then change the shared candidate block (currently lines 247-255) to branch on genre mode:

```python
        if new_count > 0 and seeds:
            if mode == "genre":
                from discover.seeds import filter_artists_by_genre
                artists = filter_artists_by_genre(lastfm_client, seeds,
                                                  seeds_cfg.get("genres") or [])
            else:
                oversample = int(quality.get("candidate_oversample", 3))
                artists = expand_similar(deps.subsonic, seeds, per_seed=20,
                                         lastfm_client=lastfm_client)
                if lastfm_client is not None:
                    k = int(quality.get("seed_artist_count", 20)) * oversample
                    artists = sorted(artists, key=lambda a: -a.get("score", 0))[:k]
                    artists = enrich_artist_info(lastfm_client, artists,
                                                 min_listeners=int(quality.get("min_artist_listeners", 5000)))
            candidates = resolve_tracks(deps.search_fn, artists, per_artist=1)
            fresh_all = list(filter_fresh(deps.subsonic.song_exists, deps.state, candidates))
            fresh_iter = iter(fresh_all)
            for c in fresh_iter:
```

(Leave the rest of the acquisition loop unchanged.)

- [ ] **Step 5: Run the full run_profile suite**

Run: `.venv/bin/python -m pytest tests/discover/test_run_profile.py -v`
Expected: PASS (all tests, including the 2 migrated and 2 new)

- [ ] **Step 6: Commit**

```bash
git add discover/engine.py tests/discover/test_run_profile.py
git commit -m "fix(discover): genre mixes use seed-direct + tag gate, no similar drift"
```

---

### Task 3: Async mix run + per-mix status store (backend)

**Files:**
- Modify: `sWebExt/py_server/server.py` — add global (~line 122), edit `_run_profile_once` (line 374), edit run route (line 1626), add status route.
- Test: `tests/server/test_routes.py` — add fixture reset, rewrite 3 run tests.

**Interfaces:**
- Produces: `GET /mixes/<id>/status -> {"status": "idle|running|ok|error|...", ...}`. `POST /mixes/<id>/run` returns `202 {"status":"started"}` (spawned) or `200 {"status":"running"}` (already in flight) or `404` (unknown id). Module global `_mix_last_results: dict[str, dict]`.

- [ ] **Step 1: Write failing route tests**

In `tests/server/test_routes.py`, add `_mix_last_results` reset to the `app` fixture (after line 20):

```python
        srv._mix_last_results = {}
```

Replace `test_post_mixes_run_triggers_run`, `test_post_mixes_run_busy_returns_409`, and `test_post_mixes_run_error_returns_500` with:

```python
def test_post_mixes_run_starts_background_and_returns_202(client, tmp_path):
    """POST /mixes/<id>/run spawns a background run and returns 202 started."""
    import json as _json
    profile = _make_valid_profile(id="mymix", name="My Mix")
    cfg = {"mixes": [profile]}
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(_json.dumps(cfg))
    with patch("sWebExt.py_server.server._CONFIG_PATH", str(cfg_file)), \
         patch("sWebExt.py_server.server._discover_running") as lock:
        lock.locked.return_value = False
        resp = client.post("/mixes/mymix/run")
    assert resp.status_code == 202
    assert _json.loads(resp.data)["status"] == "started"


def test_post_mixes_run_already_running_returns_200_running(client, tmp_path):
    """POST while a run is in flight → 200 {'status':'running'}, no new thread."""
    import json as _json
    profile = _make_valid_profile(id="mymix", name="My Mix")
    cfg = {"mixes": [profile]}
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(_json.dumps(cfg))
    with patch("sWebExt.py_server.server._CONFIG_PATH", str(cfg_file)), \
         patch("sWebExt.py_server.server._discover_running") as lock:
        lock.locked.return_value = True
        resp = client.post("/mixes/mymix/run")
    assert resp.status_code == 200
    assert _json.loads(resp.data)["status"] == "running"


def test_post_mixes_run_unknown_id_returns_404(client, tmp_path):
    import json as _json
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(_json.dumps({"mixes": []}))
    with patch("sWebExt.py_server.server._CONFIG_PATH", str(cfg_file)):
        resp = client.post("/mixes/nope/run")
    assert resp.status_code == 404


def test_mixes_status_defaults_idle(client):
    import json as _json
    resp = client.get("/mixes/whatever/status")
    assert resp.status_code == 200
    assert _json.loads(resp.data)["status"] == "idle"


def test_run_profile_once_records_status(tmp_path):
    """_run_profile_once stores its result in _mix_last_results keyed by id."""
    from sWebExt.py_server import server as srv
    srv._mix_last_results = {}
    profile = _make_valid_profile(id="mymix", name="My Mix")
    with patch("sWebExt.py_server.server._build_discover_deps", return_value=None):
        srv._run_profile_once(profile)
    assert "mymix" in srv._mix_last_results
    assert srv._mix_last_results["mymix"]["status"] in ("disabled", "ok", "error")
```

(`tests/server/test_routes.py` already imports `_make_valid_profile`; if not, copy the helper used by the existing DELETE tests at line ~429.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/server/test_routes.py -k "mixes_run or mixes_status or run_profile_once_records" -v`
Expected: FAIL (route still synchronous; no `_mix_last_results`; no status route).

- [ ] **Step 3: Add the status global**

In `sWebExt/py_server/server.py`, after line 121 (`_discover_running = threading.Lock()`):

```python
_mix_last_results: dict = {}   # mix_id -> last run result dict (status polling)
```

- [ ] **Step 4: Record status inside `_run_profile_once`**

Edit `_run_profile_once` (line 374) to stamp `_mix_last_results`. The acquire-failure return happens **before** `try`, so the `finally: release()` only runs on the path that actually acquired the lock:

```python
def _run_profile_once(profile) -> dict:
    pid = profile["id"]
    _mix_last_results[pid] = {"status": "running"}
    if not _discover_running.acquire(blocking=False):
        result = {"status": "busy", "reason": "another discover run in progress"}
        _mix_last_results[pid] = result
        return result
    try:
        deps = _build_discover_deps()
        if deps is None:
            result = {"status": "disabled", "reason": "navidrome creds missing"}
            _mix_last_results[pid] = result
            return result
        from discover.engine import run_profile
        result = run_profile(deps, _get_config(), profile)
        result.setdefault("status", "ok")
        logger.info("[MIXES] %s run complete: %s", pid, result)
        _record_last_run(pid)
        _mix_last_results[pid] = result
        return result
    except Exception as e:
        logger.exception("[MIXES] %s run failed", pid)
        result = {"status": "error", "error": str(e)}
        _mix_last_results[pid] = result
        return result
    finally:
        _discover_running.release()
```

- [ ] **Step 5: Rewrite the run route + add status route**

Replace `mixes_run` (lines 1626-1637):

```python
@app.route("/mixes/<mix_id>/run", methods=["POST"])
def mixes_run(mix_id):
    mixes = _load_mixes()
    profile = next((m for m in mixes if m["id"] == mix_id), None)
    if profile is None:
        return jsonify({"status": "error", "error": f"mix {mix_id!r} not found"}), 404
    if _discover_running.locked():
        return jsonify({"status": "running"}), 200
    _mix_last_results[mix_id] = {"status": "running"}
    threading.Thread(target=_run_profile_once, args=(profile,), daemon=True).start()
    return jsonify({"status": "started", "mix_id": mix_id}), 202


@app.route("/mixes/<mix_id>/status", methods=["GET"])
def mixes_status(mix_id):
    return jsonify(_mix_last_results.get(mix_id, {"status": "idle"}))
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/server/test_routes.py -v`
Expected: PASS (rewritten + new tests; the `app` fixture patches `threading.Thread` so no real thread starts).

- [ ] **Step 7: Commit**

```bash
git add sWebExt/py_server/server.py tests/server/test_routes.py
git commit -m "feat(server): run mixes in background thread with per-mix status polling"
```

---

### Task 4: Frontend — non-blocking run button with status polling

**Files:**
- Modify: `web/static/app.js:361-388` (run button) — add a poll loop mirroring `pollEnrich` (line 532).

**Interfaces:**
- Consumes: `POST /mixes/<id>/run` (202 started / 200 running), `GET /mixes/<id>/status` (Task 3).

- [ ] **Step 1: Replace the run button handler**

In `web/static/app.js`, replace the RUN button block (lines 361-388, inside `if (!isNew) { ... }`) with:

```javascript
  if (!isNew) {
    let mixPollTimer = null;
    const runBtn = document.createElement('button');
    runBtn.className = 'btn run';
    runBtn.textContent = '▶ run now';

    function pollMix() {
      if (mixPollTimer) clearInterval(mixPollTimer);
      statusLine.style.display = '';
      statusLine.textContent = 'Running…';
      runBtn.disabled = true;
      runBtn.textContent = 'running';
      mixPollTimer = setInterval(async () => {
        try {
          const s = await API('/mixes/' + encodeURIComponent(mix.id) + '/status');
          if (s.status === 'running' || s.status === 'started') {
            statusLine.textContent = 'Running…';
            return;
          }
          clearInterval(mixPollTimer); mixPollTimer = null;
          runBtn.disabled = false; runBtn.textContent = '▶ run now';
          if (s.status === 'error') {
            statusLine.textContent = 'Error: ' + (s.error || 'run failed');
          } else if (s.status === 'busy' || s.status === 'disabled') {
            statusLine.textContent = s.status + (s.reason ? ': ' + s.reason : '');
          } else {
            const parts = [];
            if (s.acquired !== undefined) parts.push('acquired: ' + s.acquired);
            if (s.library_added !== undefined) parts.push('added: ' + s.library_added);
            statusLine.textContent = 'Done. ' + (parts.join(', ') || (s.status || 'ok'));
          }
        } catch(e) { /* transient — keep polling */ }
      }, 3000);
    }

    runBtn.onclick = async () => {
      statusLine.style.display = '';
      statusLine.textContent = 'Starting run…';
      try {
        await API('/mixes/' + encodeURIComponent(mix.id) + '/run', {method: 'POST'});
        pollMix();  // 'started' or 'running' — begin polling either way
      } catch(e) {
        statusLine.textContent = 'Error: ' + (e.message || 'unknown');
      }
    };
    actions.appendChild(runBtn);
  }
```

- [ ] **Step 2: Verify (manual smoke — no JS test harness in repo)**

Run the server and exercise the button:
Run: `.venv/bin/python -m pytest tests/server/test_routes.py -q` (confirm backend green), then start the app and click ▶ run on a mix.
Expected: button shows "running", status line shows "Running…", then "Done. acquired: N" without any error toast. (If using the `verify` or `run` skill, drive the browser to confirm.)

- [ ] **Step 3: Commit**

```bash
git add web/static/app.js
git commit -m "feat(web): poll mix run status instead of blocking on the request"
```

---

### Task 5: Rebuild PHONK MIX (operational, post-deploy)

**Files:** none (operational step). Not committed.

**Interfaces:** none.

- [ ] **Step 1: Deploy + restart**

Restart the running server so the new code is live:
Run: `systemctl --user restart amusicserver`
Expected: service active (`systemctl --user status amusicserver`).

- [ ] **Step 2: Clear the existing playlist**

The m3u lives at the path from the log: `/mnt/nas1/media/music/PHONK MIX.m3u`. Remove it so the next run rebuilds from scratch (rap mp3s stay on disk per the design):
Run: `rm "/mnt/nas1/media/music/PHONK MIX.m3u"`
Expected: file removed (confirm with `ls "/mnt/nas1/media/music/" | grep -i phonk`).

- [ ] **Step 3: Trigger a fresh run and confirm on-genre**

Trigger via the webUI ▶ run (now async) or:
Run: `curl -s -X POST http://localhost:5000/mixes/PHONK/run`
Then poll: `curl -s http://localhost:5000/mixes/PHONK/status`
Expected: status goes `running` → `ok` with `acquired > 0`; spot-check the new `PHONK MIX.m3u` entries are phonk (e.g. check a few files' genre tags) and not rap.

---

### Task 6: Broaden the gate's tag view (whole-branch review finding #1)

**Why:** The gate (`filter_artists_by_genre`) reuses `get_artist_tags`, which truncates to the top-3 tags with weight ≥ 10. An underground genre artist whose target-genre tag ranks 4th or below weight 10 is wrongly dropped — defeating the design's "rescue underground phonk" goal. Fix: give the gate a broader tag view (more tags, weight floor 1) without changing `get_artist_tags`' defaults for its other callers (`library/enrich.py`, `insights/genres.py`, `sWebExt/py_server/server.py`, and `tests/lastfm/test_tags.py`).

**Files:**
- Modify: `lastfm/tags.py` (parameterize `_clean_tags` and `get_artist_tags`)
- Modify: `discover/seeds.py` (gate calls with broader params)
- Test: `tests/discover/test_seeds.py`, `tests/lastfm/test_tags.py`

**Interfaces:**
- Produces: `get_artist_tags(client, artist, top_n=_TOP_N, min_weight=_MIN_WEIGHT)` — defaults unchanged (top-3, weight≥10). `filter_artists_by_genre` calls it with `top_n=25, min_weight=1`.

- [ ] **Step 1: Write the failing test (gate keeps low-ranked genre tag)**

Add to `tests/discover/test_seeds.py`:

```python
def _client_with_weighted_tags(tag_map):
    """tag_map: {artist: [(tag, count), ...]} -> fake lastfm client."""
    def call(method, **kwargs):
        if method == "artist.getTopTags":
            name = kwargs.get("artist")
            pairs = tag_map.get(name, [])
            return {"toptags": {"tag": [{"name": t, "count": c} for t, c in pairs]}}
        return {}
    return SimpleNamespace(call=call)


def test_gate_keeps_artist_with_low_ranked_genre_tag():
    # "phonk" is 4th AND below weight 10 — old top-3/weight-10 view dropped it.
    client = _client_with_weighted_tags({
        "Underground": [("memphis", 50), ("trap", 40), ("lo-fi", 20), ("phonk", 8)]
    })
    artists = [{"id": "-1", "name": "Underground"}]
    assert len(filter_artists_by_genre(client, artists, ["phonk"])) == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/discover/test_seeds.py::test_gate_keeps_artist_with_low_ranked_genre_tag -v`
Expected: FAIL (current gate uses top-3/weight-10 → "phonk" at rank 4, weight 8 is excluded → artist dropped → len 0).

- [ ] **Step 3: Parameterize `_clean_tags` and `get_artist_tags`**

In `lastfm/tags.py`, change `_clean_tags` signature + the two filter/truncate lines:

```python
def _clean_tags(raw_tags, top_n: int = _TOP_N, min_weight: int = _MIN_WEIGHT) -> list[dict]:
```
```python
        if weight < min_weight:
            continue
```
```python
    result.sort(key=lambda x: -x["weight"])
    return result[:top_n]
```

And `get_artist_tags`:

```python
def get_artist_tags(client, artist: str, top_n: int = _TOP_N, min_weight: int = _MIN_WEIGHT) -> list[dict]:
```
```python
    raw = (data.get("toptags", {}) or {}).get("tag", []) or []
    return _clean_tags(raw, top_n=top_n, min_weight=min_weight)
```

(Leave `get_track_tags`' call to `_clean_tags(raw)` as-is — it keeps the defaults.)

- [ ] **Step 4: Point the gate at the broader view**

In `discover/seeds.py`, add constants near the top (after the module docstring/imports) and use them in `filter_artists_by_genre`:

```python
# Gate tag view: broader than the default top-3/weight-10 so an underground
# artist whose target-genre tag ranks low still passes the genre gate.
_GATE_TAG_TOP_N = 25
_GATE_TAG_MIN_WEIGHT = 1
```
```python
        tag_names = [t["name"] for t in get_artist_tags(
            lastfm_client, name, top_n=_GATE_TAG_TOP_N, min_weight=_GATE_TAG_MIN_WEIGHT)]
```

- [ ] **Step 5: Add a defaults-unchanged regression test**

Add to `tests/lastfm/test_tags.py` (confirms other callers' behavior is preserved):

```python
def test_get_artist_tags_defaults_still_top3_weight10():
    client = SimpleNamespace(call=lambda method, **kw: {"toptags": {"tag": [
        {"name": "a", "count": 50}, {"name": "b", "count": 40}, {"name": "c", "count": 30},
        {"name": "d", "count": 20}, {"name": "lowtag", "count": 5},
    ]}})
    names = [t["name"] for t in get_artist_tags(client, "X")]
    assert names == ["a", "b", "c"]          # top-3 only
    assert "lowtag" not in names             # weight 5 < 10 excluded
```

(If `SimpleNamespace` isn't imported in `tests/lastfm/test_tags.py`, add `from types import SimpleNamespace`.)

- [ ] **Step 6: Run the affected suites**

Run: `.venv/bin/python -m pytest tests/discover/test_seeds.py tests/lastfm/test_tags.py -v`
Expected: PASS (new gate test, new defaults test, and all existing tag/gate tests).

Then full discover + lastfm: `.venv/bin/python -m pytest tests/discover/ tests/lastfm/ -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add lastfm/tags.py discover/seeds.py tests/discover/test_seeds.py tests/lastfm/test_tags.py
git commit -m "fix(discover): widen genre-gate tag view so low-ranked genre tags still match"
```

---

## Self-Review

**Spec coverage:**
- Issue 2 seed-direct → Task 2 (genre seed line + branch). ✓
- Issue 2 genre gate → Task 1 (`filter_artists_by_genre`) + wired in Task 2. ✓
- Issue 2 margin (limit_per_genre=60) → Task 2 Step 4. ✓
- Issue 1 background thread + status store → Task 3. ✓
- Issue 1 status endpoint + frontend polling → Task 3 + Task 4. ✓
- Issue 1 "already running" not an error → Task 3 (200 running) + Task 4 (handled, no throw). ✓
- Issue 3 rebuild from scratch → Task 5. ✓
- Tests for gate / genre-mode / async → Tasks 1, 2, 3. ✓
- Out-of-scope (follow/dedup, track-level gate, file deletion) → respected. ✓

**Placeholder scan:** No TBD/TODO; all code shown. ✓

**Type consistency:** `filter_artists_by_genre(lastfm_client, artists, genres)` signature identical in Tasks 1 and 2. `_mix_last_results` dict + `_run_profile_once` stamping consistent across Task 3 steps. Status string vocabulary (`idle/running/ok/error/busy/disabled`) consistent between backend (Task 3) and frontend (Task 4). ✓

**Known breakage handled:** Existing `test_run_profile.py` genre tests that rely on `expand_similar` are migrated to `mode="manual"` (Task 2 Step 1). Existing synchronous `test_routes.py` run tests are rewritten for async (Task 3 Step 1). ✓
