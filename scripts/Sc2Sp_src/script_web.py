#!/usr/bin/env python3
"""Lightweight web entry for SoundCloud requests.
The server invokes this with: <url> [m3u]
Uses the Sc2Sp pipeline (resolve -> HLS -> ffmpeg) with yt-dlp as fallback.
"""
import sys
import os
import json
import time
import logging
import re
import shutil
from urllib.parse import urlparse, urlunparse
from requests.exceptions import HTTPError
import requests

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


def persist_client_id(cid: str):
    try:
        cfg = _load_config()
        cfg['sc_client_id'] = cid
        with open(_CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        logger.info('Persisted client_id to config')
    except Exception:
        logger.exception('Failed to persist client_id')


# ── URL helpers ────────────────────────────────────────────────────────────────

def sanitize_request_url(u: str) -> str:
    """Strip query strings/fragments so SoundCloud resolve doesn't fail."""
    try:
        p = urlparse(u)
        return urlunparse((p.scheme or 'https', p.netloc, p.path, '', '', '')).rstrip('/')
    except Exception:
        return u


# ── Selenium client_id fetch ───────────────────────────────────────────────────

def fetch_client_id_via_selenium(target_url: str | None = None) -> str | None:
    """Open Chrome headless, sniff network logs for a SoundCloud client_id."""
    if webdriver is None:
        logger.warning('[SC-SELENIUM] Selenium not available')
        return None

    options = ChromeOptions()
    options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-gpu')
    options.add_argument('--disable-dev-shm-usage')
    options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})

    # Prefer the system chromedriver — on Arch/Debian it is kept version-matched
    # to the installed browser by the package manager.  webdriver_manager caches
    # a specific version that can become stale (e.g. v114 vs Chromium 145).
    system_cd = shutil.which('chromedriver')
    if system_cd:
        logger.debug('[SC-SELENIUM] Using system chromedriver: %s', system_cd)
        service = ChromeService(system_cd)
    elif ChromeDriverManager is not None:
        logger.debug('[SC-SELENIUM] System chromedriver not found, using webdriver_manager')
        service = ChromeService(ChromeDriverManager().install())
    else:
        logger.error('[SC-SELENIUM] No chromedriver available')
        return None

    # Also resolve the browser binary explicitly so Selenium finds Chromium
    # even when the system uses a non-standard name (chromium vs google-chrome).
    chromium_bin = (
        shutil.which('chromium') or
        shutil.which('chromium-browser') or
        shutil.which('google-chrome')
    )
    if chromium_bin:
        options.binary_location = chromium_bin
        logger.debug('[SC-SELENIUM] Browser binary: %s', chromium_bin)

    try:
        driver = webdriver.Chrome(service=service, options=options)
    except Exception:
        logger.exception('[SC-SELENIUM] Failed to start ChromeDriver')
        return None

    try:
        # Must be called before navigating; without this Chrome does not
        # record Network.requestWillBeSent events in the performance log.
        driver.execute_cdp_cmd('Network.enable', {})
        logger.debug('[SC-SELENIUM] Network.enable sent')

        target = target_url or 'https://soundcloud.com/'
        logger.debug('[SC-SELENIUM] Navigating to %s', target)
        driver.get(target)
        time.sleep(3)

        client_id = None
        try:
            entries = driver.get_log('performance')
            logger.debug('[SC-SELENIUM] Performance log entries: %d', len(entries))

            api_hits = 0
            for entry in entries:
                msg = json.loads(entry.get('message', '{}')).get('message', {})
                if msg.get('method') == 'Network.requestWillBeSent':
                    url = msg.get('params', {}).get('request', {}).get('url', '')
                    if 'api-v2.soundcloud.com' in url:
                        api_hits += 1
                        if 'client_id=' in url:
                            m = re.search(r'client_id=([A-Za-z0-9_-]+)', url)
                            if m:
                                client_id = m.group(1)
                                logger.info('[SC-SELENIUM] Found client_id: %s', client_id)
                                break

            if client_id is None:
                logger.warning(
                    '[SC-SELENIUM] client_id not found — '
                    'total log entries: %d, api-v2 hits: %d',
                    len(entries), api_hits,
                )
        except Exception:
            logger.exception('[SC-SELENIUM] Failed to read performance logs')

        return client_id
    finally:
        try:
            driver.quit()
        except Exception:
            pass


# ── Download ───────────────────────────────────────────────────────────────────

def download_single_soundcloud(url: str, out_dir: str) -> str | None:
    os.makedirs(out_dir, exist_ok=True)

    try:
        import Sc2Sp.script2 as sc2
    except Exception:
        logger.exception('Failed to import Sc2Sp.script2; falling back to yt-dlp')
        return _yt_dlp_fallback(url, out_dir)

    url = sanitize_request_url(url)
    client = getattr(sc2, 'client_id', None)
    logger.info('Calling Sc2.process_track on %s', url)

    try:
        result = sc2.process_track(url, client, out_dir)
    except HTTPError as he:
        code = getattr(getattr(he, 'response', None), 'status_code', None)
        logger.warning('Sc2.process_track raised HTTPError: %s', code)
        if code == 401:
            logger.info('Fetching fresh client_id via Selenium')
            new_cid = fetch_client_id_via_selenium(target_url=url)
            if new_cid:
                persist_client_id(new_cid)
                sc2.client_id = new_cid
                logger.info('Retrying process_track with new client_id')
                result = sc2.process_track(url, new_cid, out_dir)
            else:
                logger.error('Could not obtain new client_id via Selenium')
                raise
        else:
            raise
    except Exception:
        logger.exception('Sc2 pipeline failed')
        return None

    mp3 = result.get('mp3')
    if mp3 and os.path.exists(mp3):
        logger.info('MP3 ready: %s', mp3)
    else:
        logger.warning('MP3 not found after process_track: %s', mp3)
    return mp3


def _yt_dlp_fallback(url: str, out_dir: str) -> str | None:
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
        'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '320'}],
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
        logger.exception('yt-dlp fallback download failed')
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
    # Strip flags (e.g. -one sent by server) and collect positional args
    clean_args = [a for a in argv[1:] if not a.startswith('-')]

    if len(clean_args) < 1:
        print('Usage: script_web.py <url> [m3u]')
        return 2

    url = clean_args[0]
    m3u = clean_args[1] if len(clean_args) > 1 else None

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
