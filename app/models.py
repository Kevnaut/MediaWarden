from datetime import datetime
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, Float
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Library(Base):
    __tablename__ = "libraries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    root_path: Mapped[str] = mapped_column(Text, nullable=False)

    enable_filesystem: Mapped[bool] = mapped_column(Boolean, default=True)
    enable_plex: Mapped[bool] = mapped_column(Boolean, default=False)
    enable_arr: Mapped[bool] = mapped_column(Boolean, default=False)

    trash_retention_days: Mapped[int] = mapped_column(Integer, default=30)
    min_seed_time_minutes: Mapped[int] = mapped_column(Integer, default=0)
    min_seed_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    min_seeders: Mapped[int] = mapped_column(Integer, default=0)

    plex_url: Mapped[str | None] = mapped_column(Text)
    plex_token: Mapped[str | None] = mapped_column(Text)
    arr_url: Mapped[str | None] = mapped_column(Text)
    arr_key: Mapped[str | None] = mapped_column(Text)
    qb_url: Mapped[str | None] = mapped_column(Text)
    qb_username: Mapped[str | None] = mapped_column(Text)
    qb_password: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    media_items = relationship("MediaItem", back_populates="library", cascade="all, delete-orphan")
    trash_entries = relationship("TrashEntry", back_populates="library", cascade="all, delete-orphan")


class MediaItem(Base):
    __tablename__ = "media_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    library_id: Mapped[int] = mapped_column(ForeignKey("libraries.id"), nullable=False)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    resolution: Mapped[str | None] = mapped_column(String(32))
    modified_at: Mapped[datetime | None] = mapped_column(DateTime)

    last_watched_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_scan_at: Mapped[datetime | None] = mapped_column(DateTime)

    torrent_hash: Mapped[str | None] = mapped_column(String(64))
    torrent_ratio: Mapped[str | None] = mapped_column(String(32))
    torrent_seed_time: Mapped[int | None] = mapped_column(Integer)
    torrent_seeders: Mapped[int | None] = mapped_column(Integer)

    is_in_trash: Mapped[bool] = mapped_column(Boolean, default=False)
    is_missing: Mapped[bool] = mapped_column(Boolean, default=False)
    trashed_at: Mapped[datetime | None] = mapped_column(DateTime)
    trashed_path: Mapped[str | None] = mapped_column(Text)

    library = relationship("Library", back_populates="media_items")


class TrashEntry(Base):
    __tablename__ = "trash_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    library_id: Mapped[int] = mapped_column(ForeignKey("libraries.id"), nullable=False)
    media_item_id: Mapped[int] = mapped_column(ForeignKey("media_items.id"), nullable=False)
    original_path: Mapped[str] = mapped_column(Text, nullable=False)
    trashed_path: Mapped[str] = mapped_column(Text, nullable=False)
    trashed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    purge_after: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    library = relationship("Library", back_populates="trash_entries")
