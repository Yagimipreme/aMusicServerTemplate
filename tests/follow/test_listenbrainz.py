from follow import listenbrainz as lb


class FakeResp:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, payload):
        self._payload = payload
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, params))
        return FakeResp(self._payload)


def test_fresh_releases_parses_and_passes_params():
    payload = {"payload": {"releases": [
        {"artist_credit_name": "Massive Attack",
         "artist_mbids": ["mbid-1"],
         "release_date": "2026-06-12",
         "release_group_mbid": "rg-9",
         "release_name": "New Thing",
         "release_group_primary_type": "Single"},
    ]}}
    fake = FakeSession(payload)
    client = lb.ListenBrainzClient(session=fake)
    got = client.fresh_releases(pivot_date="2026-06-14", days=7, past=True)
    assert got[0] == {
        "artist_mbids": ["mbid-1"],
        "release_date": "2026-06-12",
        "release_group_mbid": "rg-9",
        "release_name": "New Thing",
        "primary_type": "Single",
        "artist_name": "Massive Attack",
    }
    _, params = fake.calls[0]
    assert params["release_date"] == "2026-06-14"
    assert params["days"] == 7
    assert params["past"] == "true"
    assert params["future"] == "false"


def test_fresh_releases_handles_missing_payload():
    client = lb.ListenBrainzClient(session=FakeSession({}))
    assert client.fresh_releases(pivot_date="2026-06-14", days=7) == []
