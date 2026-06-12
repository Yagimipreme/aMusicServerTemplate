"""Mix profile schema, validation, and legacy-config migration."""
import re
import logging

logger = logging.getLogger(__name__)

VALID_CADENCES = {"daily", "weekly"}
VALID_MODES = {"history", "genre", "manual", "playlist"}
VALID_DAYS = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}
MODE_REQUIRED = {"genre": "genres", "manual": "artists", "playlist": "playlist"}


def validate_profile(p: dict, existing: list | None = None) -> dict:
    """Return {} if valid, else {field: error}. existing = other profiles (id-uniqueness)."""
    errors = {}
    if not p.get("id") or not str(p["id"]).strip():
        errors["id"] = "required"
    elif existing and any(o["id"] == p["id"] for o in existing):
        errors["id"] = "duplicate id"
    if not p.get("name"):
        errors["name"] = "required"
    elif existing and any(o.get("name", "").casefold() == str(p["name"]).casefold() for o in existing):
        errors["name"] = "duplicate name"
    sched = p.get("schedule") or {}
    if sched.get("cadence") not in VALID_CADENCES:
        errors["schedule.cadence"] = "must be daily or weekly"
    elif sched["cadence"] == "weekly" and str(sched.get("run_day", "")).lower() not in VALID_DAYS:
        errors["schedule.run_day"] = "valid weekday required for weekly cadence"
    try:
        h = int(sched.get("run_hour", -1))
        if not 0 <= h <= 23:
            raise ValueError
    except (TypeError, ValueError):
        errors["schedule.run_hour"] = "must be 0..23"
    try:
        if not 0.0 <= float(p.get("new_ratio", -1)) <= 1.0:
            raise ValueError
    except (TypeError, ValueError):
        errors["new_ratio"] = "must be 0..1"
    try:
        count = int(p.get("count", 0))
        cap = int(p.get("cap", 0))
        if count < 1:
            errors["count"] = "must be >= 1"
        elif cap < count:
            errors["cap"] = "must be >= count"
    except (TypeError, ValueError):
        errors["count"] = "count and cap must be integers"
    seeds = p.get("seeds") or {}
    mode = seeds.get("mode")
    if mode not in VALID_MODES:
        errors["seeds.mode"] = "invalid mode"
    elif mode in MODE_REQUIRED and not seeds.get(MODE_REQUIRED[mode]):
        errors[f"seeds.{MODE_REQUIRED[mode]}"] = f"required for mode={mode}"
    return errors


def migrate_config(cfg: dict) -> list:
    """Synthesize `mixes` from legacy discover.* keys. Returns existing list untouched if present."""
    if isinstance(cfg.get("mixes"), list):
        return cfg["mixes"]
    disc = cfg.get("discover") or {}
    # Honor legacy discover.schedule=="daily" for the weekly profile's cadence (issue 16)
    legacy_schedule = disc.get("schedule", "weekly")
    weekly_cadence = "daily" if str(legacy_schedule).lower() == "daily" else "weekly"
    weekly = {
        "id": "weekly", "name": disc.get("playlist_name", "Weekly Mix"),
        "enabled": True, "auto_generated": False,
        "schedule": {"cadence": weekly_cadence, "run_day": disc.get("run_day", "sunday"),
                     "run_hour": int(disc.get("run_hour", 22))},
        "count": int(disc.get("weekly_count", 30)),
        "cap": int(disc.get("playlist_cap", 100)),
        "new_ratio": 1.0,
        "seeds": {"mode": "history", "genres": [], "artists": [],
                  "playlist": disc.get("seed_playlist", "")},
        "quality": {},
    }
    mixes = [weekly]
    daily = disc.get("daily") or {}
    # Always create the daily built-in (spec defaults when daily key absent)
    d_count = max(1, int(daily.get("count", 7)))
    mixes.append({
        "id": "daily", "name": daily.get("playlist_name", "Daily Mix"),
        "enabled": bool(daily.get("enabled", True)), "auto_generated": False,
        "schedule": {"cadence": "daily", "run_day": "",
                     "run_hour": int(daily.get("run_hour", 7))},
        "count": d_count,
        "cap": d_count * max(1, int(daily.get("window_days", 7))),
        "new_ratio": 1.0,
        "seeds": {"mode": "history", "genres": [], "artists": [], "playlist": ""},
        "quality": {},
    })
    return mixes


# ── Genre profile bootstrapper ────────────────────────────────────────────────

_STAGGER = ["monday", "tuesday", "wednesday", "thursday"]


def suggest_genre_profiles(subsonic, existing_mixes: list, top_n: int = 4) -> list:
    """Suggest auto-generated genre profiles based on top library genres.

    Skips genres already covered by existing profiles. Staggers run_day Mon→Thu.
    Returns up to top_n new profile dicts.
    """
    covered = {g.lower() for m in existing_mixes
               for g in (m.get("seeds") or {}).get("genres") or []}
    existing_ids = {m["id"] for m in existing_mixes}
    out = []
    for g in subsonic.get_genres():
        if len(out) >= top_n:
            break
        name = g["name"].strip()
        tag = name.lower()
        slug = re.sub(r"[^a-z0-9]+", "-", tag).strip("-")
        if not slug or tag in covered or f"genre-{slug}" in existing_ids:
            continue
        out.append({
            "id": f"genre-{slug}", "name": f"{name.title()} Mix",
            "enabled": True, "auto_generated": True,
            "schedule": {"cadence": "weekly",
                         "run_day": _STAGGER[len(out) % len(_STAGGER)], "run_hour": 7},
            "count": 15, "cap": 60, "new_ratio": 0.3,
            "seeds": {"mode": "genre", "genres": [tag], "artists": [], "playlist": ""},
            "quality": {},
        })
    return out
