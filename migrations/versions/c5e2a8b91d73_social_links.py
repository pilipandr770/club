"""Ссылки на соцсети в карточке и согласие на публичный показ

Revision ID: c5e2a8b91d73
Revises: b3d9f1a27c40
Create Date: 2026-10-24 14:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "c5e2a8b91d73"
down_revision = "b3d9f1a27c40"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("profiles") as batch:
        batch.add_column(sa.Column("social_links", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("links_public", sa.Boolean(), server_default="false", nullable=False))


def downgrade():
    with op.batch_alter_table("profiles") as batch:
        batch.drop_column("links_public")
        batch.drop_column("social_links")
