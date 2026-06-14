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
