"""Add emails + phones JSONB arrays to contact tables.

Revision ID: 20260521_0054
Revises: 20260520_0053
Create Date: 2026-05-21

PDL-driven discovery returns multi-value emails (work + personal) and
multi-value phones (mobile + work), so the contact tables need array
storage alongside the legacy scalar ``email`` / ``phone`` columns.

Columns are nullable: existing rows stay untouched (no backfill needed).
The orchestrator populates them from new provider hits; the Pydantic
``ExecutiveContactItem`` / ``InvestorContactItem`` / ``AdvisorContactItem``
schemas synthesize a 1-element list from the scalar on read for legacy
rows. Same two columns land on all three contact tables for schema
parity even though only executive + investor get enrichment paths in
this rollout.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "20260521_0054"
down_revision: str | None = "20260520_0053"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TABLES = ("executive_contacts", "investor_contacts", "advisor_contacts")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column("emails", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        )
        op.add_column(
            table,
            sa.Column("phones", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        )


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.drop_column(table, "phones")
        op.drop_column(table, "emails")
