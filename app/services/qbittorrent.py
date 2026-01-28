import logging
import os
import requests

from sqlalchemy.orm import Session

from ..models import Library, MediaItem

logger = logging.getLogger("mediawarden.qbittorrent")


class QbittorrentError(RuntimeError):
    pass


def _login(library: Library) -> requests.Session:
    if not library.qb_url or not library.qb_username or not library.qb_password:
        raise QbittorrentError("qBittorrent credentials not configured")
    session = requests.Session()
    url = f"{library.qb_url}/api/v2/auth/login"
    resp = session.post(
        url,
        data={"username": library.qb_username, "password": library.qb_password},
        timeout=10,
    )
    if resp.status_code != 200 or resp.text.strip() != "Ok.":
        raise QbittorrentError("qBittorrent login failed")
    return session


def _normalize_path(path: str) -> str:
    return path.replace("\\", "/")


def _map_qb_path(library: Library, path: str) -> str:
    normalized = _normalize_path(path)
    qb_root = library.qb_root_path
    if qb_root:
        qb_root_norm = _normalize_path(qb_root.rstrip("/"))
        if normalized.startswith(qb_root_norm):
            suffix = normalized[len(qb_root_norm):]
            return _normalize_path(f"{library.root_path.rstrip('/')}{suffix}")
    return normalized


def _torrent_files(session: requests.Session, library: Library, torrent_hash: str) -> list[str]:
    url = f"{library.qb_url}/api/v2/torrents/files"
    resp = session.get(url, params={"hash": torrent_hash}, timeout=10)
    resp.raise_for_status()
    files = resp.json()
    names = []
    for entry in files:
        name = entry.get("name")
        if name:
            names.append(name)
    return names


def _build_file_paths(torrent: dict, files: list[str]) -> list[str]:
    save_path = _normalize_path(torrent.get("save_path") or "")
    content_path = _normalize_path(torrent.get("content_path") or "")
    if files and save_path:
        return [os.path.join(save_path, name) for name in files]
    if content_path:
        if files:
            return [os.path.join(content_path, name) for name in files]
        return [content_path]
    return []


def fetch_torrents(library: Library) -> tuple[requests.Session, list[dict]]:
    session = _login(library)
    url = f"{library.qb_url}/api/v2/torrents/info"
    resp = session.get(url, timeout=15)
    resp.raise_for_status()
    return session, resp.json()


def remove_torrent(library: Library, torrent_hash: str, delete_files: bool = False) -> None:
    session = _login(library)
    url = f"{library.qb_url}/api/v2/torrents/delete"
    resp = session.post(url, data={"hashes": torrent_hash, "deleteFiles": str(delete_files).lower()}, timeout=10)
    resp.raise_for_status()


def build_torrent_index(library: Library) -> dict[str, dict]:
    session, torrents = fetch_torrents(library)
    index: dict[str, dict] = {}
    for torrent in torrents:
        torrent_hash = torrent.get("hash")
        if not torrent_hash:
            continue
        try:
            files = _torrent_files(session, library, torrent_hash)
        except Exception as exc:
            logger.warning("qbittorrent.files.failed", extra={"hash": torrent_hash, "error": str(exc)})
            files = []
        for raw_path in _build_file_paths(torrent, files):
            mapped = _map_qb_path(library, raw_path)
            index[mapped] = torrent
    return index


def sync_library_torrents(db: Session, library: Library) -> int:
    if not library.enable_arr or not library.qb_url:
        return 0
    index = build_torrent_index(library)
    items = db.query(MediaItem).filter(MediaItem.library_id == library.id).all()
    updated = 0
    for item in items:
        torrent = index.get(item.path)
        changed = False
        if torrent:
            ratio = torrent.get("ratio")
            seed_time = torrent.get("seeding_time")
            seeders = torrent.get("num_seeds")
            leechers = torrent.get("num_leechs")
            if item.torrent_hash != torrent.get("hash"):
                item.torrent_hash = torrent.get("hash")
                changed = True
            if ratio is not None and item.torrent_ratio != float(ratio):
                item.torrent_ratio = float(ratio)
                changed = True
            if seed_time is not None and item.torrent_seed_time != int(seed_time):
                item.torrent_seed_time = int(seed_time)
                changed = True
            if seeders is not None and item.torrent_seeders != int(seeders):
                item.torrent_seeders = int(seeders)
                changed = True
            if leechers is not None and item.torrent_leechers != int(leechers):
                item.torrent_leechers = int(leechers)
                changed = True
        else:
            if item.torrent_hash is not None:
                item.torrent_hash = None
                changed = True
            if item.torrent_ratio is not None:
                item.torrent_ratio = None
                changed = True
            if item.torrent_seed_time is not None:
                item.torrent_seed_time = None
                changed = True
            if item.torrent_seeders is not None:
                item.torrent_seeders = None
                changed = True
            if item.torrent_leechers is not None:
                item.torrent_leechers = None
                changed = True
        if changed:
            updated += 1
    db.commit()
    return updated
