"""Add user_activity table for broad-surface user instrumentation.

Revision ID: 20260520_0053
Revises: 20260519_0052
Create Date: 2026-05-20

Stores nav clicks, route changes, opened external links, search-bar
usage, and form-field interactions for the authenticated app surface.
Kept separate from ``audit_log`` because the write volume is an order
of magnitude higher than login/logout + security shield events, and we
want to prune/partition independently without inflating the
``ix_audit_log_user_timestamp`` index footprint that
``/settings/users/{id}/activities`` relies on for the existing branches.

``details`` is JSONB (not Text) so the union endpoint can filter on
keys cheaply once we add per-key queries; for now it just deserializes
into the FE row tooltip pipeline alongside ``audit_log.details``.

``user_id`` ON DELETE CASCADE keeps the row pruning aligned with the
existing per-user delete flow in ``users_admin.delete_user`` — the
admin viewer reads by user, so orphans aren't useful.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "20260520_0053"
down_revision: str | None = "20260519_0052"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_activity",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.String(length=255),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("session_id", sa.String(length=255), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("path", sa.String(length=512), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_user_activity_user_created",
        "user_activity",
        ["user_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_user_activity_action_created",
        "user_activity",
        ["action", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_user_activity_action_created", table_name="user_activity")
    op.drop_index("ix_user_activity_user_created", table_name="user_activity")
    op.drop_table("user_activity")
