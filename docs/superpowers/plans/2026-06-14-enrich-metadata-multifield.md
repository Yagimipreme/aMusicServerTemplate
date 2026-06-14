# Enrich Metadata — Status Fix + Multi-Field Broadening — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the broken Enrich-metadata UI status contract and broaden enrichment from genre-only to also fill year, album, album-artist, MusicBrainz IDs, and cover art.

**Architecture:** One combined per-file pass. `library/enrich.py:run()` is refactored to apply field-fillers per file: genre from Last.fm (existing) and year/album/album-artist/MBIDs/cover-art from a single MusicBrainz recording resolution (`library/mbmeta.py`) plus Cover Art Archive (`library/coverart.py`). The Flask layer publishes a live `running` state with `files_done`/`files_total` and a UI-aligned terminal result.

**Tech Stack:** Python 3.13, Flask, eyed3 (ID3 tags), requests, MusicBrainz JSON API, Last.fm API, Cover Art Archive. Tests: pytest with `unittest.mock`.

**Spec:** `docs/superpowers/specs/2026-06-14-enrich-metadata-multifield-design.md`

---

## File Structure

- `follow/musicbrainz.py` — **modify**: add `search_recording(artist, title, limit)`.
- `library/mbmeta.py` — **create**: `resolve()` + `_pick_release()` + helpers. Resolves a track to canonical MB metadata.
- `library/coverart.py` — **create**: `fetch_front(release_mbid, size)` against Cover Art Archive.
- `library/enrich.py` — **modify**: refactor `run()` to multi-field + progress callback. Adds MBID frame helpers.
- `sWebExt/py_server/server.py` — **modify**: POST running-state + skip-guard; `_run_enrich_once` config/clients/progress; `_enrich_fields()` helper.
- `config.example.json` — **modify**: new `enrich` schema.
- `web/static/app.js` — **verify only** (no code change expected).
- Tests: `tests/follow/test_musicbrainz.py` (extend), `tests/library/test_mbmeta.py` (create), `tests/library/test_coverart.py` (create), `tests/library/test_enrich.py` (rewrite), `tests/server/test_routes.py` (extend).

**Run all tests with:** `python -m pytest -q` from the repo root (venv active).

---

## Task 1: MusicBrainz `search_recording`

**Files:**
- Modify: `follow/musicbrainz.py` (add method to `MusicBrainzClient`)
- Test: `tests/follow/test_musicbrainz.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/follow/test_musicbrainz.py`:

```python
def test_search_recording_parses():
    payload = {"recordings": [
        {"id": "rec-1", "score": 100, "title": "Teardrop",
         "artist-credit": [{"name": "Massive Attack",
                            "artist": {"id": "art-1", "name": "Massive Attack"}}],
         "releases": [
            {"id": "rel-1", "title": "Mezzanine", "date": "1998-04-20",
             "status": "Official",
             "release-group": {"id": "rg-1", "primary-type": "Album"}},
         ]},
    ]}
    client = mb.MusicBrainzClient(session=FakeSession([payload]), min_interval=0)
    got = client.search_recording("Massive Attack", "Teardrop")
    assert got[0]["mbid"] == "rec-1"
    assert got[0]["score"] == 100
    assert got[0]["artist_mbid"] == "art-1"
    assert got[0]["artist_name"] == "Massive Attack"
    assert got[0]["releases"][0] == {
        "mbid": "rel-1", "title": "Mezzanine", "date": "1998-04-20",
        "rg_mbid": "rg-1", "primary_type": "Album", "status": "Official"}


def test_search_recording_empty_when_no_recordings():
    client = mb.MusicBrainzClient(session=FakeSession([{"recordings": []}]), min_interval=0)
    assert client.search_recording("X", "Y") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/follow/test_musicbrainz.py::test_search_recording_parses -v`
Expected: FAIL — `AttributeError: 'MusicBrainzClient' object has no attribute 'search_recording'`

- [ ] **Step 3: Write the implementation**

Add this method to the `MusicBrainzClient` class in `follow/musicbrainz.py` (after `get_release_tracks`):

```python
    def search_recording(self, artist: str, title: str, limit: int = 5) -> list:
        data = self._get("recording", {
            "query": f'artist:"{artist}" AND recording:"{title}"',
            "limit": limit,
        })
        out = []
        for r in data.get("recordings", []) or []:
            credits = r.get("artist-credit", []) or []
            artist_mbid = ""
            artist_name = ""
            if credits:
                a = credits[0].get("artist", {}) or {}
                artist_mbid = a.get("id", "") or ""
                artist_name = credits[0].get("name", "") or a.get("name", "") or ""
            releases = []
            for rel in r.get("releases", []) or []:
                rg = rel.get("release-group", {}) or {}
                releases.append({
                    "mbid": rel.get("id", "") or "",
                    "title": rel.get("title", "") or "",
                    "date": rel.get("date", "") or "",
                    "rg_mbid": rg.get("id", "") or "",
                    "primary_type": rg.get("primary-type", "") or "",
                    "status": rel.get("status", "") or "",
                })
            out.append({
                "mbid": r.get("id", "") or "",
                "score": r.get("score", 0) or 0,
                "title": r.get("title", "") or "",
                "artist_mbid": artist_mbid,
                "artist_name": artist_name,
                "releases": releases,
            })
        return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/follow/test_musicbrainz.py -v`
Expected: PASS (all, including the two new tests)

- [ ] **Step 5: Commit**

```bash
git add follow/musicbrainz.py tests/follow/test_musicbrainz.py
git commit -m "feat(musicbrainz): add search_recording for metadata enrichment"
```

---

## Task 2: `library/mbmeta.py` — resolve track to canonical metadata

**Files:**
- Create: `library/mbmeta.py`
- Test: `tests/library/test_mbmeta.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/library/test_mbmeta.py`:

```python
"""Tests for library/mbmeta.py — resolve a track to canonical MB metadata."""
from unittest.mock import MagicMock

from library import mbmeta
from follow.musicbrainz import MBError


def _client(recordings):
    c = MagicMock()
    c.search_recording.return_value = recordings
    return c


def _recording(score=100, releases=None):
    return {
        "mbid": "rec-1", "score": score, "title": "Teardrop",
        "artist_mbid": "art-1", "artist_name": "Massive Attack",
        "releases": releases if releases is not None else [
            {"mbid": "rel-1", "title": "Mezzanine", "date": "1998-04-20",
             "rg_mbid": "rg-1", "primary_type": "Album", "status": "Official"},
        ],
    }


def test_resolve_returns_canonical_fields():
    meta = mbmeta.resolve(_client([_recording()]), "Massive Attack", "Teardrop", 90)
    assert meta == {
        "score": 100, "recording_mbid": "rec-1", "artist_mbid": "art-1",
        "album": "Mezzanine", "album_artist": "Massive Attack", "year": "1998",
        "release_mbid": "rel-1", "rg_mbid": "rg-1",
    }


def test_resolve_returns_none_below_score():
    assert mbmeta.resolve(_client([_recording(score=50)]), "A", "B", 90) is None


def test_resolve_returns_none_when_no_recordings():
    assert mbmeta.resolve(_client([]), "A", "B", 90) is None


def test_resolve_returns_none_on_mb_error():
    c = MagicMock()
    c.search_recording.side_effect = MBError("boom")
    assert mbmeta.resolve(c, "A", "B", 90) is None


def test_pick_release_prefers_official_album_then_earliest():
    releases = [
        {"mbid": "comp", "title": "Best Of", "date": "2010-01-01",
         "rg_mbid": "rg-c", "primary_type": "Album", "status": "Bootleg"},
        {"mbid": "early", "title": "Mezzanine", "date": "1998-04-20",
         "rg_mbid": "rg-e", "primary_type": "Album", "status": "Official"},
        {"mbid": "late", "title": "Reissue", "date": "2008-01-01",
         "rg_mbid": "rg-l", "primary_type": "Album", "status": "Official"},
    ]
    meta = mbmeta.resolve(_client([_recording(releases=releases)]), "A", "B", 90)
    assert meta["release_mbid"] == "early"
    assert meta["year"] == "1998"


def test_resolve_handles_recording_with_no_releases():
    meta = mbmeta.resolve(_client([_recording(releases=[])]), "A", "B", 90)
    assert meta["album"] == ""
    assert meta["year"] == ""
    assert meta["release_mbid"] == ""
    assert meta["recording_mbid"] == "rec-1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/library/test_mbmeta.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'library.mbmeta'`

- [ ] **Step 3: Write the implementation**

Create `library/mbmeta.py`:

```python
"""Resolve a track to canonical MusicBrainz metadata for tag enrichment."""
import logging

from follow.musicbrainz import MBError

logger = logging.getLogger(__name__)


def _pick_release(releases):
    """Choose the canonical release: prefer official albums, then earliest date."""
    if not releases:
        return None

    def date_key(rel):
        # Empty dates sort last.
        return rel.get("date") or "9999-99-99"

    official_albums = [
        r for r in releases
        if r.get("primary_type") == "Album" and r.get("status") == "Official"
    ]
    pool = official_albums or releases
    return sorted(pool, key=date_key)[0]


def _year_from_date(date_str):
    """Extract a 4-digit year from a MusicBrainz date like '1998-04-20'."""
    if date_str and len(date_str) >= 4 and date_str[:4].isdigit():
        return date_str[:4]
    return ""


def resolve(mb_client, artist, title, min_score):
    """Return canonical metadata for the best-matching recording, or None.

    Returns None if no recording matches or the top match scores below min_score.
    """
    try:
        recordings = mb_client.search_recording(artist, title)
    except MBError:
        logger.warning("mbmeta: search_recording failed for %s / %s",
                       artist, title, exc_info=True)
        return None

    if not recordings:
        return None

    rec = recordings[0]
    try:
        score = int(rec.get("score", 0))
    except (ValueError, TypeError):
        score = 0
    if score < min_score:
        return None

    release = _pick_release(rec.get("releases", [])) or {}
    return {
        "score": score,
        "recording_mbid": rec.get("mbid", ""),
        "artist_mbid": rec.get("artist_mbid", ""),
        "album": release.get("title", ""),
        "album_artist": rec.get("artist_name", ""),
        "year": _year_from_date(release.get("date", "")),
        "release_mbid": release.get("mbid", ""),
        "rg_mbid": release.get("rg_mbid", ""),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/library/test_mbmeta.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add library/mbmeta.py tests/library/test_mbmeta.py
git commit -m "feat(library): mbmeta resolves tracks to canonical MusicBrainz metadata"
```

---

## Task 3: `library/coverart.py` — fetch front cover from Cover Art Archive

**Files:**
- Create: `library/coverart.py`
- Test: `tests/library/test_coverart.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/library/test_coverart.py`:

```python
"""Tests for library/coverart.py — Cover Art Archive front-image fetch."""
from unittest.mock import MagicMock

import requests

from library import coverart


def _resp(status=200, content=b"JPEGBYTES", content_type="image/jpeg"):
    r = MagicMock()
    r.status_code = status
    r.content = content
    r.headers = {"Content-Type": content_type}
    return r


def test_fetch_front_returns_bytes_and_mime():
    sess = MagicMock()
    sess.get.return_value = _resp()
    got = coverart.fetch_front("rel-1", size="500", session=sess)
    assert got == (b"JPEGBYTES", "image/jpeg")
    url = sess.get.call_args[0][0]
    assert url == "https://coverartarchive.org/release/rel-1/front-500"


def test_fetch_front_returns_none_on_404():
    sess = MagicMock()
    sess.get.return_value = _resp(status=404)
    assert coverart.fetch_front("rel-1", session=sess) is None


def test_fetch_front_returns_none_on_network_error():
    sess = MagicMock()
    sess.get.side_effect = requests.exceptions.Timeout("slow")
    assert coverart.fetch_front("rel-1", session=sess) is None


def test_fetch_front_returns_none_for_empty_mbid():
    assert coverart.fetch_front("", session=MagicMock()) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/library/test_coverart.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'library.coverart'`

- [ ] **Step 3: Write the implementation**

Create `library/coverart.py`:

```python
"""Fetch front cover art from the Cover Art Archive."""
import logging

import requests

logger = logging.getLogger(__name__)

_BASE = "https://coverartarchive.org"
_TIMEOUT = 10
_USER_AGENT = "aMusicServer/1.0 (https://github.com/Yagimipreme/aMusicServer)"


def fetch_front(release_mbid, size="500", session=None):
    """Return (image_bytes, mime_type) for a release front cover, or None.

    size is the Cover Art Archive thumbnail suffix ("250", "500", "1200").
    Returns None for an empty mbid, a 404 (no art), or any network error.
    """
    if not release_mbid:
        return None
    sess = session or requests
    url = f"{_BASE}/release/{release_mbid}/front-{size}"
    try:
        resp = sess.get(url, headers={"User-Agent": _USER_AGENT},
                        timeout=_TIMEOUT, allow_redirects=True)
        if resp.status_code != 200:
            return None
        mime = resp.headers.get("Content-Type", "image/jpeg")
        return resp.content, mime
    except requests.exceptions.RequestException:
        logger.warning("coverart: fetch failed for %s", release_mbid, exc_info=True)
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/library/test_coverart.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add library/coverart.py tests/library/test_coverart.py
git commit -m "feat(library): coverart fetches front images from Cover Art Archive"
```

---

## Task 4: Refactor `library/enrich.py` to multi-field + progress

This is the core change. The new `run()` signature, MBID frame helpers, and a full rewrite of `tests/library/test_enrich.py` (the old genre-only signature is removed).

**Files:**
- Modify: `library/enrich.py` (rewrite `run()`, add helpers and imports)
- Test: `tests/library/test_enrich.py` (rewrite)

- [ ] **Step 1: Rewrite the test file**

Replace the entire contents of `tests/library/test_enrich.py` with:

```python
"""Tests for library/enrich.py — multi-field tag enrichment."""
from unittest.mock import MagicMock, patch

import eyed3.core
import eyed3.id3

from library.enrich import run


# ── Fakes ──────────────────────────────────────────────────────────────────────

class FakeFrames:
    """Stand-in for eyed3 user_text_frames / unique_file_ids accessors."""
    def __init__(self):
        self._d = {}

    def get(self, key):
        return self._d.get(key)

    def set(self, text, description=None):
        # user_text_frames.set(text, description); unique_file_ids.set(data, owner)
        self._d[description if description is not None else text] = text


class FakeImages:
    def __init__(self, has=False):
        self._has = has
        self.set_calls = []

    def __iter__(self):
        return iter([object()] if self._has else [])

    def set(self, type_, data, mime):
        self.set_calls.append((type_, data, mime))


class FakeTag:
    def __init__(self, genre=None, year=None, album="", album_artist="",
                 has_mbids=False, has_cover=False, artist="Massive Attack",
                 title="Teardrop"):
        self.artist = artist
        self.title = title
        self.album = album
        self.album_artist = album_artist
        self.recording_date = None
        self._year = year
        if genre is None:
            self.genre = None
        else:
            g = MagicMock()
            g.name = genre
            g.id = None
            self.genre = g
        self.user_text_frames = FakeFrames()
        self.unique_file_ids = FakeFrames()
        if has_mbids:
            self.user_text_frames.set("existing", "MusicBrainz Artist Id")
        self.images = FakeImages(has=has_cover)
        self.saved = False

    def getBestDate(self):
        return self._year

    def initTag(self):
        pass

    def save(self):
        self.saved = True


def _audio(tag):
    a = MagicMock()
    a.tag = tag
    return a


def _records(*paths):
    return [{"path": p, "artist": "Massive Attack", "title": "Teardrop"}
            for p in paths]


def _scan(records):
    return patch("library.enrich.scan", return_value=records)


def _lastfm(track_tags=None, artist_tags=None):
    from lastfm.client import LastFMNotFound
    client = MagicMock()

    def fake_call(method, **params):
        if method == "track.getTopTags":
            if track_tags is None:
                raise LastFMNotFound(6, "not found")
            return {"toptags": {"tag": track_tags}}
        if method == "artist.getTopTags":
            return {"toptags": {"tag": artist_tags or []}}
        return {}

    client.call.side_effect = fake_call
    return client


def _mb_meta(**overrides):
    meta = {
        "score": 100, "recording_mbid": "rec-1", "artist_mbid": "art-1",
        "album": "Mezzanine", "album_artist": "Massive Attack", "year": "1998",
        "release_mbid": "rel-1", "rg_mbid": "rg-1",
    }
    meta.update(overrides)
    return meta


def _all_fields():
    return {f: {"enabled": True, "only_missing": True}
            for f in ("genre", "year", "album", "album_artist", "mbids", "cover_art")}


# ── Genre (Last.fm) ────────────────────────────────────────────────────────────

def test_genre_written_when_missing():
    tag = FakeTag(genre=None)
    fields = {"genre": {"enabled": True, "only_missing": True}}
    with _scan(_records("/a.mp3")), \
         patch("library.enrich.eyed3.load", return_value=_audio(tag)):
        result = run("/lib", lastfm_client=_lastfm(track_tags=[{"name": "trip-hop", "count": "90"}]),
                     fields=fields)
    assert result["per_field"]["genre"] == 1
    assert result["enriched"] == 1
    assert tag.saved is True


def test_genre_skipped_when_present_and_only_missing():
    tag = FakeTag(genre="Electronic")
    fields = {"genre": {"enabled": True, "only_missing": True}}
    with _scan(_records("/a.mp3")), \
         patch("library.enrich.eyed3.load", return_value=_audio(tag)):
        result = run("/lib", lastfm_client=_lastfm(track_tags=[{"name": "trip-hop", "count": "90"}]),
                     fields=fields)
    assert result["per_field"]["genre"] == 0
    assert result["skipped"] == 1
    assert tag.saved is False


# ── MusicBrainz fields ─────────────────────────────────────────────────────────

def test_mb_fields_written_when_missing():
    tag = FakeTag(genre="Electronic")  # genre present so only MB fields fire
    fields = _all_fields()
    fields["genre"]["enabled"] = False
    with _scan(_records("/a.mp3")), \
         patch("library.enrich.eyed3.load", return_value=_audio(tag)), \
         patch("library.mbmeta.resolve", return_value=_mb_meta()), \
         patch("library.coverart.fetch_front", return_value=(b"IMG", "image/jpeg")):
        result = run("/lib", mb_client=MagicMock(), fields=fields, min_musicbrainz_score=90)
    assert tag.album == "Mezzanine"
    assert tag.album_artist == "Massive Attack"
    assert tag.recording_date == eyed3.core.Date(1998)
    assert tag.user_text_frames.get("MusicBrainz Album Id") == "rel-1"
    assert tag.unique_file_ids.get("http://musicbrainz.org") == b"rec-1"
    assert len(tag.images.set_calls) == 1
    assert result["per_field"]["album"] == 1
    assert result["per_field"]["year"] == 1
    assert result["per_field"]["album_artist"] == 1
    assert result["per_field"]["mbids"] == 1
    assert result["per_field"]["cover_art"] == 1
    assert result["enriched"] == 1


def test_mb_fields_skipped_when_present():
    tag = FakeTag(genre="Electronic", year=eyed3.core.Date(2001),
                  album="Existing", album_artist="Existing AA",
                  has_mbids=True, has_cover=True)
    fields = _all_fields()
    fields["genre"]["enabled"] = False
    with _scan(_records("/a.mp3")), \
         patch("library.enrich.eyed3.load", return_value=_audio(tag)), \
         patch("library.mbmeta.resolve", return_value=_mb_meta()) as resolve_mock, \
         patch("library.coverart.fetch_front", return_value=(b"IMG", "image/jpeg")):
        result = run("/lib", mb_client=MagicMock(), fields=fields)
    # All MB fields already present → resolve never even called (no needed fields)
    resolve_mock.assert_not_called()
    assert tag.album == "Existing"
    assert result["skipped"] == 1
    assert tag.saved is False


def test_mb_overwrites_when_only_missing_false():
    tag = FakeTag(genre="Electronic", album="Old Album")
    fields = {"album": {"enabled": True, "only_missing": False}}
    with _scan(_records("/a.mp3")), \
         patch("library.enrich.eyed3.load", return_value=_audio(tag)), \
         patch("library.mbmeta.resolve", return_value=_mb_meta()):
        result = run("/lib", mb_client=MagicMock(), fields=fields)
    assert tag.album == "Mezzanine"
    assert result["per_field"]["album"] == 1


def test_no_write_when_mb_returns_none():
    tag = FakeTag(genre="Electronic")
    fields = _all_fields()
    fields["genre"]["enabled"] = False
    with _scan(_records("/a.mp3")), \
         patch("library.enrich.eyed3.load", return_value=_audio(tag)), \
         patch("library.mbmeta.resolve", return_value=None):
        result = run("/lib", mb_client=MagicMock(), fields=fields)
    assert result["enriched"] == 0
    assert result["skipped"] == 1
    assert tag.saved is False


# ── Progress + result shape ────────────────────────────────────────────────────

def test_progress_callback_fires_per_file():
    tags = [FakeTag(genre=None), FakeTag(genre=None)]
    audios = [_audio(t) for t in tags]
    calls = []
    with _scan(_records("/a.mp3", "/b.mp3")), \
         patch("library.enrich.eyed3.load", side_effect=audios):
        run("/lib", lastfm_client=_lastfm(track_tags=[{"name": "idm", "count": "90"}]),
            fields={"genre": {"enabled": True, "only_missing": True}},
            progress=lambda done, total: calls.append((done, total)))
    assert calls[0] == (0, 2)
    assert calls[-1] == (2, 2)


def test_result_has_expected_keys():
    with _scan([]):
        result = run("/lib")
    assert set(result.keys()) == {
        "processed", "files_total", "enriched", "per_field", "skipped", "errors"}
    assert set(result["per_field"].keys()) == {
        "genre", "year", "album", "album_artist", "mbids", "cover_art"}


def test_limit_caps_files():
    records = _records(*[f"/s{i}.mp3" for i in range(5)])
    with _scan(records), \
         patch("library.enrich.eyed3.load", return_value=_audio(FakeTag(genre="Rock"))):
        result = run("/lib", fields=_all_fields(), limit=2)
    assert result["files_total"] == 2


def test_load_failure_counts_as_error():
    with _scan(_records("/a.mp3")), \
         patch("library.enrich.eyed3.load", return_value=None):
        result = run("/lib", fields=_all_fields())
    assert result["errors"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/library/test_enrich.py -v`
Expected: FAIL — `TypeError` on the new `run()` keyword args (old `run()` doesn't accept `fields`/`mb_client`/`progress`).

- [ ] **Step 3: Rewrite `library/enrich.py`**

Replace the entire contents of `library/enrich.py` with:

```python
"""Library tag enrichment: backfill ID3 metadata from Last.fm + MusicBrainz."""

import logging

import eyed3
import eyed3.core
import eyed3.id3
import eyed3.id3.frames

from library.scanner import scan

logger = logging.getLogger(__name__)

_ALL_FIELDS = ("genre", "year", "album", "album_artist", "mbids", "cover_art")

_MB_TXXX_ARTIST = "MusicBrainz Artist Id"
_MB_TXXX_ALBUM = "MusicBrainz Album Id"
_MB_TXXX_RG = "MusicBrainz Release Group Id"
_MB_UFID_OWNER = "http://musicbrainz.org"


def _default_fields():
    return {f: {"enabled": True, "only_missing": True} for f in _ALL_FIELDS}


def _has_mbids(tag):
    """True if any MusicBrainz ID frame is already present."""
    try:
        if tag.user_text_frames.get(_MB_TXXX_ARTIST):
            return True
        if tag.unique_file_ids.get(_MB_UFID_OWNER):
            return True
    except Exception:
        pass
    return False


def _write_mbids(tag, meta):
    """Write available MusicBrainz ID frames. Returns True if any were written."""
    wrote = False
    if meta.get("artist_mbid"):
        tag.user_text_frames.set(meta["artist_mbid"], _MB_TXXX_ARTIST)
        wrote = True
    if meta.get("release_mbid"):
        tag.user_text_frames.set(meta["release_mbid"], _MB_TXXX_ALBUM)
        wrote = True
    if meta.get("rg_mbid"):
        tag.user_text_frames.set(meta["rg_mbid"], _MB_TXXX_RG)
        wrote = True
    if meta.get("recording_mbid"):
        tag.unique_file_ids.set(meta["recording_mbid"].encode("utf-8"),
                                _MB_UFID_OWNER)
        wrote = True
    return wrote


def _needed(fields, name, is_empty):
    cfg = fields.get(name, {})
    if not cfg.get("enabled"):
        return False
    if cfg.get("only_missing", True) and not is_empty:
        return False
    return True


def run(song_dir, lastfm_client=None, mb_client=None, fields=None,
        min_musicbrainz_score=90, cover_art_size="500", limit=None,
        progress=None):
    """Walk song_dir and enrich ID3 tags from Last.fm (genre) + MusicBrainz.

    fields   : per-field config {name: {"enabled": bool, "only_missing": bool}}.
               Defaults to all six fields enabled + only_missing.
    progress : optional callback progress(done, total) called once before the
               loop with (0, total) and after each file with (i, total).

    Returns {processed, files_total, enriched, per_field, skipped, errors}.
    """
    from lastfm.tags import get_track_tags, get_artist_tags
    from library import mbmeta, coverart

    if fields is None:
        fields = _default_fields()

    records = scan(song_dir)
    if limit is not None:
        records = records[:limit]

    total = len(records)
    per_field = {f: 0 for f in _ALL_FIELDS}
    processed = 0
    enriched = 0
    skipped = 0
    errors = 0

    if progress:
        progress(0, total)

    for i, rec in enumerate(records, start=1):
        path = rec["path"]
        artist = rec.get("artist", "")
        title = rec.get("title", "")

        try:
            audio = eyed3.load(path)
        except Exception:
            logger.warning("enrich: could not load %s", path)
            errors += 1
            if progress:
                progress(i, total)
            continue
        if audio is None:
            logger.warning("enrich: eyed3 could not load %s", path)
            errors += 1
            if progress:
                progress(i, total)
            continue
        if audio.tag is None:
            audio.initTag()
        tag = audio.tag
        processed += 1

        genre_empty = not (tag.genre and (tag.genre.name or tag.genre.id is not None))
        year_empty = tag.getBestDate() is None
        album_empty = not (tag.album or "").strip()
        album_artist_empty = not (tag.album_artist or "").strip()
        mbids_empty = not _has_mbids(tag)
        cover_empty = len(list(tag.images)) == 0

        need_genre = _needed(fields, "genre", genre_empty)
        need_year = _needed(fields, "year", year_empty)
        need_album = _needed(fields, "album", album_empty)
        need_album_artist = _needed(fields, "album_artist", album_artist_empty)
        need_mbids = _needed(fields, "mbids", mbids_empty)
        need_cover = _needed(fields, "cover_art", cover_empty)

        wrote = False

        # Genre via Last.fm
        if need_genre and lastfm_client and artist and title:
            tags = get_track_tags(lastfm_client, artist, title)
            if not tags:
                tags = get_artist_tags(lastfm_client, artist)
            if tags:
                tag.genre = eyed3.id3.Genre(name=", ".join(t["name"] for t in tags))
                per_field["genre"] += 1
                wrote = True

        # MusicBrainz-backed fields (one resolve per file)
        need_mb = (need_year or need_album or need_album_artist
                   or need_mbids or need_cover)
        if need_mb and mb_client and artist and title:
            meta = mbmeta.resolve(mb_client, artist, title, min_musicbrainz_score)
            if meta:
                if need_year and meta["year"]:
                    tag.recording_date = eyed3.core.Date(int(meta["year"]))
                    per_field["year"] += 1
                    wrote = True
                if need_album and meta["album"]:
                    tag.album = meta["album"]
                    per_field["album"] += 1
                    wrote = True
                if need_album_artist and meta["album_artist"]:
                    tag.album_artist = meta["album_artist"]
                    per_field["album_artist"] += 1
                    wrote = True
                if need_mbids and _write_mbids(tag, meta):
                    per_field["mbids"] += 1
                    wrote = True
                if need_cover and meta["release_mbid"]:
                    art = coverart.fetch_front(meta["release_mbid"], cover_art_size)
                    if art:
                        img_bytes, mime = art
                        tag.images.set(
                            eyed3.id3.frames.ImageFrame.FRONT_COVER, img_bytes, mime)
                        per_field["cover_art"] += 1
                        wrote = True

        if wrote:
            try:
                tag.save()
                enriched += 1
            except Exception:
                logger.exception("enrich: failed to save %s", path)
                errors += 1
        else:
            skipped += 1

        if progress:
            progress(i, total)

    return {
        "processed": processed,
        "files_total": total,
        "enriched": enriched,
        "per_field": per_field,
        "skipped": skipped,
        "errors": errors,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/library/test_enrich.py -v`
Expected: PASS (all tests in the rewritten file)

- [ ] **Step 5: Commit**

```bash
git add library/enrich.py tests/library/test_enrich.py
git commit -m "feat(library): enrich.run fills genre + MusicBrainz fields with progress"
```

---

## Task 5: Wire the Flask layer — status contract + clients + config

**Files:**
- Modify: `sWebExt/py_server/server.py` (`library_enrich`, `_run_enrich_once`, add `_enrich_fields`)
- Test: `tests/server/test_routes.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/server/test_routes.py`:

```python
def test_post_enrich_sets_running_immediately(client):
    # threading.Thread is patched in the `app` fixture, so the worker never runs.
    resp = client.post("/library/enrich")
    assert resp.status_code == 200
    assert json.loads(resp.data)["status"] == "running"

    status = client.get("/library/enrich/status")
    data = json.loads(status.data)
    assert data["status"] == "running"
    assert data["files_total"] == 0
    assert data["files_done"] == 0


def test_run_enrich_once_disabled_when_config_disabled():
    from sWebExt.py_server import server as srv
    srv._enrich_last_result = {"status": "idle"}
    with patch("discover.config.load_config",
               return_value={"enrich": {"enabled": False}, "song_dir": "/x"}):
        result = srv._run_enrich_once()
    assert result["status"] == "disabled"
    assert "disabled" in result["reason"]


def test_run_enrich_once_ok_result_has_ui_fields():
    from sWebExt.py_server import server as srv
    srv._enrich_last_result = {"status": "idle"}
    fake_result = {"processed": 2, "files_total": 2, "enriched": 2,
                   "per_field": {}, "skipped": 0, "errors": 0}
    with patch("discover.config.load_config",
               return_value={"enrich": {"enabled": True}, "song_dir": "/x",
                             "lastfm_api_key": "k"}), \
         patch("library.enrich.run", return_value=dict(fake_result)), \
         patch("follow.musicbrainz.MusicBrainzClient"), \
         patch("lastfm.client.LastFMClient"):
        result = srv._run_enrich_once()
    assert result["status"] == "ok"
    assert result["enriched"] == 2
    assert result["files_done"] == result["files_total"]


def test_enrich_fields_legacy_only_missing_genre():
    from sWebExt.py_server import server as srv
    fields = srv._enrich_fields({"only_missing_genre": False})
    assert fields["genre"] == {"enabled": True, "only_missing": False}
    assert fields["album"] == {"enabled": True, "only_missing": True}


def test_enrich_fields_explicit_block_passthrough():
    from sWebExt.py_server import server as srv
    block = {"fields": {"genre": {"enabled": False, "only_missing": True}}}
    assert srv._enrich_fields(block) == block["fields"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/server/test_routes.py -k enrich -v`
Expected: FAIL — `test_post_enrich_sets_running_immediately` sees `started` not `running`; `_enrich_fields` does not exist; disabled test fails (flag not honored).

- [ ] **Step 3: Add the `_enrich_fields` helper**

In `sWebExt/py_server/server.py`, add near the enrich globals (after `_enrich_last_result` is defined around line 69-72):

```python
_ENRICH_ALL_FIELDS = ("genre", "year", "album", "album_artist", "mbids", "cover_art")


def _enrich_fields(enrich_cfg):
    """Return the per-field config dict, mapping legacy only_missing_genre."""
    fields = enrich_cfg.get("fields")
    if fields:
        return fields
    only_missing_genre = enrich_cfg.get("only_missing_genre", True)
    built = {f: {"enabled": True, "only_missing": True}
             for f in _ENRICH_ALL_FIELDS}
    built["genre"]["only_missing"] = bool(only_missing_genre)
    return built
```

- [ ] **Step 4: Replace `_run_enrich_once`**

Replace the whole `_run_enrich_once` function (currently `server.py:526-559`) with:

```python
def _run_enrich_once(limit=None) -> dict:
    global _enrich_last_result
    if not _enrich_running.acquire(blocking=False):
        return {"status": "skipped", "reason": "already running"}
    try:
        from discover.config import load_config
        cfg = load_config(_CONFIG_PATH)
        enrich_cfg = cfg.get("enrich") or {}
        if not enrich_cfg.get("enabled", False):
            result = {"status": "disabled", "reason": "enrich disabled in config"}
            _enrich_last_result = result
            return result
        song_dir = cfg.get("song_dir", "")
        if not song_dir:
            result = {"status": "disabled", "reason": "song_dir not set"}
            _enrich_last_result = result
            return result

        fields = _enrich_fields(enrich_cfg)
        min_score = int(enrich_cfg.get("min_musicbrainz_score", 90))
        cover_size = str(enrich_cfg.get("cover_art_size", "500"))

        lfm = None
        api_key = cfg.get("lastfm_api_key", "")
        if api_key:
            from lastfm.client import LastFMClient
            lfm = LastFMClient(api_key)
        from follow.musicbrainz import MusicBrainzClient
        mbc = MusicBrainzClient()

        from library.enrich import run as enrich_run

        def _progress(done, total):
            global _enrich_last_result
            _enrich_last_result = {"status": "running",
                                   "files_done": done, "files_total": total}

        result = enrich_run(song_dir, lastfm_client=lfm, mb_client=mbc,
                            fields=fields, min_musicbrainz_score=min_score,
                            cover_art_size=cover_size, limit=limit,
                            progress=_progress)
        result["status"] = "ok"
        result["files_done"] = result.get("files_total", 0)
        logger.info("[ENRICH] complete: %s", result)
        _enrich_last_result = result
        return result
    except Exception as e:
        logger.exception("[ENRICH] failed")
        result = {"status": "error", "error": str(e)}
        _enrich_last_result = result
        return result
    finally:
        _enrich_running.release()
```

- [ ] **Step 5: Replace the POST handler**

Replace the `library_enrich` route (currently `server.py:981-987`) with:

```python
@app.route("/library/enrich", methods=["POST"])
def library_enrich():
    global _enrich_last_result
    if _enrich_running.locked():
        return jsonify({"status": "skipped", "reason": "already running"})
    body = request.get_json(force=True, silent=True) or {}
    limit = body.get("limit", None)
    _enrich_last_result = {"status": "running", "files_done": 0, "files_total": 0}
    t = threading.Thread(target=_run_enrich_once, kwargs={"limit": limit}, daemon=True)
    t.start()
    return jsonify({"status": "running"})
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/server/test_routes.py -k enrich -v`
Expected: PASS (all enrich route tests, including the original `test_enrich_status_returns_idle`)

- [ ] **Step 7: Commit**

```bash
git add sWebExt/py_server/server.py tests/server/test_routes.py
git commit -m "feat(server): enrich publishes running state + honors enabled/fields config"
```

---

## Task 6: Update `config.example.json`

**Files:**
- Modify: `config.example.json`

- [ ] **Step 1: Replace the `enrich` block**

Find the existing block (around `config.example.json:52-55`):

```json
"enrich": {
  "enabled": false,
  "only_missing_genre": true
}
```

Replace it with (preserve the trailing comma/placement exactly as the surrounding JSON requires):

```json
"enrich": {
  "enabled": false,
  "min_musicbrainz_score": 90,
  "cover_art_size": "500",
  "fields": {
    "genre":        { "enabled": true, "only_missing": true },
    "year":         { "enabled": true, "only_missing": true },
    "album":        { "enabled": true, "only_missing": true },
    "album_artist": { "enabled": true, "only_missing": true },
    "mbids":        { "enabled": true, "only_missing": true },
    "cover_art":    { "enabled": true, "only_missing": true }
  }
}
```

- [ ] **Step 2: Validate JSON**

Run: `python -c "import json; json.load(open('config.example.json')); print('valid')"`
Expected: `valid`

- [ ] **Step 3: Commit**

```bash
git add config.example.json
git commit -m "feat(config): per-field enrich schema with MB score + cover-art size"
```

---

## Task 7: Verify the UI contract & full suite

**Files:**
- Read only: `web/static/app.js` (no change expected)

- [ ] **Step 1: Confirm the frontend already matches the contract**

Read `web/static/app.js` around lines 532-558. Confirm:
- the `running` branch reads `s.files_total` / `s.files_done` (now provided), and
- the result branch reads `s.enriched` (now provided), and
- the `running`/`started` status check still matches (backend now emits `running`).

No code change is expected. If any field name diverges from the backend (`enriched`, `files_total`, `files_done`, `running`), fix the JS to match the backend and note it.

- [ ] **Step 2: Run the full test suite**

Run: `python -m pytest -q`
Expected: PASS (whole suite green). Investigate and fix any failure before proceeding.

- [ ] **Step 3: Final commit (only if Step 1 required a JS change)**

```bash
git add web/static/app.js
git commit -m "fix(web): align enrich status field names with backend contract"
```

---

## Self-Review Notes (for the implementer)

- **Per-field accounting:** `enriched` counts files with ≥1 written field; `per_field[x]` counts each field. A file where nothing was needed/resolved counts as `skipped`.
- **One MB call per file:** `mbmeta.resolve` is called at most once per file, only when an MB-backed field is actually needed (gated by `only_missing`). Cover art is fetched only when `cover_art` is needed and a `release_mbid` was resolved.
- **eyed3 APIs used:** `tag.genre` (Genre), `tag.recording_date` (eyed3.core.Date), `tag.album`, `tag.album_artist`, `tag.user_text_frames.set/get` (TXXX), `tag.unique_file_ids.set/get` (UFID, bytes payload), `tag.images.set(ImageFrame.FRONT_COVER, bytes, mime)`, `tag.getBestDate()`, `tag.save()`.
- **Back-compat:** `_enrich_fields` maps legacy `only_missing_genre` when no `fields` block is present.
- **No Navidrome rescan / no cancel / MP3-only** — intentionally out of scope per the spec.
```