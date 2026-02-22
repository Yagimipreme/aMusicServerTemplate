#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# sTownload – Spotify Playlist Downloader

import requests
import yt_dlp
from yt_dlp.utils import PostProcessingError, DownloadError
import logging
import eyed3
import os
import re
import csv
import json
from pathlib import Path


# ── Paths ──────────────────────────────────────────────────────────────────────

BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
PLAYLIST_DIR = os.path.join(BASE_DIR, "Playlists")

TESTING  = False
CSV_MODE = False

# Updated from config in __main__
DOWNLOAD_DIR = os.path.join(BASE_DIR, "Songs")

logger = logging.getLogger(__name__)
logging.basicConfig(
    filename=os.path.join(BASE_DIR, 'example.log'),
    encoding='utf-8',
    level=logging.DEBUG,
)


# ── Config ─────────────────────────────────────────────────────────────────────

def get_default_music_dir() -> str:
    return str(Path.home() / "Music")


def load_config() -> dict:
    """Load config.json from the project root (two levels up from this script)."""
    config_path = os.path.abspath(os.path.join(BASE_DIR, "../../config.json"))
    try:
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            print(f"[INFO] Config loaded from: {config_path}")
            return config
    except Exception as e:
        print(f"[WARN] Could not load config: {e}")

    print("[WARN] No config found – using defaults")
    return {
        "song_dir": get_default_music_dir(),
        "sp_playlist_ids": [],
    }


# ── Spotify helpers ────────────────────────────────────────────────────────────

def extract_playlist_id(playlist_url: str) -> str:
    match = re.search(r'playlist/([a-zA-Z0-9]+)', playlist_url)
    if not match:
        raise ValueError(f"Invalid Spotify playlist URL: {playlist_url}")
    return match.group(1)


def get_playlist_content(playlist_url: str) -> list[dict]:
    playlist_id = extract_playlist_id(playlist_url)
    print(f"PLAYLIST-ID: {playlist_id}")

    response = requests.get(
        "https://spotisaver.net/api/get_playlist.php",
        params={"id": playlist_id, "type": "playlist", "lang": "en"},
        headers={
            "accept": "*/*",
            "referer": f"https://spotisaver.net/en/playlist/{playlist_id}/",
            "user-agent": "Mozilla/5.0",
        },
    )
    response.raise_for_status()
    data = response.json()
    return data.get("tracks") or data.get("data") or []


def normalize_api_tracks(api_tracks: list) -> list[dict]:
    normalized = []
    for t in api_tracks:
        title = t.get("title") or t.get("name")
        raw_artists = t.get("artists", [])
        artists = [a.get("name") if isinstance(a, dict) else str(a) for a in raw_artists]
        album = t["album"].get("name", "") if isinstance(t.get("album"), dict) else t.get("album", "")
        normalized.append({"title": title, "album": album, "artists": artists})
    return normalized


# ── Download ───────────────────────────────────────────────────────────────────

def sanitize_filename(name: str) -> str:
    return re.sub(r'[^\w\-_. ]', "_", name)


def get_song(search_query: str, output_title: str) -> bool:
    logger.info("Starting yt-dlp on: %s", search_query)

    yt_opts = {
        'format': 'bestaudio/best',
        'noplaylist': True,
        'outtmpl': os.path.join(DOWNLOAD_DIR, output_title + ".%(ext)s"),
        'writethumbnail': True,
        'quiet': True,
        'no_warnings': True,
        'extractor_args': {'youtube': {'player_client': ['android']}},
        'postprocessors': [
            {'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '320'},
            {'key': 'FFmpegThumbnailsConvertor', 'format': 'jpg'},
            {'key': 'EmbedThumbnail'},
        ],
    }

    try:
        with yt_dlp.YoutubeDL(yt_opts) as ydl:
            ydl.download([f"ytsearch1:{search_query}"])
        return True
    except (PostProcessingError, DownloadError) as e:
        print(f"yt-dlp error: {e}")
        return False
    except Exception as e:
        print(f"Unexpected error: {e}")
        return False


def process_tracks(tracks: list[dict], playlist_name: str):
    print(f"Tracks found: {len(tracks)}")
    track_paths = []

    for track in tracks:
        title   = track["title"]
        artists = track.get("artists", [])
        album   = track.get("album", "")
        safe_title   = sanitize_filename(f"{title} - {artists[0]}" if artists else title)
        search_query = f"{title} {artists[0]}" if artists else title

        print(f"Downloading: {title} by {', '.join(artists)}")
        ok = get_song(search_query, output_title=safe_title)

        audio_path = os.path.join(DOWNLOAD_DIR, safe_title + ".mp3")
        cover_path = os.path.join(DOWNLOAD_DIR, safe_title + ".jpg")

        if not ok or not os.path.exists(audio_path):
            print(f"  Skipped (no download): {title}")
            continue

        # Tag the MP3
        audiofile = eyed3.load(audio_path)
        if audiofile is not None:
            if audiofile.tag is None:
                audiofile.initTag()
            audiofile.tag.title  = title
            audiofile.tag.artist = ", ".join(artists) if artists else ""
            audiofile.tag.album  = album
            audiofile.tag.save()

        # Clean up cover
        if os.path.exists(cover_path):
            try:
                os.remove(cover_path)
            except Exception as e:
                print(f"  [WARNING] Could not delete cover: {e}")

        track_paths.append(audio_path)
        print(f"  Done: {safe_title}.mp3")

    if track_paths:
        write_m3u(playlist_name, track_paths)

    print(f"Finished playlist: {playlist_name}")


# ── M3U ────────────────────────────────────────────────────────────────────────

def write_m3u(playlist_name: str, track_paths: list[str]):
    m3u_path = os.path.join(DOWNLOAD_DIR, sanitize_filename(playlist_name) + ".m3u")
    with open(m3u_path, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for p in track_paths:
            f.write(os.path.relpath(p, DOWNLOAD_DIR) + "\n")
    print(f"Playlist written: {m3u_path}")


# ── CSV helpers (TESTING mode only) ───────────────────────────────────────────

def get_csv_playlist(csv_path: str) -> list[dict]:
    tracks = []
    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f, delimiter=','):
            title = row.get("Track Name")
            if not title:
                continue
            artists_str = row.get("Artist Name(s)", "")
            artists = (
                [a.strip() for a in re.split(r',|&| feat\.? ', artists_str, flags=re.IGNORECASE) if a.strip()]
                if artists_str else []
            )
            tracks.append({"title": title, "album": row.get("Album Name", ""), "artists": artists})
    return tracks


def get_csv_name(csv_path: str) -> str:
    return os.path.splitext(os.path.basename(csv_path))[0]


# ── Main ───────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    config = load_config()
    DOWNLOAD_DIR = config.get("song_dir") or get_default_music_dir()
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    print(f"[INFO] Song dir: {DOWNLOAD_DIR}")

    if TESTING:
        print("[TEST MODE] CSV only")
        csv_path = config.get("csv_path")
        if not csv_path:
            raise ValueError("csv_path missing in config.json")
        process_tracks(get_csv_playlist(csv_path), get_csv_name(csv_path))
        exit()

    playlist_ids = config.get("sp_playlist_ids", [])
    if isinstance(playlist_ids, str):
        playlist_ids = [playlist_ids]

    if not playlist_ids:
        print("[WARN] No sp_playlist_ids in config.json – nothing to do.")
        exit()

    for pid in playlist_ids:
        playlist_url = f"https://open.spotify.com/playlist/{pid}"
        print(f"\n[INFO] Loading playlist: {playlist_url}")
        try:
            raw_tracks = get_playlist_content(playlist_url)
            tracks = normalize_api_tracks(raw_tracks)
            if not tracks:
                print(f"[WARN] No tracks found for {pid}")
                continue
            process_tracks(tracks, f"spotify_{pid}")
        except Exception as e:
            print(f"[ERROR] Failed for playlist {pid}: {e}")
