"""Tests for discover/profiles.py — schema validation and legacy migration."""
import pytest
from discover.profiles import validate_profile, migrate_config


# ── validate_profile happy path ───────────────────────────────────────────────

def make_valid_profile(**overrides):
    p = {
        "id": "weekly",
        "name": "Weekly Mix",
        "enabled": True,
        "auto_generated": False,
        "schedule": {"cadence": "weekly", "run_day": "sunday", "run_hour": 22},
        "count": 30,
        "cap": 100,
        "new_ratio": 1.0,
        "seeds": {"mode": "history", "genres": [], "artists": [], "playlist": ""},
        "quality": {},
    }
    p.update(overrides)
    return p


def test_validate_profile_happy_path():
    errors = validate_profile(make_valid_profile())
    assert errors == {}


def test_validate_profile_daily_cadence():
    p = make_valid_profile()
    p["schedule"] = {"cadence": "daily", "run_day": "", "run_hour": 7}
    errors = validate_profile(p)
    assert errors == {}


# ── Rejection cases ───────────────────────────────────────────────────────────

def test_validate_profile_bad_cadence():
    p = make_valid_profile()
    p["schedule"]["cadence"] = "monthly"
    errors = validate_profile(p)
    assert "schedule.cadence" in errors


def test_validate_profile_ratio_below_zero():
    p = make_valid_profile(new_ratio=-0.1)
    errors = validate_profile(p)
    assert "new_ratio" in errors


def test_validate_profile_ratio_above_one():
    p = make_valid_profile(new_ratio=1.1)
    errors = validate_profile(p)
    assert "new_ratio" in errors


def test_validate_profile_count_zero():
    p = make_valid_profile(count=0)
    errors = validate_profile(p)
    assert "count" in errors


def test_validate_profile_cap_below_count():
    p = make_valid_profile(count=30, cap=10)
    errors = validate_profile(p)
    assert "cap" in errors


def test_validate_profile_run_hour_above_23():
    p = make_valid_profile()
    p["schedule"]["run_hour"] = 24
    errors = validate_profile(p)
    assert "schedule.run_hour" in errors


def test_validate_profile_run_hour_negative():
    p = make_valid_profile()
    p["schedule"]["run_hour"] = -1
    errors = validate_profile(p)
    assert "schedule.run_hour" in errors


def test_validate_profile_missing_run_day_for_weekly():
    p = make_valid_profile()
    p["schedule"] = {"cadence": "weekly", "run_day": "badday", "run_hour": 7}
    errors = validate_profile(p)
    assert "schedule.run_day" in errors


def test_validate_profile_no_run_day_needed_for_daily():
    p = make_valid_profile()
    p["schedule"] = {"cadence": "daily", "run_day": "", "run_hour": 7}
    errors = validate_profile(p)
    assert "schedule.run_day" not in errors


def test_validate_profile_invalid_seeds_mode():
    p = make_valid_profile()
    p["seeds"]["mode"] = "unknown"
    errors = validate_profile(p)
    assert "seeds.mode" in errors


def test_validate_profile_genre_mode_missing_genres():
    p = make_valid_profile()
    p["seeds"] = {"mode": "genre", "genres": [], "artists": [], "playlist": ""}
    errors = validate_profile(p)
    assert "seeds.genres" in errors


def test_validate_profile_genre_mode_with_genres():
    p = make_valid_profile()
    p["seeds"] = {"mode": "genre", "genres": ["techno"], "artists": [], "playlist": ""}
    errors = validate_profile(p)
    assert "seeds.genres" not in errors


def test_validate_profile_manual_mode_missing_artists():
    p = make_valid_profile()
    p["seeds"] = {"mode": "manual", "genres": [], "artists": [], "playlist": ""}
    errors = validate_profile(p)
    assert "seeds.artists" in errors


def test_validate_profile_manual_mode_with_artists():
    p = make_valid_profile()
    p["seeds"] = {"mode": "manual", "genres": [], "artists": ["Aphex Twin"], "playlist": ""}
    errors = validate_profile(p)
    assert "seeds.artists" not in errors


def test_validate_profile_playlist_mode_missing_playlist():
    p = make_valid_profile()
    p["seeds"] = {"mode": "playlist", "genres": [], "artists": [], "playlist": ""}
    errors = validate_profile(p)
    assert "seeds.playlist" in errors


def test_validate_profile_playlist_mode_with_playlist():
    p = make_valid_profile()
    p["seeds"] = {"mode": "playlist", "genres": [], "artists": [], "playlist": "Liked Songs"}
    errors = validate_profile(p)
    assert "seeds.playlist" not in errors


def test_validate_profile_missing_id():
    p = make_valid_profile()
    p["id"] = ""
    errors = validate_profile(p)
    assert "id" in errors


def test_validate_profile_missing_name():
    p = make_valid_profile()
    p["name"] = ""
    errors = validate_profile(p)
    assert "name" in errors


def test_validate_profile_duplicate_id():
    p = make_valid_profile(id="myprofile")
    existing = [make_valid_profile(id="myprofile")]
    errors = validate_profile(p, existing=existing)
    assert "id" in errors


def test_validate_profile_update_same_id_not_duplicate():
    """When updating, other profiles list excludes self → no duplicate error."""
    p = make_valid_profile(id="weekly")
    # other profiles excludes the one being updated
    others = [make_valid_profile(id="daily")]
    errors = validate_profile(p, existing=others)
    assert "id" not in errors


# ── migrate_config ────────────────────────────────────────────────────────────

def make_legacy_cfg(with_daily=True):
    cfg = {
        "discover": {
            "playlist_name": "Weekly Mix",
            "run_day": "sunday",
            "run_hour": 22,
            "weekly_count": 30,
            "playlist_cap": 100,
            "seed_playlist": "Liked Songs",
        }
    }
    if with_daily:
        cfg["discover"]["daily"] = {
            "enabled": True,
            "count": 7,
            "run_hour": 7,
            "window_days": 7,
            "playlist_name": "Daily Mix",
        }
    return cfg


def test_migrate_config_legacy_weekly_and_daily():
    cfg = make_legacy_cfg(with_daily=True)
    mixes = migrate_config(cfg)
    assert len(mixes) == 2
    ids = {m["id"] for m in mixes}
    assert "weekly" in ids
    assert "daily" in ids


def test_migrate_config_legacy_weekly_only():
    cfg = make_legacy_cfg(with_daily=False)
    mixes = migrate_config(cfg)
    assert len(mixes) == 1
    assert mixes[0]["id"] == "weekly"


def test_migrate_config_weekly_values():
    cfg = make_legacy_cfg(with_daily=False)
    mixes = migrate_config(cfg)
    w = mixes[0]
    assert w["name"] == "Weekly Mix"
    assert w["schedule"]["cadence"] == "weekly"
    assert w["schedule"]["run_day"] == "sunday"
    assert w["schedule"]["run_hour"] == 22
    assert w["count"] == 30
    assert w["cap"] == 100
    assert w["new_ratio"] == 1.0
    assert w["seeds"]["mode"] == "history"
    assert w["seeds"]["playlist"] == "Liked Songs"
    assert w["auto_generated"] is False
    assert w["enabled"] is True


def test_migrate_config_daily_values():
    cfg = make_legacy_cfg(with_daily=True)
    mixes = migrate_config(cfg)
    d = next(m for m in mixes if m["id"] == "daily")
    assert d["name"] == "Daily Mix"
    assert d["schedule"]["cadence"] == "daily"
    assert d["schedule"]["run_hour"] == 7
    assert d["count"] == 7
    assert d["cap"] == 49  # 7 * 7
    assert d["new_ratio"] == 1.0
    assert d["seeds"]["mode"] == "history"
    assert d["enabled"] is True


def test_migrate_config_idempotent_with_mixes_present():
    """If mixes key is already a list, return it untouched."""
    existing = [{"id": "custom", "name": "Custom"}]
    cfg = {"mixes": existing, "discover": {"run_day": "monday"}}
    result = migrate_config(cfg)
    assert result is existing
    assert len(result) == 1
    assert result[0]["id"] == "custom"


def test_migrate_config_idempotent_no_duplicates():
    """Calling migrate_config twice on the same config doesn't duplicate profiles."""
    cfg = make_legacy_cfg(with_daily=True)
    mixes1 = migrate_config(cfg)
    # Simulate the server persisting mixes back
    cfg2 = dict(cfg)
    cfg2["mixes"] = mixes1
    mixes2 = migrate_config(cfg2)
    assert len(mixes2) == 2  # no duplicates
