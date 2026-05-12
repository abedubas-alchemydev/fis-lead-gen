"""Add advisor_id to favorite_list_item.

Revision ID: 20260508_0031
Revises: 20260508_0030
Create Date: 2026-05-08

The Investment Advisor Master List ships behind the same favorites surface
as the BD list — a user can favorite an advisor the same way they favorite
a broker-dealer. Rather than introduce a parallel ``advisor_favorite_list``
table, we polymorph the existing ``favorite_list_item`` row to point at
either a BD or an advisor (but never both).

Two changes here:

1. ``broker_dealer_id`` becomes nullable (it was NOT NULL because every
   row had to point at a BD).
2. New ``advisor_id`` column with FK to ``investment_advisors.id`` and a
   CHECK constraint enforcing exactly one of (broker_dealer_id, advisor_id)
   is non-null per row.

The existing ``uq_favorite_list_item_list_bd`` unique constraint protects
against duplicate (list, BD) entries; we add a sibling
``uq_favorite_list_item_list_advisor`` for the advisor side. Each
constraint is partial (covers only rows of its own type) — Postgres treats
NULLs as distinct in unique indexes, so duplicate (list, NULL) rows would
otherwise sneak past.

Existing rows are unaffected: ``broker_dealer_id`` stays populated, and
the CHECK passes because exactly one column is non-null.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "20260508_0031"
down_revision: str | None = "20260508_0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "favorite_list_item",
        "broker_dealer_id",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.add_column(
        "favorite_list_item",
        sa.Column("advisor_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_favorite_list_item_advisor_id",
        "favorite_list_item",
        "investment_advisors",
        ["advisor_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_favorite_list_item_advisor_id",
        "favorite_list_item",
        ["advisor_id"],
        unique=False,
    )
    # Partial unique index: only enforce uniqueness of (list, advisor) pairs
    # for rows that actually point at an advisor. Mirrors the existing
    # uq_favorite_list_item_list_bd constraint shape.
    op.create_index(
        "uq_favorite_list_item_list_advisor",
        "favorite_list_item",
        ["list_id", "advisor_id"],
        unique=True,
        postgresql_where=sa.text("advisor_id IS NOT NULL"),
    )
    # Exactly-one-of guard. Without this, a row could carry both a
    # broker_dealer_id and an advisor_id (or neither) and the FE would
    # silently render whichever side it found first. The XOR via "exactly
    # one IS NOT NULL" rules that out at the DB layer.
    op.create_check_constraint(
        "ck_favorite_list_item_exactly_one_target",
        "favorite_list_item",
        "(broker_dealer_id IS NOT NULL) <> (advisor_id IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_favorite_list_item_exactly_one_target",
        "favorite_list_item",
        type_="check",
    )
    op.drop_index(
        "uq_favorite_list_item_list_advisor", table_name="favorite_list_item"
    )
    op.drop_index(
        "ix_favorite_list_item_advisor_id", table_name="favorite_list_item"
    )
    op.drop_constraint(
        "fk_favorite_list_item_advisor_id",
        "favorite_list_item",
        type_="foreignkey",
    )
    op.drop_column("favorite_list_item", "advisor_id")
    op.alter_column(
        "favorite_list_item",
        "broker_dealer_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
