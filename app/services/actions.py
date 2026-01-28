import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from ..models import Library, MediaItem
from .trash import move_to_trash
from .qbittorrent import remove_torrent, QbittorrentError

logger = logging.getLogger("mediawarden.actions")


@dataclass
class ActionPlan:
    action: str
    media_item_id: int
    will_move_media: bool
    will_remove_torrent: bool
    warnings: list[str]


def plan_action(item: MediaItem, action: str) -> ActionPlan:
    warnings: list[str] = []
    will_move_media = action in {"media_only", "both"}
    will_remove_torrent = action in {"torrent_only", "both"}

    if will_move_media:
        if item.is_in_trash:
            warnings.append("Media is already in trash.")
        if item.is_missing:
            warnings.append("Media file is missing from disk.")
        path = Path(item.path)
        if path.exists():
            try:
                if path.stat().st_nlink > 1:
                    warnings.append("Media has multiple hardlinks; moving it could affect other seeds.")
            except OSError:
                warnings.append("Unable to check hardlink count.")

    if will_remove_torrent:
        if not item.torrent_hash:
            warnings.append("No torrent hash detected; torrent removal will be skipped.")

    return ActionPlan(
        action=action,
        media_item_id=item.id,
        will_move_media=will_move_media,
        will_remove_torrent=will_remove_torrent,
        warnings=warnings,
    )


def execute_action(db: Session, library: Library, item: MediaItem, action: str) -> dict:
    plan = plan_action(item, action)
    result = {"action": action, "media_item_id": item.id, "warnings": plan.warnings}

    if plan.will_remove_torrent:
        if library.enable_arr and library.qb_url and item.torrent_hash:
            try:
                remove_torrent(library, item.torrent_hash, delete_files=False)
                result["torrent_removed"] = True
                item.torrent_hash = None
                item.torrent_ratio = None
                item.torrent_seed_time = None
                item.torrent_seeders = None
                item.torrent_leechers = None
                db.commit()
            except QbittorrentError as exc:
                logger.warning(
                    "action.torrent.failed",
                    extra={"media_item_id": item.id, "reason": str(exc)},
                )
                result["torrent_removed"] = False
                result["torrent_reason"] = str(exc)
            except Exception as exc:
                logger.warning(
                    "action.torrent.failed",
                    extra={"media_item_id": item.id, "reason": str(exc)},
                )
                result["torrent_removed"] = False
                result["torrent_reason"] = "unexpected_error"
        else:
            logger.info(
                "action.torrent.skipped",
                extra={"media_item_id": item.id, "reason": "integration_not_configured"},
            )
            result["torrent_removed"] = False
            result["torrent_reason"] = "integration_not_configured"

    if plan.will_move_media:
        move_result = move_to_trash(db, library, item)
        result["media_moved"] = move_result.get("moved", False)
        result["media_reason"] = move_result.get("reason")
        result["trashed_path"] = move_result.get("trashed_path")

    logger.info("action.executed", extra={"media_item_id": item.id, "action": action})
    return result
