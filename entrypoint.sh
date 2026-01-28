#!/usr/bin/env sh
set -e

alembic upgrade head

exec uvicorn app.main:app --host 0.0.0.0 --port ${APP_PORT:-8000}
