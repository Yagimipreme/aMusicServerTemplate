"""One follow run: detect → resolve → acquire → playlist → notify → save."""
import datetime
import logging

from follow import detect, notify

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 3


def _today_iso():
    return datetime.date.today().isoformat()


def run_once(mb_client, lb_client, follows, state, search_fn, download_fn,
             song_dir, cfg, resolve_fn=None, acquire_fn=None, assemble_fn=None,
             push_fn=None, today=None) -> dict:
    if resolve_fn is None:
        from discover.resolve import resolve_tracks as resolve_fn
    if acquire_fn is None:
        from discover.acquire import acquire as acquire_fn
    if assemble_fn is None:
        from discover.assemble import write_weekly_mix as assemble_fn
    if push_fn is None:
        push_fn = notify.push_summary
    today = today or _today_iso()

    lookback = int(cfg.get("lookback_days", 7))
    backfill = int(cfg.get("default_backfill_days", 30))
    playlist_name = cfg.get("playlist_name", "NEW RELEASES")
    playlist_cap = int(cfg.get("playlist_cap", 100))
    notify_cfg = cfg.get("notify") or {}

    fresh = lb_client.fresh_releases(today, days=lookback, past=True)
    targets = detect.detect_targets(mb_client, fresh, follows, state, backfill, today)

    # Re-attempt previously-pending releases (use stored artist/title)
    target_rgs = {t["rg_mbid"] for t in targets}
    for p in list(state.pending()):
        if p["rg_mbid"] not in target_rgs:
            targets.append({
                "rg_mbid": p["rg_mbid"], "artist": p["artist"], "title": p["title"],
                "release_name": p["title"], "release_date": "", "primary_type": "",
            })

    paths = []
    summary_lines = []
    acquired = 0
    unavailable = 0

    for t in targets:
        candidates = []
        try:
            candidates = resolve_fn(
                search_fn, [{"name": t["artist"], "top_track": t["title"]}],
                per_artist=1)
        except Exception:
            logger.warning("follow: resolve failed for %s – %s", t["artist"], t["title"])

        got = []
        for c in candidates:
            try:
                got = acquire_fn(download_fn, c)
            except Exception:
                got = []
            if got:
                break

        if got:
            paths.extend(got)
            state.mark_acquired(t["rg_mbid"])
            state.drop_pending(t["rg_mbid"])
            notify.record_event(state, t["artist"], t["title"], t["release_name"],
                                t["release_date"], t["primary_type"], "acquired")
            summary_lines.append(f"{t['artist']} – {t['title']}")
            acquired += 1
        else:
            existing = next((p for p in state.pending()
                             if p["rg_mbid"] == t["rg_mbid"]), None)
            if existing is None:
                state.add_pending(t["rg_mbid"], t["artist"], t["title"])
            elif existing["attempts"] >= _MAX_ATTEMPTS - 1:
                state.drop_pending(t["rg_mbid"])
                notify.record_event(state, t["artist"], t["title"], t["release_name"],
                                    t["release_date"], t["primary_type"], "unavailable")
                unavailable += 1
            else:
                state.bump_pending(t["rg_mbid"])

    if paths:
        assemble_fn(song_dir, paths, playlist_name, playlist_cap)

    push_fn(summary_lines,
            webhook_url=notify_cfg.get("webhook_url", ""),
            ntfy_topic=notify_cfg.get("ntfy_topic", ""))

    state.set_runs(last_run=datetime.datetime.now().isoformat())
    state.save()
    return {"acquired": acquired, "unavailable": unavailable}
