import os
import subprocess
import shutil
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

from sqlalchemy.orm import Session

from ..models import Library, MediaItem
from .integrations import trigger_plex_rescan
import logging

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".wmv", ".flv", ".ts"}

logger = logging.getLogger("mediawarden.filesystem")


def _iter_files(root: str) -> Iterable[Path]:
    for dirpath, _, filenames in os.walk(root):
        for filename in filenames:
            path = Path(dirpath) / filename
            if path.suffix.lower() in VIDEO_EXTENSIONS:
                yield path


def detect_resolution(path: Path) -> str | None:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    output = result.stdout.strip()
    if not output or "," not in output:
        return None
    width, height = output.split(",", 1)
    return f"{width}x{height}"


def scan_library(
    db: Session,
    library: Library,
    total_files: int | None = None,
    progress: Callable[[dict], None] | None = None,
) -> dict:
    if not library.enable_filesystem:
        return {"scanned": 0, "updated": 0, "created": 0, "missing": 0}

    logger.info("filesystem.scan.start", extra={"library_id": library.id, "root": library.root_path})

    existing = {item.path: item for item in db.query(MediaItem).filter(MediaItem.library_id == library.id).all()}
    seen = set()
    created = 0
    updated = 0

    scanned = 0
    for path in _iter_files(library.root_path):
        seen.add(str(path))
        scanned += 1
        try:
            stat = path.stat()
        except FileNotFoundError:
            continue
        item = existing.get(str(path))
        if not item:
            resolution = detect_resolution(path)
            item = MediaItem(
                library_id=library.id,
                name=path.name,
                path=str(path),
                size_bytes=stat.st_size,
                modified_at=datetime.utcfromtimestamp(stat.st_mtime),
                last_scan_at=datetime.utcnow(),
                resolution=resolution,
                is_missing=False,
            )
            db.add(item)
            created += 1
        else:
            changed = False
            if item.size_bytes != stat.st_size:
                item.size_bytes = stat.st_size
                changed = True
            mod = datetime.utcfromtimestamp(stat.st_mtime)
            if item.modified_at != mod:
                item.modified_at = mod
                changed = True
            if item.is_missing:
                item.is_missing = False
                changed = True
            item.last_scan_at = datetime.utcnow()
            if changed:
                updated += 1
        # Plex metadata sync handled via manual Plex sync action.
        if progress:
            progress(
                {
                    "scanned_count": scanned,
                    "total_count": total_files or 0,
                    "created_count": created,
                    "updated_count": updated,
                    "missing_count": 0,
                }
            )

    missing = 0
    for path, item in existing.items():
        if path not in seen and not item.is_in_trash:
            if not item.is_missing:
                item.is_missing = True
                missing += 1

    db.commit()
    if progress:
        progress(
            {
                "scanned_count": scanned,
                "total_count": total_files or scanned,
                "created_count": created,
                "updated_count": updated,
                "missing_count": missing,
            }
        )
    if library.enable_plex and (created or updated):
        trigger_plex_rescan(library)
    logger.info(
        "filesystem.scan.done",
        extra={
            "library_id": library.id,
            "created_count": created,
            "updated_count": updated,
            "missing_count": missing,
        },
    )
    return {"scanned": len(seen), "updated": updated, "created": created, "missing": missing}
