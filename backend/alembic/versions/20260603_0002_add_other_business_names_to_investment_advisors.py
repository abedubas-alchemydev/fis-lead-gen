"""Add other_business_names to investment_advisors.

Revision ID: 20260603_0002
Revises: 20260603_0001
Create Date: 2026-06-03

Investment Advisors get the same "alternate / other business names"
treatment Broker Dealers already have via ``broker_dealers.dba_names``
(migration 20260508_0032). For IAs the names come from Form ADV
Schedule D Section 1.B "Other Business Names", surfaced in the IAPD
per-firm JSON the refresh orchestrator already fetches
(``iacontent.basicInformation.otherNames``) — the bulk IAPD Compilation
Report CSV has no other-names column.

This migration adds a first-class ``other_business_names`` JSONB column
so the refresh sub-pipeline + a one-shot backfill script have a place to
land the parsed names. The column is intentionally kept OUT of the bulk
ingest upsert (``investment_advisors._record_to_columns``) so a
re-ingest can never clobber the enriched value — same protection the
other enrichment columns (``registration_date``, ``direct_owners``, …)
already rely on.

Additive, nullable, no constraint changes — safe to ship before the
backfill runs. Existing rows land with NULL until then.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


revision: str = "20260603_0002"
down_revision: str | None = "20260603_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "investment_advisors",
        sa.Column(
            "other_business_names",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("investment_advisors", "other_business_names")
