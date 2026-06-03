"""Add last_gap_fill_attempt_at to institutional_investors.

Revision ID: 20260528_0067
Revises: 20260528_0066
Create Date: 2026-05-28

Cooldown stamp for per-investor gap-fill contact runs triggered from
the new Outreach Contacts page. Mirrors the advisor-side column added
in ``20260528_0062`` and the broker-dealer-side column added in
``20260508_0033`` so the three contact tables share the same 30-day
fairness budget.

Additive, nullable, no constraint changes -- safe to ship without a
backfill (existing rows land with NULL = "never attempted").
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "20260528_0067"
down_revision: str | None = "20260528_0066"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "institutional_investors",
        sa.Column(
            "last_gap_fill_attempt_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_institutional_investors_last_gap_fill_attempt_at",
        "institutional_investors",
        ["last_gap_fill_attempt_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_institutional_investors_last_gap_fill_attempt_at",
        table_name="institutional_investors",
    )
    op.drop_column("institutional_investors", "last_gap_fill_attempt_at")
