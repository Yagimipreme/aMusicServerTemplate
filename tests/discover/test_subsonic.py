from discover.subsonic import Subsonic


def make_client(responses):
    """responses: dict mapping a substring-of-URL -> parsed json dict."""
    def fake_fetch(url):
        for needle, payload in responses.items():
            if needle in url:
                return payload
        raise AssertionError(f"unexpected url: {url}")
    return Subsonic("http://nd:4533", "user", "pw", fetch_json=fake_fetch)


def test_get_frequent_artists_dedupes_and_keeps_order():
    payload = {"subsonic-response": {"status": "ok", "albumList2": {"album": [
        {"artist": "Boards of Canada", "artistId": "a1"},
        {"artist": "Aphex Twin", "artistId": "a2"},
        {"artist": "Boards of Canada", "artistId": "a1"},
    ]}}}
    c = make_client({"getAlbumList2": payload})
    artists = c.get_frequent_artists(size=50)
    assert artists == [
        {"id": "a1", "name": "Boards of Canada"},
        {"id": "a2", "name": "Aphex Twin"},
    ]


def test_get_artist_info2_returns_not_owned_similar():
    payload = {"subsonic-response": {"status": "ok", "artistInfo2": {"similarArtist": [
        {"id": "-1", "name": "Zmajor"},
        {"id": "b9", "name": "OwnedGuy"},
    ]}}}
    c = make_client({"getArtistInfo2": payload})
    sim = c.get_artist_info2("a1", count=20)
    assert {"id": "-1", "name": "Zmajor"} in sim
    assert {"id": "b9", "name": "OwnedGuy"} in sim


def test_song_exists_true_when_search_returns_song():
    payload = {"subsonic-response": {"status": "ok", "searchResult3": {"song": [
        {"id": "s1", "title": "Roygbiv", "artist": "Boards of Canada"},
    ]}}}
    c = make_client({"search3": payload})
    assert c.song_exists("Boards of Canada", "Roygbiv") is True


def test_song_exists_false_when_no_song():
    payload = {"subsonic-response": {"status": "ok", "searchResult3": {}}}
    c = make_client({"search3": payload})
    assert c.song_exists("Nobody", "Nothing") is False


def test_start_scan_returns_true_on_ok():
    payload = {"subsonic-response": {"status": "ok"}}
    c = make_client({"startScan": payload})
    assert c.start_scan() is True
