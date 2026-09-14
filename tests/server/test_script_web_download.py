"""download_url must never leave (or report) an empty mp3 in the library.

Regression: when the NAS filled up, yt-dlp's final move from the local temp
dir created the destination file and then failed with ENOSPC, leaving a
0-byte .mp3 that Navidrome indexed and the mix engine put into playlists.
"""
import errno
import importlib.util
import os

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SCRIPT = os.path.join(_ROOT, "scripts", "sTownload", "script_web.py")


@pytest.fixture
def script_web():
    spec = importlib.util.spec_from_file_location("sTownload_web_test", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_ydl(out_dir, write_bytes, raise_exc=None):
    class FakeYDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download=True):
            with open(os.path.join(out_dir, "Song.mp3"), "wb") as f:
                f.write(write_bytes)
            if raise_exc:
                raise raise_exc
            return {"title": "Song", "ext": "webm"}

        def prepare_filename(self, entry):
            return os.path.join(out_dir, "Song.webm")

    return FakeYDL


def test_failed_move_removes_empty_mp3(script_web, tmp_path, monkeypatch):
    out = str(tmp_path)
    enospc = OSError(errno.ENOSPC, "No space left on device")
    monkeypatch.setattr(script_web, "YoutubeDL", _fake_ydl(out, b"", enospc))

    title, paths = script_web.download_url("https://www.youtube.com/watch?v=x", out)

    assert paths == []
    assert not os.path.exists(os.path.join(out, "Song.mp3"))


def test_empty_mp3_is_not_reported_as_downloaded(script_web, tmp_path, monkeypatch):
    out = str(tmp_path)
    monkeypatch.setattr(script_web, "YoutubeDL", _fake_ydl(out, b""))

    _, paths = script_web.download_url("https://www.youtube.com/watch?v=x", out)

    assert paths == []
    assert not os.path.exists(os.path.join(out, "Song.mp3"))


def test_real_mp3_is_kept_and_reported(script_web, tmp_path, monkeypatch):
    out = str(tmp_path)
    monkeypatch.setattr(script_web, "YoutubeDL", _fake_ydl(out, b"ID3audio"))

    _, paths = script_web.download_url("https://www.youtube.com/watch?v=x", out)

    assert paths == [os.path.join(out, "Song.mp3")]


def test_preexisting_empty_mp3_from_other_runs_is_untouched(script_web, tmp_path, monkeypatch):
    out = str(tmp_path)
    old = tmp_path / "Old.mp3"
    old.write_bytes(b"")
    os.utime(old, (1000, 1000))
    monkeypatch.setattr(script_web, "YoutubeDL", _fake_ydl(out, b"ID3audio"))

    script_web.download_url("https://www.youtube.com/watch?v=x", out)

    assert old.exists()
