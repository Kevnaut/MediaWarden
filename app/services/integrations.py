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
    logger.info("torrent.rules.skipped", extra={"library_id": library.id, "media_item_id": item.id})
    return {
        "ok": False,
        "reason": "not_implemented",
        "min_seed_time_minutes": library.min_seed_time_minutes,
        "min_seed_ratio": library.min_seed_ratio,
        "min_seeders": library.min_seeders,
    }
