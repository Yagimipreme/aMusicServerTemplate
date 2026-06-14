"""Runtime state for the follow feature (follow_state.json).

Unlike discover/state.py, acquired_release_groups never expires — a release
must never be re-downloaded.
"""
import datetime
import json
import os
import threading

_FEED_CAP = 200
_lock = threading.RLock()


class FollowState:
    def __init__(self, path, data):
        self._path = path
        self._d = data

    # ── acquired (idempotency) ──
    def has_acquired(self, rg_mbid: str) -> bool:
        return rg_mbid in self._d["acquired_release_groups"]

    def mark_acquired(self, rg_mbid: str) -> None:
        self._d["acquired_release_groups"][rg_mbid] = datetime.datetime.now().isoformat()

    # ── backfill markers ──
    def is_backfilled(self, mbid: str) -> bool:
        return mbid in self._d["backfilled_mbids"]

    def mark_backfilled(self, mbid: str) -> None:
        if mbid not in self._d["backfilled_mbids"]:
            self._d["backfilled_mbids"].append(mbid)

    # ── pending (retry) ──
    def pending(self) -> list:
        return self._d["pending"]

    def add_pending(self, rg_mbid: str, artist: str, title: str) -> None:
        if any(p["rg_mbid"] == rg_mbid for p in self._d["pending"]):
            return
        self._d["pending"].append(
            {"rg_mbid": rg_mbid, "artist": artist, "title": title, "attempts": 1})

    def bump_pending(self, rg_mbid: str) -> None:
        for p in self._d["pending"]:
            if p["rg_mbid"] == rg_mbid:
                p["attempts"] += 1

    def drop_pending(self, rg_mbid: str) -> None:
        self._d["pending"] = [p for p in self._d["pending"] if p["rg_mbid"] != rg_mbid]

    # ── feed + unseen ──
    def feed(self) -> list:
        return self._d["feed"]

    def append_feed(self, entry: dict) -> None:
        entry = {**entry, "ts": datetime.datetime.now().isoformat()}
        self._d["feed"].append(entry)
        if len(self._d["feed"]) > _FEED_CAP:
            self._d["feed"] = self._d["feed"][-_FEED_CAP:]
        self._d["unseen_count"] += 1

    def mark_seen(self) -> None:
        self._d["unseen_count"] = 0

    # ── scheduling stamps ──
    def set_runs(self, last_run=None, next_run=None) -> None:
        if last_run is not None:
            self._d["last_run"] = last_run
        if next_run is not None:
            self._d["next_run"] = next_run

    def summary(self) -> dict:
        return {
            "unseen_count": self._d["unseen_count"],
            "last_run": self._d.get("last_run"),
            "next_run": self._d.get("next_run"),
            "acquired_count": len(self._d["acquired_release_groups"]),
        }

    def save(self) -> None:
        with _lock:
            tmp = self._path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._d, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)


def _empty() -> dict:
    return {
        "acquired_release_groups": {},
        "backfilled_mbids": [],
        "pending": [],
        "feed": [],
        "unseen_count": 0,
        "last_run": None,
        "next_run": None,
    }


def load(path: str) -> FollowState:
    data = _empty()
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                loaded = json.load(f)
            for k, v in _empty().items():
                data[k] = loaded.get(k, v)
        except Exception:
            pass
    return FollowState(path, data)
