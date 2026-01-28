"""initial schema

Revision ID: 0001_initial
Revises: 
Create Date: 2026-01-27 00:00:00
"""

from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True)

    op.create_table(
        "libraries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("root_path", sa.Text(), nullable=False),
        sa.Column("enable_filesystem", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("enable_plex", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("enable_arr", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("trash_retention_days", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("min_seed_time_minutes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("min_seed_ratio", sa.Float(), nullable=False, server_default="0"),
        sa.Column("min_seeders", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("plex_url", sa.Text()),
        sa.Column("plex_token", sa.Text()),
        sa.Column("arr_url", sa.Text()),
        sa.Column("arr_key", sa.Text()),
        sa.Column("qb_url", sa.Text()),
        sa.Column("qb_username", sa.Text()),
        sa.Column("qb_password", sa.Text()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "media_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("library_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("resolution", sa.String(length=32)),
        sa.Column("modified_at", sa.DateTime()),
        sa.Column("last_watched_at", sa.DateTime()),
        sa.Column("last_scan_at", sa.DateTime()),
        sa.Column("torrent_hash", sa.String(length=64)),
        sa.Column("torrent_ratio", sa.String(length=32)),
        sa.Column("torrent_seed_time", sa.Integer()),
        sa.Column("torrent_seeders", sa.Integer()),
        sa.Column("is_in_trash", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_missing", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("trashed_at", sa.DateTime()),
        sa.Column("trashed_path", sa.Text()),
        sa.ForeignKeyConstraint(["library_id"], ["libraries.id"]),
    )

    op.create_table(
        "trash_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("library_id", sa.Integer(), nullable=False),
        sa.Column("media_item_id", sa.Integer(), nullable=False),
        sa.Column("original_path", sa.Text(), nullable=False),
        sa.Column("trashed_path", sa.Text(), nullable=False),
        sa.Column("trashed_at", sa.DateTime(), nullable=False),
        sa.Column("purge_after", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["library_id"], ["libraries.id"]),
        sa.ForeignKeyConstraint(["media_item_id"], ["media_items.id"]),
    )


def downgrade() -> None:
    op.drop_table("trash_entries")
    op.drop_table("media_items")
    op.drop_table("libraries")
    op.drop_index("ix_users_username", table_name="users")
    op.drop_table("users")
