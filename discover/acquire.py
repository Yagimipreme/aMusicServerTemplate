import logging

logger = logging.getLogger(__name__)


def acquire(download_fn, candidate):
    """Download one candidate; return list of resulting mp3 paths ([] on failure).

    download_fn(url) may return either [paths] or (title, [paths]) — both handled.
    """
    try:
        result = download_fn(candidate["url"])
    except Exception:
        logger.exception("acquire: download failed for %s", candidate.get("url"))
        return []
    if isinstance(result, tuple):
        result = result[1]
    return list(result or [])
