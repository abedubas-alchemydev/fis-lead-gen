"""add chatbot_learned_term (Doxie learned-term glossary)

Backs Doxie's ``research_term`` tool: when it researches an unfamiliar term
on the public web, the definition is written here so the next ask is
recalled from the glossary instead of re-hitting a search provider. Global
table — one row per normalized term, shared across users. The unique index
on ``term_normalized`` powers both the lookup and the ON CONFLICT upsert.

Revision ID: 20260603_0001
Revises: 20260530_0001
Create Date: 2026-06-03
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "20260603_0001"
down_revision: str | None = "20260530_0001"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "chatbot_learned_term",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("term", sa.String(length=255), nullable=False),
        sa.Column("term_normalized", sa.String(length=255), nullable=False),
        sa.Column("definition", sa.Text(), nullable=False),
        sa.Column("source_url", sa.String(length=2048), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=True),
        sa.Column("created_by_user_id", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["user.id"], ondelete="SET NULL"
        ),
    )
    # Unique so ON CONFLICT (term_normalized) can upsert, and fast for the
    # glossary lookup the research tool runs before any web call.
    op.create_index(
        "ix_chatbot_learned_term_normalized",
        "chatbot_learned_term",
        ["term_normalized"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_chatbot_learned_term_normalized",
        table_name="chatbot_learned_term",
    )
    op.drop_table("chatbot_learned_term")
