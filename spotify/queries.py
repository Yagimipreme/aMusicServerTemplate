"""Spotify high-level queries — artist overview, playlist, artist search."""
import logging

from spotify.client import _url_to_uri

logger = logging.getLogger(__name__)


def get_artist_overview(client, uri_or_url: str) -> dict:
    """queryArtistOverview -> {top_tracks: list[Track], related_artists: list[Artist]}"""
    uri = _url_to_uri(uri_or_url)
    data = client.graphql(
        "queryArtistOverview",
        {"uri": uri, "locale": "", "includePrerelease": False},
    )
    artist_union = (data.get("data") or {}).get("artistUnion") or {}

    top_tracks = []
    for item in (artist_union.get("discography") or {}).get("topTracks", {}).get("items", []):
        t = item.get("track") or {}
        artists = [a.get("profile", {}).get("name", "") for a in
                   (t.get("artists") or {}).get("items", [])]
        sources = (t.get("albumOfTrack") or {}).get("coverArt", {}).get("sources", [])
        artwork = sources[0]["url"] if sources else ""
        top_tracks.append({
            "id": t.get("id", ""),
            "uri": t.get("uri", ""),
            "title": t.get("name", ""),
            "artist": ", ".join(artists),
            "album": "",
            "artwork_url": artwork,
            "duration_ms": (t.get("duration") or {}).get("totalMilliseconds", 0),
            "playcount": int(t.get("playcount") or 0),
            "source": "spotify",
        })

    related_artists = []
    for a in ((artist_union.get("relatedContent") or {})
              .get("relatedArtists", {}).get("items", [])):
        sources = ((a.get("visuals") or {}).get("avatarImage") or {}).get("sources", [])
        artwork = sources[0]["url"] if sources else ""
        related_artists.append({
            "id": a.get("id", ""),
            "uri": a.get("uri", ""),
            "name": (a.get("profile") or {}).get("name", ""),
            "artwork_url": artwork,
            "followers": 0,
            "monthly_listeners": 0,
        })

    return {"top_tracks": top_tracks, "related_artists": related_artists}


def get_playlist(client, uri_or_url: str, limit: int = 50) -> dict:
    """fetchPlaylist -> {name: str, tracks: list[Track]}

    enableWatchFeedEntrypoint=False is REQUIRED — omitting causes 400.
    """
    uri = _url_to_uri(uri_or_url)
    data = client.graphql(
        "fetchPlaylist",
        {"uri": uri, "offset": 0, "limit": limit, "enableWatchFeedEntrypoint": False},
    )
    playlist = (data.get("data") or {}).get("playlistV2") or {}
    name = playlist.get("name", "")

    tracks = []
    for item in (playlist.get("content") or {}).get("items", []):
        t = ((item.get("itemV2") or {}).get("data")) or {}
        artists = [a.get("profile", {}).get("name", "") for a in
                   (t.get("artists") or {}).get("items", [])]
        sources = ((t.get("albumOfTrack") or {}).get("coverArt") or {}).get("sources", [])
        artwork = sources[0]["url"] if sources else ""
        uri_t = t.get("uri", "")
        track_id = uri_t.split(":")[-1] if uri_t else ""
        tracks.append({
            "id": track_id,
            "uri": uri_t,
            "title": t.get("name", ""),
            "artist": ", ".join(artists),
            "album": "",
            "artwork_url": artwork,
            "duration_ms": (t.get("duration") or {}).get("totalMilliseconds", 0),
            "playcount": 0,
            "source": "spotify",
        })

    return {"name": name, "tracks": tracks}


def search_artists(client, query: str, limit: int = 10) -> list:
    """Search Spotify artists by name using the searchDesktop operation."""
    try:
        data = client.graphql(
            "searchDesktop",
            {"searchTerm": query, "offset": 0, "limit": limit,
             "numberOfTopResults": limit, "includeAudiobooks": False},
        )
        items = (((data.get("data") or {}).get("searchV2") or {})
                 .get("artists", {}).get("items", []))
        artists = []
        for a in items:
            d = a.get("data") or {}
            sources = ((d.get("visuals") or {}).get("avatarImage") or {}).get("sources", [])
            artwork = sources[0]["url"] if sources else ""
            artists.append({
                "id": d.get("id", ""),
                "uri": d.get("uri", ""),
                "name": (d.get("profile") or {}).get("name", ""),
                "artwork_url": artwork,
                "followers": 0,
                "monthly_listeners": 0,
            })
        return artists
    except Exception:
        logger.warning("[SPOTIFY] search_artists failed", exc_info=True)
        return []
