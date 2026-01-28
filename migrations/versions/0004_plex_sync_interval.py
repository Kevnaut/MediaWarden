"""add plex sync interval

Revision ID: 0004_plex_sync_interval
Revises: 0003_plex_section
Create Date: 2026-01-28 00:00:00
"""

from alembic import op
import sqlalchemy as sa

revision = "0004_plex_sync_interval"
down_revision = "0003_plex_section"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("libraries", sa.Column("plex_sync_interval_hours", sa.Float()))


def downgrade() -> None:
    op.drop_column("libraries", "plex_sync_interval_hours")
