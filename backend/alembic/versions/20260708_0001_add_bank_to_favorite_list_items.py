"""Add bank polymorphic FK to favorite_list_item (first-class "bank" favorite kind).

Revision ID: 20260708_0001
Revises: 20260703_0004
Create Date: 2026-07-08

The /banks vertical joins the favorites surface: a user can pin a bank to a
favorite list the same way they pin a broker-dealer / advisor / institutional
investor / Form 4 reporting owner. The DOX Share "lead share export" also
reads ``FavoriteListItem.bank_id``, so the column is a cross-team contract.
This adds:

- ``bank_id`` (FK banks, nullable) — the fifth typed target column.

and folds it into the two existing per-type guards so the invariants still
hold, mirroring how migrations 0031 → 0045 → 0055 grew the polymorphism:

- ``ck_favorite_list_item_exactly_one_target`` — the 4-way "exactly one
  target FK is non-null" XOR check (migration 0055) becomes a 5-way check.
  Existing rows are unaffected (exactly one of the prior four FKs is
  populated, so the 5-way check still passes).
- ``uq_favorite_list_item_list_bank`` — partial unique index on
  ``(list_id, bank_id) WHERE bank_id IS NOT NULL``, the sibling of the
  BD / advisor / investor / reporting-owner partial uniques. Partial so
  rows of the other four types (bank_id NULL) never collide.

The FK uses ``ON DELETE CASCADE`` to match the four sibling target FKs — a
favorite must not outlive the bank it points at. Indexed to match the
sibling columns' read patterns (the /my-favorites hydration outer-joins on
each target column).

DOWNGRADE NOTE (delete-by-design, mirroring migration 0055): bank favorites
have no representation in the pre-bank (4-way) world — a row with only
``bank_id`` set would have zero of the four counted FKs populated and so
violate the 4-way check the downgrade re-adds. The downgrade therefore
DELETES bank favorite rows before restoring the constraint. Unlike
``outreach_sends`` (migration 20260703_0003, which fails-by-design because
sends are a compliance/audit trail), favorite_list_item rows are user
preferences a user can recreate in two clicks — so the 0055 precedent
(drop the unrepresentable rows, keep the downgrade runnable) applies here.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260708_0001"
down_revision: str | None = "20260703_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "favorite_list_item",
        sa.Column("bank_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_favorite_list_item_bank_id",
        "favorite_list_item",
        "banks",
        ["bank_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_favorite_list_item_bank_id",
        "favorite_list_item",
        ["bank_id"],
        unique=False,
    )
    # Partial unique index mirroring the BD / advisor / investor /
    # reporting-owner pattern — a (list, bank) pair is unique only for rows
    # whose bank_id is populated. ON CONFLICT writers must bind it with the
    # matching ``index_where`` predicate.
    op.create_index(
        "uq_favorite_list_item_list_bank",
        "favorite_list_item",
        ["list_id", "bank_id"],
        unique=True,
        postgresql_where=sa.text("bank_id IS NOT NULL"),
    )
    # Extend the 4-way XOR to a 5-way XOR (sum-of-non-null-counts = 1).
    op.drop_constraint(
        "ck_favorite_list_item_exactly_one_target",
        "favorite_list_item",
        type_="check",
    )
    op.create_check_constraint(
        "ck_favorite_list_item_exactly_one_target",
        "favorite_list_item",
        "(CASE WHEN broker_dealer_id IS NOT NULL THEN 1 ELSE 0 END "
        "+ CASE WHEN advisor_id IS NOT NULL THEN 1 ELSE 0 END "
        "+ CASE WHEN institutional_investor_id IS NOT NULL THEN 1 ELSE 0 END "
        "+ CASE WHEN reporting_owner_id IS NOT NULL THEN 1 ELSE 0 END "
        "+ CASE WHEN bank_id IS NOT NULL THEN 1 ELSE 0 END) = 1",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_favorite_list_item_exactly_one_target",
        "favorite_list_item",
        type_="check",
    )
    # Bank favorites can't be represented under the 4-way check re-added
    # below (their only populated target FK is bank_id) — drop them so the
    # downgrade doesn't abort on existing bank favorites. Deliberate: see
    # the DOWNGRADE NOTE in the module docstring.
    op.execute("DELETE FROM favorite_list_item WHERE bank_id IS NOT NULL")
    # Restore the 4-way XOR (from migration 0055).
    op.create_check_constraint(
        "ck_favorite_list_item_exactly_one_target",
        "favorite_list_item",
        "(CASE WHEN broker_dealer_id IS NOT NULL THEN 1 ELSE 0 END "
        "+ CASE WHEN advisor_id IS NOT NULL THEN 1 ELSE 0 END "
        "+ CASE WHEN institutional_investor_id IS NOT NULL THEN 1 ELSE 0 END "
        "+ CASE WHEN reporting_owner_id IS NOT NULL THEN 1 ELSE 0 END) = 1",
    )
    op.drop_index(
        "uq_favorite_list_item_list_bank",
        table_name="favorite_list_item",
    )
    op.drop_index(
        "ix_favorite_list_item_bank_id",
        table_name="favorite_list_item",
    )
    op.drop_constraint(
        "fk_favorite_list_item_bank_id",
        "favorite_list_item",
        type_="foreignkey",
    )
    op.drop_column("favorite_list_item", "bank_id")
