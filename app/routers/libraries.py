import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode, quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy import func
from sqlalchemy.orm import Session
from types import SimpleNamespace

from ..db import get_db
from ..deps import get_current_user
from ..models import Library, MediaItem, TrashEntry
from ..services import plex as plex_service
from apscheduler.schedulers.base import BaseScheduler
from ..services.filesystem import scan_library, _iter_files
from ..services.qbittorrent import sync_library_torrents
from ..services.trash import restore_from_trash, purge_entry_now, restore_all_trash, purge_all_trash
from ..db import SessionLocal
from ..utils import parse_duration_to_seconds

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


def _normalize_url(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned or cleaned.lower() in {"none", "null"}:
        return None
    if cleaned.startswith("http://") or cleaned.startswith("https://"):
        return cleaned
    return f"http://{cleaned}"


def _normalize_token(value: str | None) -> str | None:
    if value is None:
        return None
    token = value.strip()
    if not token:
        return None
    # If user pasted from a URL, strip query params.
    if "&" in token:
        token = token.split("&", 1)[0]
    if "?" in token:
        token = token.split("?", 1)[0]
    return token


def _normalize_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned or cleaned.lower() in {"none", "null"}:
        return None
    return cleaned


def _validate_arr_config(
    enable_arr: bool,
    qb_url: str | None,
    qb_username: str | None,
    qb_password: str | None,
    sonarr_url: str | None,
    sonarr_key: str | None,
    radarr_url: str | None,
    radarr_key: str | None,
) -> list[str]:
    if not enable_arr:
        return []
    errors: list[str] = []
    if not qb_url or not qb_username or not qb_password:
        errors.append("qBittorrent URL, username, and password are required when ARR integration is enabled.")
    has_sonarr = bool(sonarr_url and sonarr_key)
    has_radarr = bool(radarr_url and radarr_key)
    if not (has_sonarr or has_radarr):
        errors.append("Provide Sonarr or Radarr URL + API key to enable ARR integration.")
    if (sonarr_url and not sonarr_key) or (sonarr_key and not sonarr_url):
        errors.append("Sonarr URL and API key must both be filled.")
    if (radarr_url and not radarr_key) or (radarr_key and not radarr_url):
        errors.append("Radarr URL and API key must both be filled.")
    return errors


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


def _count_tv_show_flags(items: list[MediaItem], root_path: str) -> dict[str, dict[str, int]]:
    flags: dict[str, dict[str, int]] = {}
    for item in items:
        try:
            rel = Path(item.path).relative_to(root_path)
            parts = rel.parts
        except ValueError:
            parts = (item.name,)
        if not parts:
            continue
        show = parts[0]
        entry = flags.setdefault(show, {"trash": 0, "missing": 0})
        if item.is_in_trash:
            entry["trash"] += 1
        if item.is_missing:
            entry["missing"] += 1
    return flags


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
        if library.enable_arr and library.qb_url:
            try:
                updated = sync_library_torrents(db, library)
                logger.info("torrent.sync.done", extra={"library_id": library.id, "updated": updated})
            except Exception as exc:
                logger.warning("torrent.sync.failed", extra={"library_id": library.id, "error": str(exc)})
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
        {"request": request, "library": None, "paths": _list_library_paths(), "plex_sections": []},
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
    plex_section_id: str | None = Form(None),
    plex_root_path: str | None = Form(None),
    plex_sync_interval_hours: str | None = Form(None),
    sonarr_url: str | None = Form(None),
    sonarr_key: str | None = Form(None),
    radarr_url: str | None = Form(None),
    radarr_key: str | None = Form(None),
    overseerr_url: str | None = Form(None),
    overseerr_key: str | None = Form(None),
    qb_url: str | None = Form(None),
    qb_username: str | None = Form(None),
    qb_password: str | None = Form(None),
    qb_root_path: str | None = Form(None),
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    norm_plex_url = _normalize_url(plex_url)
    norm_plex_token = _normalize_token(plex_token)
    norm_plex_section = _normalize_text(plex_section_id)
    norm_plex_root = _normalize_text(plex_root_path)
    norm_plex_interval = _parse_float(plex_sync_interval_hours, 0.0) if plex_sync_interval_hours else None
    norm_sonarr_url = _normalize_url(sonarr_url)
    norm_sonarr_key = _normalize_token(sonarr_key)
    norm_radarr_url = _normalize_url(radarr_url)
    norm_radarr_key = _normalize_token(radarr_key)
    norm_overseerr_url = _normalize_url(overseerr_url)
    norm_overseerr_key = _normalize_token(overseerr_key)
    norm_qb_url = _normalize_url(qb_url)
    norm_qb_username = qb_username.strip() if qb_username else None
    norm_qb_password = qb_password.strip() if qb_password else None
    norm_qb_root = _normalize_text(qb_root_path)
    errors = _validate_arr_config(
        enable_arr,
        norm_qb_url,
        norm_qb_username,
        norm_qb_password,
        norm_sonarr_url,
        norm_sonarr_key,
        norm_radarr_url,
        norm_radarr_key,
    )
    if errors:
        form_library = SimpleNamespace(
            id=None,
            name=name,
            root_path=root_path,
            enable_filesystem=enable_filesystem,
            enable_plex=enable_plex,
            enable_arr=enable_arr,
            trash_retention_days=_parse_int(trash_retention_days, 30),
            min_seed_time_minutes=_parse_int(min_seed_time_minutes, 0),
            min_seed_ratio=_parse_float(min_seed_ratio, 0.0),
            min_seeders=_parse_int(min_seeders, 0),
            display_mode=display_mode or "flat",
            plex_url=norm_plex_url or "",
            plex_token=norm_plex_token or "",
            plex_section_id=norm_plex_section or "",
            plex_root_path=norm_plex_root or "",
            plex_sync_interval_hours=norm_plex_interval,
            sonarr_url=norm_sonarr_url or "",
            sonarr_key=norm_sonarr_key or "",
            radarr_url=norm_radarr_url or "",
            radarr_key=norm_radarr_key or "",
            overseerr_url=norm_overseerr_url or "",
            overseerr_key=norm_overseerr_key or "",
            qb_url=norm_qb_url or "",
            qb_username=norm_qb_username or "",
            qb_password=norm_qb_password or "",
            qb_root_path=norm_qb_root or "",
        )
        return request.app.state.templates.TemplateResponse(
            "library_form.html",
            {
                "request": request,
                "library": form_library,
                "paths": _list_library_paths(),
                "plex_sections": [],
                "errors": errors,
            },
        )
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
        plex_url=norm_plex_url,
        plex_token=norm_plex_token,
        plex_section_id=norm_plex_section,
        plex_root_path=norm_plex_root,
        plex_sync_interval_hours=norm_plex_interval,
        sonarr_url=norm_sonarr_url,
        sonarr_key=norm_sonarr_key,
        radarr_url=norm_radarr_url,
        radarr_key=norm_radarr_key,
        overseerr_url=norm_overseerr_url,
        overseerr_key=norm_overseerr_key,
        qb_url=norm_qb_url,
        qb_username=norm_qb_username,
        qb_password=norm_qb_password,
        qb_root_path=norm_qb_root,
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
    min_seed_time: str | None = None,
    min_ratio: str | None = None,
    min_seeders: str | None = None,
    min_leechers: str | None = None,
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
    if library.enable_arr:
        if min_seed_time:
            seconds = parse_duration_to_seconds(min_seed_time)
            if seconds is not None:
                query = query.filter(MediaItem.torrent_seed_time >= seconds)
        if min_ratio:
            try:
                query = query.filter(MediaItem.torrent_ratio >= float(min_ratio))
            except ValueError:
                pass
        if min_seeders:
            try:
                query = query.filter(MediaItem.torrent_seeders >= int(min_seeders))
            except ValueError:
                pass
        if min_leechers:
            try:
                query = query.filter(MediaItem.torrent_leechers >= int(min_leechers))
            except ValueError:
                pass
    sort_map = {
        "name": MediaItem.name,
        "size": MediaItem.size_bytes,
        "modified": MediaItem.modified_at,
        "resolution": MediaItem.resolution,
        "seed_time": MediaItem.torrent_seed_time,
        "ratio": MediaItem.torrent_ratio,
        "seeders": MediaItem.torrent_seeders,
        "leechers": MediaItem.torrent_leechers,
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
    tv_show_flags = None
    if library.display_mode == "tv_hierarchy":
        if show:
            tv_tree = _build_tv_tree(items, library.root_path)
        else:
            tv_shows = _list_tv_shows(items, library.root_path)
            tv_show_counts = _count_tv_shows(items, library.root_path)
            tv_show_flags = _count_tv_show_flags(items, library.root_path)

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
            "min_seed_time": min_seed_time,
            "min_ratio": min_ratio,
            "min_seeders": min_seeders,
            "min_leechers": min_leechers,
            "sort_key": sort_key,
            "sort_dir": sort_dir,
            "sort_url": sort_url,
            "tv_tree": tv_tree,
            "tv_shows": tv_shows,
            "tv_show_counts": tv_show_counts,
            "tv_show_flags": tv_show_flags,
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
        {
            "request": request,
            "library": library,
            "paths": paths,
            "plex_sections": request.app.state.plex_sections,
        },
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


@router.post("/trash/{entry_id}/purge")
async def trash_purge(entry_id: int, db: Session = Depends(get_db), _user=Depends(get_current_user)):
    entry = db.get(TrashEntry, entry_id)
    if not entry:
        return RedirectResponse(url="/", status_code=302)
    library = db.get(Library, entry.library_id)
    if not library:
        return RedirectResponse(url="/", status_code=302)
    purge_entry_now(db, entry)
    return RedirectResponse(url=f"/libraries/{library.id}/trash", status_code=302)


@router.post("/libraries/{library_id}/trash/restore-all")
async def trash_restore_all(library_id: int, db: Session = Depends(get_db), _user=Depends(get_current_user)):
    library = db.get(Library, library_id)
    if not library:
        return RedirectResponse(url="/", status_code=302)
    restore_all_trash(db, library)
    return RedirectResponse(url=f"/libraries/{library.id}/trash", status_code=302)


@router.post("/libraries/{library_id}/trash/purge-all")
async def trash_purge_all(library_id: int, db: Session = Depends(get_db), _user=Depends(get_current_user)):
    library = db.get(Library, library_id)
    if not library:
        return RedirectResponse(url="/", status_code=302)
    purge_all_trash(db, library)
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
    plex_section_id: str | None = Form(None),
    plex_root_path: str | None = Form(None),
    plex_sync_interval_hours: str | None = Form(None),
    sonarr_url: str | None = Form(None),
    sonarr_key: str | None = Form(None),
    radarr_url: str | None = Form(None),
    radarr_key: str | None = Form(None),
    overseerr_url: str | None = Form(None),
    overseerr_key: str | None = Form(None),
    qb_url: str | None = Form(None),
    qb_username: str | None = Form(None),
    qb_password: str | None = Form(None),
    qb_root_path: str | None = Form(None),
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    library = db.get(Library, library_id)
    if not library:
        return RedirectResponse(url="/", status_code=302)
    norm_plex_url = _normalize_url(plex_url)
    norm_plex_token = _normalize_token(plex_token)
    norm_plex_section = _normalize_text(plex_section_id)
    norm_plex_root = _normalize_text(plex_root_path)
    norm_plex_interval = _parse_float(plex_sync_interval_hours, 0.0) if plex_sync_interval_hours else None
    norm_sonarr_url = _normalize_url(sonarr_url)
    norm_sonarr_key = _normalize_token(sonarr_key)
    norm_radarr_url = _normalize_url(radarr_url)
    norm_radarr_key = _normalize_token(radarr_key)
    norm_overseerr_url = _normalize_url(overseerr_url)
    norm_overseerr_key = _normalize_token(overseerr_key)
    norm_qb_url = _normalize_url(qb_url)
    norm_qb_username = qb_username.strip() if qb_username else None
    norm_qb_password = qb_password.strip() if qb_password else None
    norm_qb_root = _normalize_text(qb_root_path)
    errors = _validate_arr_config(
        enable_arr,
        norm_qb_url,
        norm_qb_username,
        norm_qb_password,
        norm_sonarr_url,
        norm_sonarr_key,
        norm_radarr_url,
        norm_radarr_key,
    )
    if errors:
        form_library = SimpleNamespace(
            id=library.id,
            name=name,
            root_path=root_path,
            enable_filesystem=enable_filesystem,
            enable_plex=enable_plex,
            enable_arr=enable_arr,
            trash_retention_days=_parse_int(trash_retention_days, 30),
            min_seed_time_minutes=_parse_int(min_seed_time_minutes, 0),
            min_seed_ratio=_parse_float(min_seed_ratio, 0.0),
            min_seeders=_parse_int(min_seeders, 0),
            display_mode=display_mode or "flat",
            plex_url=norm_plex_url or "",
            plex_token=norm_plex_token or "",
            plex_section_id=norm_plex_section or "",
            plex_root_path=norm_plex_root or "",
            plex_sync_interval_hours=norm_plex_interval,
            sonarr_url=norm_sonarr_url or "",
            sonarr_key=norm_sonarr_key or "",
            radarr_url=norm_radarr_url or "",
            radarr_key=norm_radarr_key or "",
            overseerr_url=norm_overseerr_url or "",
            overseerr_key=norm_overseerr_key or "",
            qb_url=norm_qb_url or "",
            qb_username=norm_qb_username or "",
            qb_password=norm_qb_password or "",
            qb_root_path=norm_qb_root or "",
        )
        paths = _list_library_paths()
        if root_path and root_path not in paths:
            paths.append(root_path)
        return request.app.state.templates.TemplateResponse(
            "library_form.html",
            {
                "request": request,
                "library": form_library,
                "paths": sorted(paths),
                "plex_sections": request.app.state.plex_sections,
                "errors": errors,
            },
        )
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
    library.plex_url = norm_plex_url
    library.plex_token = norm_plex_token
    library.plex_section_id = norm_plex_section
    library.plex_root_path = norm_plex_root
    library.plex_sync_interval_hours = norm_plex_interval
    library.sonarr_url = norm_sonarr_url
    library.sonarr_key = norm_sonarr_key
    library.radarr_url = norm_radarr_url
    library.radarr_key = norm_radarr_key
    library.overseerr_url = norm_overseerr_url
    library.overseerr_key = norm_overseerr_key
    library.qb_url = norm_qb_url
    library.qb_username = norm_qb_username
    library.qb_password = norm_qb_password
    library.qb_root_path = norm_qb_root
    db.commit()
    scheduler: BaseScheduler | None = getattr(request.app.state, "scheduler", None)
    if scheduler:
        job_id = f"plex_sync_{library.id}"
        if library.plex_sync_interval_hours and library.plex_sync_interval_hours > 0:
            scheduler.add_job(
                "app.scheduler:_sync_plex_library",
                "interval",
                hours=library.plex_sync_interval_hours,
                id=job_id,
                replace_existing=True,
                args=[library.id],
            )
        else:
            if scheduler.get_job(job_id):
                scheduler.remove_job(job_id)
    logger.info("library.updated", extra={"library_id": library.id, "library_name": library.name})
    return RedirectResponse(url=f"/libraries/{library_id}", status_code=302)


@router.post("/libraries/{library_id}/scan")
async def library_scan(request: Request, library_id: int, db: Session = Depends(get_db), _user=Depends(get_current_user)):
    library = db.get(Library, library_id)
    if library:
        _start_scan(request.app, library.id)
        logger.info("library.scan.requested", extra={"library_id": library.id})
    return RedirectResponse(url=f"/libraries/{library_id}", status_code=302)


@router.post("/libraries/{library_id}/plex-sync")
async def library_plex_sync(library_id: int, db: Session = Depends(get_db), _user=Depends(get_current_user)):
    library = db.get(Library, library_id)
    if not library or not library.enable_plex:
        return RedirectResponse(url=f"/libraries/{library_id}", status_code=302)
    try:
        mapping = plex_service.fetch_metadata_map(library)
        if mapping:
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
            logger.info("plex.sync.done", extra={"library_id": library.id, "updated": updated})
    except Exception as exc:
        logger.warning("plex.sync.failed", extra={"library_id": library.id, "error": str(exc)})
    return RedirectResponse(url=f"/libraries/{library_id}", status_code=302)


@router.post("/libraries/{library_id}/torrent-sync")
async def library_torrent_sync(library_id: int, db: Session = Depends(get_db), _user=Depends(get_current_user)):
    library = db.get(Library, library_id)
    if not library or not library.enable_arr:
        return RedirectResponse(url=f"/libraries/{library_id}", status_code=302)
    try:
        updated = sync_library_torrents(db, library)
        logger.info("torrent.sync.done", extra={"library_id": library.id, "updated": updated})
    except Exception as exc:
        logger.warning("torrent.sync.failed", extra={"library_id": library.id, "error": str(exc)})
    return RedirectResponse(url=f"/libraries/{library_id}", status_code=302)


@router.post("/libraries/{library_id}/plex-discover")
async def library_plex_discover(request: Request, library_id: int, db: Session = Depends(get_db), _user=Depends(get_current_user)):
    library = db.get(Library, library_id)
    if not library or not library.enable_plex:
        return RedirectResponse(url=f"/libraries/{library_id}/edit", status_code=302)
    try:
        sections = plex_service.get_sections(library)
        request.app.state.plex_sections = sections
        logger.info("plex.sections.loaded", extra={"library_id": library.id, "count": len(sections)})
    except Exception as exc:
        logger.warning("plex.sections.failed", extra={"library_id": library.id, "error": str(exc)})
        request.app.state.plex_sections = []
    return RedirectResponse(url=f"/libraries/{library_id}/edit", status_code=302)


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
