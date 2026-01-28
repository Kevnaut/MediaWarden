import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode, quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_user
from ..models import Library, MediaItem, TrashEntry
from ..services.filesystem import scan_library, _iter_files
from ..services.trash import restore_from_trash
from ..db import SessionLocal

router = APIRouter()
logger = logging.getLogger("mediawarden.libraries")


def _list_library_paths() -> list[str]:
    root = Path("/libraries")
    if not root.exists():
        return []
    return sorted([str(p) for p in root.iterdir() if p.is_dir()])


def _parse_int(value: str | None, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _parse_float(value: str | None, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _build_tv_tree(items: list[MediaItem], root_path: str) -> dict:
    tree: dict[str, dict[str, list[MediaItem]]] = {}
    for item in items:
        try:
            rel = Path(item.path).relative_to(root_path)
            parts = rel.parts
        except ValueError:
            parts = (item.name,)
        if len(parts) >= 3:
            show = parts[0]
            season = parts[1]
        elif len(parts) == 2:
            show = parts[0]
            season = "Season"
        else:
            show = "Unsorted"
            season = "Unsorted"
        tree.setdefault(show, {}).setdefault(season, []).append(item)
    return tree


def _list_tv_shows(items: list[MediaItem], root_path: str) -> list[str]:
    shows: set[str] = set()
    for item in items:
        try:
            rel = Path(item.path).relative_to(root_path)
            parts = rel.parts
        except ValueError:
            parts = (item.name,)
        if parts:
            shows.add(parts[0])
    return sorted(shows)


def _count_tv_shows(items: list[MediaItem], root_path: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        try:
            rel = Path(item.path).relative_to(root_path)
            parts = rel.parts
        except ValueError:
            parts = (item.name,)
        if parts:
            counts[parts[0]] = counts.get(parts[0], 0) + 1
    return counts


def _update_scan_status(app, library_id: int, **fields) -> None:
    with app.state.scan_lock:
        status = app.state.scan_status.setdefault(library_id, {})
        status.update(fields)


def _scan_worker(app, library_id: int) -> None:
    db = SessionLocal()
    try:
        library = db.get(Library, library_id)
        if not library:
            _update_scan_status(app, library_id, state="error", error="Library not found")
            return
        if not library.enable_filesystem:
            _update_scan_status(
                app,
                library_id,
                state="done",
                scanned_count=0,
                total_count=0,
                created_count=0,
                updated_count=0,
                missing_count=0,
                finished_at=datetime.utcnow().isoformat() + "Z",
            )
            return
        total = sum(1 for _ in _iter_files(library.root_path))
        _update_scan_status(app, library_id, total_count=total)

        def _progress(payload: dict) -> None:
            _update_scan_status(app, library_id, **payload)

        scan_library(db, library, total_files=total, progress=_progress)
        _update_scan_status(app, library_id, state="done", finished_at=datetime.utcnow().isoformat() + "Z")
    except Exception as exc:
        _update_scan_status(app, library_id, state="error", error=str(exc))
        logger.exception("scan.worker.error", extra={"library_id": library_id, "error": str(exc)})
    finally:
        db.close()


def _start_scan(app, library_id: int) -> None:
    status = app.state.scan_status.get(library_id)
    if status and status.get("state") == "running":
        return
    _update_scan_status(
        app,
        library_id,
        state="running",
        scanned_count=0,
        total_count=0,
        created_count=0,
        updated_count=0,
        missing_count=0,
        started_at=datetime.utcnow().isoformat() + "Z",
        finished_at=None,
        error=None,
    )
    thread = threading.Thread(target=_scan_worker, args=(app, library_id), daemon=True)
    thread.start()


@router.get("/")
async def dashboard(request: Request, db: Session = Depends(get_db), _user=Depends(get_current_user)):
    libraries = db.query(Library).order_by(Library.name).all()
    counts = {
        lib.id: db.query(func.count(MediaItem.id)).filter(MediaItem.library_id == lib.id).scalar()
        for lib in libraries
    }
    return request.app.state.templates.TemplateResponse(
        "dashboard.html", {"request": request, "libraries": libraries, "counts": counts}
    )


@router.get("/libraries/new")
async def library_new(request: Request, _user=Depends(get_current_user)):
    return request.app.state.templates.TemplateResponse(
        "library_form.html",
        {"request": request, "library": None, "paths": _list_library_paths()},
    )


@router.post("/libraries/new")
async def library_create(
    request: Request,
    name: str = Form(...),
    root_path: str = Form(...),
    enable_filesystem: bool = Form(False),
    enable_plex: bool = Form(False),
    enable_arr: bool = Form(False),
    trash_retention_days: str | None = Form("30"),
    min_seed_time_minutes: str | None = Form("0"),
    min_seed_ratio: str | None = Form("0"),
    min_seeders: str | None = Form("0"),
    display_mode: str | None = Form("flat"),
    plex_url: str | None = Form(None),
    plex_token: str | None = Form(None),
    arr_url: str | None = Form(None),
    arr_key: str | None = Form(None),
    qb_url: str | None = Form(None),
    qb_username: str | None = Form(None),
    qb_password: str | None = Form(None),
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    library = Library(
        name=name.strip(),
        root_path=root_path.strip(),
        enable_filesystem=enable_filesystem,
        enable_plex=enable_plex,
        enable_arr=enable_arr,
        trash_retention_days=_parse_int(trash_retention_days, 30),
        min_seed_time_minutes=_parse_int(min_seed_time_minutes, 0),
        min_seed_ratio=_parse_float(min_seed_ratio, 0.0),
        min_seeders=_parse_int(min_seeders, 0),
        display_mode=display_mode or "flat",
        plex_url=plex_url,
        plex_token=plex_token,
        arr_url=arr_url,
        arr_key=arr_key,
        qb_url=qb_url,
        qb_username=qb_username,
        qb_password=qb_password,
    )
    db.add(library)
    db.commit()
    logger.info("library.created", extra={"library_id": library.id, "library_name": library.name})
    _start_scan(request.app, library.id)
    return RedirectResponse(url=f"/libraries/{library.id}", status_code=302)


@router.get("/libraries/{library_id}")
async def library_detail(
    request: Request,
    library_id: int,
    q: str | None = None,
    in_trash: bool | None = None,
    missing: bool | None = None,
    min_size_gb: str | None = None,
    resolution: str | None = None,
    older_than_days: str | None = None,
    sort: str | None = None,
    direction: str | None = None,
    show: str | None = None,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    library = db.get(Library, library_id)
    if not library:
        return RedirectResponse(url="/", status_code=302)
    query = db.query(MediaItem).filter(MediaItem.library_id == library.id)
    show_prefix = None
    if library.display_mode == "tv_hierarchy" and show:
        show_prefix = f"{library.root_path.rstrip('/')}/{show}"
        query = query.filter(MediaItem.path.like(f"{show_prefix}%"))
    if q:
        like = f"%{q}%"
        query = query.filter(MediaItem.name.ilike(like))
    if in_trash is not None:
        query = query.filter(MediaItem.is_in_trash == in_trash)
    if missing is not None:
        query = query.filter(MediaItem.is_missing == missing)

    if min_size_gb:
        try:
            min_bytes = float(min_size_gb) * 1024 * 1024 * 1024
            query = query.filter(MediaItem.size_bytes >= int(min_bytes))
        except ValueError:
            pass
    if resolution:
        like = f"%{resolution}%"
        query = query.filter(MediaItem.resolution.ilike(like))
    if older_than_days:
        try:
            cutoff = datetime.utcnow() - timedelta(days=int(older_than_days))
            query = query.filter(MediaItem.modified_at <= cutoff)
        except ValueError:
            pass

    sort_map = {
        "name": MediaItem.name,
        "size": MediaItem.size_bytes,
        "modified": MediaItem.modified_at,
        "resolution": MediaItem.resolution,
    }
    sort_key = sort if sort in sort_map else "name"
    sort_dir = "desc" if direction == "desc" else "asc"
    order_col = sort_map[sort_key].desc() if sort_dir == "desc" else sort_map[sort_key].asc()
    query = query.order_by(order_col)

    items = query.limit(2000).all()

    params = dict(request.query_params)

    def sort_url(field: str) -> str:
        next_dir = "asc"
        if sort_key == field and sort_dir == "asc":
            next_dir = "desc"
        params["sort"] = field
        params["direction"] = next_dir
        return f"/libraries/{library_id}?" + urlencode(params)

    def show_url(name: str) -> str:
        return f"/libraries/{library_id}?show=" + quote(name)

    tv_tree = None
    tv_shows = None
    tv_show_counts = None
    if library.display_mode == "tv_hierarchy":
        if show:
            tv_tree = _build_tv_tree(items, library.root_path)
        else:
            tv_shows = _list_tv_shows(items, library.root_path)
            tv_show_counts = _count_tv_shows(items, library.root_path)

    return request.app.state.templates.TemplateResponse(
        "library_detail.html",
        {
            "request": request,
            "library": library,
            "items": items,
            "q": q,
            "in_trash": in_trash,
            "missing": missing,
            "min_size_gb": min_size_gb,
            "resolution": resolution,
            "older_than_days": older_than_days,
            "sort_key": sort_key,
            "sort_dir": sort_dir,
            "sort_url": sort_url,
            "tv_tree": tv_tree,
            "tv_shows": tv_shows,
            "tv_show_counts": tv_show_counts,
            "current_show": show,
            "show_url": show_url,
            "show_prefix": show_prefix,
        },
    )


@router.get("/libraries/{library_id}/edit")
async def library_edit(request: Request, library_id: int, db: Session = Depends(get_db), _user=Depends(get_current_user)):
    library = db.get(Library, library_id)
    if not library:
        return RedirectResponse(url="/", status_code=302)
    paths = _list_library_paths()
    if library.root_path and library.root_path not in paths:
        paths.append(library.root_path)
        paths = sorted(paths)
    return request.app.state.templates.TemplateResponse(
        "library_form.html",
        {"request": request, "library": library, "paths": paths},
    )


@router.get("/libraries/{library_id}/trash")
async def library_trash(request: Request, library_id: int, db: Session = Depends(get_db), _user=Depends(get_current_user)):
    library = db.get(Library, library_id)
    if not library:
        return RedirectResponse(url="/", status_code=302)
    entries = (
        db.query(TrashEntry)
        .filter(TrashEntry.library_id == library.id)
        .order_by(TrashEntry.trashed_at.desc())
        .all()
    )
    return request.app.state.templates.TemplateResponse(
        "trash.html",
        {"request": request, "library": library, "entries": entries},
    )


@router.post("/trash/{entry_id}/restore")
async def trash_restore(entry_id: int, db: Session = Depends(get_db), _user=Depends(get_current_user)):
    entry = db.get(TrashEntry, entry_id)
    if not entry:
        return RedirectResponse(url="/", status_code=302)
    library = db.get(Library, entry.library_id)
    if not library:
        return RedirectResponse(url="/", status_code=302)
    restore_from_trash(db, library, entry)
    return RedirectResponse(url=f"/libraries/{library.id}/trash", status_code=302)


@router.post("/libraries/{library_id}/edit")
async def library_update(
    request: Request,
    library_id: int,
    name: str = Form(...),
    root_path: str = Form(...),
    enable_filesystem: bool = Form(False),
    enable_plex: bool = Form(False),
    enable_arr: bool = Form(False),
    trash_retention_days: str | None = Form("30"),
    min_seed_time_minutes: str | None = Form("0"),
    min_seed_ratio: str | None = Form("0"),
    min_seeders: str | None = Form("0"),
    display_mode: str | None = Form("flat"),
    plex_url: str | None = Form(None),
    plex_token: str | None = Form(None),
    arr_url: str | None = Form(None),
    arr_key: str | None = Form(None),
    qb_url: str | None = Form(None),
    qb_username: str | None = Form(None),
    qb_password: str | None = Form(None),
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    library = db.get(Library, library_id)
    if not library:
        return RedirectResponse(url="/", status_code=302)
    library.name = name.strip()
    library.root_path = root_path.strip()
    library.enable_filesystem = enable_filesystem
    library.enable_plex = enable_plex
    library.enable_arr = enable_arr
    library.trash_retention_days = _parse_int(trash_retention_days, 30)
    library.min_seed_time_minutes = _parse_int(min_seed_time_minutes, 0)
    library.min_seed_ratio = _parse_float(min_seed_ratio, 0.0)
    library.min_seeders = _parse_int(min_seeders, 0)
    library.display_mode = display_mode or "flat"
    library.plex_url = plex_url
    library.plex_token = plex_token
    library.arr_url = arr_url
    library.arr_key = arr_key
    library.qb_url = qb_url
    library.qb_username = qb_username
    library.qb_password = qb_password
    db.commit()
    logger.info("library.updated", extra={"library_id": library.id, "library_name": library.name})
    return RedirectResponse(url=f"/libraries/{library_id}", status_code=302)


@router.post("/libraries/{library_id}/scan")
async def library_scan(request: Request, library_id: int, db: Session = Depends(get_db), _user=Depends(get_current_user)):
    library = db.get(Library, library_id)
    if library:
        _start_scan(request.app, library.id)
        logger.info("library.scan.requested", extra={"library_id": library.id})
    return RedirectResponse(url=f"/libraries/{library_id}", status_code=302)


@router.get("/libraries/{library_id}/scan-status")
async def library_scan_status(request: Request, library_id: int, _user=Depends(get_current_user)):
    status = request.app.state.scan_status.get(library_id)
    if not status:
        status = {"state": "idle"}
    return JSONResponse(status)


@router.post("/libraries/{library_id}/delete")
async def library_delete(library_id: int, db: Session = Depends(get_db), _user=Depends(get_current_user)):
    library = db.get(Library, library_id)
    if library:
        db.delete(library)
        db.commit()
        logger.info("library.deleted", extra={"library_id": library_id})
    return RedirectResponse(url="/", status_code=302)


@router.post("/libraries/{library_id}/missing/clear")
async def library_clear_missing(library_id: int, db: Session = Depends(get_db), _user=Depends(get_current_user)):
    library = db.get(Library, library_id)
    if not library:
        return RedirectResponse(url="/", status_code=302)
    deleted = (
        db.query(MediaItem)
        .filter(MediaItem.library_id == library.id, MediaItem.is_missing.is_(True), MediaItem.is_in_trash.is_(False))
        .delete(synchronize_session=False)
    )
    db.commit()
    logger.info("library.missing.cleared", extra={"library_id": library.id, "deleted": deleted})
    return RedirectResponse(url=f"/libraries/{library.id}", status_code=302)
