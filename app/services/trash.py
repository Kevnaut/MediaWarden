import logging
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..models import Library, MediaItem, TrashEntry

logger = logging.getLogger("mediawarden.trash")


def _trash_root(library: Library) -> Path:
    return Path(library.root_path) / ".trash"


def move_to_trash(db: Session, library: Library, item: MediaItem) -> dict:
    src = Path(item.path)
    if item.is_in_trash:
        return {"moved": False, "reason": "already_in_trash"}
    if not src.exists():
        return {"moved": False, "reason": "missing"}

    try:
        relative = src.relative_to(library.root_path)
    except ValueError:
        relative = src.name
    trash_root = _trash_root(library)
    dst = trash_root / relative
    dst.parent.mkdir(parents=True, exist_ok=True)

    shutil.move(str(src), str(dst))
    # Remove now-empty parent folders up to library root.
    try:
        parent = src.parent
        root = Path(library.root_path)
        while parent != root and parent.exists():
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
    except Exception:
        pass

    trashed_at = datetime.utcnow()
    purge_after = trashed_at + timedelta(days=library.trash_retention_days)

    item.is_in_trash = True
    item.is_missing = False
    item.trashed_at = trashed_at
    item.trashed_path = str(dst)

    entry = TrashEntry(
        library_id=library.id,
        media_item_id=item.id,
        original_path=str(src),
        trashed_path=str(dst),
        trashed_at=trashed_at,
        purge_after=purge_after,
    )
    db.add(entry)
    db.commit()

    logger.info(
        "trash.move",
        extra={"library_id": library.id, "media_item_id": item.id, "src": str(src), "dst": str(dst)},
    )
    return {"moved": True, "trashed_path": str(dst), "purge_after": purge_after.isoformat()}


def restore_from_trash(db: Session, library: Library, entry: TrashEntry) -> dict:
    src = Path(entry.trashed_path)
    dst = Path(entry.original_path)
    if not src.exists():
        return {"restored": False, "reason": "missing"}
    if ".trash" not in src.parts:
        return {"restored": False, "reason": "invalid_trash_path"}
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    item = db.get(MediaItem, entry.media_item_id)
    if item:
        item.is_in_trash = False
        item.trashed_at = None
        item.trashed_path = None
        item.path = str(dst)
    db.delete(entry)
    db.commit()
    logger.info(
        "trash.restore",
        extra={"library_id": library.id, "media_item_id": entry.media_item_id, "dst": str(dst)},
    )
    return {"restored": True, "restored_path": str(dst)}


def purge_expired_trash() -> None:
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        entries = db.query(TrashEntry).filter(TrashEntry.purge_after <= now).all()
        purged = 0
        for entry in entries:
            path = Path(entry.trashed_path)
            if path.exists() and ".trash" in path.parts:
                try:
                    if path.is_dir():
                        shutil.rmtree(path)
                    else:
                        path.unlink()
                except Exception as exc:
                    logger.error("trash.purge.error", extra={"path": str(path), "error": str(exc)})
                    continue
            db.delete(entry)
            purged += 1
        db.commit()
        if purged:
            logger.info("trash.purge.done", extra={"purged": purged})
    finally:
        db.close()
