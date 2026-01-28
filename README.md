# MediaWarden

Self-hosted media library governance for NAS environments. MediaWarden tracks libraries, supports safe trashing workflows, and provides a simple admin UI with dry-run safety.

## Stack
- Python 3.11
- FastAPI
- SQLite (SQLAlchemy + Alembic)
- APScheduler
- Docker

## Features
- Multiple libraries with per-library configuration
- Filesystem scan with resolution detection (ffprobe if available)
- Safe actions: dry-run preview, explicit confirmation, .trash staging
- Trash retention with scheduled purge job
- Simple admin UI (tables + filters + bulk actions)
- Structured JSON logging with rotation
- Plex integration (sync metadata + rescan)
- ARR + qBittorrent integration (torrent metadata + safe removal)

## Quick start (Docker)
1. Copy `.env.example` to `.env` and edit `SECRET_KEY`.
2. Update `docker-compose.yml` with your media bind mounts.
3. Run:
   ```bash
   docker compose up -d --build
   ```
4. Open `http://<nas-ip>:8000` and create the first admin user.

## Library paths in the UI
When using the provided compose file, mount your media into `/libraries/...` and use those paths in the UI.

Example compose mounts:
```yaml
- "/volume1/Cassidy Share/Plex Media/Movies:/libraries/movies"
- "/volume1/Cassidy Share/Plex Media/TV Shows:/libraries/tv"
```

## Notes
- Media is never deleted directly: files are moved to per-library `.trash`.
- Torrent removal never deletes files directly; it only removes torrents while media is handled via `.trash`.

## ARR + qBittorrent integration
Enable ARR integration on a library to:
- Sync torrent metadata (seed time, ratio, seeders, leechers)
- Filter/sort by torrent stats
- Remove torrents safely without deleting files

Minimum required settings when ARR integration is enabled:
- qBittorrent URL, username, password
- Sonarr or Radarr URL + API key

Optional:
- Overseerr URL + API key
- qBittorrent root path (used to map qBittorrent paths to the library root)

## Development
```bash
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload
```

## License
MIT
