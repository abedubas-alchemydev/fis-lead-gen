"""Add bank_id to the email-extractor tables (bank-scoped domain scans).

Revision ID: 20260703_0004
Revises: 20260703_0003
Create Date: 2026-07-03

The cross-vertical Email Extractor already tags a scan (and every email it
discovers) with the ``bd_id`` / ``advisor_id`` it was launched from, so the
firm detail page can list "scans run for this firm". The bank profile is
gaining the same "Extract Email" affordance, so both tables need a matching
nullable ``bank_id``.

Modeled exactly like the existing ``bd_id`` / ``advisor_id`` columns:
``ON DELETE SET NULL`` (a scan's history survives the deletion of the bank it
was tied to) and indexed (the per-bank history list filters on it). Both are
mutually exclusive with the other two FKs at the request layer
(``ScanCreateRequest``), not enforced by a DB constraint — same as the
existing bd/advisor pair.

Additive + nullable; no data backfill.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260703_0004"
down_revision: str | None = "20260703_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # extraction_run.bank_id — mirrors bd_id / advisor_id on the same table.
    op.add_column(
        "extraction_run",
        sa.Column("bank_id", sa.Integer(), nullable=True),
    )
    op.create_index("ix_extraction_run_bank_id", "extraction_run", ["bank_id"])
    op.create_foreign_key(
        "fk_extraction_run_bank_id",
        "extraction_run",
        "banks",
        ["bank_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # discovered_email.bank_id — mirrors bd_id / advisor_id on the same table.
    op.add_column(
        "discovered_email",
        sa.Column("bank_id", sa.Integer(), nullable=True),
    )
    op.create_index("ix_discovered_email_bank_id", "discovered_email", ["bank_id"])
    op.create_foreign_key(
        "fk_discovered_email_bank_id",
        "discovered_email",
        "banks",
        ["bank_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_discovered_email_bank_id", "discovered_email", type_="foreignkey")
    op.drop_index("ix_discovered_email_bank_id", table_name="discovered_email")
    op.drop_column("discovered_email", "bank_id")

    op.drop_constraint("fk_extraction_run_bank_id", "extraction_run", type_="foreignkey")
    op.drop_index("ix_extraction_run_bank_id", table_name="extraction_run")
    op.drop_column("extraction_run", "bank_id")
