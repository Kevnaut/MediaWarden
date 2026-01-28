"""add plex section settings

Revision ID: 0003_plex_section
Revises: 0002_display_mode
Create Date: 2026-01-28 00:00:00
"""

from alembic import op
import sqlalchemy as sa

revision = "0003_plex_section"
down_revision = "0002_display_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("libraries", sa.Column("plex_section_id", sa.String(length=32)))
    op.add_column("libraries", sa.Column("plex_root_path", sa.Text()))


def downgrade() -> None:
    op.drop_column("libraries", "plex_root_path")
    op.drop_column("libraries", "plex_section_id")
