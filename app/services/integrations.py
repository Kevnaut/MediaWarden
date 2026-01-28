import logging

from ..models import Library, MediaItem
from .plex import refresh_section

logger = logging.getLogger("mediawarden.integrations")


def trigger_plex_rescan(library: Library) -> None:
    if not library.enable_plex or not library.plex_url:
        return
    try:
        refresh_section(library, path=library.root_path)
    except Exception as exc:
        logger.warning("plex.rescan.failed", extra={"library_id": library.id, "error": str(exc)})


def evaluate_torrent_rules(library: Library, item: MediaItem) -> dict:
    if not library.enable_arr or not library.qb_url:
        return {
            "ok": False,
            "reason": "integration_not_configured",
            "min_seed_time_minutes": library.min_seed_time_minutes,
            "min_seed_ratio": library.min_seed_ratio,
            "min_seeders": library.min_seeders,
        }
    if not item.torrent_hash:
        return {
            "ok": False,
            "reason": "no_torrent",
            "min_seed_time_minutes": library.min_seed_time_minutes,
            "min_seed_ratio": library.min_seed_ratio,
            "min_seeders": library.min_seeders,
        }
    seed_time_seconds = item.torrent_seed_time or 0
    ratio = item.torrent_ratio or 0.0
    seeders = item.torrent_seeders or 0
    min_seed_time_seconds = library.min_seed_time_minutes * 60
    seed_time_ok = seed_time_seconds >= min_seed_time_seconds
    ratio_ok = ratio >= library.min_seed_ratio
    seeders_ok = seeders >= library.min_seeders
    ok = seed_time_ok and ratio_ok and seeders_ok
    return {
        "ok": ok,
        "reason": "meets_thresholds" if ok else "below_thresholds",
        "seed_time_seconds": seed_time_seconds,
        "ratio": ratio,
        "seeders": seeders,
        "min_seed_time_minutes": library.min_seed_time_minutes,
        "min_seed_ratio": library.min_seed_ratio,
        "min_seeders": library.min_seeders,
    }
