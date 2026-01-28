FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN useradd -m appuser

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./
COPY entrypoint.sh ./

RUN chmod +x /app/entrypoint.sh && mkdir -p /data /logs && chown -R appuser:appuser /app /data /logs

USER appuser

EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]
