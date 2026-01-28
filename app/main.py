import logging
import threading
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates
from sqlalchemy import inspect, text

from .config import settings
from .db import Base, SessionLocal, engine
from .logging import setup_logging
from .models import User
from .routers import auth, libraries, logs, media, users
from .scheduler import create_scheduler
from .models import Library

setup_logging()
logger = logging.getLogger("mediawarden")

app = FastAPI(title="MediaWarden")

app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.state.templates = Jinja2Templates(directory="app/templates")


@app.on_event("startup")
def on_startup():
    settings.ensure_paths()
    inspector = inspect(engine)
    if "alembic_version" not in inspector.get_table_names():
        Base.metadata.create_all(bind=engine)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL)"
                )
            )
            conn.execute(text("DELETE FROM alembic_version"))
            conn.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:rev)"),
                {"rev": "0001_initial"},
            )
    scheduler = create_scheduler()
    scheduler.start()
    app.state.scheduler = scheduler
    app.state.scan_status = {}
    app.state.scan_lock = threading.Lock()
    app.state.plex_sections = []
    # Register Plex sync jobs for libraries with a configured interval.
    db = SessionLocal()
    try:
        libraries = db.query(Library).all()
        for lib in libraries:
            if lib.plex_sync_interval_hours and lib.plex_sync_interval_hours > 0:
                job_id = f"plex_sync_{lib.id}"
                scheduler.add_job(
                    "app.scheduler:_sync_plex_library",
                    "interval",
                    hours=lib.plex_sync_interval_hours,
                    id=job_id,
                    replace_existing=True,
                    args=[lib.id],
                )
    finally:
        db.close()
    logger.info("startup")


@app.on_event("shutdown")
def on_shutdown():
    scheduler = getattr(app.state, "scheduler", None)
    if scheduler:
        scheduler.shutdown(wait=False)
    logger.info("shutdown")


@app.exception_handler(HTTPException)
async def auth_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code == 401:
        db = SessionLocal()
        try:
            has_users = db.query(User).count() > 0
        finally:
            db.close()
        target = "/login" if has_users else "/setup"
        return RedirectResponse(url=target, status_code=302)
    raise exc


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("unhandled_error", extra={"path": str(request.url.path)})
    return PlainTextResponse("Internal Server Error", status_code=500)


app.include_router(auth.router)
app.include_router(libraries.router)
app.include_router(media.router)
app.include_router(logs.router)
app.include_router(users.router)
