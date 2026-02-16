#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# ============================================================
#  sTownload – CSV & Spotify-API Playlist Downloader
# ============================================================

import requests
import yt_dlp
from yt_dlp.utils import PostProcessingError, DownloadError
import logging
import eyed3
import os
import re
import csv

# ============================================================
#  KONFIGURATION
# ============================================================

CSV_MODE = False   # True → nur CSV einlesen; False → Spotify-API Mode

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PLAYLIST_DIR = os.path.join(BASE_DIR, "Playlists")
SONG_DIR = os.path.join(BASE_DIR, "Songs")
VENV_DIR = os.path.join(BASE_DIR, "venv")
TESTING = False
#txt_path = os.path.join(PLAYLIST_DIR, "playlists.txt")
#csv_path = os.path.join(PLAYLIST_DIR, "🏠🏠🏠.csv")

DOWNLOAD_DIR = SONG_DIR  # Ziel für MP3 + Cover

BASE_URL = 'https://spotify-exporter-backend.fly.dev/api/public-playlist'

logger = logging.getLogger(__name__)
logging.basicConfig(filename='example.log', encoding='utf-8', level=logging.DEBUG)


# ============================================================
#  HILFSFUNKTIONEN
# ============================================================

def extract_playlist_id(playlist_url: str) -> str:
    match = re.search(r'playlist/([a-zA-Z0-9]+)', playlist_url)
    if not match:
        raise ValueError("Ungültige Spotify Playlist URL")
    return match.group(1)


def get_playlist_content(playlist_url: str) -> list[dict]:
    playlist_id = extract_playlist_id(playlist_url)
    print("PLAYLIST-ID :",{playlist_id})

    api_url = "https://spotisaver.net/api/get_playlist.php"

    params = {
        "id": playlist_id,
        "type": "playlist",
        "lang": "en"
    }

    headers = {
        "accept": "*/*",
        "referer": f"https://spotisaver.net/en/playlist/{playlist_id}/",
        "user-agent": "Mozilla/5.0"
    }

    response = requests.get(api_url, params=params, headers=headers)
    response.raise_for_status()

    data = response.json()

    # Je nach API-Struktur anpassen:
    tracks = data.get("tracks") or data.get("data") or []

    return tracks

def normalize_api_tracks(api_tracks):
    normalized = []

    for t in api_tracks:
        title = t.get("title") or t.get("name")

        # artists kann Liste von dicts sein
        raw_artists = t.get("artists", [])
        artists = []

        for a in raw_artists:
            if isinstance(a, dict):
                artists.append(a.get("name"))
            else:
                artists.append(str(a))

        # album kann dict sein
        album = ""
        if isinstance(t.get("album"), dict):
            album = t["album"].get("name", "")
        else:
            album = t.get("album", "")

        normalized.append({
            "title": title,
            "album": album,
            "artists": artists
        })

    return normalized


def resource_path(relative_path):
    """ Holt den Pfad für Ressourcen, egal ob EXE oder .py """
    base_path = getattr(sys, '_MEIPASS', os.path.abspath("."))
    return os.path.join(base_path, relative_path)

def sanitize_filename(name: str) -> str:
    """Ungültige Zeichen aus Dateinamen entfernen."""
    return re.sub(r'[^\w\-_. ]', "_", name)


def get_song(search_query: str, output_title: str) -> bool:
    """Download über yt-dlp mit MP3-Konvertierung & Thumbnail."""
    logger.info("Starting yt-dlp on : %s", str(search_query))

    yt_opts = {
        'format': 'bestaudio/best',
        'noplaylist': True,
        'outtmpl': os.path.join(DOWNLOAD_DIR, output_title + ".%(ext)s"),
        'verbose': True,
        'writethumbnail': True,
        'extractor_args': {
            'youtube': {
            'player_client': ['android']
            }
        },

        'postprocessors': [
        {
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '320'
        },
        {
            'key': 'FFmpegThumbnailsConvertor',
            'format': 'jpg'
        },
        {   
            'key': 'EmbedThumbnail'
        },
    ],
    'writethumbnail': True,

    }

    try:
        with yt_dlp.YoutubeDL(yt_opts) as ydl:
            ydl.download([f"ytsearch1:{search_query}"])
        return True

    except (PostProcessingError, DownloadError) as e:
        print("YT-DLP/FFmpeg-Fehler:", e)
        return False

    except Exception as e:
        print("Unerwarteter Fehler:", e)
        return False


def get_playlist(path: str):
    """TXT-Playlist mit Spotify-Links einlesen."""
    logger.info("Reading playlist list: %s", str(path))
    with open(path, 'r', encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]



def write_m3u(playlist_name: str, track_paths: list[str]):
    safe_name = sanitize_filename(playlist_name)
    m3u_path = os.path.join(DOWNLOAD_DIR, safe_name + ".m3u")

    rel_paths = [os.path.relpath(p, DOWNLOAD_DIR) for p in track_paths]

    with open(m3u_path, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for rel in rel_paths:
            f.write(rel + "\n")

    print("Geschriebene Playlist:", m3u_path)



# ============================================================
#  CSV – PLAYLIST EINLESEN
# ============================================================

def get_csv_playlist(csv_path: str):
    """
    CSV einlesen und in dieselbe Struktur bringen wie API-Daten:
    [
      {"title": ..., "album": ..., "artists": [...]},
      ...
    ]
    """
    tracks = []

    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter=',')

        for row in reader:
            title = row.get("Track Name")
            album = row.get("Album Name")
            artists_str = row.get("Artist Name(s)")

            if not title:
                continue  # ungültige Zeile

            artists = []
            if artists_str:
                tmp = re.split(r',|&| feat\.? ', artists_str, flags=re.IGNORECASE)
                artists = [a.strip() for a in tmp if a.strip()]

            tracks.append(
                {
                    "title": title,
                    "album": album or "",
                    "artists": artists,
                }
            )
            print(title)
            print("ALBUM :")
            print(album)
            print("ARTIST :")
            print(artists)
            

    return tracks


def get_csv_name(csv_path: str) -> str:
    """Playlist Name (Dateiname ohne .csv)."""
    base = os.path.basename(csv_path)
    name, _ = os.path.splitext(base)
    return name


# ============================================================
#  ZENTRALE TRACK-VERARBEITUNG (für CSV & API)
# ============================================================

def process_tracks(tracks, playlist_name: str):
    print(f"Gefundene Tracks: {len(tracks)}")
    playlist_track_paths = []

    for track in tracks:
        title = track["title"]
        artists = track.get("artists", [])
        safe_title = sanitize_filename(f"{title} - {artists[0]}" if artists else title)
        
        album = track.get("album", "")
        print(f"INFO : {title} BY {artists}")
        

        logger.info("Extracted : %s | %s | %s", title, artists, album)

        # Suchstring
        search_query = f"{title} {artists[0]}" if artists else title

        print(f"Lade: {title} ({search_query})")
        ok = get_song(search_query, output_title=safe_title)

        audio_path = os.path.join(DOWNLOAD_DIR, safe_title + ".mp3")
        cover_path = os.path.join(DOWNLOAD_DIR, safe_title + ".jpg")

        if (not ok) or (not os.path.exists(audio_path)):
            print(f"Kein Download für '{title}' – übersprungen.")
            continue

        # MP3 laden
        audiofile = eyed3.load(audio_path)
        if audiofile is None:
            print("Konnte MP3 nicht laden:", audio_path)
            continue
        if audiofile.tag is None:
            audiofile.initTag()

        # → Tags setzen
        audiofile.tag.artist = ", ".join(artists) if artists else ""
        audiofile.tag.album = album
        audiofile.tag.title = title
        print(f"INFO : {audiofile.tag.artist}")
        print(f"INFO : {audiofile.tag.title}")
        

        # → Cover setzen
        if os.path.exists(cover_path):
            with open(cover_path, "rb") as img:
                audiofile.tag.images.set(
                    eyed3.id3.frames.ImageFrame.FRONT_COVER,
                    img.read(),
                    "image/jpg"
                )

        audiofile.tag.save()
        if os.path.exists(cover_path):
            try:
                os.remove(cover_path)
                print(f"[CLEANUP] Deleted cover: {cover_path}")
            except Exception as e:
                print(f"[WARNING] Could not delete cover: {e}")
                print(f"ERFOLG : {title} | Album: {album} | Artists: {artists}")

        #playlist_track_paths.append(audio_path)

    # → M3U schreiben
    if playlist_track_paths:
        write_m3u(playlist_name, playlist_track_paths)
    else:
        print("Keine Tracks für Playlist:", playlist_name)


import json

import os
import json
from pathlib import Path


def get_default_music_dir():
    return str(Path.home() / "Music")


def load_config():
    """
    Lädt config.json:
    1. Versucht Projekt-Root (wie bei deinem SC Script)
    2. Fallback: AppData
    3. Default song_dir → ~/Music
    """

    config = {}

    # -------------------------------------------------
    # 1️⃣ Projekt-root config.json
    # -------------------------------------------------
    try:
        base_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "../../")
        )
        print(f"BASEDIR :", base_dir)
        config_path = os.path.join(base_dir, "config.json")
        print(f"CONFIG_PATH :", config_path)

        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
                print(f"[INFO] Config loaded from project: {config_path}")
                return config

    except Exception as e:
        print("[WARN] Projekt-Config nicht geladen:", e)

    # -------------------------------------------------
    # 2️⃣ AppData Fallback
    # -------------------------------------------------
    try:
        appdata_path = os.path.join(
            os.getenv("APPDATA"),
            "MusicServerTemp",
            "config.json"
        )

        if os.path.exists(appdata_path):
            with open(appdata_path, "r", encoding="utf-8") as f:
                config = json.load(f)
                print(f"[INFO] Config loaded from AppData: {appdata_path}")
                return config

    except Exception as e:
        print("[WARN] AppData-Config nicht geladen:", e)

    # -------------------------------------------------
    # 3️⃣ Default config
    # -------------------------------------------------
    print("[WARN] Keine config gefunden – nutze Defaults")

    return {
        "song_dir": get_default_music_dir(),
        "sp_playlist_ids": []
    }



def createPlaylistFileOnly(csv_path: str):
    playlist_name = get_csv_name(csv_path)
    print(f"Playlist-Name aus CSV: {playlist_name}")

    track_paths: list[str] = []

    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter=",")  # ggf. ";" anpassen

        for row in reader:
            title = row.get("Track Name")
            album = row.get("Album Name")
            artist = row.get("Artist Name(s)")

            if not title:
                continue

            print(f"TITLE : {title} , ALBUM : {album}, BY : {artist}")

            # MP3 suchen
            local_song = find(title)
            if local_song is None:
                print(f"Überspringe, Datei nicht gefunden: {title}")
                continue

            # Metadaten hinzufügen
            print(f"GOING TO AUDIOFILE : {local_song}")
            audiofile = eyed3.load(local_song)
            if audiofile is None:
                print(f"Konnte MP3 nicht laden: {local_song}")
                continue

            if audiofile.tag is None:
                audiofile.initTag()

            audiofile.tag.title = title
            audiofile.tag.artist = artist
            audiofile.tag.album = album
            audiofile.tag.save()

            # Für Playlist merken
            track_paths.append(local_song)

    # M3U nur schreiben, wenn wir überhaupt Tracks haben
    if track_paths:
        write_m3u(playlist_name, track_paths)
    else:
        print("Keine Tracks gefunden, keine Playlist erzeugt.")

        

def find(title: str) -> str | None:
    candidate = os.path.join(DOWNLOAD_DIR, title + ".mp3")
    if os.path.exists(candidate):
        print(f"CANDIDATE : {candidate}")
        return candidate
    else:
        print(f"NICHT GEFUNDEN: {candidate}")
        return None


# ============================================================
#  MAIN
# ============================================================

if __name__ == '__main__':

    config = load_config()
    DOWNLOAD_DIR = config.get("song_dir") or get_default_music_dir()

    # -----------------------------------------
    # Playlist IDs aus config laden
    # -----------------------------------------
    playlist_ids = config.get("sp_playlist_ids", [])
    if isinstance(playlist_ids, str):
        playlist_ids = [playlist_ids]

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    # -----------------------------------------
    # TESTING MODE
    # -----------------------------------------
    if TESTING:
        print("[TEST MODE] Nur lokale CSV Verarbeitung")
        csv_path = config.get("csv_path")
        if not csv_path:
            raise ValueError("csv_path fehlt in config.json")

        createPlaylistFileOnly(csv_path)
        print("DONE")
        exit()

    # -----------------------------------------
    # Spotify Playlist IDs via API
    # -----------------------------------------
    if playlist_ids:
        for pid in playlist_ids:
            playlist_url = f"https://open.spotify.com/playlist/{pid}"
            print(f"[INFO] Lade Playlist: {playlist_url}")

            try:
                raw_tracks = get_playlist_content(playlist_url)
                tracks = normalize_api_tracks(raw_tracks)

                if not tracks:
                    print(f"[WARN] Keine Tracks gefunden für {pid}")
                    continue

                playlist_name = f"spotify_{pid}"
                process_tracks(tracks, playlist_name)

            except Exception as e:
                print(f"[ERROR] Fehler bei Playlist {pid}: {e}")

    else:
        print("[WARN] Keine sp_playlist_ids in config.json gefunden.")
