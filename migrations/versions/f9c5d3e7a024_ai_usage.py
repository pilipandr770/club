"""Расход ИИ по дням

Revision ID: f9c5d3e7a024
Revises: e8b4c2d6f913
Create Date: 2026-10-30 14:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "f9c5d3e7a024"
down_revision = "e8b4c2d6f913"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ai_usage",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("feature", sa.String(length=24), nullable=False),
        sa.Column("model", sa.String(length=80), nullable=False),
        sa.Column("calls", sa.Integer(), nullable=False),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.Column("tokens_in", sa.BigInteger(), nullable=False),
        sa.Column("tokens_out", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("day", "feature", "model", name=op.f("pk_ai_usage")),
    )


def downgrade():
    op.drop_table("ai_usage")
