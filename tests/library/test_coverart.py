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
