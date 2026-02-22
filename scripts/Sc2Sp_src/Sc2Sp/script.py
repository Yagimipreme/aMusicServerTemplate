import datetime
from selenium.webdriver.common.by import By
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver import ActionChains
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import csv
import os
import subprocess
import shutil
import json
import random
import sys
from pathlib import Path
import re
import argparse
import glob
from urllib.parse import urlparse, parse_qs, urljoin, urlunparse, unquote

import script2

# ── Paths ──────────────────────────────────────────────────────────────────────

_SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "../../../"))
_CONFIG_PATH  = os.path.join(_PROJECT_ROOT, "config.json")

# ── Constants ──────────────────────────────────────────────────────────────────

BASE      = "https://soundcloud.com"
client_id = ""
HOT       = True

# ── Config ─────────────────────────────────────────────────────────────────────

def get_config_path() -> str:
    return _CONFIG_PATH


def load_config():
    global url, path, topsong, is_timed
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)
        url      = config.get("sc_username", "")
        path     = config.get("song_dir") or str(Path.home() / "Music")
        topsong  = config.get("sc_topsong", "")
        is_timed = config.get("is_timed", False)
        print(f"[INFO] Config loaded: url={url}, path={path}")
    except Exception as e:
        print(f"[WARN] Could not load config: {e}")


def write_to_config(data, pos):
    try:
        if os.path.exists(_CONFIG_PATH):
            with open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
                config = json.load(f)
        else:
            config = {}
        config[pos] = data
        with open(_CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"[WARN] Could not write config: {e}")


# ── Stats ──────────────────────────────────────────────────────────────────────

def get_stats_path() -> Path:
    stats_dir = Path(_PROJECT_ROOT) / "logs"
    stats_dir.mkdir(parents=True, exist_ok=True)
    return stats_dir / "stats.json"


def ensure_stats_file():
    stats_path = get_stats_path()
    if not stats_path.exists():
        with open(stats_path, "w", encoding="utf-8") as f:
            json.dump({"last_run": None, "songs_downloaded": 0, "uptime_seconds": 0}, f, indent=4)


def update_stats_after_run(song_count):
    stats_path = get_stats_path()
    try:
        with open(stats_path, "r", encoding="utf-8") as f:
            stats = json.load(f)
        stats["last_run"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        stats["songs_downloaded"] += song_count
        with open(stats_path, "w", encoding="utf-8") as f:
            json.dump(stats, f, indent=4)
    except Exception as e:
        print(f"[WARN] Could not update stats: {e}")


# ── URL helpers ────────────────────────────────────────────────────────────────

def _to_abc(href: str, base: str = BASE) -> str | None:
    if not href:
        return None
    href = href.strip()
    if href.startswith(('javascript:', 'mailto:', '#')):
        return None
    abs_url = urljoin(base, href)
    u = urlparse(abs_url)
    host = u.netloc.lower()
    if host.startswith('www.'):
        host = host[4:]
    if not host.endswith('soundcloud.com'):
        return None
    p = unquote(u.path)
    p = re.sub(r'/+', '/', p).rstrip('/')
    return urlunparse((u.scheme, host, p, '', '', ''))


def _norm(s: str) -> str:
    if not s:
        return ''
    if not re.match(r'^https?://', s):
        s = urljoin('https://soundcloud.com', s)
    u = urlparse(s)
    host = u.netloc.lower()
    if host.startswith('www.'):
        host = host[4:]
    p = re.sub(r'/+', '/', unquote(u.path)).rstrip('/')
    return f'{host}{p}'


def _to_abs(href: str) -> str:
    if not href:
        return ''
    href = href.strip()
    return href if href.startswith('http') else (BASE + href)


# ── Input helpers ──────────────────────────────────────────────────────────────

def get_input():
    print('Enter Playlist or UserLikes URL:')
    u = input().strip()
    write_to_config(data=u, pos='sc_username')
    return u


def get_latest_mp3(download_folder):
    mp3_files = glob.glob(os.path.join(download_folder, '*.mp3'))
    if not mp3_files:
        print('No mp3 found')
        return None
    return max(mp3_files, key=os.path.getctime)


# ── Download jobs ──────────────────────────────────────────────────────────────

executor = ThreadPoolExecutor(max_workers=3)
futures  = []


def submitter(title, href):
    fut = executor.submit(script2.process_track, href, client_id=client_id, out_dir=path, title_override=title)
    futures.append(fut)
    return fut


# ── Selenium helpers ───────────────────────────────────────────────────────────

def scroll(driver):
    ActionChains(driver).scroll_by_amount(0, 1000000).perform()


def grab_client_id2(driver, target_url):
    if target_url is None:
        target = globals().get('url', '') or 'https://soundcloud.com/'
    else:
        target = target_url
    driver.get(target)
    time.sleep(2)
    logs = driver.get_log('performance')
    for entry in logs:
        msg = json.loads(entry['message'])['message']
        if msg.get('method') == 'Network.requestWillBeSent':
            req_url = msg['params']['request'].get('url', '')
            if 'api-v2.soundcloud.com' in req_url and 'client_id=' in req_url:
                parsed = urlparse(req_url)
                cid = parse_qs(parsed.query).get('client_id', [None])[0]
                if cid:
                    print('Found client_id:', cid)
                    return cid
    print('[WARN] client_id not found in network logs.')
    return None


def getSongUrl(driver, url, topsong, on_item=None):
    topsong_norm = _norm(topsong) if topsong else None
    print(f'Starting webdriver on: {url}')
    driver.get(url)
    time.sleep(5)

    WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.CSS_SELECTOR, 'li.soundList__item')))
    time.sleep(1)

    seen_hrefs = set()
    items      = []
    wait       = WebDriverWait(driver, 10)

    for _ in range(40):
        anchors = driver.find_elements(By.CSS_SELECTOR, 'li.soundList__item a.sc-link-primary[href]')
        found_topsong = False

        for a in anchors:
            try:
                href = _to_abc(a.get_attribute('href'))
                if not href or href in seen_hrefs:
                    continue
                title = a.text.strip()
                seen_hrefs.add(href)
                items.append({'title': title, 'href': href})
                print(f'FOUND: {title} -> {href}')
                if on_item:
                    try:
                        on_item(title, href)
                    except Exception as e:
                        print(f'[ERROR] on_item failed for {title}: {e}')
                if topsong_norm and _norm(href) == topsong_norm:
                    print(f'[INFO] Topsong reached: {topsong_norm}')
                    found_topsong = True
                    break
            except Exception:
                continue

        if found_topsong:
            break

        before = len(seen_hrefs)
        driver.execute_script('window.scrollBy(0, document.body.scrollHeight)')
        time.sleep(random.uniform(2.0, 5.0))
        try:
            wait.until(lambda d: len(d.find_elements(By.CSS_SELECTOR, 'li.soundList__item a.sc-link-primary[href]')) > len(anchors))
        except Exception:
            if len(seen_hrefs) == before:
                print('No new items after scroll -> stopping.')
                break

    if topsong_norm:
        cut = next((i for i, it in enumerate(items) if _norm(it['href']) == topsong_norm), None)
        if cut is not None:
            items = items[:cut]

    return [it['href'] for it in items], items, topsong


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-s', dest='spotify_dir', help='override download directory', type=str)
    parser.add_argument('-t', dest='topsong', help='stop at this song URL', type=str)
    parser.add_argument('-one', dest='one', help='download a single URL only')
    args = parser.parse_args()

    url      = ''
    path     = ''
    topsong  = ''
    is_timed = False

    load_config()

    if args.spotify_dir:
        print(f'Overriding path: {args.spotify_dir}')
        path = args.spotify_dir
        write_to_config(path, 'song_dir')
    if args.topsong:
        topsong = args.topsong
        write_to_config(topsong, 'sc_topsong')

    if not path or path.strip() == '':
        path = str(Path.home() / 'Music')
        print(f'[WARN] No path in config, using default: {path}')

    os.makedirs(path, exist_ok=True)

    if url == '':
        print('No sc_username set in config!')
        url = get_input()

    # Chrome setup — prefer system chromedriver (version-matched by pacman)
    # over webdriver_manager's cached binary which can lag behind Chromium.
    system_cd = shutil.which('chromedriver')
    if system_cd:
        service = Service(executable_path=system_cd)
    else:
        service = Service(executable_path=ChromeDriverManager().install())

    options = webdriver.ChromeOptions()
    options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-gpu')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--window-size=1280,900')
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_experimental_option('excludeSwitches', ['enable-automation'])
    options.add_experimental_option('useAutomationExtension', False)
    options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})
    options.add_experimental_option('prefs', {
        'download.default_directory': os.path.abspath(path),
        'download.prompt_for_download': False,
        'download.directory_upgrade': True,
        'safebrowsing.enabled': True,
    })

    chromium_bin = (
        shutil.which('chromium') or
        shutil.which('chromium-browser') or
        shutil.which('google-chrome')
    )
    if chromium_bin:
        options.binary_location = chromium_bin

    ublock_path = os.path.join(_PROJECT_ROOT, 'ublock.crx')
    if os.path.exists(ublock_path):
        options.add_extension(ublock_path)
    else:
        print(f'[WARN] ublock.crx not found at {ublock_path}, continuing without it.')

    driver = webdriver.Chrome(service=service, options=options)
    driver.execute_cdp_cmd('Network.enable', {})

    client_id = grab_client_id2(driver, url) or script2.client_id

    if args.one:
        submitter('Single', args.one)
    else:
        getSongUrl(driver, url=url, topsong=topsong, on_item=submitter)

    print('[INFO] Waiting for downloads to finish...')
    count = 0
    for f in as_completed(futures):
        try:
            result = f.result()
            print('[OK]', result['title'], '->', result['mp3'])
            count += 1
        except Exception as e:
            print('[ERROR]', e)

    try:
        ensure_stats_file()
        update_stats_after_run(count)
        driver.quit()
    except Exception:
        pass

    executor.shutdown(wait=True)
