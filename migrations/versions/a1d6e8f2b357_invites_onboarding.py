"""Приглашения и напоминание о заполнении профиля

Revision ID: a1d6e8f2b357
Revises: f9c5d3e7a024
Create Date: 2026-10-30 18:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "a1d6e8f2b357"
down_revision = "f9c5d3e7a024"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "invites",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("note", sa.String(length=300), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("plan", sa.String(length=20), server_default="guest", nullable=False),
        sa.Column("months", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_uses", sa.Integer(), server_default="1", nullable=False),
        sa.Column("uses", sa.Integer(), server_default="0", nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], name=op.f("fk_invites_created_by_id_users"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invites")),
    )
    op.create_index(op.f("ix_invites_code"), "invites", ["code"], unique=True)
    op.add_column("users", sa.Column("invite_id", sa.Integer(), nullable=True))
    op.add_column("users", sa.Column("onboarding_nudged_at", sa.DateTime(), nullable=True))
    op.create_foreign_key("fk_users_invite_id_invites", "users", "invites", ["invite_id"], ["id"], ondelete="SET NULL")
    # Уже зарегистрированные участники напоминание не получают
    op.execute("UPDATE users SET onboarding_nudged_at = now()")


def downgrade():
    op.drop_constraint("fk_users_invite_id_invites", "users", type_="foreignkey")
    op.drop_column("users", "onboarding_nudged_at")
    op.drop_column("users", "invite_id")
    op.drop_index(op.f("ix_invites_code"), table_name="invites")
    op.drop_table("invites")
