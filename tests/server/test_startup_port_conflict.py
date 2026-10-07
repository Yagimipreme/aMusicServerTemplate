"""Startup must bind the port before spawning background threads.

Regression (2026-10-07): a duplicate systemd unit started server.py while
another instance held :5000. Each attempt spawned the Selenium client_id
scrape thread, then app.run() failed to bind and the process exited mid-scrape.
Daemon threads never run their `finally`, so every restart (139k of them)
orphaned a Chromium profile dir in /tmp until the tmpfs ran out of inodes.
"""
import os
import socket
import sys
from unittest.mock import patch

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def srv():
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)
    with patch("threading.Thread"):
        from sWebExt.py_server import server as mod
    return mod


def test_port_in_use_exits_before_starting_threads(srv):
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("0.0.0.0", 0))
    blocker.listen(1)
    port = blocker.getsockname()[1]
    try:
        with patch.object(srv.threading, "Thread") as thread_cls, \
             patch.object(srv, "_start_zeroconf") as zc:
            with pytest.raises(SystemExit) as exc:
                srv.start_background_server(port=port)
        assert exc.value.code != 0
        thread_cls.assert_not_called()
        zc.assert_not_called()
    finally:
        blocker.close()


def test_threads_start_after_bind(srv):
    order = []

    class FakeServer:
        def serve_forever(self):
            order.append("serve")

    def fake_make_server(host, port, app, threaded):
        order.append("bind")
        return FakeServer()

    def fake_thread(*a, **kw):
        order.append("thread")

        class T:
            def start(self):
                pass
        return T()

    with patch.object(srv, "make_server", side_effect=fake_make_server), \
         patch.object(srv.threading, "Thread", side_effect=fake_thread), \
         patch.object(srv, "_start_zeroconf"):
        srv.start_background_server(port=5000)

    assert order[0] == "bind"
    assert "thread" in order
    assert order[-1] == "serve"
