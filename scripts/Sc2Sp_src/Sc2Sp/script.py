
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
import subprocess
from pathlib import Path
import re
import argparse
import glob
from urllib.parse import urlparse, parse_qs

import script2

from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

BASE = "https://soundcloud.com"
client_id = ""

service = Service()

def run_ffmpeg_to_mp3(m3u8, mp3_path, art_out_path=None):
    # Wir holen uns die ffmpeg.exe, die wir in der .spec definiert haben
    ffmpeg_bin = resource_path("ffmpeg.exe")
    
    # Unter Windows sind Anführungszeichen um Pfade wichtig, 
    # subprocess.run mit einer Liste erledigt das sauber für uns.
    cmd = [
        ffmpeg_bin,
        '-y',               # Überschreiben ohne Nachfrage
        '-i', m3u8,         # Input Stream
        '-c', 'copy',       # Falls möglich, Stream nur kopieren (schneller)
        '-bsf:a', 'aac_adtstoasc', # Fix für m3u8 zu mp3/m4a
        mp3_path
    ]
    
    # Falls du das Cover direkt einbetten willst, kämen hier weitere Flags.
    # Für den Anfang reicht der einfache Download:
    subprocess.run(cmd, check=True, creationflags=subprocess.CREATE_NO_WINDOW)

def get_browser_paths():
    # Use manager for WIN-machines
    driver_path = ChromeDriverManager().install()
    return None, driver_path

def resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)

def get_config_path():
    # Dieser Pfad muss exakt identisch mit dem im Launcher sein!
    appdata = os.getenv('APPDATA')
    config_dir = os.path.join(appdata, "MusicServerTemp")
    # Wir stellen sicher, dass der Ordner existiert
    if not os.path.exists(config_dir):
        os.makedirs(config_dir, exist_ok=True)
    return os.path.join(config_dir, "config.json")

def load_config():
    global url, path, topsong, is_timed
    filename = get_config_path()
    try:
        with open(filename, "r", encoding="utf-8") as f:
            config = json.load(f)
            # WICHTIG: Nutze die Keys, die der Launcher schreibt!
            # Im Launcher-Code oben haben wir "sc_profile" und "song_dir" genutzt.
            url = config.get("sc_profile", "") 
            path = config.get("song_dir", "")
            topsong = config.get("topsong", "")
            # ... restliche Logik ...
            print(f"Config loaded from AppData: url={url}, path={path}")
    except FileNotFoundError:
        print(f"CRITICAL: Config not found at {filename}")

from urllib.parse import urljoin, urlparse, urlunparse, unquote

def _ensure_dir(p: str) -> str:
    if p is None or str(p).strip().lower() == "":
        raise ValueError("out_dir ist 'None'")
    p = os.path.abspath(os.path.expanduser(str(p).strip()))
    os.makedirs(p, exist_ok=True)
    if not os.path.isdir(p):
        raise FileNotFoundError(f"Download-Pfad existiert nicht: {p}")
    return p

def _to_abc(href: str, base: str = "https://soundcloud.com") -> str | None:
    """
    Macht aus einem href eine absolute, bereinigte SoundCloud-URL:
    - ignoriert javascript:, mailto:, #...
    - macht relative Links absolut (urljoin mit base)
    - entfernt Query + Fragment
    - normalisiert Host + Pfad
    """
    if not href:
        return None
    href = href.strip()
    if href.startswith(("javascript:", "mailto:", "#")):
        return None

    abs_url = urljoin(base, href)
    u = urlparse(abs_url)

    # Nur SoundCloud-Links zulassen (alles andere ignorieren)
    host = u.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if not host.endswith("soundcloud.com"):
        return None

    path = unquote(u.path)
    path = re.sub(r"/+", "/", path).rstrip("/")  # // → / ; trailing slash weg
    return urlunparse((u.scheme, host, path, "", "", ""))

def _norm(s: str) -> str:
    """
    Normiert eine (evtl. relative) URL für stabile Vergleiche:
    - host ohne www., lowercase
    - nur host + path (ohne query/fragment)
    - Pfad ohne trailing slash, dekodiert, // → /
    """
    if not s:
        return ""
    # Relatives als SoundCloud-URL interpretieren
    if not re.match(r"^https?://", s):
        s = urljoin("https://soundcloud.com", s)
    u = urlparse(s)
    host = u.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = re.sub(r"/+", "/", unquote(u.path)).rstrip("/")
    return f"{host}{path}"

#Set playlist to keep looking at 
def get_input():
    print("Enter Playlist or UserLikes :")
    url = input().strip()
    write_to_config(data=url, pos="url")
    return url

#Set spotify folder to get downloaded songs to 
def set_spotify_folder():
        path = input("Enter full-path to spotify-locale directory :").strip()
        #resolved_path = os.path.expanduser(path)
        resolved_path = os.path.abspath(path)
        write_to_config(data=path, pos="path")
        return path

def set_topsong(topsong):
    write_to_config(data=topsong, pos="topsong")

def set_timed():
    pass

def write_to_config(data, pos):
    filename = get_config_path()
    # Falls Datei fehlt, leeres Gerüst erstellen
    if not os.path.exists(filename):
        config = {}
    else:
        with open(filename, "r", encoding="utf-8") as f:
            config = json.load(f)
            
    config[pos] = data
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=4)

def wait_for_download(path, timeout=300):
    seconds = 0
    while True:
        files = glob.glob(os.path.join(path, "*.crdownload"))
        if not files: 
            break
        time.sleep(2)
        seconds +=1
        if seconds > timeout:
            raise Exception("Download Timeout")
    print("Download complete")

def scroll(driver):
    ActionChains(driver).scroll_by_amount(0, 1000000).perform()

def scroll_to_btn(driver, btn) :
    ActionChains(driver).scroll_to_element(btn)

def _to_abs(href: str) -> str:
    if not href:
        return ""
    href = href.strip()
    return href if href.startswith("http") else (BASE + href)

def _norm(u: str) -> str:
    # einfache Normalisierung für den Vergleich
    return _to_abs(u).rstrip("/")

def get_latest_mp3(download_folder): 
        mp3_files = glob.glob(os.path.join(download_folder, '*mp3'))
        if not mp3_files:
            print("No mp3 found for eyed3")
            return None
        latest_mp3 = max(mp3_files, key=os.path.getctime)
        return latest_mp3

def getSongUrl(driver, url, topsong, on_item=None):
    topsong_norm = _norm(topsong) if topsong else None
    # Initialize the Chrome driver
    print(f"Starting webdriver on :{url}")
    print(f"Topsong :{topsong}")
    driver.get(url)
    time.sleep(3)  # Give time to load the page

    #Playlists use different styling :
    is_playlist = "0"
    if is_playlist == "1":
        WebDriverWait(driver,20).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".sc-px-2x")))
    else :WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.CSS_SELECTOR, "li.soundList__item")))
    time.sleep(1)

    seen_hrefs = set()
    items = []

    max_scrolls = 40
    min_wait_new = 0.5
    wait = WebDriverWait(driver, 10)

    start_time = time.time()
    print("[INFO] Starting to look for sc-hrefs.")

    for i in range(max_scrolls):
        if is_playlist == "1":
            link_selector = "a.trackItem__trackTitle.sc-link-primary[href]"
            anchors = driver.find_elements(By.CSS_SELECTOR, link_selector)
        else: anchors = driver.find_elements(
            By.CSS_SELECTOR, "li.soundList__item a.sc-link-primary[href]"
        )

        found_topsong = False
        for a in anchors:
            try:
                href = _to_abc(a.get_attribute("href"))
                if not href or href in seen_hrefs:
                    continue

                title = a.text.strip()
                seen_hrefs.add(href)
                items.append({"title": title, "href": href})
                print(f"FOUND: {title} -> {href}")

                if on_item:
                    try:
                        on_item(title, href)
                    except Exception as e:
                        print(f"[ERROR] on_item callback failed for {title} / {href} :{e}")

                if topsong_norm and _norm(href) == topsong_norm:
                    print(f"[INFO] Topsong reached: {topsong_norm}")
                    found_topsong = True
                    break
            except Exception as e:
                print(f"Failed extracting href anchor :{e}")
                continue

        if found_topsong:
            break

        #how many before last scroll
        before = len(seen_hrefs)

        #scrollen
        driver.execute_script("window.scrollBy(0, document.body.scrollHeight)")
        time.sleep(random.uniform(2.0, 5.0))
        try:
            wait.until(lambda d: len(d.find_elements(
                By.CSS_SELECTOR, "li.soundList__item a.sc-link-primary[href]"
            )) > len(anchors))
        except Exception:
            time.sleep(min_wait_new)
            after = len(seen_hrefs)
            if after == before :
                print("No more new items after scroll -> stopping.")
                break
    href_list = [it["href"] for it in items]

    if topsong_norm:
        cut_idx = next((i for i, it in enumerate(items) if _norm(it["href"]) == topsong_norm), None)
        if cut_idx is not None:
            items = items[:cut_idx]     # ohne Topsong
            href_list = [it["href"] for it in items]

    return href_list, items, topsong

def make_download_job():
    def job(title, href, out_dir):
        try:
            #script2 krams hier
            pass
        except Exception as e:
            print(f"[ERROR] Download of {title} from {href} failed :{e}")
    return job

def submitter(title, href):
    #fut = executor.submit(make_download_job(), title, href, out_dir=path)
    fut = executor.submit(script2.process_track,href, client_id=client_id, out_dir=path, title_override=title)
    futures.append(fut)
    return fut

def slugify(name: str) -> str:
    s = re.sub(r"[^\w\s.-]", "", name).strip().replace(" ", "_")
    return s[:120] or "track"

def on_item(title, href, out_dir):
    return executor.submit(downloader.process_track, href, client_id, out_dir, title_override=title)

def process_track(href: str, client_id: str, out_dir: str = ".", title_override: str | None = None) -> dict:
    # 1. Pfad normalisieren (Wichtig für Windows!)
    # Verwandelt z.B. "~/Music" oder relative Pfade in saubere Windows-Pfade
    out_dir = str(Path(out_dir).expanduser().resolve())
    os.makedirs(out_dir, exist_ok=True)

    track = resolve_track(href, client_id)
    title = title_override or track.get("title") or "track"
    base = slugify(title)

    cover = os.path.join(out_dir, f"{base}.jpg")
    # Hier sicherstellen, dass pick_hls_transcoding intern auch saubere Pfade nutzt
    transcoding = pick_hls_transcoding(track, art_out_path=cover)
    m3u8 = get_playback_m3u8_url(transcoding["url"], client_id, track.get("track_authorization"))

    mp3 = os.path.join(out_dir, f"{base}.mp3")
    
    if not os.path.exists(mp3):
        print(f"[PROCESS] Downloading: {title}")
        run_ffmpeg_to_mp3(m3u8, mp3, art_out_path=cover)
    else:
        print(f"[SKIP] Already exists: {title}")

    return {"title": title, "mp3": mp3, "cover": cover, "m3u8": m3u8}

executor = ThreadPoolExecutor(max_workers=3)#
futures = []

def grab_client_id(driver):
    try:
        driver.get("https://soundcloud.com/user352647366/likes")
        driver.implicitly_wait(5)

        for req in driver.requests:
            if (
                req.host == "api-v2.soundcloud.com"
                and req.path.startswith("/announcements")
                and req.response
            ) :
                print("URL :", req.url)
                print("Status :", req.response.status_code)
                print("Header :", req.header)
                print()

                body = req.response.body
                try:
                    data = json.loads(body)
                    print("JSON :" ,json.dumps(data, indent=2)[:2000])
                except Exception:
                    print("RAW :", body[:2000])
    finally:
        driver.quit()

def grab_client_id2(driver):
    driver.get("https://soundcloud.com/user352647366/likes")
    logs = driver.get_log("performance")
    #print("Got logs: ",logs)
    client_id = None


    for entry in logs:
        msg = json.loads(entry["message"])["message"]
        method = msg.get("method")

        if method == "Network.requestWillBeSent":
            req = msg["params"]["request"]
            url = req.get("url", "")

            # Debug optional:
            # print("REQ URL:", url)

            # SoundCloud-API-Call, der den client_id-Param enthält
            if "api-v2.soundcloud.com/me" in url or "api-v2.soundcloud.com/announcements" or "api-auth.soundcloud.com/oauth/" in url:
                parsed = urlparse(url)
                qs = parse_qs(parsed.query)
                cid = qs.get("client_id", [None])[0]
                if cid:
                    client_id = cid
                    print("FOUND client_id:", client_id)
                    # wenn du nur einen brauchst, kannst du direkt return:
                    return client_id

    if client_id is None:
        print("Keine client_id in den geloggten Requests gefunden.")
    return client_id

if __name__ == "__main__":
     
    parser = argparse.ArgumentParser()
    parser.add_argument("-s", dest="spotify_dir",help="full path to spotify-local-dir", type=str)
    parser.add_argument("-t", dest="topsong",help="set topsong, script will only download songs listed above", type=str)
    args = parser.parse_args()

    # 1. Variablen Defaults
    url = ""
    path = ""
    topsong = ""
    playlist = "0"
    is_timed = False

    # 2. Load config
    load_config()

    #Parsing arguments
    if args.spotify_dir:
        print(f"Overriding path from args: {args.spotify_dir}")
        path = args.spotify_dir
        write_to_config(path, "song_dir")
    if args.topsong:
        topsong = args.topsong
        write_to_config(topsong, "topsong")

    # 4. Sicherheitscheck für out_dir
    if not path or path.strip() == "":
        # Letzter Rettungsanker: Default-Ordner in AppData oder User-Music
        path = os.path.join(os.path.expanduser("~"), "Music", "aMusicServer")
        print(f"[WARN] No path found, using default: {path}")

    CHROME_BIN, DRIVER_BIN = get_browser_paths()

    if url == "":
        print("No URL set!")
        url = get_input()

    

    #Selenium Chrome Options
    options = webdriver.ChromeOptions()

    if CHROME_BIN and str(CHROME_BIN).strip():
        options.binary_location = str(CHROME_BIN)
    #options.add_argument("--detach")
    service = Service(executable_path=DRIVER_BIN)
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--window-size=1000,1000")
    options.add_argument("--disable-blink-features=AutomationControlled")
    #options.add_argument("--disable-gpu") seems to break under linux
    options.add_argument("--no-sandbox")
    #options.add_argument("--start-maximized")
    #options.add_argument("--headless=new")  
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)

    prefs = {
        "download.default_directory": os.path.abspath(path),
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True,
        "detach": True,
        "profile.default_content_settings.popups": 0
    }

    #Ublock 
    extension_path = resource_path("ublock.crx")
    if os.path.exists(extension_path):
        options.add_extension(extension_path)
    else:
        print(f"[WARN] ublock.crx nicht gefunden unter: {extension_path}")

    options.add_experimental_option("prefs", prefs)

    #Starting webdriver
    driver = webdriver.Chrome(service=service,options=options)
    driver.execute_cdp_cmd("Network.enable", {})
 
    client_id = grab_client_id2(driver)

    #Starting new scraping session
    print("[INFO] Starting new session")
    getSongUrl(driver, url=url, topsong=topsong, on_item=submitter)

    #Pulling songs via ffmpeg

    print("[INFO] Downloading songs")
    for f in as_completed(futures):
        try:
            result = f.result()
            print("[OK]", result["title"], "->", result["mp3"])
        except Exception as e:
            print("[ERROR]", e)

    driver.quit()
executor.shutdown(wait=True)

