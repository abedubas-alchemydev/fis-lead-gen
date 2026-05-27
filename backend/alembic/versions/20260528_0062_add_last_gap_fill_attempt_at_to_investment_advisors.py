"""Add last_gap_fill_attempt_at to investment_advisors.

Revision ID: 20260528_0062
Revises: 20260526_0061
Create Date: 2026-05-28

Cooldown stamp for the bulk advisor gap-fill runner
(``scripts/gap_fill_investment_advisors.py``). Every advisor the
runner touches gets this column set to ``now()`` regardless of which
sub-pipelines fired or what they returned. The runner skips any
advisor whose stamp is within the last 30 days, so firms whose source
genuinely has no value don't keep burning Gemini/Apollo calls on
every pass.

IA analog of ``20260508_0033_add_last_gap_fill_attempt_at`` (the
broker-dealer-side column). Indexed because the runner's outer loop
filters on ``last_gap_fill_attempt_at IS NULL OR < now() - 30 days``.

Additive, nullable, no constraint changes — safe to ship without a
backfill (existing rows land with NULL = "never attempted").
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "20260528_0062"
down_revision: str | None = "20260526_0061"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "investment_advisors",
        sa.Column(
            "last_gap_fill_attempt_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_investment_advisors_last_gap_fill_attempt_at",
        "investment_advisors",
        ["last_gap_fill_attempt_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_investment_advisors_last_gap_fill_attempt_at",
        table_name="investment_advisors",
    )
    op.drop_column("investment_advisors", "last_gap_fill_attempt_at")
