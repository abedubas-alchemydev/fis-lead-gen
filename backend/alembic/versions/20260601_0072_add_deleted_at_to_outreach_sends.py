"""add deleted_at to outreach_sends

Backs the per-row "delete" button on /outreach/sent. The send-history
table is the audit trail for every outreach attempt, so the delete is a
soft delete: stamping ``deleted_at`` hides the row from the list + detail
read paths (both filter ``deleted_at IS NULL``) while keeping it on disk
for admin audits. Nullable; NULL means "live" (every existing row).

Revision ID: 20260601_0072
Revises: 20260529_0071
Create Date: 2026-06-01
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "20260601_0072"
down_revision: str | None = "20260529_0071"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "outreach_sends",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("outreach_sends", "deleted_at")
