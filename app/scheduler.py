from apscheduler.schedulers.background import BackgroundScheduler

from .config import settings
from .services.trash import purge_expired_trash


def create_scheduler():
    scheduler = BackgroundScheduler(timezone=settings.timezone)
    scheduler.add_job(purge_expired_trash, "interval", hours=6, id="purge_trash")
    return scheduler
