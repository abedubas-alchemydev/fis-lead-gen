"""Add three_year_cagr to broker_dealers.

Revision ID: 20260511_0037
Revises: 20260509_0036
Create Date: 2026-05-11

The master-list grid already surfaces ``yoy_growth`` (a single-year
delta on ``net_capital``) as the primary growth signal. YoY is noisy for
mid-sized broker-dealers — one-off filing-period quirks can swing the
number 20+ points — so it under-rewards firms whose growth is steady
across multiple cycles.

This column adds a sibling **3-year compound annual growth rate** on
``net_capital``. CAGR uses the latest and oldest of the most recent
three parsed ``financial_metrics`` rows for a BD and returns the
percentage annualized growth between them. Behaviour:

  - Returns NULL when fewer than three parsed rows exist (fail-closed,
    so the column is honest about data sparsity).
  - Returns NULL when oldest ``net_capital`` <= 0 (avoid divide-by-zero
    and undefined growth on a zero base).
  - Rounds to two decimals to match ``yoy_growth`` precision.

The rollup writers in ``services/focus_reports.py`` and
``services/focus_ceo_extraction.py`` populate this column whenever a
fresh financial_metrics row lands. A one-off backfill
(``scripts/backfill_three_year_cagr.py``) seeds the value for the
existing population without waiting for the next extraction pass.

Additive, nullable, no constraint changes — safe to ship before the
backfill runs.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "20260511_0037"
down_revision: str | None = "20260509_0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "broker_dealers",
        sa.Column("three_year_cagr", sa.Numeric(8, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("broker_dealers", "three_year_cagr")
