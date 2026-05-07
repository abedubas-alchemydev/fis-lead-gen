"""add display_name to competitor_providers

Revision ID: 20260507_0029
Revises: 20260505_0028
Create Date: 2026-05-07

Short-label column powering the consolidated clearing-partner filter
dropdown. Until now the master-list filter showed every distinct
``broker_dealers.current_clearing_partner`` raw string ("PERSHING LLC",
"PERSHING NFS", "BNY PERSHING"), so users saw 3-4 rows for what was
really one firm. The dropdown now consolidates raw values to a canonical
``CompetitorProvider`` row by alias-matching, then renders this
``display_name`` (e.g. "Pershing"). When ``display_name`` is NULL the
canonical ``name`` is used as the fallback label.

Backfill targets the six rows seeded by ``DEFAULT_COMPETITORS`` so the
column has values immediately after upgrade; new rows added by an
extended seed run pick up their values from the upsert.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "20260507_0029"
down_revision: str | None = "20260505_0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_BACKFILL = [
    ("Pershing LLC", "Pershing"),
    ("Apex Clearing Corporation", "Apex"),
    ("Hilltop Securities Inc.", "Hilltop"),
    ("RBC Correspondent Services", "RBC"),
    ("Axos Clearing LLC", "Axos"),
    ("Vision Financial Markets LLC", "Vision"),
]


def upgrade() -> None:
    op.add_column(
        "competitor_providers",
        sa.Column("display_name", sa.String(length=50), nullable=True),
    )
    for canonical_name, short_label in _BACKFILL:
        op.execute(
            sa.text(
                "UPDATE competitor_providers "
                "SET display_name = :short_label "
                "WHERE name = :canonical_name"
            ).bindparams(short_label=short_label, canonical_name=canonical_name)
        )


def downgrade() -> None:
    op.drop_column("competitor_providers", "display_name")
