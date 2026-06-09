import os
import re


def write_weekly_mix(song_dir: str, mp3_paths, name: str = "Weekly Mix") -> str:
    """Write a fresh .m3u listing the given tracks (basenames). Returns the m3u path."""
    safe = re.sub(r'[\\/:*?"<>|]', "_", name)
    m3u_path = os.path.join(song_dir, safe + ".m3u")
    lines = ["#EXTM3U"]
    for p in mp3_paths:
        lines.append(os.path.basename(p))
    with open(m3u_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return m3u_path
