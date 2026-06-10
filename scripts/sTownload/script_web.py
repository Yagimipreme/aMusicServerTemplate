#!/usr/bin/env python3
"""Receive a single URL + m3u name and download one audio file using yt-dlp.
Invoked by the server (via runpy) with: <url> [m3u_name]
"""
import sys
import os
import json
import time
import logging
import re
import shutil

try:
    import yt_dlp
except Exception:
    print('yt_dlp not available')
    raise

try:
    import eyed3
except Exception:
    eyed3 = None

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('sT_download_web')

# ── Paths ──────────────────────────────────────────────────────────────────────

_SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "../../"))
_CONFIG_PATH  = os.path.join(_PROJECT_ROOT, "config.json")


# ── Config helpers ─────────────────────────────────────────────────────────────

def _load_config() -> dict:
    try:
        with open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def get_config_song_dir() -> str:
    cfg = _load_config()
    return cfg.get('song_dir') or str(os.path.join(os.path.expanduser('~'), 'Music'))


def get_ffmpeg_location_from_config() -> str | None:
    loc = _load_config().get('ffmpeg_location')
    return loc if loc else None


def trigger_navidrome_scan():
    cfg = _load_config()
    host = cfg.get('navidrome_url', 'http://localhost:4533')
    user = cfg.get('navidrome_user', '')
    pw   = cfg.get('navidrome_pass', '')
    if not user or not pw:
        logger.info('Navidrome creds not configured, skipping scan trigger')
        return
    params = urllib.parse.urlencode({
        'u': user, 'p': pw, 'v': '1.16.1', 'c': 'musicServer', 'f': 'json'
    })
    url = f"{host}/rest/startScan.view?{params}"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            logger.info('Navidrome scan triggered: %s', resp.read().decode()[:120])
    except Exception as e:
        logger.warning('Navidrome scan trigger failed: %s', e)


# ── Download ───────────────────────────────────────────────────────────────────

import os
import shutil
import urllib.request
import urllib.parse
from yt_dlp import YoutubeDL


# A YouTube playlist landing page is /playlist?list=…  — that's the only
# pattern we treat as "give me every track". A /watch?v=X URL that also
# carries a &list= parameter is still a single song (the user is watching
# one track from inside a playlist context).
PLAYLIST_URL_RE = re.compile(r'youtube\.com/playlist\?', re.IGNORECASE)

# Anything yt-dlp / ffmpeg may leave behind that ISN'T the final mp3.
JUNK_EXTS = (
    '.part', '.webp', '.jpg', '.jpeg', '.png',
    '.mp4', '.mkv', '.m4a', '.webm', '.opus', '.temp.mp3',
)


def _is_playlist_url(url: str) -> bool:
    """True only for /playlist?list= URLs. /watch?v=X&list=Y stays single-video."""
    return bool(PLAYLIST_URL_RE.search(url)) and '/watch' not in url.lower()


def _cleanup_orphans(out_dir: str, since_ts: float):
    """Remove non-mp3 download artifacts created during this request.

    We use mtime > since_ts so we never touch files from other downloads
    or pre-existing music in the same folder.
    """
    try:
        for fn in os.listdir(out_dir):
            full = os.path.join(out_dir, fn)
            try:
                if not os.path.isfile(full):
                    continue
                if os.path.getmtime(full) < since_ts:
                    continue
            except OSError:
                continue
            low = fn.lower()
            if any(low.endswith(e) for e in JUNK_EXTS):
                try:
                    os.remove(full)
                    logger.info('cleanup: removed %s', fn)
                except OSError:
                    pass
    except OSError:
        pass


def download_url(url: str, out_dir: str) -> tuple[str | None, list[str]]:
    """
    Download a URL (single YouTube video or whole playlist) to mp3.

    Returns (playlist_title_if_any, [mp3_paths]). For single-video URLs the
    first element is None and the list has at most one path. For playlist URLs
    the first element is the playlist's name (useful as the m3u file name)
    and the list has one path per track that downloaded successfully.

    Audio-only streams only — no fallback to muxed video. If a URL has no
    audio-only stream available, yt-dlp will error instead of pulling a
    full video file just to discard the picture afterwards.
    """
    os.makedirs(out_dir, exist_ok=True)
    is_playlist = _is_playlist_url(url)
    started = time.time()

    ffmpeg_path = shutil.which("ffmpeg")
    ffmpeg_location = os.path.dirname(ffmpeg_path) if ffmpeg_path else None

    # Download fragments/temp files to local /tmp to avoid NAS rename failures
    # (CIFS/NFS mounts can fail on concurrent-fragment part-file renames).
    # yt-dlp moves the finished mp3 to out_dir only after conversion completes.
    tmp_dir = "/tmp/ytdlp_dl"
    os.makedirs(tmp_dir, exist_ok=True)

    ydl_opts = {
        # Prefer pure audio streams; fall back to small (≤480p) video if no
        # audio-only stream exists (very old YouTube uploads only ship muxed
        # streams). FFmpegExtractAudio strips the video track during the MP3
        # conversion either way, so the final mp3 is identical — this just
        # keeps the intermediate download small.
        "format": "bestaudio/best[height<=480]/best",
        "noplaylist": not is_playlist,

        "paths": {"home": out_dir, "temp": tmp_dir},
        "outtmpl": "%(title)s.%(ext)s",
        "quiet": True,
        "no_warnings": True,
        "retries": 10,
        "concurrent_fragment_downloads": 5,
        "extractor_args": {"youtube": {"player_client": ["android", "web"]}},

        "writethumbnail": True,
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"},
            {"key": "FFmpegThumbnailsConvertor", "format": "jpg"},
            {"key": "EmbedThumbnail"},
            {"key": "FFmpegMetadata"},
        ],
    }
    if ffmpeg_location:
        ydl_opts["ffmpeg_location"] = ffmpeg_location

    mp3_paths: list[str] = []
    playlist_title: str | None = None

    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

            if is_playlist:
                playlist_title = info.get('title')
                entries = info.get('entries') or []
            else:
                entries = [info]

            for entry in entries:
                if not entry:
                    continue
                fn = ydl.prepare_filename(entry)
                base, _ = os.path.splitext(fn)
                mp3 = base + ".mp3"
                if os.path.exists(mp3):
                    mp3_paths.append(mp3)
    except Exception as e:
        print(f"[ERROR] Download failed: {e}")
    finally:
        _cleanup_orphans(out_dir, started)

    return (playlist_title, mp3_paths)


def download_single(url: str, out_dir: str) -> str | None:
    """Back-compat wrapper for single-video downloads."""
    _, paths = download_url(url, out_dir)
    return paths[0] if paths else None

# ── M3U ────────────────────────────────────────────────────────────────────────

def write_m3u(m3u_name: str, mp3_path: str):
    """Append a track to the named .m3u playlist (create with #EXTM3U if absent).

    The extension passes names like 'MyHits.m3u'; CLI callers may pass 'MyHits'
    — tolerate both. Deduplicates so re-sending the same URL doesn't double-add.
    """
    safe = re.sub(r'[\\/:*?"<>|]', '_', m3u_name)
    if safe.lower().endswith('.m3u'):
        safe = safe[:-4]
    m3u_file = os.path.join(os.path.dirname(mp3_path), safe + '.m3u')
    entry = os.path.basename(mp3_path)

    if os.path.exists(m3u_file):
        try:
            with open(m3u_file, 'r', encoding='utf-8') as f:
                if entry in f.read().splitlines():
                    logger.info('Already in m3u, skipping: %s -> %s', entry, m3u_file)
                    return
        except OSError:
            logger.exception('Could not read m3u %s', m3u_file)
            return

    try:
        new_file = not os.path.exists(m3u_file)
        with open(m3u_file, 'a', encoding='utf-8') as f:
            if new_file:
                f.write('#EXTM3U\n')
            f.write(entry + '\n')
        logger.info('Appended to m3u: %s -> %s', entry, m3u_file)
    except Exception:
        logger.exception('Failed to write m3u %s', m3u_file)


# ── Main ───────────────────────────────────────────────────────────────────────

def main(argv):
    if len(argv) < 2:
        print('Usage: script_web.py <url> [m3u_name]')
        return 2

    url = argv[1]
    m3u = argv[2] if len(argv) > 2 else None

    out_dir = get_config_song_dir()
    logger.info('Downloading %s to %s', url, out_dir)
    playlist_title, downloaded = download_url(url, out_dir)

    if not downloaded:
        logger.error('No file downloaded for %s', url)
        return 1

    # Pick the m3u name: explicit selection wins; otherwise, for YouTube
    # playlist URLs we fall back to the playlist's own title (so sending the
    # /playlist?list= URL with no playlist selected still produces a useful
    # m3u file named after the playlist).
    explicit_m3u = (m3u or '').strip()
    if explicit_m3u and explicit_m3u.lower() != 'default_playlist':
        m3u_name = explicit_m3u
    elif playlist_title:
        m3u_name = playlist_title
    else:
        m3u_name = None

    logger.info('Downloaded %d file(s); m3u=%s', len(downloaded), m3u_name or '(none)')

    for path in downloaded:
        logger.info('  - %s', os.path.basename(path))
        if m3u_name:
            write_m3u(m3u_name, path)
        # Only set a title tag if yt-dlp's FFmpegMetadata didn't already.
        if eyed3:
            try:
                audio = eyed3.load(path)
                if audio and audio.tag is None:
                    audio.initTag()
                if audio and audio.tag is not None and not audio.tag.title:
                    audio.tag.title = os.path.splitext(os.path.basename(path))[0]
                    audio.tag.save()
            except Exception:
                logger.exception('Tagging failed for %s', path)
        # Apply title cleanup via library/tagger
        try:
            if _PROJECT_ROOT not in sys.path:
                sys.path.insert(0, _PROJECT_ROOT)
            from library.tagger import apply_from_config
            apply_from_config(path, _CONFIG_PATH)
        except Exception:
            logger.exception('Title cleanup failed for %s', path)
        # Write WOAS source URL tag
        try:
            from library.tagger import write_source_url
            write_source_url(path, url)
        except Exception:
            logger.exception('WOAS write failed for %s', path)

    trigger_navidrome_scan()
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
