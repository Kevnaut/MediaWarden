import logging
from datetime import datetime
from urllib.parse import urlencode, urljoin

import requests

from ..models import Library

logger = logging.getLogger("mediawarden.plex")


def _headers():
    return {"Accept": "application/json"}


def _base_url(library: Library) -> str:
    return library.plex_url.rstrip("/") + "/"


def _token_params(library: Library) -> dict:
    return {"X-Plex-Token": library.plex_token} if library.plex_token else {}


def get_sections(library: Library) -> list[dict]:
    url = urljoin(_base_url(library), "library/sections")
    resp = requests.get(url, params=_token_params(library), headers=_headers(), timeout=10)
    resp.raise_for_status()
    data = resp.json()
    return data.get("MediaContainer", {}).get("Directory", [])


def _get_section_by_id(library: Library, section_id: str) -> dict | None:
    sections = get_sections(library)
    for section in sections:
        if str(section.get("key")) == str(section_id):
            return section
    return None


def find_section_for_path(library: Library) -> dict | None:
    if library.plex_section_id:
        return {"key": library.plex_section_id}
    sections = get_sections(library)
    root = (library.plex_root_path or library.root_path).rstrip("/")
    best = None
    best_len = -1
    for section in sections:
        locations = section.get("Location", [])
        if isinstance(locations, dict):
            locations = [locations]
        for loc in locations:
            path = loc.get("path", "").rstrip("/")
            if root.startswith(path) and len(path) > best_len:
                best = section
                best_len = len(path)
    return best


def refresh_section(library: Library, path: str | None = None) -> None:
    section = find_section_for_path(library)
    if not section:
        logger.warning("plex.section.missing", extra={"library_id": library.id})
        return
    section_id = section.get("key")
    params = _token_params(library)
    if path:
        params["path"] = path
    url = urljoin(_base_url(library), f"library/sections/{section_id}/refresh")
    resp = requests.get(url, params=params, headers=_headers(), timeout=15)
    resp.raise_for_status()
    logger.info("plex.refresh", extra={"library_id": library.id, "section": section_id, "path": path or ""})


def _map_path(library: Library, plex_path: str, plex_roots: list[str]) -> str:
    for root in plex_roots:
        root = root.rstrip("/")
        if plex_path.startswith(root):
            return library.root_path.rstrip("/") + plex_path[len(root) :]
    return plex_path


def fetch_metadata_map(library: Library, limit: int | None = None) -> dict:
    section = find_section_for_path(library)
    if not section:
        logger.warning("plex.section.missing", extra={"library_id": library.id})
        return {}
    section_id = section.get("key")
    plex_roots: list[str] = []
    if library.plex_root_path:
        plex_roots.append(library.plex_root_path)
    elif library.plex_section_id:
        section_full = _get_section_by_id(library, library.plex_section_id)
        if section_full and section_full.get("Location"):
            locations = section_full.get("Location")
            if isinstance(locations, dict):
                locations = [locations]
            for loc in locations:
                path = loc.get("path")
                if path:
                    plex_roots.append(path)
    params = _token_params(library)
    if limit:
        params["X-Plex-Container-Size"] = str(limit)
    url = urljoin(_base_url(library), f"library/sections/{section_id}/all")
    resp = requests.get(url, params=params, headers=_headers(), timeout=30)
    resp.raise_for_status()
    data = resp.json()
    items = data.get("MediaContainer", {}).get("Metadata", [])
    mapping: dict[str, dict] = {}
    for item in items:
        last_viewed = item.get("lastViewedAt")
        updated_at = item.get("updatedAt")
        touched_at = None
        if last_viewed:
            touched_at = int(last_viewed)
        if updated_at:
            touched_at = max(touched_at or 0, int(updated_at)) or int(updated_at)
        media = item.get("Media", [])
        if isinstance(media, dict):
            media = [media]
        for media_item in media:
            width = media_item.get("width")
            height = media_item.get("height")
            parts = media_item.get("Part", [])
            if isinstance(parts, dict):
                parts = [parts]
            for part in parts:
                file_path = part.get("file")
                if file_path:
                    if not width:
                        width = part.get("width")
                    if not height:
                        height = part.get("height")
                    resolution = None
                    if width and height:
                        resolution = f"{width}x{height}"
                    mapped = _map_path(library, file_path, plex_roots or [file_path])
                    mapping[mapped] = {
                        "touched_at": datetime.utcfromtimestamp(touched_at) if touched_at else None,
                        "resolution": resolution,
                    }
    return mapping
