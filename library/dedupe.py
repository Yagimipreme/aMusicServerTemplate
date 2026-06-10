import logging
import os
from collections import defaultdict

from library.scanner import scan

logger = logging.getLogger(__name__)


def find_groups(records):
    """Return dict of key → list[record] for keys with 2+ records."""
    by_key = defaultdict(list)
    for r in records:
        by_key[r["key"]].append(r)
    return {k: v for k, v in by_key.items() if len(v) > 1}


def _pick_keep(group):
    """Return the record to keep: prefers has_tags=True, tie-breaks by oldest mtime."""
    tagged = [r for r in group if r["has_tags"]]
    pool = tagged if tagged else group
    return min(pool, key=lambda r: os.path.getmtime(r["path"]))


def run(song_dir, auto_delete=False):
    """Scan song_dir for duplicates. Returns report dict.

    auto_delete=False: log only (dry-run).
    auto_delete=True:  delete all but the best copy in each group.
    """
    records = scan(song_dir)
    groups = find_groups(records)

    would_delete = []
    deleted = []

    for key, group in groups.items():
        keep = _pick_keep(group)
        to_remove = [r["path"] for r in group if r["path"] != keep["path"]]
        would_delete.extend(to_remove)
        logger.info("dedup: group %r — keep=%s, remove=%s", key, keep["path"], to_remove)

        if auto_delete:
            for path in to_remove:
                try:
                    os.remove(path)
                    deleted.append(path)
                    logger.info("dedup: deleted %s", path)
                except Exception:
                    logger.exception("dedup: could not delete %s", path)

    return {"groups": len(groups), "would_delete": would_delete, "deleted": deleted}
