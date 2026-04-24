"""add discovery_source and discovery_confidence to executive_contact

Revision ID: 20260424_0015
Revises: 20260424_0014
Create Date: 2026-04-24

Paves the way for the multi-provider contact discovery chain
(Apollo match -> Hunter -> Snov). Every row newly inserted by the
orchestrator is stamped with:

* ``discovery_source`` -- the identifier of the provider that first returned
  a confident hit (``apollo_match``, ``hunter``, ``snov``,
  ``apollo_org``, ``hunter_domain``, ``snov_domain``).
* ``discovery_confidence`` -- the provider's own 0..100 confidence score so
  the UI can sort / filter by quality.

Both columns are NULLABLE. Pre-existing rows (from the legacy company-level
Apollo search and from the FOCUS CEO extractor) are intentionally left NULL
to preserve their provenance semantics: ``source`` already tells you where
they came from, and those flows never produced a per-row confidence number.

The downgrade path drops both columns with no data-loss fanfare -- they're
additive, nullable, and no FK / index depends on them.
"""

from alembic import op
import sqlalchemy as sa


revision = "20260424_0015"
down_revision = "20260424_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "executive_contacts",
        sa.Column("discovery_source", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "executive_contacts",
        sa.Column("discovery_confidence", sa.Numeric(5, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("executive_contacts", "discovery_confidence")
    op.drop_column("executive_contacts", "discovery_source")
