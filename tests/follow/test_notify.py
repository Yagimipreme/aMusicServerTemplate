from follow import notify
from follow import fstate


def test_record_acquired_appends_feed(state_path):
    st = fstate.load(state_path)
    notify.record_event(st, artist="A", title="T", release_name="R",
                        release_date="2026-06-12", primary_type="Single",
                        status="acquired")
    assert st.feed()[0]["status"] == "acquired"
    assert st.summary()["unseen_count"] == 1


def test_push_webhook_and_ntfy_called():
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        class R:
            status_code = 200
            def raise_for_status(self): pass
        return R()

    notify.push_summary(
        ["A – T", "B – U"],
        webhook_url="https://hook.example/x",
        ntfy_topic="mytopic",
        post_fn=fake_post,
    )
    urls = [c[0] for c in calls]
    assert "https://hook.example/x" in urls
    assert "https://ntfy.sh/mytopic" in urls
    # webhook gets JSON
    webhook_call = next(c for c in calls if c[0] == "https://hook.example/x")
    assert "json" in webhook_call[1]
    assert webhook_call[1]["json"]["count"] == 2


def test_push_summary_noop_when_unconfigured():
    calls = []
    notify.push_summary(["A – T"], webhook_url="", ntfy_topic="",
                        post_fn=lambda *a, **k: calls.append(1))
    assert calls == []


def test_push_summary_noop_when_empty_list():
    calls = []
    notify.push_summary([], webhook_url="https://x", ntfy_topic="t",
                        post_fn=lambda *a, **k: calls.append(1))
    assert calls == []
