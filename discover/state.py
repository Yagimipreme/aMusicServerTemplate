import datetime
import json
import logging
import os

logger = logging.getLogger(__name__)

_DEFAULT_TTL_DAYS = 90


class DiscoverState:
    def __init__(self, path: str, suggested: dict, last_run=None, ttl_days: int = _DEFAULT_TTL_DAYS):
        self._path = path
        # suggested: dict[key -> iso_timestamp_str]
        self._suggested = suggested
        self._last_run = last_run
        self._ttl_days = ttl_days

    def has(self, key: str) -> bool:
        ts = self._suggested.get(key)
        if ts is None:
            return False
        try:
            age = datetime.datetime.now() - datetime.datetime.fromisoformat(ts)
            return age.days < self._ttl_days
        except Exception:
            return True

    def add(self, key: str) -> None:
        self._suggested[key] = datetime.datetime.now().isoformat()

    @property
    def last_run(self):
        return self._last_run

    def save(self, stamp_last_run: bool = False) -> None:
        now = datetime.datetime.now()
        if stamp_last_run or self._last_run is None:
            self._last_run = now.isoformat()

        # Prune expired entries before writing
        pruned = {k: ts for k, ts in self._suggested.items()
                  if _within_ttl(ts, self._ttl_days, now)}

        # Preserve keys we don't own (e.g. lastfm_ready)
        existing = {}
        if os.path.exists(self._path):
            try:
                with open(self._path, encoding="utf-8") as f:
                    existing = json.load(f)
            except Exception:
                pass
        existing.update({"suggested": pruned, "last_run": self._last_run})

        tmp = self._path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self._path)


def _within_ttl(ts: str, ttl_days: int, now: datetime.datetime) -> bool:
    try:
        age = now - datetime.datetime.fromisoformat(ts)
        return age.days < ttl_days
    except Exception:
        return True


def load_state(path: str, ttl_days: int = _DEFAULT_TTL_DAYS) -> DiscoverState:
    suggested: dict = {}
    last_run = None
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
            raw = d.get("suggested", []) or []
            last_run = d.get("last_run")

            if isinstance(raw, dict):
                suggested = raw
            else:
                # Legacy list format — migrate, assign now() so entries are fresh
                now_ts = datetime.datetime.now().isoformat()
                suggested = {k: now_ts for k in raw if isinstance(k, str)}
        except Exception:
            logger.warning("load_state: could not read %s, starting empty", path)
    return DiscoverState(path, suggested, last_run=last_run, ttl_days=ttl_days)
