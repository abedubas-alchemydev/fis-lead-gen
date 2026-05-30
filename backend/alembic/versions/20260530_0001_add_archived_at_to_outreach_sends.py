"""add archived_at to outreach_sends

Soft-delete for the ``/outreach/sent`` Sent-history list. A row's owner can
delete one of their own sends; we stamp ``archived_at`` rather than hard-delete
so the audit / compliance record survives. Every read path filters
``archived_at IS NULL``. Mirrors the ``ChatbotConversation.archived_at``
convention (migration 20260526_0060).

The partial index serves the list hot path
``WHERE user_id = ? AND archived_at IS NULL ORDER BY sent_at DESC``.

Revision ID: 20260530_0001
Revises: 20260529_0071
Create Date: 2026-05-30
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "20260530_0001"
down_revision: str | None = "20260529_0071"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "outreach_sends",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_outreach_sends_live_user_sent",
        "outreach_sends",
        ["user_id", sa.text("sent_at DESC")],
        unique=False,
        postgresql_where=sa.text("archived_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_outreach_sends_live_user_sent",
        table_name="outreach_sends",
    )
    op.drop_column("outreach_sends", "archived_at")
