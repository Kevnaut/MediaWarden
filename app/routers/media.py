import logging

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_user
from ..models import Library, MediaItem
from ..services.actions import plan_action, execute_action
from ..services.integrations import evaluate_torrent_rules

router = APIRouter()
logger = logging.getLogger("mediawarden.actions")


@router.post("/media/bulk/preview")
async def bulk_preview(
    request: Request,
    media_ids: list[int] = Form([]),
    action: str = Form(...),
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    items = db.query(MediaItem).filter(MediaItem.id.in_(media_ids)).all() if media_ids else []
    if not items:
        return RedirectResponse(url="/", status_code=302)
    library = db.get(Library, items[0].library_id)
    plans = [(item, plan_action(item, action)) for item in items]
    return request.app.state.templates.TemplateResponse(
        "bulk_action_preview.html",
        {
            "request": request,
            "library": library,
            "action": action,
            "plans": plans,
            "execute_url": "/media/bulk/execute",
        },
    )


@router.post("/media/bulk/execute")
async def bulk_execute(
    request: Request,
    media_ids: list[int] = Form([]),
    action: str = Form(...),
    confirm: str = Form(...),
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    if confirm != "yes":
        return RedirectResponse(url="/", status_code=302)
    items = db.query(MediaItem).filter(MediaItem.id.in_(media_ids)).all() if media_ids else []
    if not items:
        return RedirectResponse(url="/", status_code=302)
    library = db.get(Library, items[0].library_id)
    for item in items:
        execute_action(db, library, item, action)
    logger.info("action.bulk.confirmed", extra={"count": len(items), "action": action})
    return RedirectResponse(url=f"/libraries/{library.id}", status_code=302)


@router.post("/media/bulk-show/preview")
async def bulk_preview_show(
    request: Request,
    library_id: int = Form(...),
    show_names: list[str] = Form([]),
    action: str = Form(...),
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    library = db.get(Library, library_id)
    if not library or not show_names:
        return RedirectResponse(url=f"/libraries/{library_id}", status_code=302)
    conditions = []
    for name in show_names:
        prefix = f"{library.root_path.rstrip('/')}/{name}"
        conditions.append(MediaItem.path.like(f"{prefix}%"))
    items = (
        db.query(MediaItem)
        .filter(MediaItem.library_id == library.id)
        .filter(or_(*conditions))
        .all()
    )
    if not items:
        return RedirectResponse(url=f"/libraries/{library.id}", status_code=302)
    plans = [(item, plan_action(item, action)) for item in items]
    return request.app.state.templates.TemplateResponse(
        "bulk_action_preview.html",
        {
            "request": request,
            "library": library,
            "action": action,
            "plans": plans,
            "execute_url": "/media/bulk-show/execute",
            "library_id": library.id,
            "show_names": show_names,
        },
    )


@router.post("/media/bulk-show/execute")
async def bulk_execute_show(
    request: Request,
    library_id: int = Form(...),
    show_names: list[str] = Form([]),
    action: str = Form(...),
    confirm: str = Form(...),
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    if confirm != "yes":
        return RedirectResponse(url=f"/libraries/{library_id}", status_code=302)
    library = db.get(Library, library_id)
    if not library or not show_names:
        return RedirectResponse(url=f"/libraries/{library_id}", status_code=302)
    conditions = []
    for name in show_names:
        prefix = f"{library.root_path.rstrip('/')}/{name}"
        conditions.append(MediaItem.path.like(f"{prefix}%"))
    items = (
        db.query(MediaItem)
        .filter(MediaItem.library_id == library.id)
        .filter(or_(*conditions))
        .all()
    )
    if not items:
        return RedirectResponse(url=f"/libraries/{library.id}", status_code=302)
    for item in items:
        execute_action(db, library, item, action)
    logger.info("action.bulk.show.confirmed", extra={"count": len(items), "action": action})
    return RedirectResponse(url=f"/libraries/{library.id}", status_code=302)


@router.post("/media/{media_id}/preview")
async def action_preview(
    request: Request,
    media_id: int,
    action: str = Form(...),
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    item = db.get(MediaItem, media_id)
    if not item:
        return RedirectResponse(url="/", status_code=302)
    library = db.get(Library, item.library_id)
    plan = plan_action(item, action)
    rule_eval = evaluate_torrent_rules(library, item)
    logger.info("action.preview", extra={"media_item_id": item.id, "action": action})
    return request.app.state.templates.TemplateResponse(
        "action_preview.html",
        {"request": request, "item": item, "library": library, "plan": plan, "rule_eval": rule_eval},
    )


@router.post("/media/{media_id}/execute")
async def action_execute(
    request: Request,
    media_id: int,
    action: str = Form(...),
    confirm: str = Form(...),
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    if confirm != "yes":
        return RedirectResponse(url="/", status_code=302)
    item = db.get(MediaItem, media_id)
    if not item:
        return RedirectResponse(url="/", status_code=302)
    library = db.get(Library, item.library_id)
    if not library:
        return RedirectResponse(url="/", status_code=302)
    execute_action(db, library, item, action)
    logger.info("action.confirmed", extra={"media_item_id": item.id, "action": action})
    return RedirectResponse(url=f"/libraries/{library.id}", status_code=302)
