import logging

from ..models import Library, MediaItem

logger = logging.getLogger("mediawarden.integrations")


def fetch_plex_metadata(library: Library, item: MediaItem) -> dict:
    if not library.enable_plex or not library.plex_url:
        return {}
    logger.info("plex.metadata.skipped", extra={"library_id": library.id, "media_item_id": item.id})
    return {}


def trigger_plex_rescan(library: Library) -> None:
    if not library.enable_plex or not library.plex_url:
        return
    logger.info("plex.rescan.skipped", extra={"library_id": library.id, "reason": "not_implemented"})


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
