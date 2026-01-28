from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from .db import get_db
from .models import User
from .security import unsign_session


SESSION_COOKIE = "mw_session"


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    user_id = unsign_session(token)
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    user = db.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    return user
