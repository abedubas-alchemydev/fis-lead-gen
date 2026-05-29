"""Add user_outreach_settings (per-user outreach signature/footer).

Revision ID: 20260529_0070
Revises: 20260529_0069
Create Date: 2026-05-29

Backs the per-user outreach signature: a footer block the compose
surfaces (per-contact Outreach modal + the /outreach Create tab)
prefill beneath the email body and append on send. One row per user,
keyed by ``user_id`` with an ON DELETE CASCADE FK to ``user`` so the
row vanishes with the account (mirrors ``user_visit``).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260529_0070"
down_revision: str | None = "20260529_0069"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_outreach_settings",
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("signature", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("user_outreach_settings")
