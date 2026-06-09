import json
import os


class DiscoverState:
    def __init__(self, path: str, suggested):
        self._path = path
        self._suggested = set(suggested)

    def has(self, key: str) -> bool:
        return key in self._suggested

    def add(self, key: str) -> None:
        self._suggested.add(key)

    def save(self) -> None:
        tmp = self._path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"suggested": sorted(self._suggested)}, f,
                      ensure_ascii=False, indent=2)
        os.replace(tmp, self._path)


def load_state(path: str) -> DiscoverState:
    suggested = []
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                suggested = json.load(f).get("suggested", []) or []
        except Exception:
            suggested = []
    return DiscoverState(path, suggested)
