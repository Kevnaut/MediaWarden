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
- Torrent integrations are stubbed; filesystem mode is fully functional.

## Development
```bash
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload
```

## License
MIT
