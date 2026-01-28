from pathlib import Path

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..deps import get_current_user

router = APIRouter()


def _tail(path: Path, max_lines: int = 400) -> str:
    if not path.exists():
        return ""
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        lines = handle.readlines()
    return "".join(lines[-max_lines:])


@router.get("/logs")
async def logs_viewer(request: Request, db: Session = Depends(get_db), _user=Depends(get_current_user)):
    log_path = Path(settings.log_dir) / "mediawarden.log"
    content = _tail(log_path)
    return request.app.state.templates.TemplateResponse(
        "logs.html", {"request": request, "log_path": str(log_path), "content": content}
    )
