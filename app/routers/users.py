import logging

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_user
from ..models import User
from ..security import hash_password

router = APIRouter()
logger = logging.getLogger("mediawarden.users")


@router.get("/users")
async def users_list(request: Request, db: Session = Depends(get_db), user=Depends(get_current_user)):
    users = db.query(User).order_by(User.username).all()
    return request.app.state.templates.TemplateResponse(
        "users.html", {"request": request, "users": users, "current_user": user}
    )


@router.post("/users")
async def users_create(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    existing = db.query(User).filter(User.username == username.strip()).first()
    if existing:
        return request.app.state.templates.TemplateResponse(
            "users.html", {"request": request, "users": db.query(User).all(), "error": "User exists"}, status_code=400
        )
    user = User(username=username.strip(), password_hash=hash_password(password))
    db.add(user)
    db.commit()
    logger.info("user.created", extra={"username": user.username})
    return RedirectResponse(url="/users", status_code=302)


@router.post("/users/{user_id}/delete")
async def users_delete(user_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    if user.id == user_id:
        return RedirectResponse(url="/users", status_code=302)
    target = db.get(User, user_id)
    if target:
        db.delete(target)
        db.commit()
        logger.info("user.deleted", extra={"user_id": user_id})
    return RedirectResponse(url="/users", status_code=302)
