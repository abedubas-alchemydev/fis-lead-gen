"""add enriched_phone to discovered_email

Apollo's /people/match response includes ``phone_numbers`` per matched
person, but the email-extractor enrichment writer (apollo_enrichment.py)
was discarding it. The 6 enrichment columns added in 20260423_0013 only
covered name/title/linkedin/company. This 7th column captures the
phone so the email-scanner flow surfaces the same phone the
contact-discovery flow already persists to ExecutiveContact.phone.

Width matches ExecutiveContact.phone (VARCHAR(64)) so future
normalization can share a single helper.

Revision ID: 20260515_0041
Revises: 20260513_0040
Create Date: 2026-05-15

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "20260515_0041"
down_revision: str | None = "20260513_0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "discovered_email",
        sa.Column("enriched_phone", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("discovered_email", "enriched_phone")
