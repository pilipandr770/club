"""Анонимная статистика посещений

Revision ID: e8b4c2d6f913
Revises: d7a3e19b5c82
Create Date: 2026-10-30 10:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "e8b4c2d6f913"
down_revision = "d7a3e19b5c82"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "visit_stats",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("metric", sa.String(length=16), nullable=False),
        sa.Column("page", sa.String(length=32), nullable=False),
        sa.Column("country", sa.String(length=2), nullable=False),
        sa.Column("city", sa.String(length=80), nullable=False),
        sa.Column("source", sa.String(length=60), nullable=False),
        sa.Column("device", sa.String(length=8), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("day", "metric", "page", "country", "city", "source", "device",
                                name=op.f("pk_visit_stats")),
    )
    op.create_table(
        "visit_seen",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("vhash", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.PrimaryKeyConstraint("day", "vhash", "kind", name=op.f("pk_visit_seen")),
    )


def downgrade():
    op.drop_table("visit_seen")
    op.drop_table("visit_stats")
