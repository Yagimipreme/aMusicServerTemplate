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

CSV_MODE = True   # True → nur CSV einlesen; False → Spotify-API Mode

BASE_DIR = "/home/iqqe/Work/sTownload"
PLAYLIST_DIR = os.path.join(BASE_DIR, "Playlists")
SONG_DIR = os.path.join(BASE_DIR, "Songs2")
VENV_DIR = os.path.join(BASE_DIR, "venv")
TESTING = True
txt_path = os.path.join(PLAYLIST_DIR, "playlists.txt")
csv_path = os.path.join(PLAYLIST_DIR, "🏠🏠🏠.csv")

DOWNLOAD_DIR = SONG_DIR  # Ziel für MP3 + Cover

BASE_URL = 'https://spotify-exporter-backend.fly.dev/api/public-playlist'

logger = logging.getLogger(__name__)
logging.basicConfig(filename='example.log', encoding='utf-8', level=logging.DEBUG)


# ============================================================
#  HILFSFUNKTIONEN
# ============================================================

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
        'postprocessors': [
            {
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '320'
            },
            {
                'format': 'jpg',
                'key': 'FFmpegThumbnailsConvertor',
                'when': 'before_dl'
            },
        ],
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
        safe_title = sanitize_filename(title)
        artists = track.get("artists", [])
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
        print(f"ERFOLG : {title} | Album: {album} | Artists: {artists}")

        playlist_track_paths.append(audio_path)

    # → M3U schreiben
    if playlist_track_paths:
        write_m3u(playlist_name, playlist_track_paths)
    else:
        print("Keine Tracks für Playlist:", playlist_name)





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

    if TESTING :
        createPlaylistFileOnly(csv_path)
        print("DONE")
        exit()

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    # --------------------------------------------------------
    #  CSV-MODUS
    # --------------------------------------------------------
    if CSV_MODE:
        tracks = get_csv_playlist(csv_path)
        playlist_name = get_csv_name(csv_path)
        print(f"CSV-PLAYLIST: {playlist_name}")
        process_tracks(tracks, playlist_name)

    # --------------------------------------------------------
    #  API-MODUS (Spotify Exporter API)
    # --------------------------------------------------------
    else:
        playlists = get_playlist(txt_path)
        print(f"PLAYLIST-URLs: {playlists}")

        for playlist_url in playlists:
            resp = requests.post(url=BASE_URL, json={"playlistUrl": playlist_url})
            resp.raise_for_status()

            data = resp.json()
            playlist_name = data["playlist"]["name"]
            tracks = data.get("tracks", [])

            process_tracks(tracks, playlist_name)

