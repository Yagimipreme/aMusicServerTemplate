"""Last.fm similar-artist and top-tracks functions, plus candidate scoring."""

import logging

logger = logging.getLogger(__name__)


def get_similar_artists(client, artist: str, limit: int = 100) -> list[dict]:
    """Return similar artists as [{"name": str, "match": float}, ...] sorted by match desc.

    Returns [] on any error.
    """
    from lastfm.client import LastFMNotFound
    try:
        data = client.call("artist.getSimilar", artist=artist, limit=limit)
    except LastFMNotFound:
        logger.debug("lastfm.similar: artist not found — %s", artist)
        return []
    except Exception:
        logger.warning("lastfm.similar: get_similar_artists failed for %s", artist, exc_info=True)
        return []

    raw = (data.get("similarartists", {}) or {}).get("artist", []) or []
    if isinstance(raw, dict):
        raw = [raw]

    result = []
    for entry in raw:
        name = (entry.get("name") or "").strip()
        try:
            match = float(entry.get("match", 0) or 0)
        except (TypeError, ValueError):
            match = 0.0
        if name:
            result.append({"name": name, "match": match})

    result.sort(key=lambda x: -x["match"])
    return result


def get_artist_top_tracks(client, artist: str, limit: int = 5) -> list[dict]:
    """Return top tracks for an artist as [{"title": str, "artist": str}, ...].

    Returns [] on any error.
    """
    from lastfm.client import LastFMNotFound
    try:
        data = client.call("artist.getTopTracks", artist=artist, limit=limit)
    except LastFMNotFound:
        logger.debug("lastfm.similar: artist not found for top tracks — %s", artist)
        return []
    except Exception:
        logger.warning("lastfm.similar: get_artist_top_tracks failed for %s", artist, exc_info=True)
        return []

    raw = (data.get("toptracks", {}) or {}).get("track", []) or []
    if isinstance(raw, dict):
        raw = [raw]

    result = []
    for entry in raw:
        title = (entry.get("name") or "").strip()
        # The artist sub-object may be a dict with a "name" key
        artist_entry = entry.get("artist", {})
        if isinstance(artist_entry, dict):
            aname = (artist_entry.get("name") or artist).strip()
        else:
            aname = str(artist_entry).strip() or artist
        if title:
            result.append({"title": title, "artist": aname})

    return result


def score_candidates(
    similar_artists: list[dict],
    genre_profile: dict[str, float],
    candidate_tags: dict[str, list[dict]],
) -> list[dict]:
    """Score candidates by match × genre_overlap and return sorted list.

    Parameters
    ----------
    similar_artists : list of {"name": str, "match": float}
    genre_profile   : normalized fingerprint from lastfm.tags.build_genre_profile
                      (may be empty — falls back to pure match score)
    candidate_tags  : dict mapping artist_name.casefold() -> list of tag dicts
                      (from lastfm.tags.get_artist_tags)

    Returns list of {"name": str, "match": float, "score": float} sorted by score desc.
    """
    results = []
    use_genre = bool(genre_profile)

    for sim in similar_artists:
        name = sim["name"]
        match = sim["match"]

        if use_genre:
            tags = candidate_tags.get(name.casefold(), [])
            # Build a normalized tag vector for the candidate
            if tags:
                max_w = max(t["weight"] for t in tags)
                candidate_vec = {
                    t["name"]: t["weight"] / max_w for t in tags
                } if max_w > 0 else {}
            else:
                candidate_vec = {}

            # Dot product of genre_profile × candidate_vec
            overlap = sum(
                genre_profile.get(tag, 0.0) * weight
                for tag, weight in candidate_vec.items()
            )
            score = match * overlap if candidate_vec else match * 0.0
        else:
            # Empty profile fallback — pure similarity score
            score = match

        results.append({"name": name, "match": match, "score": score})

    results.sort(key=lambda x: -x["score"])
    return results
