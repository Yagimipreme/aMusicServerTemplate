"""Impure adapters wiring the engine to yt-dlp and the existing downloader.

Not unit-tested (touches network/yt-dlp); exercised via the manual smoke test.
"""
import logging

logger = logging.getLogger(__name__)


def make_search_fn():
    """Return search_fn(artist_name, n) -> [{"title", "url"}] via yt-dlp flat search."""
    from yt_dlp import YoutubeDL

    def search_fn(artist_name, n):
        query = f"ytsearch{n}:{artist_name}"
        opts = {"quiet": True, "skip_download": True, "extract_flat": "in_playlist"}
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(query, download=False)
        entries = (info or {}).get("entries", []) or []
        out = []
        for e in entries:
            vid = e.get("id")
            url = e.get("url") or (f"https://www.youtube.com/watch?v={vid}" if vid else None)
            if url:
                out.append({"title": e.get("title", ""), "url": url})
        return out

    return search_fn


def make_download_fn(download_callable):
    """Wrap a (url -> result) callable into download_fn(url).

    In production this wraps `lambda url: script_web.download_url(url, song_dir)`,
    whose result is `(playlist_title, [mp3_paths])` — handled by acquire().
    """
    def download_fn(url):
        return download_callable(url)
    return download_fn
