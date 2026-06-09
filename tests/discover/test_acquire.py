from discover.acquire import acquire


def test_acquire_returns_downloaded_paths():
    def fake_download(url):
        assert url == "http://y/1"
        return ("ignored_title", ["/music/song.mp3"])

    paths = acquire(fake_download, {"artist": "A", "title": "t", "url": "http://y/1"})
    assert paths == ["/music/song.mp3"]


def test_acquire_returns_empty_on_download_error():
    def boom(url):
        raise RuntimeError("network")

    paths = acquire(boom, {"artist": "A", "title": "t", "url": "http://y/1"})
    assert paths == []


def test_acquire_handles_plain_list_return():
    def fake_download(url):
        return ["/music/a.mp3", "/music/b.mp3"]

    paths = acquire(fake_download, {"artist": "A", "title": "t", "url": "u"})
    assert paths == ["/music/a.mp3", "/music/b.mp3"]
