"""Карточка участника: тема, чего ищет, языки, ссылки

Revision ID: b3d9f1a27c40
Revises: a7c1e0d4b9f2
Create Date: 2026-10-24 10:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "b3d9f1a27c40"
down_revision = "a7c1e0d4b9f2"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("profiles") as batch:
        batch.add_column(sa.Column("cover_theme", sa.String(length=12), server_default="violet", nullable=False))
        batch.add_column(sa.Column("open_to", sa.String(length=160), server_default="", nullable=False))
        batch.add_column(sa.Column("languages", sa.String(length=160), server_default="", nullable=False))
        batch.add_column(sa.Column("website_url", sa.String(length=300), nullable=True))
        batch.add_column(sa.Column("linkedin_url", sa.String(length=300), nullable=True))


def downgrade():
    with op.batch_alter_table("profiles") as batch:
        batch.drop_column("linkedin_url")
        batch.drop_column("website_url")
        batch.drop_column("languages")
        batch.drop_column("open_to")
        batch.drop_column("cover_theme")
