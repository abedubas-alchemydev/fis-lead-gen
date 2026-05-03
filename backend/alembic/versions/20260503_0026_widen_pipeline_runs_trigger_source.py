"""Widen pipeline_runs.trigger_source to support chained populate_all triggers.

Revision ID: 20260503_0026
Revises: 20260502_0025
Create Date: 2026-05-03

The previous VARCHAR(64) ceiling overflowed when populate_all ran via Cloud
Scheduler and chained into filing_monitor with a trigger_source like
``populate_all:sa:136029935063-compute@developer.gserviceaccount.com`` (66
chars). Postgres rejected the INSERT with StringDataRightTruncation,
crashing the daily 02:00 UTC job. Widening to 255 covers the longest
realistic chain (multi-stage cron + full SA email).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "20260503_0026"
down_revision: str | None = "20260502_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "pipeline_runs",
        "trigger_source",
        existing_type=sa.String(64),
        type_=sa.String(255),
        existing_nullable=False,
    )


def downgrade() -> None:
    # Truncate any rows that exceed the 64-char cap before narrowing the
    # column, otherwise PostgreSQL ALTER COLUMN TYPE rejects the change.
    # Loses fidelity on those rows but keeps the downgrade idempotent.
    op.execute(
        "UPDATE pipeline_runs SET trigger_source = LEFT(trigger_source, 64) "
        "WHERE LENGTH(trigger_source) > 64"
    )
    op.alter_column(
        "pipeline_runs",
        "trigger_source",
        existing_type=sa.String(255),
        type_=sa.String(64),
        existing_nullable=False,
    )
