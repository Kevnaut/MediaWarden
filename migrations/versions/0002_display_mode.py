"""add library display mode

Revision ID: 0002_display_mode
Revises: 0001_initial
Create Date: 2026-01-28 00:00:00
"""

from alembic import op
import sqlalchemy as sa

revision = "0002_display_mode"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("libraries", sa.Column("display_mode", sa.String(length=32), nullable=False, server_default="flat"))


def downgrade() -> None:
    op.drop_column("libraries", "display_mode")
