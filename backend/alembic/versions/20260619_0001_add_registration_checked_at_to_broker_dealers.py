"""Add registration_checked_at to broker_dealers.

Revision ID: 20260619_0001
Revises: 20260604_0001
Create Date: 2026-06-19

Rolling-cursor stamp for the FINRA registration-date refresh
(``scripts/refresh_registration_dates.py``). Every BD the refresh
touches gets this column set to ``now()`` regardless of whether a fresh
``registration_date`` / ``formation_date`` was parsed off the Form BD —
so the cursor advances even on a parse miss and the nightly batch keeps
moving instead of re-probing the same stuck firms.

Why it exists. ``registration_date`` is NULL for most rows (the merge
path sets it EDGAR-only) and a firm already in our DB only gets a
*registration date* once FINRA flips it to SEC-Approved — an event the
INSERT-only net-new extractor never catches. The refresh re-fetches the
FINRA Form BD per firm and UPDATEs the date in place; this column drives
which firms it picks next: NULL-date firms first, then the
oldest/never-checked firms (``registration_checked_at NULLS FIRST``).

Indexed because the refresh's selection query orders on it
(``ORDER BY registration_date IS NOT NULL, registration_checked_at
NULLS FIRST, registration_checked_at``).

Additive, nullable, no server default — existing rows land with NULL
("never refreshed"), which is exactly the highest-priority bucket. Safe
to ship without a backfill.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "20260619_0001"
down_revision: str | None = "20260604_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "broker_dealers",
        sa.Column(
            "registration_checked_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_broker_dealers_registration_checked_at",
        "broker_dealers",
        ["registration_checked_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_broker_dealers_registration_checked_at",
        table_name="broker_dealers",
    )
    op.drop_column("broker_dealers", "registration_checked_at")
