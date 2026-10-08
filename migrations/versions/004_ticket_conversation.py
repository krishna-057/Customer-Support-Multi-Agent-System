"""Make an escalation request replay-safe per customer conversation.

Revision ID: 004_ticket_conversation
Revises: 003_support_articles
"""

from alembic import op

revision = "004_ticket_conversation"
down_revision = "003_support_articles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_support_ticket_conversation", "support_tickets", ["customer_id", "conversation_id"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_support_ticket_conversation", "support_tickets", type_="unique")
