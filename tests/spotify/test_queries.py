"""Tests for spotify/queries.py — verify API call shapes."""
from unittest.mock import MagicMock, call, patch
import pytest


def _make_client():
    client = MagicMock()
    return client


def test_get_artist_overview_does_not_send_watch_entrypoint():
    """queryArtistOverview must NOT include enableWatchFeedEntrypoint."""
    from spotify.queries import get_artist_overview
    client = _make_client()
    client.graphql.return_value = {
        "data": {
            "artistUnion": {
                "discography": {"topTracks": {"items": []}},
                "relatedContent": {"relatedArtists": {"items": []}}
            }
        }
    }
    get_artist_overview(client, "spotify:artist:4Z8W4")
    call_args = client.graphql.call_args
    variables = call_args[0][1]  # positional arg 2 = variables dict
    assert "enableWatchFeedEntrypoint" not in variables


def test_get_artist_overview_converts_uri():
    """open.spotify.com URL must be converted to spotify:artist: URI."""
    from spotify.queries import get_artist_overview
    client = _make_client()
    client.graphql.return_value = {
        "data": {
            "artistUnion": {
                "discography": {"topTracks": {"items": []}},
                "relatedContent": {"relatedArtists": {"items": []}}
            }
        }
    }
    get_artist_overview(client, "https://open.spotify.com/artist/4Z8W4abc")
    call_args = client.graphql.call_args
    variables = call_args[0][1]
    assert variables["uri"] == "spotify:artist:4Z8W4abc"


def test_get_playlist_sends_watch_entrypoint_false():
    """fetchPlaylist MUST send enableWatchFeedEntrypoint=False."""
    from spotify.queries import get_playlist
    client = _make_client()
    client.graphql.return_value = {
        "data": {
            "playlistV2": {
                "name": "Test Playlist",
                "content": {"items": []}
            }
        }
    }
    get_playlist(client, "spotify:playlist:abc123")
    call_args = client.graphql.call_args
    variables = call_args[0][1]
    assert variables.get("enableWatchFeedEntrypoint") is False


def test_get_artist_overview_maps_top_tracks():
    from spotify.queries import get_artist_overview
    client = _make_client()
    client.graphql.return_value = {
        "data": {
            "artistUnion": {
                "discography": {
                    "topTracks": {
                        "items": [{
                            "track": {
                                "id": "t1",
                                "uri": "spotify:track:t1",
                                "name": "Archangel",
                                "playcount": "1234567",
                                "duration": {"totalMilliseconds": 240000},
                                "artists": {"items": [{"profile": {"name": "Burial"}}]},
                                "albumOfTrack": {"coverArt": {"sources": [{"url": "https://img/cover.jpg"}]}}
                            }
                        }]
                    }
                },
                "relatedContent": {"relatedArtists": {"items": []}}
            }
        }
    }
    result = get_artist_overview(client, "spotify:artist:xxx")
    assert len(result["top_tracks"]) == 1
    t = result["top_tracks"][0]
    assert t["title"] == "Archangel"
    assert t["artist"] == "Burial"
    assert t["source"] == "spotify"
    assert "preview_url" not in t  # confirmed absent from Spotify API


def test_get_playlist_maps_tracks():
    from spotify.queries import get_playlist
    client = _make_client()
    client.graphql.return_value = {
        "data": {
            "playlistV2": {
                "name": "Chill",
                "content": {
                    "items": [{
                        "itemV2": {
                            "data": {
                                "uri": "spotify:track:p1",
                                "name": "Windowlicker",
                                "artists": {"items": [{"profile": {"name": "Aphex Twin"}}]},
                                "albumOfTrack": {"coverArt": {"sources": [{"url": "https://img/w.jpg"}]}},
                                "duration": {"totalMilliseconds": 360000},
                            }
                        }
                    }]
                }
            }
        }
    }
    result = get_playlist(client, "spotify:playlist:p123")
    assert result["name"] == "Chill"
    assert len(result["tracks"]) == 1
    t = result["tracks"][0]
    assert t["title"] == "Windowlicker"
    assert t["source"] == "spotify"
