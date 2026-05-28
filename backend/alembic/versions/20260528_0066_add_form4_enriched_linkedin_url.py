"""Add enriched_linkedin_url to form4_transactions.

Revision ID: 20260528_0066
Revises: 20260528_0065
Create Date: 2026-05-28

Form 4 Re-enrich captures phone + email today but discards the LinkedIn
URL that both PDL and Apollo return alongside them. This column lets the
``/investors`` row surface a third inline link without changing the
provider chain — the only data we needed was already in the response,
just being thrown away.

Mirrors the ``linkedin_url`` columns already on ``executive_contacts``,
``advisor_contacts``, ``investor_contacts``, and ``discovered_email``:
unbounded ``Text``, nullable, not indexed.

Additive, nullable, no backfill — existing enriched rows stay NULL until
the user clicks Re-enrich on them.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "20260528_0066"
down_revision: str | None = "20260528_0065"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "form4_transactions",
        sa.Column("enriched_linkedin_url", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("form4_transactions", "enriched_linkedin_url")
