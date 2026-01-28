import logging

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import User
from ..security import hash_password, sign_session, verify_password
from ..deps import SESSION_COOKIE

router = APIRouter()
logger = logging.getLogger("mediawarden.auth")


def _has_users(db: Session) -> bool:
    return db.query(User).count() > 0


@router.get("/setup")
async def setup_form(request: Request, db: Session = Depends(get_db)):
    if _has_users(db):
        return RedirectResponse(url="/login", status_code=302)
    return request.app.state.templates.TemplateResponse("setup.html", {"request": request})


@router.post("/setup")
async def setup_create(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    if _has_users(db):
        return RedirectResponse(url="/login", status_code=302)
    user = User(username=username.strip(), password_hash=hash_password(password))
    db.add(user)
    db.commit()
    logger.info("auth.setup", extra={"username": user.username})
    token = sign_session(user.id)
    response = RedirectResponse(url="/", status_code=302)
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax")
    return response


@router.get("/login")
async def login_form(request: Request, db: Session = Depends(get_db)):
    if not _has_users(db):
        return RedirectResponse(url="/setup", status_code=302)
    return request.app.state.templates.TemplateResponse("login.html", {"request": request, "error": None})


@router.post("/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.username == username.strip()).first()
    if not user or not verify_password(password, user.password_hash):
        logger.info("auth.login.failed", extra={"username": username.strip()})
        return request.app.state.templates.TemplateResponse(
            "login.html", {"request": request, "error": "Invalid credentials"}, status_code=401
        )
    logger.info("auth.login", extra={"username": user.username})
    token = sign_session(user.id)
    response = RedirectResponse(url="/", status_code=302)
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax")
    return response


@router.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie(SESSION_COOKIE)
    logger.info("auth.logout")
    return response
