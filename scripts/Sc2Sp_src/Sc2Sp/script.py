
import datetime
from selenium.webdriver.common.by import By
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver import ActionChains
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service as ChromeService

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

from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

BASE = "https://soundcloud.com"
client_id = ""
HOT = True

def resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath('.'), relative_path)


def run_ffmpeg_to_mp3(m3u8, mp3_path, art_out_path=None):
    ffmpeg_bin = resource_path('ffmpeg.exe')
    cmd = [
        ffmpeg_bin,
        '-y',
        '-i', m3u8,
        '-c', 'copy',
        '-bsf:a', 'aac_adtstoasc',
        mp3_path,
    ]
    subprocess.run(cmd, check=True, creationflags=subprocess.CREATE_NO_WINDOW)


def get_browser_paths():
    driver_path = ChromeDriverManager().install()
    return None, driver_path


def get_config_path():
    appdata = os.getenv('APPDATA')
    config_dir = os.path.join(appdata or os.getcwd(), 'MusicServerTemp')
    os.makedirs(config_dir, exist_ok=True)
    return os.path.join(config_dir, 'config.json')


def load_config():
    global url, path, topsong, is_timed
    print("[INFO] ITS HOT")
    if HOT:
        try: 
            base_dir = os.path.abspath(
                os.path.join(os.path.dirname(__file__), "../../../")
            )
            config_path = os.path.join(base_dir, "config.json")

            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)

                url = config.get("sc_username")
                path = config.get("path")
                topsong = config.get("sc_topsong")
                is_timed = config.get("is_timed")
                print(f'Config loaded from AppData: url={url}, path={path}')
            

        except Exception as e:
            print("Fehler beim Laden der config:", e)

    filename = get_config_path()
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            config = json.load(f)
            url = config.get('sc_profile', '')
            path = config.get('song_dir', '')
            topsong = config.get('sc_topsong', config.get('topsong', ''))
            print(f'Config loaded from AppData: url={url}, path={path}')
    except FileNotFoundError:
        print(f'CRITICAL: Config not found at {filename}')


def _ensure_dir(p: str) -> str:
    if p is None or str(p).strip() == '':
        raise ValueError("out_dir ist 'None'")
    p = os.path.abspath(os.path.expanduser(str(p).strip()))
    os.makedirs(p, exist_ok=True)
    if not os.path.isdir(p):
        raise FileNotFoundError(f'Download-Pfad existiert nicht: {p}')
    return p


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
    path = unquote(u.path)
    path = re.sub(r'/+', '/', path).rstrip('/')
    return urlunparse((u.scheme, host, path, '', '', ''))


def _norm(s: str) -> str:
    if not s:
        return ''
    if not re.match(r'^https?://', s):
        s = urljoin('https://soundcloud.com', s)
    u = urlparse(s)
    host = u.netloc.lower()
    if host.startswith('www.'):
        host = host[4:]
    path = re.sub(r'/+', '/', unquote(u.path)).rstrip('/')
    return f'{host}{path}'


def get_input():
    print('Enter Playlist or UserLikes :')
    url = input().strip()
    write_to_config(data=url, pos='url')
    return url


def set_spotify_folder():
    path = input('Enter full-path to spotify-locale directory :').strip()
    resolved_path = os.path.abspath(path)
    write_to_config(data=path, pos='path')
    return path


def set_topsong(topsong):
    write_to_config(data=topsong, pos='topsong')


def set_timed():
    pass


def write_to_config(data, pos):
    filename = get_config_path()
    if not os.path.exists(filename):
        config = {}
    else:
        with open(filename, 'r', encoding='utf-8') as f:
            config = json.load(f)
    config[pos] = data
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=4)


def wait_for_download(path, timeout=300):
    seconds = 0
    while True:
        files = glob.glob(os.path.join(path, '*.crdownload'))
        if not files:
            break
        time.sleep(2)
        seconds += 1
        if seconds > timeout:
            raise Exception('Download Timeout')
    print('Download complete')


def scroll(driver):
    ActionChains(driver).scroll_by_amount(0, 1000000).perform()


def scroll_to_btn(driver, btn):
    ActionChains(driver).scroll_to_element(btn)


def _to_abs(href: str) -> str:
    if not href:
        return ''
    href = href.strip()
    return href if href.startswith('http') else (BASE + href)


def get_latest_mp3(download_folder):
    mp3_files = glob.glob(os.path.join(download_folder, '*mp3'))
    if not mp3_files:
        print('No mp3 found for eyed3')
        return None
    latest_mp3 = max(mp3_files, key=os.path.getctime)
    return latest_mp3


def getSongUrl(driver, url, topsong, on_item=None):
    topsong_norm = _norm(topsong) if topsong else None
    print(f'Starting webdriver on :{url}')
    print(f'Topsong :{topsong}')
    #input(f'[DEBUG] using topsong: {topsong_norm} ')
    #input()
    driver.get(url)
    time.sleep(5)
    wait = WebDriverWait(driver, 10)
    '''
    close_button = wait.until(
    EC.element_to_be_clickable((By.CSS_SELECTOR, "div.auth-modal.showBackground button.modal__closeButton"))
    )
    close_button.click()
    
    close_button = driver.find_element(By.CSS_SELECTOR, "div.auth-modal.showBackground button.modal__closeButton")
    driver.execute_script("arguments[0].click();", close_button)
    '''

    is_playlist = '0'
    if is_playlist == '1':
        WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.CSS_SELECTOR, '.sc-px-2x')))
    else:
        WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.CSS_SELECTOR, 'li.soundList__item')))
    time.sleep(1)

    seen_hrefs = set()
    items = []
    max_scrolls = 40
    min_wait_new = 0.5
    wait = WebDriverWait(driver, 10)

    print('[INFO] Starting to look for sc-hrefs.')

    for i in range(max_scrolls):
        if is_playlist == '1':
            link_selector = 'a.trackItem__trackTitle.sc-link-primary[href]'
            anchors = driver.find_elements(By.CSS_SELECTOR, link_selector)
        else:
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
                        print(f'[ERROR] on_item callback failed for {title} / {href} :{e}')
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
            time.sleep(min_wait_new)
            after = len(seen_hrefs)
            if after == before:
                print('No more new items after scroll -> stopping.')
                break

    href_list = [it['href'] for it in items]
    if topsong_norm:
        cut_idx = next((i for i, it in enumerate(items) if _norm(it['href']) == topsong_norm), None)
        if cut_idx is not None:
            items = items[:cut_idx]
            href_list = [it['href'] for it in items]
    return href_list, items, topsong


def make_download_job():
    def job(title, href, out_dir):
        try:
            pass
        except Exception as e:
            print(f'[ERROR] Download of {title} from {href} failed :{e}')
    return job


def submitter(title, href):
    fut = executor.submit(script2.process_track, href, client_id=client_id, out_dir=path, title_override=title)
    #fut = executor.submit(process_track, href, client_id=client_id, out_dir=path, title_override=title)
    futures.append(fut)
    return fut


def slugify(name: str) -> str:
    s = re.sub(r"[^\w\s.-]", "", name).strip().replace(" ", "_")
    return s[:120] or 'track'


def on_item(title, href, out_dir):
    return executor.submit(downloader.process_track, href, client_id, out_dir, title_override=title)


import tempfile
import uuid

def process_track(href: str, client_id: str, out_dir: str = '.', title_override: str | None = None) -> dict:
    print(">>> PROCESS_TRACK CALLED <<<")

    out_dir = str(Path(out_dir).expanduser().resolve())
    os.makedirs(out_dir, exist_ok=True)

    track = resolve_track(href, client_id)
    title = title_override or track.get('title') or 'track'
    base = slugify(title)

    mp3 = os.path.join(out_dir, f'{base}.mp3')
    m3u8 = None
    cover = None

    if os.path.exists(mp3):
        print(f'[SKIP] Already exists: {title}')
        return {
            'title': title,
            'mp3': mp3,
            'cover': None,
            'm3u8': None
        }

    print(f'[PROCESS] Downloading: {title}')

    # 🔥 Thread-sicheres temporäres Cover
    tmp = tempfile.NamedTemporaryFile(
        suffix=f"_{uuid.uuid4().hex}.jpg",
        delete=False
    )
    cover = tmp.name
    tmp.close()

    try:
        transcoding = pick_hls_transcoding(track, art_out_path=cover)

        m3u8 = get_playback_m3u8_url(
            transcoding['url'],
            client_id,
            track.get('track_authorization')
        )

        run_ffmpeg_to_mp3(m3u8, mp3, art_out_path=cover)

    finally:
        # 💣 garantiertes Cleanup (auch bei Exception)
        if cover and os.path.exists(cover):
            try:
                os.remove(cover)
                print(f"[CLEANUP] Deleted cover: {cover}")
            except Exception as e:
                print(f"[WARNING] Could not delete cover: {e}")

    return {
        'title': title,
        'mp3': mp3,
        'cover': None,   # absichtlich None, da temp
        'm3u8': m3u8
    }



executor = ThreadPoolExecutor(max_workers=3)
futures = []


def grab_client_id(driver):
    try:
        driver.get('https://soundcloud.com/user352647366/likes')
        driver.implicitly_wait(5)
        for req in getattr(driver, 'requests', []):
            if (getattr(req, 'host', None) == 'api-v2.soundcloud.com' and getattr(req, 'path', '').startswith('/announcements') and getattr(req, 'response', None)):
                body = req.response.body
                try:
                    data = json.loads(body)
                    print('JSON :', json.dumps(data, indent=2)[:2000])
                except Exception:
                    print('RAW :', body[:2000])
    finally:
        try:
            driver.quit()
        except Exception:
            pass


def grab_client_id2(driver, target_url):
    if target_url is None:
        target = globals().get('url', '') or 'https://soundcloud.com/imshadix-fr/memories-x-bad-and-boujee-x-never-gonna-give-you-up-tiktok-mashup-tibodd-migos-x-david-guetta'
    else:
        target = target_url
    driver.get(target)
    time.sleep(2)
    logs = driver.get_log('performance')
    client_id_local = None
    for entry in logs:
        msg = json.loads(entry['message'])['message']
        method = msg.get('method')
        if method == 'Network.requestWillBeSent':
            req = msg['params']['request']
            url = req.get('url', '')
            if 'api-v2.soundcloud.com/me' in url or 'api-v2.soundcloud.com/announcements' in url or 'api-auth.soundcloud.com/oauth/' in url:
                parsed = urlparse(url)
                qs = parse_qs(parsed.query)
                cid = qs.get('client_id', [None])[0]
                if cid:
                    client_id_local = cid
                    print('FOUND client_id:', client_id_local)
                    return client_id_local
    if client_id_local is None:
        print('Keine client_id in den geloggten Requests gefunden.')
        input('[DEBUG] client_id not found, press enter to exit..')
        sys.exit(1)
    return client_id_local

def get_stats_path():
    stats_dir = Path(os.getenv('APPDATA')) / "MusicServerTemp"
    stats_dir.mkdir(parents=True, exist_ok=True)
    return stats_dir / "stats.json"

def ensure_stats_file():
    stats_path = get_stats_path()
    if not stats_path.exists():
        default_stats = {
            "last_run": None,
            "songs_downloaded": 0,
            "uptime_seconds": 0,
        }
        with open(stats_path, "w", encoding="utf-8") as f:
            json.dump(default_stats, f, indent=4)


def update_stats_after_run(song_count):
        stats_path = get_stats_path()

        with open(stats_path, "r", encoding="utf-8") as f:
            stats = json.load(f)

        stats["last_run"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        stats["songs_downloaded"] += song_count

        with open(stats_path, "w", encoding="utf-8") as f:
            json.dump(stats, f, indent=4)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-s', dest='spotify_dir', help='full path to spotify-local-dir', type=str)
    parser.add_argument('-t', dest='topsong', help='set topsong, script will only download songs listed above', type=str)
    parser.add_argument('-one', dest='one', help="only download one single URL")
    args = parser.parse_args()

    #target_url = args.url if args.url else url
    out_dir = args.spotify_dir if args.spotify_dir else Path

    url = ''
    path = ''
    topsong = ''
    playlist = '0'
    is_timed = False

    load_config()
    

    if args.spotify_dir:
        print(f'Overriding path from args: {args.spotify_dir}')
        path = args.spotify_dir
        write_to_config(path, 'song_dir')
    if args.topsong:
        topsong = args.topsong
        write_to_config(topsong, 'topsong')

    if not path or path.strip() == '':
        path = os.path.join(os.path.expanduser('~'), 'Music', 'aMusicServer')
        print(f'[WARN] No path found, using default: {path}')

    CHROME_BIN, DRIVER_BIN = get_browser_paths()

    if url == '':
        print('No URL set!')
        url = get_input()

    options = webdriver.ChromeOptions()
    if CHROME_BIN and str(CHROME_BIN).strip():
        options.binary_location = str(CHROME_BIN)
    service = Service(executable_path=DRIVER_BIN)
    options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})
    options.add_argument('--disable-popup-blocking')
    options.add_argument('--window-size=1000,1000')
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_argument('--no-sandbox')
    #options.add_argument('--headless=new')
    options.add_experimental_option('excludeSwitches', ['enable-automation'])
    options.add_experimental_option('useAutomationExtension', False)

    prefs = {
        'download.default_directory': os.path.abspath(path),
        'download.prompt_for_download': False,
        'download.directory_upgrade': True,
        'safebrowsing.enabled': True,
        'detach': True,
        'profile.default_content_settings.popups': 0,
    }

    extension_path = resource_path('ublock.crx')
    if os.path.exists(extension_path):
        options.add_extension(extension_path)
    else:
        print(f'[WARN] ublock.crx nicht gefunden unter: {extension_path}')
        input('[DEBUG] ublock-crx missing, press enter to exit..')
        sys.exit(1)

    options.add_experimental_option('prefs', prefs)
    driver = webdriver.Chrome(service=service, options=options)
    driver.execute_cdp_cmd('Network.enable', {})
    client_id = grab_client_id2(driver, "https://soundcloud.com/imshadix-fr/memories-x-bad-and-boujee-x-never-gonna-give-you-up-tiktok-mashup-tibodd-migos-x-david-guetta")
    if args.one:
        submitter('Single', args.one)
        pass

    print('[INFO] Starting new session')
    getSongUrl(driver, url=url, topsong=topsong, on_item=submitter)

    print('[INFO] Downloading songs')
    count = 0
    for f in as_completed(futures):
        try:
            count += 1
            result = f.result()
            print('[OK]', result['title'], '->', result['mp3'])
        except Exception as e:
            print('[ERROR]', e)

    try:
        update_stats_after_run(count)
        driver.quit()
    except Exception:
        pass
    executor.shutdown(wait=True)

