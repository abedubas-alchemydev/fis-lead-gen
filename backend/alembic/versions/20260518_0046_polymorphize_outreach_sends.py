"""Polymorphize outreach_sends with advisor + investor firm + contact FKs.

Revision ID: 20260518_0046
Revises: 20260518_0045
Create Date: 2026-05-18

The outreach send surface ships behind /advisor-list and /investors as
well as /master-list. The audit row needs to record which firm + contact
type the send went to. Same typed-FK XOR pattern used for
``favorite_list_item``:

* Three nullable firm FKs: ``broker_dealer_id`` (existing, now nullable),
  ``advisor_id`` (new), ``institutional_investor_id`` (new).
* Three nullable contact FKs: ``contact_id`` (existing exec_contact,
  now nullable), ``advisor_contact_id`` (new), ``investor_contact_id``
  (new).
* Two CHECK constraints enforce exactly one firm FK and exactly one
  contact FK is set per row.

Existing rows are unaffected: ``broker_dealer_id`` + ``contact_id`` stay
populated and the new XOR checks pass because exactly one of each is
non-null.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "20260518_0046"
down_revision: str | None = "20260518_0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Drop NOT NULL on the existing typed FK columns. They become
    # "the BD branch of the XOR" rather than "always populated".
    op.alter_column(
        "outreach_sends",
        "broker_dealer_id",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.alter_column(
        "outreach_sends",
        "contact_id",
        existing_type=sa.Integer(),
        nullable=True,
    )

    op.add_column(
        "outreach_sends",
        sa.Column("advisor_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "outreach_sends",
        sa.Column("institutional_investor_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "outreach_sends",
        sa.Column("advisor_contact_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "outreach_sends",
        sa.Column("investor_contact_id", sa.Integer(), nullable=True),
    )

    op.create_foreign_key(
        "fk_outreach_sends_advisor_id",
        "outreach_sends",
        "investment_advisors",
        ["advisor_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_outreach_sends_institutional_investor_id",
        "outreach_sends",
        "institutional_investors",
        ["institutional_investor_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_outreach_sends_advisor_contact_id",
        "outreach_sends",
        "advisor_contacts",
        ["advisor_contact_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_outreach_sends_investor_contact_id",
        "outreach_sends",
        "investor_contacts",
        ["investor_contact_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.create_index(
        "ix_outreach_sends_advisor_id",
        "outreach_sends",
        ["advisor_id"],
        unique=False,
    )
    op.create_index(
        "ix_outreach_sends_institutional_investor_id",
        "outreach_sends",
        ["institutional_investor_id"],
        unique=False,
    )
    op.create_index(
        "ix_outreach_sends_advisor_contact_id",
        "outreach_sends",
        ["advisor_contact_id"],
        unique=False,
    )
    op.create_index(
        "ix_outreach_sends_investor_contact_id",
        "outreach_sends",
        ["investor_contact_id"],
        unique=False,
    )

    op.create_check_constraint(
        "ck_outreach_sends_exactly_one_firm",
        "outreach_sends",
        "(CASE WHEN broker_dealer_id IS NOT NULL THEN 1 ELSE 0 END "
        "+ CASE WHEN advisor_id IS NOT NULL THEN 1 ELSE 0 END "
        "+ CASE WHEN institutional_investor_id IS NOT NULL THEN 1 ELSE 0 END) = 1",
    )
    op.create_check_constraint(
        "ck_outreach_sends_exactly_one_contact",
        "outreach_sends",
        "(CASE WHEN contact_id IS NOT NULL THEN 1 ELSE 0 END "
        "+ CASE WHEN advisor_contact_id IS NOT NULL THEN 1 ELSE 0 END "
        "+ CASE WHEN investor_contact_id IS NOT NULL THEN 1 ELSE 0 END) = 1",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_outreach_sends_exactly_one_contact",
        "outreach_sends",
        type_="check",
    )
    op.drop_constraint(
        "ck_outreach_sends_exactly_one_firm",
        "outreach_sends",
        type_="check",
    )

    op.drop_index(
        "ix_outreach_sends_investor_contact_id", table_name="outreach_sends"
    )
    op.drop_index(
        "ix_outreach_sends_advisor_contact_id", table_name="outreach_sends"
    )
    op.drop_index(
        "ix_outreach_sends_institutional_investor_id", table_name="outreach_sends"
    )
    op.drop_index("ix_outreach_sends_advisor_id", table_name="outreach_sends")

    op.drop_constraint(
        "fk_outreach_sends_investor_contact_id",
        "outreach_sends",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_outreach_sends_advisor_contact_id",
        "outreach_sends",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_outreach_sends_institutional_investor_id",
        "outreach_sends",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_outreach_sends_advisor_id",
        "outreach_sends",
        type_="foreignkey",
    )

    op.drop_column("outreach_sends", "investor_contact_id")
    op.drop_column("outreach_sends", "advisor_contact_id")
    op.drop_column("outreach_sends", "institutional_investor_id")
    op.drop_column("outreach_sends", "advisor_id")

    op.alter_column(
        "outreach_sends",
        "contact_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.alter_column(
        "outreach_sends",
        "broker_dealer_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
