"""Add dba_names to broker_dealers.

Revision ID: 20260508_0032
Revises: 20260508_0031
Create Date: 2026-05-08

FINRA's BrokerCheck firm-search payload carries a ``firm_other_names``
field with the firm's "doing business as" / alternate trade names.
Until now the FINRA service quietly shoe-horned that string into the
``business_type`` column as a fallback — DBA data was effectively
discarded.

The website resolver's domain-anchor gate (post-PR #362) keys off the
firm-name token derived from the legal LLC name. That breaks for the
common broker-dealer pattern where the firm registers under one name
and operates under a different brand:

  - Legal: ``303 ALTERNATIVES, LLC``
  - DBA:   ``303Capital Markets``
  - Site:  ``303capitalmarkets.com``

The legal-name token (``alternat``) shares zero overlap with the brand
domain. Every DBA-style firm gets rejected even when Apollo / SerpAPI
surface the correct URL.

This migration adds a first-class ``dba_names`` JSONB column so the
parser-side fix (FINRA service emitting DBAs as their own field) and
resolver-side fix (validator anchoring on legal + DBA tokens) have a
place to land. Backfill is a separate one-shot script; existing rows
land with NULL until that runs.

Additive, nullable, no constraint changes — safe to ship before the
backfill runs.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


revision: str = "20260508_0032"
down_revision: str | None = "20260508_0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "broker_dealers",
        sa.Column(
            "dba_names",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("broker_dealers", "dba_names")
