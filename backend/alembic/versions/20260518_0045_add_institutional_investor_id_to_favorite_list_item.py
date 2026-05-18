"""Add institutional_investor_id to favorite_list_item.

Revision ID: 20260518_0045
Revises: 20260518_0044
Create Date: 2026-05-18

The new Institutional Investors list ships behind the same favorites
surface as BDs and Advisors -- a user can favorite an investor the same
way they favorite a broker-dealer or an investment adviser. Extends the
existing typed-FK XOR pattern from migration 0031 to a 3-way XOR.

Two changes here:

1. New ``institutional_investor_id`` column with FK to
   ``institutional_investors.id`` and a partial unique index on
   ``(list_id, institutional_investor_id) WHERE institutional_investor_id
   IS NOT NULL`` -- same shape as the existing BD + advisor partial
   uniques.
2. Replace the binary BD/advisor XOR check with a 3-way "exactly one
   IS NOT NULL" check across all three FKs.

Existing rows are unaffected: ``broker_dealer_id`` OR ``advisor_id`` is
populated (per the prior XOR), the new check still passes because
exactly one of the three is non-null.

Sub-firm entity favorites (executive_contact, advisor_contact,
investor_contact, office) are intentionally deferred to a follow-up
migration -- this PR ships investor-firm favorites only, keeping the
constraint shape easy to reason about for review. The 7-way XOR
documented in the plan is the eventual target.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "20260518_0045"
down_revision: str | None = "20260518_0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "favorite_list_item",
        sa.Column("institutional_investor_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_favorite_list_item_institutional_investor_id",
        "favorite_list_item",
        "institutional_investors",
        ["institutional_investor_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_favorite_list_item_institutional_investor_id",
        "favorite_list_item",
        ["institutional_investor_id"],
        unique=False,
    )
    # Partial unique index mirroring the advisor + BD pattern from
    # migration 0031 -- a (list, investor) pair is unique only for rows
    # whose investor_id is populated.
    op.create_index(
        "uq_favorite_list_item_list_investor",
        "favorite_list_item",
        ["list_id", "institutional_investor_id"],
        unique=True,
        postgresql_where=sa.text("institutional_investor_id IS NOT NULL"),
    )
    # Replace the 2-way XOR with a 3-way XOR expressed as
    # sum-of-NULL-counts = 1. Cleaner than nested != for >2 columns.
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
        "+ CASE WHEN institutional_investor_id IS NOT NULL THEN 1 ELSE 0 END) = 1",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_favorite_list_item_exactly_one_target",
        "favorite_list_item",
        type_="check",
    )
    # Restore the original binary XOR.
    op.create_check_constraint(
        "ck_favorite_list_item_exactly_one_target",
        "favorite_list_item",
        "(broker_dealer_id IS NOT NULL) <> (advisor_id IS NOT NULL)",
    )
    op.drop_index(
        "uq_favorite_list_item_list_investor", table_name="favorite_list_item"
    )
    op.drop_index(
        "ix_favorite_list_item_institutional_investor_id",
        table_name="favorite_list_item",
    )
    op.drop_constraint(
        "fk_favorite_list_item_institutional_investor_id",
        "favorite_list_item",
        type_="foreignkey",
    )
    op.drop_column("favorite_list_item", "institutional_investor_id")
