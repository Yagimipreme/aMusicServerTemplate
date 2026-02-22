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


# ── Download ───────────────────────────────────────────────────────────────────

def download_single(url: str, out_dir: str) -> str | None:
    os.makedirs(out_dir, exist_ok=True)

    ffmpeg_location = get_ffmpeg_location_from_config() or shutil.which('ffmpeg')
    if ffmpeg_location and os.path.isfile(ffmpeg_location):
        ffmpeg_location = os.path.dirname(ffmpeg_location)

    ydl_opts = {
        'format': 'bestaudio/best',
        'noplaylist': True,
        'outtmpl': os.path.join(out_dir, '%(title)s.%(ext)s'),
        'writethumbnail': True,
        'quiet': True,
        'no_warnings': True,
        'postprocessors': [
            {'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '320'},
        ],
        **({'ffmpeg_location': ffmpeg_location} if ffmpeg_location else {}),
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get('title') or str(int(time.time()))
            candidate = os.path.join(out_dir, title + '.mp3')
            if os.path.exists(candidate):
                return candidate
            mp3s = [os.path.join(out_dir, p) for p in os.listdir(out_dir) if p.lower().endswith('.mp3')]
            return max(mp3s, key=os.path.getctime) if mp3s else None
    except Exception:
        logger.exception('Download failed')
        return None


# ── M3U ────────────────────────────────────────────────────────────────────────

def write_m3u(m3u_name: str, mp3_path: str):
    safe = re.sub(r'[\\/:*?"<>|]', '_', m3u_name)
    m3u_file = os.path.join(os.path.dirname(mp3_path), safe + '.m3u')
    try:
        with open(m3u_file, 'w', encoding='utf-8') as f:
            f.write('#EXTM3U\n')
            f.write(os.path.basename(mp3_path) + '\n')
        logger.info('Wrote m3u: %s', m3u_file)
    except Exception:
        logger.exception('Failed to write m3u')


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

    if m3u:
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

    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
