"""Подписки через Stripe Billing

Revision ID: c3f8a1d5b926
Revises: b2e7f4a9c168
Create Date: 2026-10-30 18:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "c3f8a1d5b926"
down_revision = "b2e7f4a9c168"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("stripe_customer_id", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("stripe_subscription_id", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("subscription_status", sa.String(length=20), nullable=True))
        batch.add_column(sa.Column("subscription_interval", sa.String(length=8), nullable=True))
        batch.add_column(sa.Column("subscription_cancels", sa.Boolean(), server_default="false", nullable=False))
        batch.create_index(batch.f("ix_users_stripe_customer_id"), ["stripe_customer_id"], unique=False)
        batch.create_index(batch.f("ix_users_stripe_subscription_id"), ["stripe_subscription_id"], unique=False)


def downgrade():
    with op.batch_alter_table("users") as batch:
        batch.drop_index(batch.f("ix_users_stripe_subscription_id"))
        batch.drop_index(batch.f("ix_users_stripe_customer_id"))
        batch.drop_column("subscription_cancels")
        batch.drop_column("subscription_interval")
        batch.drop_column("subscription_status")
        batch.drop_column("stripe_subscription_id")
        batch.drop_column("stripe_customer_id")
