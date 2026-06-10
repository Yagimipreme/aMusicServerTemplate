import datetime
import json
import logging
import os

logger = logging.getLogger(__name__)


class DiscoverState:
    def __init__(self, path: str, suggested, last_run=None):
        self._path = path
        self._suggested = set(suggested)
        self._last_run = last_run

    def has(self, key: str) -> bool:
        return key in self._suggested

    def add(self, key: str) -> None:
        self._suggested.add(key)

    @property
    def last_run(self):
        return self._last_run

    def save(self, stamp_last_run: bool = False) -> None:
        if stamp_last_run or self._last_run is None:
            self._last_run = datetime.datetime.now().isoformat()
        data = {"suggested": sorted(self._suggested), "last_run": self._last_run}
        tmp = self._path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self._path)


def load_state(path: str) -> DiscoverState:
    suggested = []
    last_run = None
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
            suggested = d.get("suggested", []) or []
            last_run = d.get("last_run")
        except Exception:
            logger.warning("load_state: could not read %s, starting empty", path)
    return DiscoverState(path, suggested, last_run=last_run)
