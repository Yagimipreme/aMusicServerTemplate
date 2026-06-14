from follow import musicbrainz as mb


class FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.exceptions.HTTPError(f"{self.status_code}")

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, payloads):
        # payloads: list popped in call order
        self._payloads = list(payloads)
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, params))
        return FakeResp(self._payloads.pop(0))


def test_search_artist_parses_candidates():
    payload = {"artists": [
        {"id": "mbid-1", "name": "Massive Attack",
         "disambiguation": "Bristol trip-hop", "score": 100},
        {"id": "mbid-2", "name": "Massive Attack Tribute", "score": 60},
    ]}
    client = mb.MusicBrainzClient(session=FakeSession([payload]), min_interval=0)
    got = client.search_artist("Massive Attack", limit=5)
    assert got[0] == {"mbid": "mbid-1", "name": "Massive Attack",
                      "disambiguation": "Bristol trip-hop", "score": 100}
    assert got[1]["disambiguation"] == ""  # missing field defaults to ""


def test_get_release_groups_parses():
    payload = {"release-groups": [
        {"id": "rg-1", "title": "Mezzanine",
         "first-release-date": "1998-04-20", "primary-type": "Album"},
        {"id": "rg-2", "title": "Ritual Spirit",
         "first-release-date": "2016-01-28", "primary-type": "EP"},
    ]}
    client = mb.MusicBrainzClient(session=FakeSession([payload]), min_interval=0)
    got = client.get_release_groups("mbid-1", limit=100)
    assert got[0] == {"rg_mbid": "rg-1", "title": "Mezzanine",
                      "first_release_date": "1998-04-20", "primary_type": "Album"}


def test_get_release_tracks_parses():
    payload = {"releases": [
        {"media": [{"tracks": [{"title": "Angel"}, {"title": "Risingson"}]}]}
    ]}
    client = mb.MusicBrainzClient(session=FakeSession([payload]), min_interval=0)
    got = client.get_release_tracks("rg-1")
    assert got == ["Angel", "Risingson"]


def test_get_release_tracks_empty_when_no_releases():
    client = mb.MusicBrainzClient(session=FakeSession([{"releases": []}]), min_interval=0)
    assert client.get_release_tracks("rg-x") == []


def test_search_artist_escapes_lucene_specials():
    """Double-quote and backslash in artist name must be escaped before interpolation."""
    payload = {"artists": []}
    session = FakeSession([payload])
    client = mb.MusicBrainzClient(session=session, min_interval=0)
    client.search_artist('The "Band"', limit=1)
    _, params = session.calls[0]
    query = params["query"]
    # The outer quotes delimit the field value; inner quotes must be escaped as \"
    assert '\\"' in query, f"Expected escaped quote in query, got: {query!r}"
    # No raw unescaped double-quote may appear inside the field value
    # Strip the outer wrapping artist:"..." to check the inner content
    assert query == r'artist:"The \"Band\""', f"Unexpected query: {query!r}"


def test_search_artist_escapes_backslash():
    """Backslash in artist name must be escaped as \\\\ before the quote escaping."""
    payload = {"artists": []}
    session = FakeSession([payload])
    client = mb.MusicBrainzClient(session=session, min_interval=0)
    client.search_artist('AC\\DC', limit=1)
    _, params = session.calls[0]
    query = params["query"]
    assert query == r'artist:"AC\\DC"', f"Unexpected query: {query!r}"


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


def test_search_recording_escapes_lucene_specials():
    sess = FakeSession([{"recordings": []}])
    client = mb.MusicBrainzClient(session=sess, min_interval=0)
    artist, title = 'AC\\DC', 'Say "Hi"'
    client.search_recording(artist, title)
    _, params = sess.calls[0]
    esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')
    assert params["query"] == f'artist:"{esc(artist)}" AND recording:"{esc(title)}"'
