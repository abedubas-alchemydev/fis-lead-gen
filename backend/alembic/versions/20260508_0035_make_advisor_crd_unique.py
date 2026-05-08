"""Promote investment_advisors.crd_number to a partial unique index.

Revision ID: 20260508_0035
Revises: 20260508_0034
Create Date: 2026-05-08

The original 0030 migration shipped ``ix_investment_advisors_crd_number``
as non-unique on the assumption that bulk imports might tolerate
occasional duplicate CRDs mid-flight. In practice IAPD's compilation
report has zero CRD collisions across ~17k rows (the only data-quality
issue is on CIK, where 3 clusters share an EDGAR ID), so the looseness
buys nothing and costs us the ability to drive
``INSERT ... ON CONFLICT (crd_number) DO UPDATE``.

Switching to a partial unique index (``WHERE crd_number IS NOT NULL``)
mirrors the existing CIK pattern from 0030 and unlocks bulk single-
statement upserts in ``InvestmentAdvisorRepository.upsert_many`` /
``upsert_by_crd`` — the previous per-row pattern took ~40 min for the
full IAPD universe over Neon.

Safety:
- Verified zero CRD dupes in staging before authoring this migration.
- The IAPD merge service (``advisor_merge.py``) already drops records
  without a CRD, so nothing in the upsert path produces NULL CRDs.
- Partial-index semantics permit the existing nullable column shape;
  no model change required.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "20260508_0035"
down_revision: str | None = "20260508_0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index(
        "ix_investment_advisors_crd_number",
        table_name="investment_advisors",
    )
    op.create_index(
        "ix_investment_advisors_crd_number",
        "investment_advisors",
        ["crd_number"],
        unique=True,
        postgresql_where=sa.text("crd_number IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_investment_advisors_crd_number",
        table_name="investment_advisors",
    )
    op.create_index(
        "ix_investment_advisors_crd_number",
        "investment_advisors",
        ["crd_number"],
        unique=False,
    )
