"""Add advisor_id to extraction_run and discovered_email.

Revision ID: 20260528_0063
Revises: 20260528_0062
Create Date: 2026-05-28

Adds an ``advisor_id`` FK column to both ``extraction_run`` and
``discovered_email`` so the email-extractor pipeline can scope scans to an
investment advisor in addition to the existing broker-dealer scoping.
Parallel to the existing ``bd_id`` column on each table — same shape
(nullable, ``ON DELETE SET NULL``, indexed) — matching the precedent set
by ``favorite_list_items`` and ``outreach_sends``.

Mutual exclusivity between ``bd_id`` and ``advisor_id`` is enforced at the
API layer (pydantic validator on ``ScanCreateRequest``) rather than via a
DB check constraint, because legitimate "neither set" rows exist today
(scans kicked off from the standalone ``/email-extractor`` page).

Additive, nullable, no backfill: existing rows land with NULL = "not tied
to an advisor".
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "20260528_0063"
down_revision: str | None = "20260528_0062"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "extraction_run",
        sa.Column("advisor_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_extraction_run_advisor_id",
        "extraction_run",
        "investment_advisors",
        ["advisor_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_extraction_run_advisor_id",
        "extraction_run",
        ["advisor_id"],
    )

    op.add_column(
        "discovered_email",
        sa.Column("advisor_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_discovered_email_advisor_id",
        "discovered_email",
        "investment_advisors",
        ["advisor_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_discovered_email_advisor_id",
        "discovered_email",
        ["advisor_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_discovered_email_advisor_id", table_name="discovered_email")
    op.drop_constraint(
        "fk_discovered_email_advisor_id", "discovered_email", type_="foreignkey"
    )
    op.drop_column("discovered_email", "advisor_id")

    op.drop_index("ix_extraction_run_advisor_id", table_name="extraction_run")
    op.drop_constraint(
        "fk_extraction_run_advisor_id", "extraction_run", type_="foreignkey"
    )
    op.drop_column("extraction_run", "advisor_id")
