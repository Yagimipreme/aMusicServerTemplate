#!/usr/bin/env python3
"""Receive a single URL + m3u name and download one audio file using yt-dlp.
This script is intended to be invoked by the local server (runpy/run in-thread)
and must be robust when executed inside a PyInstaller bundle.
"""
import sys
import os
import json
import time
import logging
import re
import shutil

ffmpeg_dir = getattr(sys, '_MEIPASS', os.path.abspath("."))

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


def get_config_song_dir():
    appdata = os.getenv('APPDATA') or os.getcwd()
    config_path = os.path.join(appdata, 'MusicServerTemp', 'config.json')
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
            return cfg.get('song_dir') or os.path.join(os.getcwd(), 'Songs')
    except Exception:
        return os.path.join(os.getcwd(), 'Songs')


def get_ffmpeg_location_from_config():
    appdata = os.getenv('APPDATA') or os.getcwd()
    config_path = os.path.join(appdata, 'MusicServerTemp', 'config.json')
    try:
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
                loc = cfg.get('ffmpeg_location')
                if loc:
                    return loc
    except Exception:
        pass
    return None


def download_single(url: str, out_dir: str) -> str | None:
    os.makedirs(out_dir, exist_ok=True)

    # Resolve ffmpeg location: prefer configured path, then PATH lookup
    ffmpeg_location = get_ffmpeg_location_from_config() or shutil.which('ffmpeg')
    if ffmpeg_location and os.path.isfile(ffmpeg_location):
        # if a binary path was returned, use its directory
        ffmpeg_location = os.path.dirname(ffmpeg_location)

    ydl_opts = {
        'format': 'bestaudio/best',
        'noplaylist': True,
        'outtmpl': os.path.join(out_dir, '%(title)s.%(ext)s'),
        'writethumbnail': True,
        'postprocessors': [
            {
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '320',
            }
        ],
        # tell yt-dlp where to find ffmpeg/ffprobe if available
        **({'ffmpeg_location': ffmpeg_dir} if ffmpeg_dir else {}),
        'quiet': True,
        'no_warnings': True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=True)
            # After postprocessing, the final extension should be .mp3
            title = info.get('title') or str(int(time.time()))
            candidate = os.path.join(out_dir, title + '.mp3')
            if os.path.exists(candidate):
                return candidate
            # fallback: pick newest mp3 in out_dir
            mp3s = [os.path.join(out_dir, p) for p in os.listdir(out_dir) if p.lower().endswith('.mp3')]
            if not mp3s:
                return None
            newest = max(mp3s, key=os.path.getctime)
            return newest
        except Exception as e:
            logger.exception('Download failed')
            return None


def write_m3u(m3u_name: str, mp3_path: str):
    try:
        safe = re.sub(r'[\\/:*?"<>|]', '_', m3u_name)
    except Exception:
        safe = m3u_name
    m3u_file = os.path.join(os.path.dirname(mp3_path), safe + '.m3u')
    try:
        with open(m3u_file, 'w', encoding='utf-8') as f:
            f.write('#EXTM3U\n')
            f.write(os.path.basename(mp3_path) + '\n')
        logger.info('Wrote m3u: %s', m3u_file)
    except Exception:
        logger.exception('Failed to write m3u')


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

    logger.info('Downloaded file: %s', downloaded)
    if m3u:
        write_m3u(m3u, downloaded)

    # Optionally tag via eyed3 (best-effort)
    if eyed3:
        try:
            audio = eyed3.load(downloaded)
            if audio and audio.tag is None:
                audio.initTag()
            # Minimal tagging: set title from filename
            if audio and audio.tag is not None:
                audio.tag.title = os.path.splitext(os.path.basename(downloaded))[0]
                audio.tag.save()
        except Exception:
            logger.exception('Tagging failed')

    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))

