"""Add reporting_owners table + reporting_owner_id to favorite_list_item.

Revision ID: 20260521_0055
Revises: 20260521_0054
Create Date: 2026-05-21

The /investors page lists SEC Form 4 insider transactions; the client
wants to favorite the *insider* (reporting owner) so a saved person can
be tracked across all their future filings. Reporting owners were only
denormalized columns on ``form4_transactions``; this promotes them to a
first-class entity (CIK + canonical name) so they slot into the existing
favorites surface as a 4th typed FK.

Two changes here, mirroring migration 0044 (table) + 0045 (XOR rewrite):

1. New ``reporting_owners`` table, keyed by CIK (unique). ``cik`` matches
   ``form4_transactions.reporting_owner_cik`` width (String(16)) so the
   investors-list join is clean. Backfilled DISTINCT-per-CIK from
   ``form4_transactions``, picking the most-recent name (insider names
   vary slightly across filings).
2. New ``reporting_owner_id`` FK on ``favorite_list_item`` + partial
   unique index, and the 3-way "exactly one IS NOT NULL" check becomes a
   4-way check. Existing rows are unaffected (exactly one of the prior
   three FKs is populated, so the 4-way check still passes).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "20260521_0055"
down_revision: str | None = "20260521_0054"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reporting_owners",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("cik", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # CIK is the natural key. Non-partial unique (cik is NOT NULL here,
    # unlike the partial-unique CIK on institutional_investors) so the
    # backfill's ON CONFLICT (cik) and the favorite endpoint's upsert can
    # target it.
    op.create_index(
        "ix_reporting_owners_cik",
        "reporting_owners",
        ["cik"],
        unique=True,
    )

    # Backfill one row per distinct CIK seen in Form 4 history, taking the
    # most-recent name as canonical. DISTINCT ON keeps the first row per
    # cik after the ORDER BY, so filed_at DESC wins. ON CONFLICT keeps a
    # downgrade-then-upgrade idempotent.
    op.execute(
        """
        INSERT INTO reporting_owners (cik, name, created_at, updated_at)
        SELECT DISTINCT ON (reporting_owner_cik)
            reporting_owner_cik, reporting_owner_name, now(), now()
        FROM form4_transactions
        WHERE reporting_owner_cik <> ''
        ORDER BY reporting_owner_cik, filed_at DESC
        ON CONFLICT (cik) DO NOTHING
        """
    )

    op.add_column(
        "favorite_list_item",
        sa.Column("reporting_owner_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_favorite_list_item_reporting_owner_id",
        "favorite_list_item",
        "reporting_owners",
        ["reporting_owner_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_favorite_list_item_reporting_owner_id",
        "favorite_list_item",
        ["reporting_owner_id"],
        unique=False,
    )
    # Partial unique index mirroring the BD / advisor / investor pattern --
    # a (list, reporting_owner) pair is unique only for rows whose
    # reporting_owner_id is populated.
    op.create_index(
        "uq_favorite_list_item_list_reporting_owner",
        "favorite_list_item",
        ["list_id", "reporting_owner_id"],
        unique=True,
        postgresql_where=sa.text("reporting_owner_id IS NOT NULL"),
    )
    # Extend the 3-way XOR to a 4-way XOR (sum-of-non-null-counts = 1).
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
        "+ CASE WHEN reporting_owner_id IS NOT NULL THEN 1 ELSE 0 END) = 1",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_favorite_list_item_exactly_one_target",
        "favorite_list_item",
        type_="check",
    )
    # Reporting-owner favorites have no representation in the pre-0055
    # (3-way) world: a row with only reporting_owner_id set would have
    # zero of the three counted FKs populated and so violate the 3-way
    # check we re-add below. Drop those rows before re-adding the
    # constraint so the downgrade doesn't abort on existing insider
    # favorites.
    op.execute(
        "DELETE FROM favorite_list_item WHERE reporting_owner_id IS NOT NULL"
    )
    # Restore the 3-way XOR (from migration 0045).
    op.create_check_constraint(
        "ck_favorite_list_item_exactly_one_target",
        "favorite_list_item",
        "(CASE WHEN broker_dealer_id IS NOT NULL THEN 1 ELSE 0 END "
        "+ CASE WHEN advisor_id IS NOT NULL THEN 1 ELSE 0 END "
        "+ CASE WHEN institutional_investor_id IS NOT NULL THEN 1 ELSE 0 END) = 1",
    )
    op.drop_index(
        "uq_favorite_list_item_list_reporting_owner",
        table_name="favorite_list_item",
    )
    op.drop_index(
        "ix_favorite_list_item_reporting_owner_id",
        table_name="favorite_list_item",
    )
    op.drop_constraint(
        "fk_favorite_list_item_reporting_owner_id",
        "favorite_list_item",
        type_="foreignkey",
    )
    op.drop_column("favorite_list_item", "reporting_owner_id")

    op.drop_index("ix_reporting_owners_cik", table_name="reporting_owners")
    op.drop_table("reporting_owners")
