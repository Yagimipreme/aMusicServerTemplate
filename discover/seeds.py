def collect_seeds(subsonic, limit: int = 20):
    """Ranked owned artists to seed discovery (most-played first)."""
    artists = subsonic.get_frequent_artists(size=max(limit, 50))
    return artists[:limit]
