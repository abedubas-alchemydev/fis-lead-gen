"""Add chatbot_conversation + chatbot_message tables for Doxie history.

Revision ID: 20260526_0060
Revises: 20260523_0059
Create Date: 2026-05-26

Persists Doxie chat turns per user so the panel can reload prior context
on open instead of starting fresh every refresh. ``chatbot_conversation``
has at most one ACTIVE row per user (``archived_at IS NULL``); the "New
chat" button archives the active row and inserts a fresh one. Archived
conversations stay in the DB for potential audit / history-browser
features but aren't surfaced in the panel.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "20260526_0060"
down_revision: str | None = "20260523_0059"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chatbot_conversation",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            name="fk_chatbot_conversation_user_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_chatbot_conversation"),
    )
    op.create_index(
        "ix_chatbot_conversation_user_id_archived_at",
        "chatbot_conversation",
        ["user_id", "archived_at"],
        unique=False,
    )
    # Partial unique index: at most ONE active (archived_at IS NULL) row
    # per user. Enforces the single-thread invariant at the DB level so a
    # racing double-POST can't create two active conversations.
    op.create_index(
        "uq_chatbot_conversation_one_active_per_user",
        "chatbot_conversation",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("archived_at IS NULL"),
    )

    op.create_table(
        "chatbot_message",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("conversation_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("page_context_path", sa.String(length=512), nullable=True),
        sa.Column("page_context_title", sa.String(length=256), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["chatbot_conversation.id"],
            name="fk_chatbot_message_conversation_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_chatbot_message"),
    )
    op.create_index(
        "ix_chatbot_message_conversation_id_created_at",
        "chatbot_message",
        ["conversation_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_chatbot_message_conversation_id_created_at",
        table_name="chatbot_message",
    )
    op.drop_table("chatbot_message")
    op.drop_index(
        "uq_chatbot_conversation_one_active_per_user",
        table_name="chatbot_conversation",
    )
    op.drop_index(
        "ix_chatbot_conversation_user_id_archived_at",
        table_name="chatbot_conversation",
    )
    op.drop_table("chatbot_conversation")
