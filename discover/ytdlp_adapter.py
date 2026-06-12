"""Impure adapters wiring the engine to yt-dlp and the existing downloader.

Not unit-tested (touches network/yt-dlp); exercised via the manual smoke test.
"""
import logging

logger = logging.getLogger(__name__)


_MAX_TRACK_SECONDS = 900  # 15 min; filters phone reviews/cooking/loops but keeps DJ edits

_DEFAULT_JUNK_KEYWORDS: frozenset = frozenset({
    "review", "tutorial", "reaction", "cooking", "recipe",
    "horoscope", "astrology", "type beat", "asmr", "unboxing",
    "vlog", "podcast", "gameplay", "walkthrough",
})


def _is_music_result(entry: dict, artist_name: str,
                     extra_junk: frozenset = frozenset()) -> bool:
    title = (entry.get("title") or "").casefold()
    channel = (
        (entry.get("uploader") or "")
        + " "
        + (entry.get("channel") or "")
    ).casefold()
    artist_cf = artist_name.casefold()

    if artist_cf not in title and artist_cf not in channel:
        return False

    junk = _DEFAULT_JUNK_KEYWORDS | extra_junk
    return not any(kw in title for kw in junk)


def make_search_fn(oversample: int = 5,
                   extra_junk_keywords: frozenset = frozenset()):
    """Return search_fn(artist_name, n, track_hint=None) via yt-dlp flat search.

    Fetches n×oversample candidates, applies _is_music_result filter, returns up to n.
    Default query suffix changed from 'music' to 'official audio'.
    """
    from yt_dlp import YoutubeDL

    def search_fn(artist_name, n, track_hint=None):
        suffix = track_hint if track_hint else "official audio"
        fetch_n = n * oversample
        query = f"ytsearch{fetch_n}:{artist_name} {suffix}"
        opts = {"quiet": True, "skip_download": True, "extract_flat": "in_playlist"}
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(query, download=False)
        entries = (info or {}).get("entries", []) or []
        out = []
        for e in entries:
            if len(out) >= n:
                break
            vid = e.get("id")
            url = e.get("url") or (
                f"https://www.youtube.com/watch?v={vid}" if vid else None
            )
            if not url:
                continue
            duration = e.get("duration") or 0
            if duration and duration > _MAX_TRACK_SECONDS:
                continue
            if not _is_music_result(e, artist_name, extra_junk_keywords):
                continue
            out.append({"title": e.get("title", ""), "url": url})
        return out

    return search_fn


# Module-level search callable — used by run_mix_from_config and tests.
search = make_search_fn()


def make_download_fn(download_callable):
    """Wrap a (url -> result) callable into download_fn(url).

    In production this wraps `lambda url: script_web.download_url(url, song_dir)`,
    whose result is `(playlist_title, [mp3_paths])` — handled by acquire().
    """
    def download_fn(url):
        return download_callable(url)
    return download_fn
