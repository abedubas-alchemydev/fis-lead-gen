"""Add last_gap_fill_attempt_at to broker_dealers.

Revision ID: 20260508_0033
Revises: 20260508_0032
Create Date: 2026-05-08

Cooldown stamp for the bulk gap-fill runner
(``scripts/gap_fill_broker_dealers.py``). Every BD the runner touches
gets this column set to ``now()`` regardless of which sub-pipelines
fired or what they returned. The runner skips any BD whose stamp is
within the last 30 days, so firms whose source genuinely has no value
don't keep burning LLM/Apollo calls on every pass.

Mirrors the existing ``last_enrich_attempt_at`` column (Apollo
contact-enrichment cooldown). Indexed because the runner's outer loop
filters on ``last_gap_fill_attempt_at IS NULL OR < now() - 30 days``.

Additive, nullable, no constraint changes — safe to ship without a
backfill (existing rows land with NULL = "never attempted").
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "20260508_0033"
down_revision: str | None = "20260508_0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "broker_dealers",
        sa.Column(
            "last_gap_fill_attempt_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_broker_dealers_last_gap_fill_attempt_at",
        "broker_dealers",
        ["last_gap_fill_attempt_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_broker_dealers_last_gap_fill_attempt_at",
        table_name="broker_dealers",
    )
    op.drop_column("broker_dealers", "last_gap_fill_attempt_at")
