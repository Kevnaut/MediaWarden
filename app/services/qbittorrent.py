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
    # Heuristic: map by matching a path segment to the library root basename or library name.
    lib_root = _normalize_path(library.root_path).rstrip("/")
    lib_base = lib_root.split("/")[-1].lower()
    lib_name = (library.name or "").strip().lower()
    parts = normalized.split("/")
    for idx, part in enumerate(parts):
        lower = part.lower()
        if lower and (lower == lib_base or (lib_name and lower == lib_name)):
            suffix = "/" + "/".join(parts[idx + 1:]) if idx + 1 < len(parts) else ""
            return _normalize_path(f"{lib_root}{suffix}")
    return normalized


def _torrent_files(session: requests.Session, library: Library, torrent_hash: str) -> list[dict]:
    url = f"{library.qb_url}/api/v2/torrents/files"
    resp = session.get(url, params={"hash": torrent_hash}, timeout=10)
    resp.raise_for_status()
    files = resp.json()
    entries = []
    for entry in files:
        name = entry.get("name")
        if not name:
            continue
        entries.append({"name": name, "size": entry.get("size")})
    return entries


def _build_file_paths(torrent: dict, files: list[dict]) -> list[tuple[str, int | None]]:
    save_path = _normalize_path(torrent.get("save_path") or "")
    content_path = _normalize_path(torrent.get("content_path") or "")
    paths: list[tuple[str, int | None]] = []
    if files and save_path:
        for entry in files:
            paths.append((os.path.join(save_path, entry["name"]), entry.get("size")))
        return paths
    if content_path:
        if files:
            for entry in files:
                paths.append((os.path.join(content_path, entry["name"]), entry.get("size")))
        else:
            paths.append((content_path, None))
    return paths


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


def build_torrent_index(library: Library) -> tuple[dict[str, dict], dict[str, list[tuple[dict, int | None]]], list[str]]:
    session, torrents = fetch_torrents(library)
    index: dict[str, dict] = {}
    basename_index: dict[str, list[tuple[dict, int | None]]] = {}
    sample_paths: list[str] = []
    for torrent in torrents:
        torrent_hash = torrent.get("hash")
        if not torrent_hash:
            continue
        try:
            files = _torrent_files(session, library, torrent_hash)
        except Exception as exc:
            logger.warning("qbittorrent.files.failed", extra={"hash": torrent_hash, "error": str(exc)})
            files = []
        for raw_path, size in _build_file_paths(torrent, files):
            mapped = _map_qb_path(library, raw_path)
            index[mapped] = torrent
            base = os.path.basename(mapped)
            if base:
                basename_index.setdefault(base, []).append((torrent, size))
            if len(sample_paths) < 3:
                sample_paths.append(mapped)
    return index, basename_index, sample_paths


def sync_library_torrents(db: Session, library: Library) -> int:
    if not library.enable_arr or not library.qb_url:
        return 0
    index, basename_index, sample_paths = build_torrent_index(library)
    items = db.query(MediaItem).filter(MediaItem.library_id == library.id).all()
    updated = 0
    matched = 0
    suffix_maps: dict[int, dict[str, list[MediaItem]]] = {3: {}, 2: {}, 1: {}}
    for item in items:
        parts = _normalize_path(item.path).split("/")
        for depth in (3, 2, 1):
            if len(parts) >= depth:
                key = "/".join(parts[-depth:]).lower()
                suffix_maps[depth].setdefault(key, []).append(item)
    for item in items:
        torrent = index.get(item.path)
        if not torrent:
            candidates = basename_index.get(os.path.basename(item.path) or "", [])
            if len(candidates) == 1:
                torrent = candidates[0][0]
            elif len(candidates) > 1 and item.size_bytes:
                for cand, size in candidates:
                    if size and abs(size - item.size_bytes) < 2 * 1024 * 1024:
                        torrent = cand
                        break
        if not torrent:
            parts = _normalize_path(item.path).split("/")
            for depth in (3, 2, 1):
                if len(parts) < depth:
                    continue
                key = "/".join(parts[-depth:]).lower()
                matches = suffix_maps[depth].get(key, [])
                if len(matches) == 1:
                    torrent = index.get(matches[0].path)
                    if torrent:
                        break
        changed = False
        if torrent:
            matched += 1
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
    logger.info(
        "torrent.sync.mapped",
        extra={"library_id": library.id, "matched": matched, "total": len(items), "samples": sample_paths},
    )
    return updated
