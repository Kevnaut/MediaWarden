"""add arr integrations and torrent fields

Revision ID: 0005_arr_qb_fields
Revises: 0004_plex_sync_interval
Create Date: 2026-01-28
"""

from alembic import op
import sqlalchemy as sa

revision = "0005_arr_qb_fields"
down_revision = "0004_plex_sync_interval"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("libraries", sa.Column("sonarr_url", sa.Text(), nullable=True))
    op.add_column("libraries", sa.Column("sonarr_key", sa.Text(), nullable=True))
    op.add_column("libraries", sa.Column("radarr_url", sa.Text(), nullable=True))
    op.add_column("libraries", sa.Column("radarr_key", sa.Text(), nullable=True))
    op.add_column("libraries", sa.Column("overseerr_url", sa.Text(), nullable=True))
    op.add_column("libraries", sa.Column("overseerr_key", sa.Text(), nullable=True))
    op.add_column("libraries", sa.Column("qb_root_path", sa.Text(), nullable=True))

    with op.batch_alter_table("media_items") as batch:
        batch.add_column(sa.Column("torrent_leechers", sa.Integer(), nullable=True))
        batch.alter_column(
            "torrent_ratio",
            existing_type=sa.String(length=32),
            type_=sa.Float(),
        )


def downgrade() -> None:
    with op.batch_alter_table("media_items") as batch:
        batch.alter_column(
            "torrent_ratio",
            existing_type=sa.Float(),
            type_=sa.String(length=32),
        )
        batch.drop_column("torrent_leechers")

    op.drop_column("libraries", "qb_root_path")
    op.drop_column("libraries", "overseerr_key")
    op.drop_column("libraries", "overseerr_url")
    op.drop_column("libraries", "radarr_key")
    op.drop_column("libraries", "radarr_url")
    op.drop_column("libraries", "sonarr_key")
    op.drop_column("libraries", "sonarr_url")
