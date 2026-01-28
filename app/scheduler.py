from apscheduler.schedulers.background import BackgroundScheduler

from .config import settings
from .db import SessionLocal
from .models import Library, MediaItem
from .services.trash import purge_expired_trash
from .services import plex as plex_service


def _sync_plex_library(library_id: int) -> None:
    db = SessionLocal()
    try:
        library = db.get(Library, library_id)
        if not library or not library.enable_plex:
            return
        mapping = plex_service.fetch_metadata_map(library)
        if not mapping:
            return
        items = db.query(MediaItem).filter(MediaItem.library_id == library.id).all()
        updated = 0
        for item in items:
            meta = mapping.get(item.path)
            if not meta:
                continue
            touched_at = meta.get("touched_at")
            resolution = meta.get("resolution")
            changed = False
            if touched_at and item.last_watched_at != touched_at:
                item.last_watched_at = touched_at
                changed = True
            if resolution and item.resolution != resolution:
                item.resolution = resolution
                changed = True
            if changed:
                updated += 1
        db.commit()
    finally:
        db.close()


def create_scheduler():
    scheduler = BackgroundScheduler(timezone=settings.timezone)
    scheduler.add_job(purge_expired_trash, "interval", hours=6, id="purge_trash")
    return scheduler
