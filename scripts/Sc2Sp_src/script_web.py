#!/usr/bin/env python3
"""Lightweight web entry for SoundCloud requests.
The server will invoke this with: <url> [m3u]
This script uses yt-dlp for SoundCloud URLs (simpler than running the full Selenium flow per-request).
If you prefer the full Selenium pipeline, we can instead call into `Sc2Sp/script.py` routines.
"""
import sys
import os
import json
import time
import logging
import re
import json
import traceback
import time
from pathlib import Path
import requests
from requests.exceptions import HTTPError
import shutil

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service as ChromeService
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    from webdriver_manager.chrome import ChromeDriverManager
except Exception:
    webdriver = None
    ChromeService = None
    ChromeOptions = None
    ChromeDriverManager = None

try:
    import yt_dlp
except Exception:
    print('yt_dlp not available')
    raise

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('sc_download_web')


def get_config_song_dir():
    appdata = os.getenv('APPDATA') or os.getcwd()
    config_path = os.path.join(appdata, 'MusicServerTemp', 'config.json')
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
            return cfg.get('song_dir') or os.path.join(os.getcwd(), 'Songs')
    except Exception:
        return os.path.join(os.getcwd(), 'Songs')


def sanitize_request_url(u: str) -> str:
    """Strip query strings/fragments and normalize SoundCloud urls for resolve.
    E.g. remove "?in=..." which can cause resolve to fail.
    """
    try:
        from urllib.parse import urlparse, urlunparse
        p = urlparse(u)
        # keep scheme + netloc + path only
        clean = urlunparse((p.scheme or 'https', p.netloc, p.path, '', '', ''))
        # remove trailing slashes
        return clean.rstrip('/')
    except Exception:
        return u


def get_launcher_config_path() -> str:
    appdata = os.getenv('APPDATA') or os.getcwd()
    cfg = os.path.join(appdata, 'MusicServerTemp', 'config.json')
    return cfg


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


def persist_client_id(cid: str):
    cfg_path = get_launcher_config_path()
    try:
        if os.path.exists(cfg_path):
            with open(cfg_path, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
        else:
            cfg = {}
        cfg['sc_client_id'] = cid
        with open(cfg_path, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        logger.info('Persisted client_id to config')
    except Exception:
        logger.exception('Failed to persist client_id')


def fetch_client_id_via_selenium(target_url: str | None = None, timeout: int = 20) -> str | None:
    """Start a Chrome instance, capture performance logs and extract client_id param.
    Returns the client_id string or None on failure.
    """
    if webdriver is None or ChromeDriverManager is None:
        logger.warning('Selenium or webdriver_manager not available')
        return None

    options = ChromeOptions()
    options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-gpu')
    options.add_argument('--disable-dev-shm-usage')
    # enable performance logging
    options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})

    try:
        service = ChromeService(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
    except Exception:
        logger.exception('Failed to start ChromeDriver')
        return None

    try:
        target = target_url or 'https://soundcloud.com/'
        logger.info('Selenium navigating to %s', target)
        driver.get(target)
        # wait a bit for network calls
        time.sleep(3)

        logs = []
        try:
            logs = driver.get_log('performance')
        except Exception:
            logger.exception('Failed to get performance logs')

        client_id = None
        for entry in logs:
            try:
                msg = json.loads(entry.get('message', '{}'))
                message = msg.get('message', {})
                method = message.get('method')
                if method == 'Network.requestWillBeSent':
                    req = message.get('params', {}).get('request', {})
                    url = req.get('url', '')
                    if 'api-v2.soundcloud.com' in url and 'client_id=' in url:
                        # parse client_id
                        m = re.search(r'client_id=([A-Za-z0-9_-]+)', url)
                        if m:
                            client_id = m.group(1)
                            logger.info('Found client_id via Selenium: %s', client_id)
                            break
            except Exception:
                continue

        return client_id
    finally:
        try:
            driver.quit()
        except Exception:
            pass


def download_single_soundcloud(url: str, out_dir: str) -> str | None:
    """Use the existing Sc2Sp pipeline to resolve HLS and convert to MP3.
    This calls into the bundled Sc2Sp package's `process_track` implementation
    (which handles resolve -> pick_hls_transcoding -> ffmpeg conversion).
    """
    os.makedirs(out_dir, exist_ok=True)
    try:
        # The server runs this script with cwd set to `scripts/Sc2Sp_src`,
        # and the Sc2Sp package lives in the `Sc2Sp` subfolder. Import accordingly.
        import Sc2Sp.script2 as sc2
    except Exception:
        logger.exception('Failed to import Sc2Sp.script2; falling back to yt_dlp')
        # fallback to yt_dlp approach if the heavy pipeline isn't available
        try:
            # Resolve ffmpeg location: prefer configured path, then PATH lookup
            ffmpeg_location = get_ffmpeg_location_from_config() or shutil.which('ffmpeg')
            if ffmpeg_location and os.path.isfile(ffmpeg_location):
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
                **({'ffmpeg_location': ffmpeg_location} if ffmpeg_location else {}),
                'quiet': True,
                'no_warnings': True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                title = info.get('title') or str(int(time.time()))
                candidate = os.path.join(out_dir, title + '.mp3')
                if os.path.exists(candidate):
                    return candidate
                mp3s = [os.path.join(out_dir, p) for p in os.listdir(out_dir) if p.lower().endswith('.mp3')]
                if not mp3s:
                    return None
                return max(mp3s, key=os.path.getctime)
        except Exception:
            logger.exception('Fallback yt_dlp download failed')
            return None

    # We have sc2 imported; use its `process_track` function. sc2 provides a default `client_id`.
    try:
        # sanitize incoming URL (remove ?in=... and other query/fragments)
        clean_url = sanitize_request_url(url)
        if clean_url != url:
            logger.info('Sanitized URL: %s -> %s', url, clean_url)
            url = clean_url

        client = getattr(sc2, 'client_id', None)
        logger.info('Using Sc2 client_id present: %s', bool(client))
        try:
            ff = sc2.ffmpeg_cmd()
            logger.info('ffmpeg resolved to: %s', ff)
        except Exception:
            logger.exception('ffmpeg not found or ffmpeg_cmd failed')

        logger.info('Calling Sc2.process_track on %s', url)
        try:
            result = sc2.process_track(url, client, out_dir)
        except HTTPError as he:
            code = None
            try:
                code = he.response.status_code
            except Exception:
                pass
            logger.warning('Sc2.process_track raised HTTPError: %s', code)
            # If unauthorized, try to fetch fresh client_id via Selenium and retry once
            if code == 401:
                logger.info('Attempting to fetch fresh client_id via Selenium')
                new_cid = fetch_client_id_via_selenium(target_url=url)
                if new_cid:
                    # persist and update module-level client_id
                    try:
                        persist_client_id(new_cid)
                        sc2.client_id = new_cid
                        client = new_cid
                        logger.info('Retrying process_track with new client_id')
                        result = sc2.process_track(url, client, out_dir)
                    except Exception:
                        logger.exception('Retry with new client_id failed')
                        raise
                else:
                    logger.error('Could not obtain new client_id via Selenium')
                    raise
            else:
                raise

        logger.info('Sc2.process_track result: %s', result)
        mp3 = result.get('mp3')
        if mp3 and os.path.exists(mp3):
            logger.info('MP3 exists: %s', mp3)
        else:
            logger.warning('MP3 not found after process_track: %s', mp3)
        return mp3
    except Exception:
        logger.exception('Sc2 pipeline failed')
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
    # Filtert alle Flags (Strings die mit '-' beginnen) aus den Argumenten
    # argv[0] ist der Skriptname, argv[1:] sind die übergebenen Werte
    clean_args = [a for a in argv[1:] if not a.startswith('-')]

    if len(clean_args) < 1:
        logger.error('Keine URL in den Argumenten gefunden. Erhalten: %s', argv)
        print('Usage: script_web.py <url> [m3u]')
        return 2
    
    # Die URL ist nun das erste Argument, das kein Flag ist
    url = clean_args[0]
    
    # Das M3U-Argument ist das zweite (falls vorhanden)
    m3u = clean_args[1] if len(clean_args) > 1 else None
    
    # Falls das M3U-Argument durch das -one Flag "verschluckt" wurde, 
    # nehmen wir den Wert aus dem ursprünglichen argv, falls dort noch etwas ist
    if not m3u and len(argv) > 3:
         m3u = argv[3]

    out_dir = get_config_song_dir()
    logger.info('SC download request: %s (M3U: %s)', url, m3u)
    
    down = download_single_soundcloud(url, out_dir)
    
    if not down:
        logger.error('No file downloaded for %s', url)
        return 1
        
    logger.info('Downloaded: %s', down)
    if m3u:
        write_m3u(m3u, down)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
