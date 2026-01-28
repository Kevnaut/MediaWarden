import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value if value not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    app_name: str = "MediaWarden"
    secret_key: str = _env("SECRET_KEY", "change-me")
    database_url: str = _env("DATABASE_URL", "sqlite:///./data/mediawarden.db")
    log_dir: str = _env("LOG_DIR", "/logs")
    log_level: str = _env("LOG_LEVEL", "INFO")
    timezone: str = _env("TIMEZONE", "UTC")
    app_host: str = _env("APP_HOST", "0.0.0.0")
    app_port: int = int(_env("APP_PORT", "8000"))

    def ensure_paths(self) -> None:
        Path(self.log_dir).mkdir(parents=True, exist_ok=True)
        if self.database_url.startswith("sqlite"):
            db_path = self.database_url.replace("sqlite:///", "")
            db_path = db_path.replace("sqlite:////", "/")
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)


settings = Settings()
