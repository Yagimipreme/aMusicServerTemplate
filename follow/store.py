"""Followed-artist list persisted to follows.json (separate from config.json).

Schema: {"artists": [{"mbid", "name", "disambiguation", "followed_at"}]}
Atomic writes via .tmp + os.replace under a module lock.
"""
import datetime
import json
import os
import threading

_lock = threading.RLock()


def _load(path: str) -> dict:
    if not os.path.exists(path):
        return {"artists": []}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not isinstance(data.get("artists"), list):
            return {"artists": []}
        return data
    except Exception:
        return {"artists": []}


def _save(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def list_follows(path: str) -> list:
    return _load(path)["artists"]


def add_follow(path: str, mbid: str, name: str, disambiguation: str = "") -> None:
    with _lock:
        data = _load(path)
        if any(a.get("mbid") == mbid for a in data["artists"]):
            return
        data["artists"].append({
            "mbid": mbid,
            "name": name,
            "disambiguation": disambiguation,
            "followed_at": datetime.datetime.now().isoformat(),
        })
        _save(path, data)


def remove_follow(path: str, mbid: str) -> None:
    with _lock:
        data = _load(path)
        data["artists"] = [a for a in data["artists"] if a.get("mbid") != mbid]
        _save(path, data)
