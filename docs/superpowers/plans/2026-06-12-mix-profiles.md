# Mix Profiles + Genre Mixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generalize Weekly/Daily into N user-editable mix profiles with genre seeds, library/discovery blend, auto-generated genre profiles, one scheduler, and CRUD routes.

**Architecture:** New `discover/profiles.py` (schema, validation, migration, bootstrapper) + `discover/library_pick.py` (library-share selection) + `run_profile()` in `discover/engine.py` reusing the existing seeds→expand→resolve→acquire pipeline. `sWebExt/py_server/server.py` swaps its two scheduler loops for one profile loop and gains `/mixes` routes.

**Tech Stack:** Python 3 / Flask / pytest (run: `./venv/bin/python -m pytest tests/ -q`). Spec: `docs/superpowers/specs/2026-06-12-mix-profiles-genre-mixes-design.md` — read it first; it is binding (data model, validation rules, error handling).

**Conventions:** TDD every task (failing test → implement → green). Commit after every task with `git add <files>` (config.json is gitignored — never `git add -A`). Full suite green before each commit.

---

### Task 1: Profile schema, validation, legacy migration — `discover/profiles.py`

**Files:** Create `discover/profiles.py`, `tests/discover/test_profiles.py`

- [ ] Write failing tests covering: `validate_profile` happy path; each rejection from the spec (bad cadence, ratio out of [0,1], count<1, cap<count, run_hour outside 0–23, missing run_day for weekly, seeds.mode invalid, mode-required field empty — genre→genres, manual→artists, playlist→playlist; duplicate id against existing list). `migrate_config`: legacy weekly+daily keys → exact 2-profile list (values per spec §Built-in profiles); idempotent (presence of `mixes` → returned untouched, no duplicates); daily block absent → only `weekly` profile.
- [ ] Implement:

```python
"""Mix profile schema, validation, and legacy-config migration."""
VALID_CADENCES = {"daily", "weekly"}
VALID_MODES = {"history", "genre", "manual", "playlist"}
VALID_DAYS = {"monday","tuesday","wednesday","thursday","friday","saturday","sunday"}
MODE_REQUIRED = {"genre": "genres", "manual": "artists", "playlist": "playlist"}

def validate_profile(p: dict, existing: list | None = None) -> dict:
    """Return {} if valid, else {field: error}. existing = other profiles (id-uniqueness)."""
    errors = {}
    if not p.get("id") or not str(p["id"]).strip():
        errors["id"] = "required"
    elif existing and any(o["id"] == p["id"] for o in existing):
        errors["id"] = "duplicate id"
    if not p.get("name"):
        errors["name"] = "required"
    sched = p.get("schedule") or {}
    if sched.get("cadence") not in VALID_CADENCES:
        errors["schedule.cadence"] = "must be daily or weekly"
    elif sched["cadence"] == "weekly" and str(sched.get("run_day","")).lower() not in VALID_DAYS:
        errors["schedule.run_day"] = "valid weekday required for weekly cadence"
    try:
        h = int(sched.get("run_hour", -1))
        if not 0 <= h <= 23: raise ValueError
    except (TypeError, ValueError):
        errors["schedule.run_hour"] = "must be 0..23"
    try:
        if not 0.0 <= float(p.get("new_ratio", -1)) <= 1.0: raise ValueError
    except (TypeError, ValueError):
        errors["new_ratio"] = "must be 0..1"
    try:
        count = int(p.get("count", 0)); cap = int(p.get("cap", 0))
        if count < 1: errors["count"] = "must be >= 1"
        elif cap < count: errors["cap"] = "must be >= count"
    except (TypeError, ValueError):
        errors["count"] = "count and cap must be integers"
    seeds = p.get("seeds") or {}
    mode = seeds.get("mode")
    if mode not in VALID_MODES:
        errors["seeds.mode"] = "invalid mode"
    elif mode in MODE_REQUIRED and not seeds.get(MODE_REQUIRED[mode]):
        errors[f"seeds.{MODE_REQUIRED[mode]}"] = f"required for mode={mode}"
    return errors

def migrate_config(cfg: dict) -> list:
    """Synthesize `mixes` from legacy discover.* keys. Returns existing list untouched if present."""
    if isinstance(cfg.get("mixes"), list):
        return cfg["mixes"]
    disc = cfg.get("discover") or {}
    weekly = {
        "id": "weekly", "name": disc.get("playlist_name", "Weekly Mix"),
        "enabled": True, "auto_generated": False,
        "schedule": {"cadence": "weekly", "run_day": disc.get("run_day", "sunday"),
                     "run_hour": int(disc.get("run_hour", 22))},
        "count": int(disc.get("weekly_count", 30)),
        "cap": int(disc.get("playlist_cap", 100)),
        "new_ratio": 1.0,
        "seeds": {"mode": "history", "genres": [], "artists": [],
                  "playlist": disc.get("seed_playlist", "")},
        "quality": {},
    }
    mixes = [weekly]
    daily = disc.get("daily") or {}
    if daily:
        d_count = max(1, int(daily.get("count", 7)))
        mixes.append({
            "id": "daily", "name": daily.get("playlist_name", "Daily Mix"),
            "enabled": bool(daily.get("enabled", True)), "auto_generated": False,
            "schedule": {"cadence": "daily", "run_day": "",
                         "run_hour": int(daily.get("run_hour", 7))},
            "count": d_count,
            "cap": d_count * max(1, int(daily.get("window_days", 7))),
            "new_ratio": 1.0,
            "seeds": {"mode": "history", "genres": [], "artists": [], "playlist": ""},
            "quality": {},
        })
    return mixes
```

- [ ] Run `./venv/bin/python -m pytest tests/discover/test_profiles.py -q` → all pass; full suite green.
- [ ] Commit: `feat(discover): mix profile schema, validation, legacy migration`

### Task 2: Subsonic genre methods

**Files:** Modify `discover/subsonic.py`, test in `tests/discover/test_subsonic_genres.py`

- [ ] Failing tests using the existing fake-`fetch_json` pattern (see `tests/` for how Subsonic is faked): `get_genres()` parses `genres.genre[] -> [{"name","songCount"}]`, tolerates missing key; `get_songs_by_genre("techno", count=N)` parses `songsByGenre.song[]` returning `[{"id","artist","title","path","played"}]` (played may be absent → None).
- [ ] Implement on `Subsonic`:

```python
def get_genres(self) -> list:
    """[{'name': str, 'songCount': int}] sorted by songCount desc."""
    sr = self._call("getGenres.view")
    raw = ((sr.get("genres") or {}).get("genre")) or []
    if isinstance(raw, dict): raw = [raw]
    out = [{"name": g.get("value") or g.get("name") or "",
            "songCount": int(g.get("songCount") or 0)} for g in raw]
    return sorted([g for g in out if g["name"]], key=lambda g: -g["songCount"])

def get_songs_by_genre(self, genre: str, count: int = 200) -> list:
    sr = self._call("getSongsByGenre.view", genre=genre, count=count)
    raw = ((sr.get("songsByGenre") or {}).get("song")) or []
    if isinstance(raw, dict): raw = [raw]
    return [{"id": s.get("id"), "artist": s.get("artist", ""),
             "title": s.get("title", ""), "path": s.get("path", ""),
             "played": s.get("played")} for s in raw]
```

Note: Subsonic genre field in getGenres responses is `value` in some servers, `name` in Navidrome docs — handle both (as above).
- [ ] Tests green; full suite green. Commit: `feat(discover): Subsonic getGenres/getSongsByGenre`

### Task 3: Genre seed artists from Last.fm tags

**Files:** Modify `discover/seeds.py`, test in `tests/discover/test_genre_seeds.py`

- [ ] Failing tests: mocks a client whose `.call("tag.gettopartists", tag=..., limit=...)` returns `{"topartists":{"artist":[{"name":"X"},...]}}`; multi-genre merge dedupes by lowercase name preserving first-seen order; a tag raising `LastFMError` contributes nothing (no raise).
- [ ] Implement in `discover/seeds.py`:

```python
def genre_seed_artists(lastfm_client, genres: list, limit_per_genre: int = 30) -> list:
    """Top Last.fm artists for each tag, merged + deduped: [{'id': '-1', 'name': str}]."""
    seen, out = set(), []
    for tag in genres or []:
        try:
            resp = lastfm_client.call("tag.gettopartists", tag=tag, limit=limit_per_genre)
        except Exception:
            logger.warning("seeds: tag.gettopartists failed for %r", tag, exc_info=True)
            continue
        artists = (resp.get("topartists") or {}).get("artist") or []
        if isinstance(artists, dict): artists = [artists]
        for a in artists:
            name = (a.get("name") or "").strip()
            if name and name.lower() not in seen:
                seen.add(name.lower()); out.append({"id": "-1", "name": name})
    return out
```

- [ ] Green; full suite green. Commit: `feat(discover): genre tag seed artists`

### Task 4: Library-share track selection — `discover/library_pick.py`

**Files:** Create `discover/library_pick.py`, `tests/discover/test_library_pick.py`

- [ ] Failing tests: genre mode pulls from `get_songs_by_genre` for each profile genre; unplayed songs come before recently-played (sort key: `played` None first, then ascending ISO date); basenames already in the m3u exclusion set are skipped; result length ≤ requested; non-genre modes use seed-artist search via `subsonic.song_exists`-style search — for non-genre modes use `search_songs(artist, count)`-shaped fallback: pass a callable; ties randomized via `random.shuffle` seeded by caller (don't assert order among equal keys).
- [ ] Implement:

```python
"""Pick owned tracks for a mix profile's library share."""
import os, random, logging
logger = logging.getLogger(__name__)

def select_library_tracks(subsonic, profile: dict, exclude_basenames: set,
                          count: int) -> list:
    """Return up to `count` song dicts (with 'path') from the library.

    genre mode: union of get_songs_by_genre per genre.
    other modes: songs by the profile's seed artists (search3 via subsonic.search_songs
    if available, else empty). Prefers never/least-recently played. Excludes basenames
    already in the playlist. Never touches DiscoverState.
    """
    if count <= 0:
        return []
    seeds_cfg = profile.get("seeds") or {}
    pool, seen_ids = [], set()
    if seeds_cfg.get("mode") == "genre":
        for g in seeds_cfg.get("genres") or []:
            try:
                songs = subsonic.get_songs_by_genre(g, count=200)
            except Exception:
                logger.warning("library_pick: getSongsByGenre failed for %r", g, exc_info=True)
                continue
            for s in songs:
                if s.get("id") and s["id"] not in seen_ids:
                    seen_ids.add(s["id"]); pool.append(s)
    else:
        search = getattr(subsonic, "search_songs", None)
        if callable(search):
            for a in (seeds_cfg.get("artists") or [])[:20]:
                try:
                    for s in search(a, count=20):
                        if s.get("id") and s["id"] not in seen_ids:
                            seen_ids.add(s["id"]); pool.append(s)
                except Exception:
                    continue
    pool = [s for s in pool
            if os.path.basename(s.get("path") or "") not in exclude_basenames
            and s.get("path")]
    random.shuffle(pool)                                  # tie-break
    pool.sort(key=lambda s: (s.get("played") is not None, s.get("played") or ""))
    return pool[:count]
```

If `Subsonic` has no `search_songs`, add one in this task (search3.view, songCount=count, parse `searchResult3.song[]` to the same dict shape as `get_songs_by_genre`) with a test.
- [ ] Green; full suite green. Commit: `feat(discover): library-share track selection`

### Task 5: `run_profile()` engine + wrappers

**Files:** Modify `discover/engine.py`, create `tests/discover/test_run_profile.py`

- [ ] Failing tests (reuse the fake-deps builder pattern from `tests/discover/test_daily.py`): ratio 1.0 → all acquired, no library calls; ratio 0.0 → no acquisition, library only; ratio 0.3/count 10 → 3 new + 7 library; new-share shortfall backfilled by library (and vice versa), total ≤ count; history mode + no client → `{"status":"skipped"}`; genre mode + no scrobble history runs anyway (no `lastfm_is_ready` call — assert via monkeypatch counter); acquisitions recorded in state, library picks NOT; m3u written via `write_weekly_mix(name=profile["name"], cap=profile["cap"])`; `state.save(stamp_last_run=False)`.
- [ ] Implement `run_profile(deps, cfg, profile)`:

```python
def run_profile(deps, cfg, profile):
    """Run one mix profile: blend of newly-acquired + owned tracks. Spec §Engine."""
    disc = cfg.get("discover") or {}
    quality = {**disc, **(profile.get("quality") or {})}
    count = max(1, int(profile["count"]))
    cap = max(count, int(profile["cap"]))
    new_ratio = float(profile.get("new_ratio", 1.0))
    new_count = round(count * new_ratio)
    lib_count = count - new_count
    seeds_cfg = profile.get("seeds") or {}
    mode = seeds_cfg.get("mode", "history")
    lastfm_client = getattr(deps, "lastfm_client", None)
    lastfm_username = cfg.get("lastfm_username", "")

    acquired_paths = []
    if new_count > 0:
        if mode == "history":
            if not lastfm_client or not lastfm_is_ready(lastfm_client, lastfm_username, cfg):
                if lib_count == 0:
                    return {"profile": profile["id"], "status": "skipped",
                            "reason": "lastfm not ready"}
                new_count, lib_count = 0, count
            else:
                seeds = collect_seeds(deps.subsonic, limit=int(quality.get("seed_artist_count", 20)),
                                      lastfm_client=lastfm_client, lastfm_username=lastfm_username,
                                      lastfm_period=quality.get("lastfm_period", "1month"),
                                      lastfm_periods=quality.get("lastfm_periods") or None,
                                      seed_playlist=seeds_cfg.get("playlist", ""))
        if mode == "genre":
            seeds = genre_seed_artists(lastfm_client, seeds_cfg.get("genres") or []) if lastfm_client else []
        elif mode == "manual":
            seeds = [{"id": "-1", "name": a} for a in seeds_cfg.get("artists") or []]
        elif mode == "playlist":
            seeds = collect_seeds(deps.subsonic, limit=int(quality.get("seed_artist_count", 20)),
                                  lastfm_client=lastfm_client, lastfm_username=lastfm_username,
                                  seed_playlist=seeds_cfg.get("playlist", ""))
        if new_count > 0 and seeds:
            artists = expand_similar(deps.subsonic, seeds, per_seed=20, lastfm_client=lastfm_client)
            if lastfm_client is not None:
                artists = sorted(artists, key=lambda a: -a.get("score", 0))[:60]
                artists = enrich_artist_info(lastfm_client, artists,
                                             min_listeners=int(quality.get("min_artist_listeners", 5000)))
            candidates = resolve_tracks(deps.search_fn, artists, per_artist=1)
            fresh = filter_fresh(deps.subsonic.song_exists, deps.state, candidates)
            for c in fresh:
                if len(acquired_paths) >= new_count:
                    break
                paths = acquire(deps.download_fn, c)
                if not paths:
                    continue
                acquired_paths.extend(paths[: new_count - len(acquired_paths)])
                deps.state.add(track_key(c["artist"], c["title"]))

    # library share + backfill of any new-share shortfall
    lib_paths = []
    lib_needed = count - len(acquired_paths) if lib_count > 0 or len(acquired_paths) < new_count else 0
    lib_needed = min(lib_needed, count - len(acquired_paths))
    if lib_needed > 0:
        existing = _existing_playlist_basenames(deps.song_dir, profile["name"])
        from discover.library_pick import select_library_tracks
        picks = select_library_tracks(deps.subsonic, profile, existing, lib_needed)
        lib_paths = [s["path"] for s in picks]

    m3u = None
    if acquired_paths or lib_paths:
        m3u = write_weekly_mix(deps.song_dir, acquired_paths + lib_paths,
                               name=profile["name"], cap=cap)
        try:
            deps.subsonic.start_scan()
        except Exception:
            logger.exception("discover: scan trigger failed")
    deps.state.save(stamp_last_run=False)
    return {"profile": profile["id"], "acquired": len(acquired_paths),
            "library_added": len(lib_paths), "m3u": m3u}
```

Add helper `_existing_playlist_basenames(song_dir, name)` (read the m3u the same way `write_weekly_mix` does — extract its read block into a shared private function in `discover/assemble.py` named `read_playlist_basenames(song_dir, name) -> list`, import it; DRY, do not duplicate the sanitize/read logic).
- [ ] Convert `run_daily(deps, cfg)` body to: build the `daily` profile dict from config (same field mapping as `migrate_config`) and `return run_profile(deps, cfg, profile)` — adjust its return shape only if tests demand (`test_daily.py` asserts on `acquired`/`m3u`/skip status, which `run_profile` provides; keep `{"status":"skipped","reason":"lastfm not ready"}` contract). Keep `run_mix`/`run_weekly` untouched (legacy path still used until Task 7 swaps the scheduler; spec keeps their signatures).
- [ ] Green incl. existing `test_daily.py`; full suite green. Commit: `feat(discover): run_profile engine with blend + backfill`

### Task 6: Genre-profile bootstrapper

**Files:** Modify `discover/profiles.py`, extend `tests/discover/test_profiles.py`

- [ ] Failing tests: top-N by songCount (fake subsonic.get_genres); skips genres already covered by existing profiles' `seeds.genres` (case-insensitive); staggers run_day mon→thu cycling; defaults per spec (weekly, hour 7, count 15, cap 60, ratio 0.3, auto_generated True, id slugified lowercase); skips empty/junk genre names; never returns a profile whose id collides with existing.
- [ ] Implement:

```python
import re
_STAGGER = ["monday", "tuesday", "wednesday", "thursday"]

def suggest_genre_profiles(subsonic, existing_mixes: list, top_n: int = 4) -> list:
    covered = {g.lower() for m in existing_mixes
               for g in (m.get("seeds") or {}).get("genres") or []}
    existing_ids = {m["id"] for m in existing_mixes}
    out = []
    for g in subsonic.get_genres():
        if len(out) >= top_n:
            break
        name = g["name"].strip()
        tag = name.lower()
        slug = re.sub(r"[^a-z0-9]+", "-", tag).strip("-")
        if not slug or tag in covered or f"genre-{slug}" in existing_ids:
            continue
        out.append({
            "id": f"genre-{slug}", "name": f"{name.title()} Mix",
            "enabled": True, "auto_generated": True,
            "schedule": {"cadence": "weekly",
                         "run_day": _STAGGER[len(out) % len(_STAGGER)], "run_hour": 7},
            "count": 15, "cap": 60, "new_ratio": 0.3,
            "seeds": {"mode": "genre", "genres": [tag], "artists": [], "playlist": ""},
            "quality": {},
        })
    return out
```

- [ ] Green; full suite green. Commit: `feat(discover): auto-suggest genre profiles`

### Task 7: Profile scheduler loop in server

**Files:** Modify `sWebExt/py_server/server.py`, create `tests/server/test_mix_scheduler.py`

- [ ] Failing tests for a new pure helper `_profile_next_run(profile, now)` → datetime (daily: today at run_hour or +1d; weekly: next run_day at run_hour; mirrors `_seconds_until_next_run` math but per-profile and testable with injected `now`). Test both cadences + clamping of bad run_hour (reuse `max(0, min(23, ...))`).
- [ ] Implement scheduler (replaces `_discover_weekly_loop` AND `_discover_daily_loop`; delete both and their thread starts):

```python
_mix_wake = threading.Event()

def _load_mixes() -> list:
    cfg = _get_config()
    from discover.profiles import migrate_config
    mixes = migrate_config(cfg)
    if "mixes" not in cfg:                       # persist migration once
        cfg["mixes"] = mixes
        _atomic_write_config(cfg)                # extract existing .tmp+os.replace into helper if not present
    return mixes

def _run_profile_once(profile) -> dict:
    if not _discover_running.acquire(blocking=False):
        return {"status": "busy", "reason": "another discover run in progress"}
    try:
        deps = _build_discover_deps()
        if deps is None:
            return {"status": "disabled", "reason": "navidrome creds missing"}
        from discover.engine import run_profile
        result = run_profile(deps, _get_config(), profile)
        logger.info("[MIXES] %s run complete: %s", profile["id"], result)
        return result
    except Exception as e:
        logger.exception("[MIXES] %s run failed", profile.get("id"))
        return {"status": "error", "error": str(e)}
    finally:
        _discover_running.release()

def _mix_scheduler_loop():
    while True:
        try:
            mixes = [m for m in _load_mixes() if m.get("enabled")]
            now = datetime.datetime.now()
            next_runs = {m["id"]: _profile_next_run(m, now) for m in mixes}
            _persist_next_runs({k: v.isoformat() for k, v in next_runs.items()})  # into discover_state.json, preserve other keys
            if not next_runs:
                _mix_wake.wait(3600); _mix_wake.clear(); continue
            soonest = min(next_runs.values())
            _mix_wake.wait(max(1.0, (soonest - now).total_seconds()))
            _mix_wake.clear()
            now = datetime.datetime.now()
            for m in [m for m in _load_mixes() if m.get("enabled")]:
                if _profile_next_run(m, now - datetime.timedelta(minutes=1)) <= now:
                    _run_profile_once(m)         # sequential; failures logged inside
        except Exception:
            logger.exception("[MIXES] scheduler iteration failed; retrying in 3600s")
            time.sleep(3600)
```

Keep the weekly initial-run-on-empty-state check: move the existing block from `_discover_weekly_loop` into `_mix_scheduler_loop` startup (before the while), running the `weekly` profile only. `POST`/`PUT`-style mutations (Task 8) call `_mix_wake.set()`.
- [ ] Tests green (helper-level; loop itself is not unit-tested — same as existing loops); full suite green (old loop tests in `tests/discover/test_scheduler.py` may reference removed functions — update them to target `_profile_next_run`). Commit: `feat(server): unified mix profile scheduler`

### Task 8: /mixes routes

**Files:** Modify `sWebExt/py_server/server.py`, extend `tests/server/test_routes.py`

- [ ] Failing tests: GET returns migrated mixes + next_runs; POST create (valid → 201, appears in config), POST update existing id (200, `auto_generated` forced False), POST invalid → 400 with `errors` dict from `validate_profile`; DELETE removes (200) / unknown id 404; `POST /mixes/<id>/run` happy (monkeypatch `_run_profile_once`) + busy 409 passthrough; `POST /mixes/suggest` appends only new profiles and persists; legacy aliases `POST /discover/run` → weekly profile, `POST /discover/run_daily` → daily profile (rewire the existing routes' bodies to `_run_profile_once(<profile from _load_mixes()>)`, keep their busy-409 contract; daily alias returns 404 if no `daily` profile exists).
- [ ] Implement routes (pattern: load config → mutate `mixes` list → `_atomic_write_config` → `_mix_wake.set()`):

```python
@app.route("/mixes", methods=["GET"])
def mixes_get():
    mixes = _load_mixes()
    state = {}
    try:
        with open(os.path.join(_PROJECT_ROOT, "discover_state.json"), encoding="utf-8") as f:
            state = json.load(f)
    except Exception:
        pass
    return jsonify({"mixes": mixes, "next_runs": state.get("next_runs", {})})

@app.route("/mixes", methods=["POST"])
def mixes_post():
    from discover.profiles import validate_profile
    body = request.get_json(force=True, silent=True) or {}
    cfg = _get_config(); mixes = _load_mixes()
    idx = next((i for i, m in enumerate(mixes) if m["id"] == body.get("id")), None)
    others = [m for i, m in enumerate(mixes) if i != idx]
    errors = validate_profile(body, existing=others if idx is None else others)
    if errors:
        return jsonify({"status": "error", "errors": errors}), 400
    body["auto_generated"] = False if idx is not None else bool(body.get("auto_generated"))
    if idx is None: mixes.append(body); code = 201
    else: mixes[idx] = body; code = 200
    cfg["mixes"] = mixes; _atomic_write_config(cfg); _mix_wake.set()
    return jsonify({"status": "ok", "mix": body}), code
```

(DELETE, `/run`, `/suggest` follow the same shape; `/suggest` builds `Subsonic` from config like `_build_discover_deps` does and calls `suggest_genre_profiles`.)
- [ ] Green; full suite green. Commit: `feat(server): /mixes CRUD + run + suggest routes`

### Task 9: Settings schema cleanup + example config

**Files:** Modify `sWebExt/py_server/server.py` (SETTINGS_SCHEMA), `config.example.json`

- [ ] Remove `discover.daily.*` entries from `SETTINGS_SCHEMA` (spec: superseded by Mixes UI); update the route test that counts/lists schema entries. Add `"mixes": []`-free note: do NOT add mixes to example config (migration synthesizes them).
- [ ] Full suite green. Commit: `chore(settings): drop daily rows superseded by mixes UI`

### Task 10: Final verification

- [ ] `./venv/bin/python -m pytest tests/ -q` → all green, count reported.
- [ ] Manual smoke: `./venv/bin/python -c "from discover.profiles import migrate_config; import json; print(json.dumps(migrate_config(json.load(open('config.json'))), indent=2))"` → two profiles printed.
- [ ] `git log --oneline` shows one commit per task. Report files changed + test count.

## Self-review notes (already applied)

- `run_weekly`/`run_mix` intentionally kept (Starter Mix bootstrap path still routes through `run_mix`; profiles don't cover bootstrap — spec keeps that behavior via the weekly initial-run).
- `read_playlist_basenames` extraction keeps m3u read/sanitize logic in one place.
- Migration persists on first `_load_mixes()` so the scheduler and routes share one source of truth.
