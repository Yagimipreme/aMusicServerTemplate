import logging

logger = logging.getLogger(__name__)


def resolve_tracks(search_fn, artists, per_artist: int = 1):
    """For each artist, search for tracks; each hit becomes a Candidate.

    search_fn(artist_name, n) -> [{"title": str, "url": str}, ...]
    """
    out = []
    for artist in artists:
        name = artist["name"]
        try:
            hits = search_fn(name, per_artist, track_hint=artist.get("top_track"))
        except Exception:
            logger.exception("resolve: search failed for %s", name)
            continue
        for hit in hits:
            url = hit.get("url")
            if not url:
                continue
            out.append({"artist": name, "title": hit.get("title", ""), "url": url})
    return out
