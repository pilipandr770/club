"""laws corpus (pgvector, law_* tables)

Revision ID: a7c1e0d4b9f2
Revises: 5ed6a42fbce5
Create Date: 2026-10-22 10:00:00

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "a7c1e0d4b9f2"
down_revision = "5ed6a42fbce5"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "law_docs",
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("abbreviation", sa.String(length=120), nullable=False),
        sa.Column("title", sa.String(length=400), nullable=False),
        sa.Column("category", sa.String(length=80), nullable=False),
        sa.Column("source_url", sa.String(length=300), nullable=False),
        sa.Column("stand", sa.JSON(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("slug", name=op.f("pk_law_docs")),
    )
    op.create_index(op.f("ix_law_docs_category"), "law_docs", ["category"])
    op.create_table(
        "law_sections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("enbez", sa.String(length=80), nullable=False),
        sa.Column("titel", sa.String(length=400), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("stand", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["slug"], ["law_docs.slug"], name=op.f("fk_law_sections_slug_law_docs"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_law_sections")),
    )
    op.create_index(op.f("ix_law_sections_slug"), "law_sections", ["slug"])
    op.create_index("ix_law_sections_slug_enbez", "law_sections", ["slug", "enbez"])
    op.execute("""
        CREATE TABLE law_chunks (
            id VARCHAR(64) NOT NULL,
            slug VARCHAR(120) NOT NULL,
            enbez VARCHAR(80) NOT NULL,
            titel VARCHAR(400) NOT NULL,
            part VARCHAR(20) NOT NULL,
            text TEXT NOT NULL,
            text_hash VARCHAR(64) NOT NULL,
            embedding vector(1024),
            search_vector tsvector GENERATED ALWAYS AS (to_tsvector('german', coalesce(titel, '') || ' ' || coalesce(text, ''))) STORED,
            CONSTRAINT pk_law_chunks PRIMARY KEY (id),
            CONSTRAINT fk_law_chunks_slug_law_docs FOREIGN KEY (slug) REFERENCES law_docs (slug) ON DELETE CASCADE
        )
    """)
    op.create_index(op.f("ix_law_chunks_slug"), "law_chunks", ["slug"])
    op.execute("CREATE INDEX ix_law_chunks_search ON law_chunks USING gin (search_vector)")
    # HNSW-индекс по векторам создаёт команда flask laws-import после загрузки (так быстрее, чем строить по строкам)
    op.create_table(
        "law_sync_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("last_run_at", sa.DateTime(), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_law_sync_state")),
    )


def downgrade():
    op.drop_table("law_sync_state")
    op.drop_table("law_chunks")
    op.drop_table("law_sections")
    op.drop_table("law_docs")
