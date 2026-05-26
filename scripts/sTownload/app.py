#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# sTownload – Spotify Playlist Downloader

import yt_dlp
from yt_dlp.utils import PostProcessingError, DownloadError
import logging
import eyed3
import os
import re
import csv
import json
import glob
from pathlib import Path


# ── Paths ──────────────────────────────────────────────────────────────────────

BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, "../../"))

# Updated from config in __main__
DOWNLOAD_DIR  = os.path.join(BASE_DIR, "Songs")
PLAYLISTS_DIR = os.path.join(PROJECT_ROOT, "playlists")

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
        "playlists_dir": "",
    }


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


# ── Playlist CSV parsing ──────────────────────────────────────────────────────
#
# Bulk Spotify is driven by per-playlist CSV files. Export each playlist once
# via https://exportify.app (or any tool that produces the same column set),
# drop the .csv files into the playlists/ directory. We accept the Exportify
# schema (`Track Name`, `Artist Name(s)`, `Album Name`) and, as a fallback,
# a simple `title,artists,album` column set.

EXPORTIFY_COLS = ("Track Name", "Artist Name(s)", "Album Name")
SIMPLE_COLS    = ("title", "artists", "album")


def get_csv_playlist(csv_path: str) -> list[dict]:
    """Read one CSV file and return a list of {title, album, artists[]} dicts."""
    tracks = []
    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter=',')
        if not reader.fieldnames:
            return tracks

        # Pick the schema based on which title column is present
        if "Track Name" in reader.fieldnames:
            title_col, artists_col, album_col = EXPORTIFY_COLS
        elif "title" in reader.fieldnames:
            title_col, artists_col, album_col = SIMPLE_COLS
        else:
            print(f"  [WARN] {os.path.basename(csv_path)} has no recognized title "
                  f"column ({EXPORTIFY_COLS[0]!r} or {SIMPLE_COLS[0]!r}); skipping")
            return tracks

        for row in reader:
            title = (row.get(title_col) or "").strip()
            if not title:
                continue
            artists_str = (row.get(artists_col) or "").strip()
            artists = (
                [a.strip() for a in re.split(r',|&| feat\.? ', artists_str, flags=re.IGNORECASE) if a.strip()]
                if artists_str else []
            )
            tracks.append({
                "title":   title,
                "album":   (row.get(album_col) or "").strip(),
                "artists": artists,
            })
    return tracks


def get_csv_name(csv_path: str) -> str:
    return os.path.splitext(os.path.basename(csv_path))[0]


def discover_playlists(playlists_dir: str) -> list[str]:
    """Return all .csv files under playlists_dir, sorted, ignoring dotfiles."""
    if not playlists_dir or not os.path.isdir(playlists_dir):
        return []
    found = sorted(glob.glob(os.path.join(playlists_dir, "*.csv")))
    return [p for p in found if not os.path.basename(p).startswith(".")]


# ── Main ───────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    config = load_config()
    DOWNLOAD_DIR = config.get("song_dir") or get_default_music_dir()
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    print(f"[INFO] Song dir: {DOWNLOAD_DIR}")

    PLAYLISTS_DIR = config.get("playlists_dir") or PLAYLISTS_DIR
    print(f"[INFO] Playlists dir: {PLAYLISTS_DIR}")

    csv_files = discover_playlists(PLAYLISTS_DIR)
    if not csv_files:
        print(f"[WARN] No CSV playlists found in {PLAYLISTS_DIR} — nothing to do.")
        print( "       Export your Spotify playlists at https://exportify.app")
        print( "       and drop the .csv files into that folder.")
        exit()

    print(f"[INFO] Found {len(csv_files)} playlist CSV(s)")

    for csv_path in csv_files:
        name = get_csv_name(csv_path)
        print(f"\n[INFO] Loading playlist: {name}  ({csv_path})")
        try:
            tracks = get_csv_playlist(csv_path)
            if not tracks:
                print(f"[WARN] No tracks parsed from {name}")
                continue
            process_tracks(tracks, name)
        except Exception as e:
            print(f"[ERROR] Failed for playlist {name}: {e}")
