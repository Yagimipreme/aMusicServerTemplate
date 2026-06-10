"""Last.fm seed functions: user play-history based artist lists."""

import logging

logger = logging.getLogger(__name__)


def get_top_artists(client, username: str, period: str = "1month", limit: int = 20) -> list[str]:
    """Return a list of artist name strings ranked by play count (most played first).

    period: 7day | 1month | 3month | 6month | overall
    """
    try:
        data = client.call(
            "user.getTopArtists",
            user=username,
            period=period,
            limit=limit,
        )
    except Exception:
        logger.warning("lastfm.seeds: get_top_artists failed for %s", username, exc_info=True)
        return []

    artists_root = data.get("topartists", {}) or {}
    raw = artists_root.get("artist", []) or []
    if isinstance(raw, dict):
        # Single-result API quirk: dict instead of list
        raw = [raw]

    names = []
    for entry in raw:
        name = (entry.get("name") or "").strip()
        if name:
            names.append(name)
    return names


def get_weekly_chart(client, username: str) -> list[str]:
    """Return artist names from the current weekly artist chart, ranked by play count."""
    try:
        data = client.call("user.getWeeklyArtistChart", user=username)
    except Exception:
        logger.warning("lastfm.seeds: get_weekly_chart failed for %s", username, exc_info=True)
        return []

    chart = data.get("weeklyartistchart", {}) or {}
    raw = chart.get("artist", []) or []
    if isinstance(raw, dict):
        raw = [raw]

    names = []
    for entry in raw:
        name = (entry.get("name") or "").strip()
        if name:
            names.append(name)
    return names
