"""Add website_source to broker_dealers.

Revision ID: 20260501_0023
Revises: 20260429_0022
Create Date: 2026-05-01

PR #114 added FE rendering for a website link under firm name on
``/master-list/{id}``, but ``broker_dealer.website`` was null for almost
every firm because the FINRA "Web Address" field wasn't being plucked
on the keys we read and no Apollo fallback was wired. This migration
adds ``website_source`` so the persisted row records *which* upstream
populated the value — ``'finra'`` (BrokerCheck Form BD Web Address) or
``'apollo'`` (organizations/search fallback). NULL for legacy rows that
predate the backfill or never matched either source.

The ``website`` column itself already exists (Tri-Stream Revision 1,
``20260424_0010_add_tri_stream_columns.py``), so this migration is a
single ``add_column``. Atomic-ship rule: this PR also lands the FINRA
extractor pluck change and the Apollo organizations fallback together,
so the column has consumers the moment the migration runs.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "20260501_0023"
down_revision: str | None = "20260429_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "broker_dealers",
        sa.Column("website_source", sa.String(length=16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("broker_dealers", "website_source")
