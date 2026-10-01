"""Prevent competing open actions on one payment or subscription.

Revision ID: 002_open_requests
Revises: 001_initial_crm
"""

import sqlalchemy as sa
from alembic import op

revision = "002_open_requests"
down_revision = "001_initial_crm"
branch_labels = None
depends_on = None


def upgrade() -> None:
    open_status = sa.text("status IN ('pending', 'approved')")
    op.create_index(
        "uq_refund_open_payment",
        "refund_requests",
        ["payment_id"],
        unique=True,
        postgresql_where=open_status,
    )
    op.create_index(
        "uq_cancellation_open_subscription",
        "cancellation_requests",
        ["subscription_id"],
        unique=True,
        postgresql_where=open_status,
    )


def downgrade() -> None:
    op.drop_index("uq_cancellation_open_subscription", table_name="cancellation_requests")
    op.drop_index("uq_refund_open_payment", table_name="refund_requests")
