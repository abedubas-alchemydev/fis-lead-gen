"""Add session.last_activity_at + indexes for the user-activity admin view.

Revision ID: 20260519_0048
Revises: 20260518_0047
Create Date: 2026-05-19

The user-activity admin page (``/settings/users/{id}/activities``) needs
two things this migration sets up:

1. ``session.last_activity_at`` so we can render "user is online" by
   asking whether any session for that user was touched in the past N
   minutes. Default NOW() so existing rows are non-null and the column
   can be NOT NULL from day one. Bumped by ``get_current_user`` on every
   authenticated request (with a 60-second skew gate so we don't write
   on every request).
2. ``ix_session_last_activity_at`` so the online-status max() query
   over the user's sessions stays fast as the session table grows.
3. ``ix_audit_log_user_timestamp`` so the activity-feed UNION over
   ``audit_log`` filtered by ``user_id`` and ordered by ``timestamp DESC``
   doesn't sequential-scan once login events accumulate.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260519_0048"
down_revision: str | None = "20260518_0047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "session",
        sa.Column(
            "last_activity_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_session_last_activity_at",
        "session",
        [sa.text("last_activity_at DESC")],
    )
    op.create_index(
        "ix_audit_log_user_timestamp",
        "audit_log",
        ["user_id", sa.text("timestamp DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_audit_log_user_timestamp", table_name="audit_log")
    op.drop_index("ix_session_last_activity_at", table_name="session")
    op.drop_column("session", "last_activity_at")
