import ffmpeg
import subprocess
import requests
import shlex
import re
import os
from shutil import which

#url = f"https://api-v2.soundcloud.com/media/soundcloud:tracks:2151272385/164bb1b9-1b7b-4680-af50-ed4bec1efd93/stream/hls?client_id={client_id}"
#curl this all and get : 
stream_url="https://playback.media-streaming.soundcloud.cloud/9y6JyzKZ85h5/aac_160k/48e9dab2-d0c8-404a-8cbf-df7334076c3a/playlist.m3u8?expires=1757490458&Policy=eyJTdGF0ZW1lbnQiOlt7IlJlc291cmNlIjoiaHR0cHM6Ly9wbGF5YmFjay5tZWRpYS1zdHJlYW1pbmcuc291bmRjbG91ZC5jbG91ZC85eTZKeXpLWjg1aDUvYWFjXzE2MGsvNDhlOWRhYjItZDBjOC00MDRhLThjYmYtZGY3MzM0MDc2YzNhL3BsYXlsaXN0Lm0zdTg~ZXhwaXJlcz0xNzU3NDkwNDU4IiwiQ29uZGl0aW9uIjp7IkRhdGVMZXNzVGhhbiI6eyJBV1M6RXBvY2hUaW1lIjoxNzU3NDkwNDU4fX19XX0_&Signature=Otz4-v2LJB2nfpbY7n6UeEj9C7oy2u8oXo20V3XJzzNdcBUTnonjJsowPgmKR8u~YP~JLnGXSY5cei89Nvyhv93TFO6tMHz-i1FXf0dkvrI2mQXzSIUFNoNOQVHf~9Gnd866rW~SjAxkNi0gDPO8Bk0Df7eJvXIJVefDdp5954yPJXuMjfrkmaMGktcHvPfoRmDtGCocr21QhZtgnqWg9BKa~dkeYUdPCdxNXJUpcl6KFR60d155xlE-hMi0zCLmWs4BHfgD4p8hxtQurpwQvPn~0-KIfXjI7YZjw7jvAh7JrAAbgDMUKZ9KSPxPENySllAgjkQXCSo0IGTeD9pe0A__&Key-Pair-Id=K34606QXLEIRF3"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116 Safari/537.36"
FFMPEG = "ffmpeg"
client_id = "V8Xd6biqlKU0Xeyo7T2IHUzBAyrMJWjx"

def slugify(name: str) -> str:
    s = re.sub(r"[^\w\s.-]", "", name).strip().replace(" ", "_")
    return s[:120] or "track"


def ensure_unique_path(path: str) -> str:
    if not os.path.exists(path):
        return path
    root, ext = os.path.splitext(path)
    i = 1
    while True:
        cand = f"{root} ({i}){ext}"
        if not os.path.exists(cand):
            return cand
        i += 1

def resolve_track(track_page_url: str, client_id: str) -> dict:
    print(f"[DEBUG] Resolving track url: {track_page_url} with client_id {client_id}")
    """Gibt die Track-JSON zurück (inkl. media.transcodings und evtl. track_authorization)."""
    r = requests.get(
        "https://api-v2.soundcloud.com/resolve",
        params={"url": track_page_url, "client_id": client_id},
        headers={"User-Agent": USER_AGENT},
        timeout=20,
    )
    r.raise_for_status()
    return r.json()

def pick_hls_transcoding(track_json: dict, art_out_path: str | None = None):
    # --- HLS-Transcoding wählen ---
    trans = (track_json.get("media") or {}).get("transcodings", [])
    preferred = [t for t in trans if t.get("format", {}).get("protocol") == "hls" and "aac_160" in t.get("preset","")]
    hls = preferred[0] if preferred else next((t for t in trans if t.get("format", {}).get("protocol") == "hls"), None)
    if not hls:
        raise RuntimeError("Kein HLS-Transcoding gefunden.")
    #print(track_json)
    # --- Artwork optional herunterladen ---
    if art_out_path:
        art_url = track_json.get("artwork_url") or (track_json.get("user") or {}).get("avatar_url")
        print(f"[INFO] ART_URL : {art_url}" )
        
        if art_url:
            # t500x500 versuchen, sonst Original
            hi = re.sub(r'-(large|t\d+x\d+)\.jpg$', '-t500x500.jpg', art_url)
            for candidate in [hi, art_url]:
                try:
                    r = requests.get(candidate, headers={"User-Agent": USER_AGENT}, timeout=20, stream=True)
                    if r.ok and "image" in r.headers.get("Content-Type",""):
                        with open(art_out_path, "wb") as f:
                            for chunk in r.iter_content(8192):
                                f.write(chunk)
                        break
                except requests.RequestException:
                    # einfach nächsten Kandidaten probieren
                    pass

    return hls

def get_playback_m3u8_url(transcoding_url: str, client_id: str, track_auth: str|None) -> str:
    params = {"client_id": client_id}
    if track_auth: params["track_authorization"] = track_auth
    r = requests.get(transcoding_url, params=params, headers={"User-Agent": USER_AGENT}, timeout=20)
    r.raise_for_status()
    j = r.json()
    return j["url"]  # -> https://playback.media-streaming.soundcloud.cloud/.../playlist.m3u8?... (zeitlich befristet)


def ffmpeg_cmd():
    exe = FFMPEG if FFMPEG else "ffmpeg"
    if FFMPEG:
        return FFMPEG
    # prüfen, ob im PATH
    if which("ffmpeg") is None:
        raise FileNotFoundError(
            "ffmpeg nicht gefunden. Entweder ffmpeg in PATH aufnehmen (ffmpeg -version testen) "
            "oder FFMPEG = r'C:\\ffmpeg\\bin\\ffmpeg.exe' im Script setzen."
        )
    return exe


def sanitize_url(u: str) -> str:
    # Remove accidental leading/trailing quotes
    if len(u) >= 2 and ((u[0] == u[-1] == '"') or (u[0] == u[-1] == "'")):
        return u[1:-1]
    return u

def run_ffmpeg_to_mp3(m3u8_url: str, out_path: str, art_out_path="cover.jpg"):
    url = sanitize_url(m3u8_url)
    ua = "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36"

    has_cover = bool(art_out_path) and os.path.exists(art_out_path)
    print(has_cover)

    if has_cover:
        # Mit Cover (keine Änderung deiner Audio-Settings, nur zusätzliche Map-/Metadata-Flags + ohne -vn)
        cmd = [
            ffmpeg_cmd(),
            "-headers", ua,
            "-i", url,
            "-i", art_out_path,          # <<< dein Cover
            "-map", "0:a",
            "-map", "1",
            "-acodec", "libmp3lame",
            "-ab", "192k",               # unverändert
            "-ar", "44100",             # unverändert
            "-id3v2_version", "3",
            "-metadata:s:v", "title=Album cover",
            "-metadata:s:v", "comment=Cover (front)",
            "-disposition:v", "attached_pic",
            out_path
        ]
    else:
        # Ohne Cover -> exakt deine bisherigen Flags (inkl. -vn)
        cmd = [
            ffmpeg_cmd(),
            "-headers", ua,
            "-i", url,
            "-vn",
            "-acodec", "libmp3lame",
            "-ab", "192k",
            "-ar", "44100",
            out_path
        ]
    #print("[INFO] Starte ffmpeg:", " ".join(shlex.quote(c) for c in cmd))
    try :
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        print("[OK] Fertig:", out_path)
    except subprocess.CalledProcessError as e:
        print("[ERROR] ffmpeg Fehler:", e.stdout)
        print("[ERROR] ffmpeg Fehler:", e.stderr)
        raise

def process_track(href: str, client_id: str, out_dir: str = ".", title_override: str | None = None) -> dict:
    """
    End-to-End: href (track_page_url) -> resolve -> Cover -> m3u8 -> ffmpeg -> MP3
    - MP3-Dateiname = Titel (slugified)
    - Ausgabeort = out_dir
    """
    track = resolve_track(href, client_id)
    title = title_override or track.get("title") or "track"
    base = slugify(title)
    os.makedirs(out_dir, exist_ok=True)

    cover = os.path.join(out_dir, f"{base}.jpg")
    transcoding = pick_hls_transcoding(track, art_out_path=cover)
    m3u8 = get_playback_m3u8_url(transcoding["url"], client_id, track.get("track_authorization"))

    mp3 = ensure_unique_path(os.path.join(out_dir, f"{base}.mp3"))
    run_ffmpeg_to_mp3(m3u8, mp3, art_out_path=cover)

    return {"title": title, "mp3": mp3, "cover": cover, "m3u8": m3u8}


