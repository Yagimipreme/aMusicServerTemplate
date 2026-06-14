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
