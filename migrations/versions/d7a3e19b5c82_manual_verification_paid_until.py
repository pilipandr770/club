"""Ручное подтверждение личности и срок оплаты тарифа

Revision ID: d7a3e19b5c82
Revises: c5e2a8b91d73
Create Date: 2026-10-26 10:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "d7a3e19b5c82"
down_revision = "c5e2a8b91d73"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("verification_method", sa.String(length=12), nullable=True))
        batch.add_column(sa.Column("verified_by_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("verification_note", sa.String(length=300), nullable=True))
        batch.add_column(sa.Column("paid_until", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("payment_note", sa.String(length=300), nullable=True))
        batch.create_foreign_key(batch.f("fk_users_verified_by_id_users"), "users", ["verified_by_id"], ["id"],
                                 ondelete="SET NULL")
    # Уже подтверждённых раньше считаем подтверждёнными вручную, если Stripe-сессии нет
    op.execute("UPDATE users SET verification_method = CASE WHEN stripe_verification_session_id IS NOT NULL "
               "THEN 'stripe' ELSE 'manual' END WHERE verification_status = 'verified'")


def downgrade():
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint(batch.f("fk_users_verified_by_id_users"), type_="foreignkey")
        batch.drop_column("payment_note")
        batch.drop_column("paid_until")
        batch.drop_column("verification_note")
        batch.drop_column("verified_by_id")
        batch.drop_column("verification_method")
