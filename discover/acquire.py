import logging

logger = logging.getLogger(__name__)


def acquire(download_fn, candidate):
    """Download one candidate; return list of resulting mp3 paths ([] on failure).

    download_fn(url) may return either [paths] or (title, [paths]) — both handled.
    Writes WOAS (Official Audio Source) ID3 tag when download_ref is set on candidate.
    """
    try:
        result = download_fn(candidate["url"])
    except Exception:
        logger.exception("acquire: download failed for %s", candidate.get("url"))
        return []
    if isinstance(result, tuple):
        result = result[1]
    paths = list(result or [])

    # Write source URL to WOAS ID3 frame
    source_url = candidate.get("download_ref", "")
    if source_url and paths:
        try:
            import sys
            import os
            _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if _root not in sys.path:
                sys.path.insert(0, _root)
            from library.tagger import write_source_url
            for path in paths:
                write_source_url(path, source_url)
        except Exception:
            logger.warning("acquire: WOAS write failed", exc_info=True)

    return paths
