"""Versioned support evidence with a cosine pgvector index.

Revision ID: 003_support_articles
Revises: 002_open_requests
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import VECTOR

revision = "003_support_articles"
down_revision = "002_open_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "support_articles",
        sa.Column("id", sa.String(20), primary_key=True),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("section", sa.String(80), nullable=False),
        sa.Column("question", sa.String(300), nullable=False),
        sa.Column("answer", sa.String(1200), nullable=False),
        sa.Column("revision", sa.String(20), nullable=False),
        sa.Column("embedding", VECTOR(256), nullable=False),
    )
    op.create_index(
        "ix_support_articles_embedding_hnsw",
        "support_articles",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_support_articles_embedding_hnsw", table_name="support_articles")
    op.drop_table("support_articles")
