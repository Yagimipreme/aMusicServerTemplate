"""Last.fm tag preview: what a tag actually means before it seeds a mix."""
import logging

logger = logging.getLogger(__name__)

_TOP_ARTISTS_LIMIT = 10


def _as_list(raw):
    if isinstance(raw, dict):
        return [raw]
    return raw or []


def get_tag_preview(client, tag: str) -> dict:
    """Return {'tag', 'taggings', 'reach', 'top_artists', 'similar'} for a tag.

    Sub-call failures degrade to empty values; raises only when every
    sub-call fails (total Last.fm outage).
    """
    name = (tag or "").strip()
    failures = 0
    last_exc = None

    taggings = reach = 0
    try:
        info = client.call("tag.getInfo", tag=name)
        t = info.get("tag") or {}
        taggings = int(t.get("total") or 0)
        reach = int(t.get("reach") or 0)
    except Exception as exc:
        failures += 1
        last_exc = exc
        logger.warning("tag_preview: tag.getInfo failed for %r", name)

    top_artists = []
    try:
        data = client.call("tag.getTopArtists", tag=name, limit=_TOP_ARTISTS_LIMIT)
        raw = _as_list((data.get("topartists") or {}).get("artist"))
        top_artists = [a.get("name", "") for a in raw if a.get("name")]
    except Exception as exc:
        failures += 1
        last_exc = exc
        logger.warning("tag_preview: tag.getTopArtists failed for %r", name)

    similar = []
    try:
        data = client.call("tag.getSimilar", tag=name)
        raw = _as_list((data.get("similartags") or {}).get("tag"))
        similar = [t.get("name", "") for t in raw if t.get("name")]
    except Exception as exc:
        failures += 1
        last_exc = exc
        logger.warning("tag_preview: tag.getSimilar failed for %r", name)

    if failures == 3 and last_exc is not None:
        raise last_exc

    return {
        "tag": name,
        "taggings": taggings,
        "reach": reach,
        "top_artists": top_artists,
        "similar": similar,
    }
