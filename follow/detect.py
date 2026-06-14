"""Pure detection: combine the ListenBrainz feed with one-time MusicBrainz
backfill, dedupe against acquired releases, and map each new release-group to
download targets per the scope rule (singles/EPs full, albums = 1 track)."""
import datetime
import logging

logger = logging.getLogger(__name__)

_FULL_TYPES = {"single", "ep"}


def _within_days(date_str: str, days: int, today: str) -> bool:
    try:
        d = datetime.date.fromisoformat(date_str[:10])
        t = datetime.date.fromisoformat(today[:10])
    except Exception:
        return False
    return 0 <= (t - d).days <= days


def _targets_for_release(mb_client, rg, today_unused=None) -> list:
    """rg: {rg_mbid, artist, release_name, release_date, primary_type}."""
    try:
        tracks = mb_client.get_release_tracks(rg["rg_mbid"])
    except Exception:
        logger.warning("detect: get_release_tracks failed for %s", rg["rg_mbid"])
        tracks = []

    ptype = (rg["primary_type"] or "").casefold()
    if ptype in _FULL_TYPES and tracks:
        titles = tracks
    elif tracks:
        # representative track: title-track match, else first
        match = next((t for t in tracks
                      if t.casefold() == (rg["release_name"] or "").casefold()), None)
        titles = [match or tracks[0]]
    else:
        titles = [rg["release_name"]] if rg["release_name"] else []

    return [{
        "rg_mbid": rg["rg_mbid"],
        "artist": rg["artist"],
        "title": title,
        "release_name": rg["release_name"],
        "release_date": rg["release_date"],
        "primary_type": rg["primary_type"],
    } for title in titles if title]


def detect_targets(mb_client, fresh_releases, follows, state,
                   default_backfill_days: int, today: str) -> list:
    followed = {f["mbid"]: f["name"] for f in follows}
    release_groups = {}   # rg_mbid -> rg dict (deduped)

    # 1. Feed branch
    for r in fresh_releases:
        if state.has_acquired(r["release_group_mbid"]):
            continue
        matched = [m for m in r["artist_mbids"] if m in followed]
        if not matched:
            continue
        rg_mbid = r["release_group_mbid"]
        if not rg_mbid or rg_mbid in release_groups:
            continue
        release_groups[rg_mbid] = {
            "rg_mbid": rg_mbid,
            "artist": r["artist_name"] or followed[matched[0]],
            "release_name": r["release_name"],
            "release_date": r["release_date"],
            "primary_type": r["primary_type"],
        }

    # 2. Backfill branch (once per artist)
    for f in follows:
        mbid = f["mbid"]
        if state.is_backfilled(mbid):
            continue
        try:
            rgs = mb_client.get_release_groups(mbid)
        except Exception:
            logger.warning("detect: get_release_groups failed for %s", mbid)
            rgs = []
        for rg in rgs:
            if not _within_days(rg["first_release_date"], default_backfill_days, today):
                continue
            if state.has_acquired(rg["rg_mbid"]) or rg["rg_mbid"] in release_groups:
                continue
            release_groups[rg["rg_mbid"]] = {
                "rg_mbid": rg["rg_mbid"],
                "artist": f["name"],
                "release_name": rg["title"],
                "release_date": rg["first_release_date"],
                "primary_type": rg["primary_type"],
            }
        state.mark_backfilled(mbid)

    # 3. Map to targets
    targets = []
    for rg in release_groups.values():
        targets.extend(_targets_for_release(mb_client, rg))
    return targets
