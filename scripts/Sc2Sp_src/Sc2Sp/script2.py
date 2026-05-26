import subprocess
import requests
import shlex
import re
import os
import sys
import json
from shutil import which

# ── Paths ──────────────────────────────────────────────────────────────────────

_SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "../../../"))
_CONFIG_PATH  = os.path.join(_PROJECT_ROOT, "config.json")


# ── Config ─────────────────────────────────────────────────────────────────────

def _load_config() -> dict:
    try:
        with open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"

# Loaded from config at startup; falls back to a known-good default
_cfg = _load_config()
client_id = _cfg.get('sc_client_id') or "V8Xd6biqlKU0Xeyo7T2IHUzBAyrMJWjx"


# ── ffmpeg ─────────────────────────────────────────────────────────────────────

def ffmpeg_cmd() -> str:
    # 1) Explicit override in config
    loc = _load_config().get('ffmpeg_location')
    if loc and os.path.isfile(loc):
        return loc

    # 2) System PATH
    if which('ffmpeg'):
        return 'ffmpeg'

    raise FileNotFoundError(
        'ffmpeg not found. Install it (e.g. pacman -S ffmpeg) '
        'or set ffmpeg_location in config.json.'
    )


# ── Helpers ────────────────────────────────────────────────────────────────────

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


def sanitize_url(u: str) -> str:
    if len(u) >= 2 and ((u[0] == u[-1] == '"') or (u[0] == u[-1] == "'")):
        return u[1:-1]
    return u


# ── SoundCloud API ─────────────────────────────────────────────────────────────

def resolve_track(track_page_url: str, client_id: str) -> dict:
    print(f"[DEBUG] Resolving: {track_page_url}")
    r = requests.get(
        "https://api-v2.soundcloud.com/resolve",
        params={"url": track_page_url, "client_id": client_id},
        headers={"User-Agent": USER_AGENT},
        timeout=20,
    )
    r.raise_for_status()
    return r.json()


def pick_hls_transcoding(track_json: dict, art_out_path: str | None = None):
    trans = (track_json.get("media") or {}).get("transcodings", [])
    preferred = [t for t in trans if t.get("format", {}).get("protocol") == "hls" and "aac_160" in t.get("preset", "")]
    hls = preferred[0] if preferred else next((t for t in trans if t.get("format", {}).get("protocol") == "hls"), None)
    if not hls:
        raise RuntimeError("No HLS transcoding found.")

    if art_out_path:
        art_url = track_json.get("artwork_url") or (track_json.get("user") or {}).get("avatar_url")
        if art_url:
            hi = re.sub(r'-(large|t\d+x\d+)\.jpg$', '-t500x500.jpg', art_url)
            for candidate in [hi, art_url]:
                try:
                    r = requests.get(candidate, headers={"User-Agent": USER_AGENT}, timeout=20, stream=True)
                    if r.ok:
                        with open(art_out_path, "wb") as f:
                            for chunk in r.iter_content(8192):
                                f.write(chunk)
                        break
                except requests.RequestException:
                    pass

    return hls


def get_playback_m3u8_url(transcoding_url: str, client_id: str, track_auth: str | None) -> str:
    params = {"client_id": client_id}
    if track_auth:
        params["track_authorization"] = track_auth
    r = requests.get(transcoding_url, params=params, headers={"User-Agent": USER_AGENT}, timeout=20)
    r.raise_for_status()
    return r.json()["url"]


def get_playback_m3u8_url_safe(transcoding_url: str, client_id: str, track_auth: str | None) -> str | None:
    """Like get_playback_m3u8_url but returns None on 4xx instead of raising."""
    params = {"client_id": client_id}
    if track_auth:
        params["track_authorization"] = track_auth
    r = requests.get(transcoding_url, params=params, headers={"User-Agent": USER_AGENT}, timeout=20)
    if r.status_code in (401, 403, 404):
        return None
    r.raise_for_status()
    return r.json()["url"]


# ── ffmpeg conversion ──────────────────────────────────────────────────────────

def run_ffmpeg_to_mp3(m3u8_url: str, out_path: str, art_out_path: str | None = None):
    url = sanitize_url(m3u8_url)
    ua = f"User-Agent: {USER_AGENT}"
    has_cover = bool(art_out_path) and os.path.exists(art_out_path)

    if has_cover:
        cmd = [
            ffmpeg_cmd(),
            "-headers", ua,
            "-i", url,
            "-i", art_out_path,
            "-map", "0:a",
            "-map", "1",
            "-acodec", "libmp3lame",
            "-ab", "192k",
            "-ar", "44100",
            "-id3v2_version", "3",
            "-metadata:s:v", "title=Album cover",
            "-metadata:s:v", "comment=Cover (front)",
            "-disposition:v", "attached_pic",
            out_path,
        ]
    else:
        cmd = [
            ffmpeg_cmd(),
            "-headers", ua,
            "-i", url,
            "-vn",
            "-acodec", "libmp3lame",
            "-ab", "192k",
            "-ar", "44100",
            out_path,
        ]

    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        print("[OK] Done:", out_path)
    except subprocess.CalledProcessError as e:
        print("[ERROR] ffmpeg stdout:", e.stdout)
        print("[ERROR] ffmpeg stderr:", e.stderr)
        raise


# ── Main pipeline ──────────────────────────────────────────────────────────────

def process_track(href: str, client_id: str, out_dir: str = ".", title_override: str | None = None) -> dict:
    import tempfile, uuid

    out_dir = os.path.abspath(os.path.expanduser(str(out_dir)))
    os.makedirs(out_dir, exist_ok=True)

    track = resolve_track(href, client_id)
    title = title_override or track.get("title") or "track"
    base  = slugify(title)
    mp3   = ensure_unique_path(os.path.join(out_dir, f"{base}.mp3"))

    if os.path.exists(mp3):
        print(f"[SKIP] Already exists: {title}")
        return {"title": title, "mp3": mp3, "cover": None, "m3u8": None}

    print(f"[PROCESS] Downloading: {title}")

    tmp = tempfile.NamedTemporaryFile(suffix=f"_{uuid.uuid4().hex}.jpg", delete=False)
    cover = tmp.name
    tmp.close()

    try:
        trans_list = (track.get("media") or {}).get("transcodings", [])
        # Sort: free/mp3 HLS first (opus/mp3_standard), aac_160 last (Go+ only)
        hls_trans = [t for t in trans_list if t.get("format", {}).get("protocol") == "hls"]
        hls_trans.sort(key=lambda t: (1 if "aac_160" in t.get("preset", "") else 0))

        # Also fetch cover art using the first transcoding (artwork is on track JSON anyway)
        pick_hls_transcoding(track, art_out_path=cover)  # just for cover side-effect

        m3u8 = None
        for t in hls_trans:
            print(f"[TRY] transcoding preset={t.get('preset')} url={t['url'][:60]}...")
            m3u8 = get_playback_m3u8_url_safe(t["url"], client_id, track.get("track_authorization"))
            if m3u8:
                print(f"[OK] Using preset: {t.get('preset')}")
                break
            print(f"[SKIP] 4xx on preset={t.get('preset')}, trying next...")

        if not m3u8:
            raise RuntimeError("All HLS transcodings returned 4xx — track may be geo-blocked or Go+ only.")

        run_ffmpeg_to_mp3(m3u8, mp3, art_out_path=cover)
    finally:
        if cover and os.path.exists(cover):
            try:
                os.remove(cover)
            except Exception as e:
                print(f"[WARNING] Could not delete cover: {e}")

    return {"title": title, "mp3": mp3, "cover": None, "m3u8": m3u8}
