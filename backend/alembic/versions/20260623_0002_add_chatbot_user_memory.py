"""add chatbot_user_memory (Doxie private per-user memory)

Backs Doxie's ``remember_fact`` tool: when a user states a durable fact or
preference, the model writes a row here so every future chat can fold the
caller's memories into the system prompt. Strictly per-user — one row per
remembered fact, scoped to ``user_id`` and never shared cross-user (the
opposite of the global ``chatbot_learned_term`` glossary). ``user_id``
CASCADEs on user delete; ``source_message_id`` SET NULLs so pruning an old
chat turn doesn't drop the learned memory.

Chained onto Lane D's ``20260623_0001`` (per-turn token-usage capture) so
the two Doxie migrations land in a single linear chain.

Revision ID: 20260623_0002
Revises: 20260623_0001
Create Date: 2026-06-23
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "20260623_0002"
down_revision: str | None = "20260623_0001"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "chatbot_user_memory",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=True),
        sa.Column("source_message_id", sa.BigInteger(), nullable=True),
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
        # CASCADE: a user's private memories must not outlive their account.
        sa.ForeignKeyConstraint(
            ["user_id"], ["user.id"], ondelete="CASCADE"
        ),
        # SET NULL: a pruned chat turn shouldn't drop the learned memory.
        sa.ForeignKeyConstraint(
            ["source_message_id"], ["chatbot_message.id"], ondelete="SET NULL"
        ),
    )
    # Covers the per-user, newest-first list query the management page and
    # the per-turn prompt injection both run.
    op.create_index(
        "ix_chatbot_user_memory_user_id_created_at",
        "chatbot_user_memory",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_chatbot_user_memory_user_id_created_at",
        table_name="chatbot_user_memory",
    )
    op.drop_table("chatbot_user_memory")
