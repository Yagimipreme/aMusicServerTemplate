import os
import re


def write_weekly_mix(song_dir: str, mp3_paths, name: str = "Weekly Mix",
                     cap: int = 100) -> str:
    """Append new tracks to the playlist and rotate out oldest when over cap.

    Maintains a sliding window of the most recently added tracks. Existing
    tracks are read from the .m3u file, new basenames are appended (deduped),
    then the list is trimmed to `cap` from the front (oldest dropped first).
    Returns the m3u path.
    """
    safe = re.sub(r'[\\/:*?"<>|]', "_", name)
    m3u_path = os.path.join(song_dir, safe + ".m3u")

    # Read existing track list (preserves insertion order — oldest first)
    existing = []
    if os.path.exists(m3u_path):
        with open(m3u_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    existing.append(line)

    # Append new tracks, skipping duplicates already in the list
    existing_set = set(existing)
    for p in mp3_paths:
        basename = os.path.basename(p)
        if basename not in existing_set:
            existing.append(basename)
            existing_set.add(basename)

    # Drop oldest entries when over cap
    if cap and len(existing) > cap:
        existing = existing[-cap:]

    lines = ["#EXTM3U"] + existing
    with open(m3u_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return m3u_path
