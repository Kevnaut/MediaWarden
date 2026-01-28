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


def find_section_for_path(library: Library) -> dict | None:
    sections = get_sections(library)
    root = library.root_path.rstrip("/")
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


def fetch_metadata_map(library: Library, limit: int | None = None) -> dict:
    section = find_section_for_path(library)
    if not section:
        logger.warning("plex.section.missing", extra={"library_id": library.id})
        return {}
    section_id = section.get("key")
    params = _token_params(library)
    if limit:
        params["X-Plex-Container-Size"] = str(limit)
    url = urljoin(_base_url(library), f"library/sections/{section_id}/all")
    resp = requests.get(url, params=params, headers=_headers(), timeout=30)
    resp.raise_for_status()
    data = resp.json()
    items = data.get("MediaContainer", {}).get("Metadata", [])
    mapping = {}
    for item in items:
        last_viewed = item.get("lastViewedAt")
        if not last_viewed:
            continue
        media = item.get("Media", [])
        if isinstance(media, dict):
            media = [media]
        for media_item in media:
            parts = media_item.get("Part", [])
            if isinstance(parts, dict):
                parts = [parts]
            for part in parts:
                file_path = part.get("file")
                if file_path:
                    mapping[file_path] = datetime.utcfromtimestamp(int(last_viewed))
    return mapping
