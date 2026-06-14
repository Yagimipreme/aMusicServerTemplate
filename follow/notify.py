"""Feed recording + optional external push (webhook JSON / ntfy plain text)."""
import logging

logger = logging.getLogger(__name__)


def record_event(state, artist, title, release_name, release_date,
                 primary_type, status) -> None:
    state.append_feed({
        "artist": artist, "title": title, "release_name": release_name,
        "release_date": release_date, "primary_type": primary_type,
        "status": status,
    })


def push_summary(lines, webhook_url="", ntfy_topic="", post_fn=None) -> None:
    """POST a short summary of newly-acquired tracks. No-op if nothing to send."""
    if not lines:
        return
    if post_fn is None:
        import requests
        post_fn = requests.post

    message = f"{len(lines)} new release(s) from your follows:\n" + "\n".join(lines)

    if webhook_url:
        try:
            resp = post_fn(webhook_url,
                           json={"count": len(lines), "tracks": lines,
                                 "message": message},
                           timeout=10)
            resp.raise_for_status()
        except Exception:
            logger.warning("follow: webhook push failed", exc_info=True)

    if ntfy_topic:
        try:
            resp = post_fn(f"https://ntfy.sh/{ntfy_topic}",
                           data=message.encode("utf-8"),
                           headers={"Title": "New Releases"}, timeout=10)
            resp.raise_for_status()
        except Exception:
            logger.warning("follow: ntfy push failed", exc_info=True)
