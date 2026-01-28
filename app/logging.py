import json
import logging
import logging.handlers
from datetime import datetime
from pathlib import Path

from .config import settings


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        reserved = {
            "name",
            "msg",
            "args",
            "levelname",
            "levelno",
            "pathname",
            "filename",
            "module",
            "exc_info",
            "exc_text",
            "stack_info",
            "lineno",
            "funcName",
            "created",
            "msecs",
            "relativeCreated",
            "thread",
            "threadName",
            "processName",
            "process",
        }
        for key, value in record.__dict__.items():
            if key not in reserved and key not in payload:
                payload[key] = value
        return json.dumps(payload, ensure_ascii=True)


def setup_logging() -> None:
    settings.ensure_paths()
    log_dir = Path(settings.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "mediawarden.log"

    handler = logging.handlers.TimedRotatingFileHandler(
        log_path, when="midnight", backupCount=30, encoding="utf-8"
    )
    handler.setFormatter(JsonFormatter())

    console = logging.StreamHandler()
    console.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.setLevel(settings.log_level.upper())
    root.handlers.clear()
    root.addHandler(handler)
    root.addHandler(console)
