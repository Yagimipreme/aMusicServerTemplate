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


def download_single(url: str, out_dir: str) -> str | None:
    """
    Downloads a single URL as MP3 with embedded thumbnail.
    Works on headless Debian servers.
    Returns path to final mp3 file or None if failed.
    """

    os.makedirs(out_dir, exist_ok=True)

    # Resolve ffmpeg location
    ffmpeg_path = shutil.which("ffmpeg")
    ffmpeg_location = os.path.dirname(ffmpeg_path) if ffmpeg_path else None

    ydl_opts = {
        # Audio selection
        "format": "bestaudio/best",
        "noplaylist": True,

        # Output
        "outtmpl": os.path.join(out_dir, "%(title)s.%(ext)s"),

        # Clean console
        "quiet": True,
        "no_warnings": True,

        # Stability
        "retries": 10,
        "concurrent_fragment_downloads": 5,

        # Extractor tweaks (helps avoid YouTube issues)
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "web"]
            }
        },

        # Post-processing: Convert to MP3 + embed thumbnail
        "writethumbnail": True,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            },
            {
                "key": "EmbedThumbnail",
            },
            {
                "key": "FFmpegMetadata",
            },
        ],
    }

    if ffmpeg_location:
        ydl_opts["ffmpeg_location"] = ffmpeg_location

    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

            # After conversion, extension is always mp3
            filename = ydl.prepare_filename(info)
            base, _ = os.path.splitext(filename)
            final_path = base + ".mp3"

            return final_path if os.path.exists(final_path) else None

    except Exception as e:
        print(f"[ERROR] Download failed: {e}")
        return None

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
    downloaded = download_single(url, out_dir)

    if not downloaded:
        logger.error('No file downloaded for %s', url)
        return 1

    logger.info('Downloaded: %s', downloaded)

    if m3u and m3u.strip() and m3u.strip().lower() != 'default_playlist':
        write_m3u(m3u, downloaded)

    if eyed3:
        try:
            audio = eyed3.load(downloaded)
            if audio and audio.tag is None:
                audio.initTag()
            if audio and audio.tag is not None:
                audio.tag.title = os.path.splitext(os.path.basename(downloaded))[0]
                audio.tag.save()
        except Exception:
            logger.exception('Tagging failed')

    trigger_navidrome_scan()

    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
