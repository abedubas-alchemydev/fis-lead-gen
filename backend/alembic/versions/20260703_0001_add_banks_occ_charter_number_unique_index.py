"""add a partial unique index on banks.occ_charter_number (full-directory OCC upsert)

The bank vertical is expanding from the ~59 new/pending charters it tracked
first to the FULL official directory: the ~4,300 ACTIVE FDIC-insured
institutions (``FdicBankFindService.fetch_all_active_institutions``) plus the
OCC-only *uninsured* trust companies the FDIC BankFind list never carries.
Those OCC-only rows are synced by
``BankRepository.upsert_occ_institutions`` via
``INSERT ... ON CONFLICT (occ_charter_number) DO UPDATE`` — which needs a
UNIQUE index on ``occ_charter_number`` to serve as the conflict target.

PARTIAL (``WHERE occ_charter_number IS NOT NULL``) for two reasons:

- it is the exact predicate the upsert's ``ON CONFLICT ... index_where``
  passes, so Postgres can infer this index as the arbiter; and
- ``occ_charter_number`` stays NULLable for every FDIC/state row that never
  had an OCC charter number, and the partial index keeps only the
  OCC-numbered rows (small + relevant). Postgres already treats NULLs as
  distinct under a unique index, so the predicate changes nothing about NULL
  handling — it just scopes the index.

Additive and reversible; no data migration. The new/pending/digital-asset
views become filtered subsets of the full set (``list_banks`` gains the
``charter_types`` / ``new_charters_only`` filters), so the original ~59-row
behaviour is preserved.

Revision ID: 20260703_0001
Revises: 20260702_0004
Create Date: 2026-07-03
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "20260703_0001"
down_revision: str | None = "20260702_0004"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Partial UNIQUE index = the ON CONFLICT (occ_charter_number) arbiter for
    # BankRepository.upsert_occ_institutions. NULLs are excluded so the many
    # FDIC/state rows without an OCC charter number are unaffected.
    op.create_index(
        "uq_banks_occ_charter_number",
        "banks",
        ["occ_charter_number"],
        unique=True,
        postgresql_where=sa.text("occ_charter_number IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_banks_occ_charter_number", table_name="banks")
