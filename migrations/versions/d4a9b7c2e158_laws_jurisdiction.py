"""Корпус законов: юрисдикция (DE, IL) и ревизия источника

Revision ID: d4a9b7c2e158
Revises: c3f8a1d5b926
Create Date: 2026-10-31 12:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "d4a9b7c2e158"
down_revision = "c3f8a1d5b926"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("law_docs") as batch:
        batch.add_column(sa.Column("jurisdiction", sa.String(length=2), server_default="DE", nullable=False))
        batch.add_column(sa.Column("revision", sa.String(length=40), server_default="", nullable=False))
        batch.create_index(batch.f("ix_law_docs_jurisdiction"), ["jurisdiction"], unique=False)
    with op.batch_alter_table("law_chunks") as batch:
        batch.add_column(sa.Column("jurisdiction", sa.String(length=2), server_default="DE", nullable=False))
        batch.create_index(batch.f("ix_law_chunks_jurisdiction"), ["jurisdiction"], unique=False)
    # Векторные индексы (общий и частичный для IL) строит не миграция, а загрузчик корпуса: laws-index и laws-il-sync


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_law_chunks_embedding_il")  # если его успел создать laws-il-sync
    with op.batch_alter_table("law_chunks") as batch:
        batch.drop_index(batch.f("ix_law_chunks_jurisdiction"))
        batch.drop_column("jurisdiction")
    with op.batch_alter_table("law_docs") as batch:
        batch.drop_index(batch.f("ix_law_docs_jurisdiction"))
        batch.drop_column("revision")
        batch.drop_column("jurisdiction")
