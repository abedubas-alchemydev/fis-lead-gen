"""Allow ad-hoc (free-form recipient) outreach_sends rows.

Revision ID: 20260528_0063
Revises: 20260528_0062
Create Date: 2026-05-28

The new ``/outreach/sent?tab=create`` workspace lets users send outreach
to an arbitrary email address that has no contact / firm record in our
system. Migration 0046 wired the table for the BD / advisor / investor
XOR but required *exactly one* firm + contact FK per row. Adhoc rows
need to set zero of each, so this revision:

* Relaxes both XOR check constraints from ``= 1`` to ``<= 1``.
* Adds ``recipient_email`` + ``recipient_name`` columns. They are
  always populated for adhoc rows; contact-based rows leave them NULL
  and continue to derive the address from the joined contact row.

No data backfill: pre-existing rows already satisfy ``<= 1`` (they
satisfy ``= 1``) and don't need ``recipient_email`` because the
``_row_to_item`` flattener still pulls from the contact join for them.
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
    op.drop_constraint(
        "ck_outreach_sends_exactly_one_firm",
        "outreach_sends",
        type_="check",
    )
    op.drop_constraint(
        "ck_outreach_sends_exactly_one_contact",
        "outreach_sends",
        type_="check",
    )

    op.add_column(
        "outreach_sends",
        sa.Column("recipient_email", sa.String(length=320), nullable=True),
    )
    op.add_column(
        "outreach_sends",
        sa.Column("recipient_name", sa.String(length=255), nullable=True),
    )

    # Relaxed XOR: adhoc rows set zero firm FKs / zero contact FKs and
    # carry ``recipient_email`` instead. Keep the "no more than one"
    # invariant so a row can't straddle firm types.
    op.create_check_constraint(
        "ck_outreach_sends_at_most_one_firm",
        "outreach_sends",
        "(CASE WHEN broker_dealer_id IS NOT NULL THEN 1 ELSE 0 END "
        "+ CASE WHEN advisor_id IS NOT NULL THEN 1 ELSE 0 END "
        "+ CASE WHEN institutional_investor_id IS NOT NULL THEN 1 ELSE 0 END) <= 1",
    )
    op.create_check_constraint(
        "ck_outreach_sends_at_most_one_contact",
        "outreach_sends",
        "(CASE WHEN contact_id IS NOT NULL THEN 1 ELSE 0 END "
        "+ CASE WHEN advisor_contact_id IS NOT NULL THEN 1 ELSE 0 END "
        "+ CASE WHEN investor_contact_id IS NOT NULL THEN 1 ELSE 0 END) <= 1",
    )
    # Adhoc rows must carry a recipient_email. Contact-based rows pull
    # the address from the joined contact at read time and may leave
    # this NULL (historical data).
    op.create_check_constraint(
        "ck_outreach_sends_adhoc_has_recipient",
        "outreach_sends",
        "(contact_id IS NOT NULL "
        "OR advisor_contact_id IS NOT NULL "
        "OR investor_contact_id IS NOT NULL "
        "OR recipient_email IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_outreach_sends_adhoc_has_recipient",
        "outreach_sends",
        type_="check",
    )
    op.drop_constraint(
        "ck_outreach_sends_at_most_one_contact",
        "outreach_sends",
        type_="check",
    )
    op.drop_constraint(
        "ck_outreach_sends_at_most_one_firm",
        "outreach_sends",
        type_="check",
    )

    op.drop_column("outreach_sends", "recipient_name")
    op.drop_column("outreach_sends", "recipient_email")

    # Restore the strict-XOR check constraints from 0046. Any adhoc rows
    # written between this revision and the downgrade will violate them
    # and the downgrade will fail -- intentional, since downgrading
    # past this point requires deciding what to do with the orphans.
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
