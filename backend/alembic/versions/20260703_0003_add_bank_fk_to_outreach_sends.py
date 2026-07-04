"""Add bank polymorphic FKs to outreach_sends (first-class "bank" outreach kind).

Revision ID: 20260703_0003
Revises: 20260703_0002
Create Date: 2026-07-03

Per-contact Outreach is coming to the bank profile, so a send row needs to be
able to point at a bank + a bank_contact the same way it already points at a
broker-dealer/advisor/investor + their contact. This adds:

- ``bank_id`` (FK banks, nullable) — the firm side.
- ``bank_contact_id`` (FK bank_contacts, nullable) — the contact side.

and folds the new pair into the three existing partial CHECK constraints so
the invariants still hold:

- ``ck_outreach_sends_at_most_one_firm`` — at most one of the (now four)
  firm FKs is non-null.
- ``ck_outreach_sends_at_most_one_contact`` — at most one of the (now four)
  contact FKs is non-null.
- ``ck_outreach_sends_adhoc_has_recipient`` — a row must carry a contact FK
  (any of the four) OR a free-form ``recipient_email``. Bank sends set
  ``bank_contact_id`` and no ``recipient_email``, so this MUST list the new
  column or every bank send would violate it.

Both FKs use ``ON DELETE CASCADE`` to match the sibling firm/contact FKs
(migration 0046) — a send row must not outlive the bank / bank_contact it
references. Indexed to match the sibling columns' read patterns.

DOWNGRADE HAZARD (fail-by-design, mirroring migration 0002): once any bank
outreach has been sent, ``outreach_sends`` holds rows whose only contact linkage
is ``bank_contact_id`` (no ``recipient_email``, no other contact FK). The
downgrade restores the pre-bank three-way ``ck_outreach_sends_adhoc_has_recipient``
CHECK, which those rows violate — so the downgrade RAISES on existing bank sends
rather than silently deleting them. ``outreach_sends`` is a compliance/audit trail
(a record of emails actually sent), so a rollback must not discard it
automatically. Postgres DDL is transactional, so a failed downgrade rolls back
whole — no half-dropped constraint persists. To revert with bank sends on file,
first consciously handle them (delete or re-home to ``recipient_email``), then
re-run the downgrade.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260703_0003"
down_revision: str | None = "20260703_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "outreach_sends",
        sa.Column("bank_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "outreach_sends",
        sa.Column("bank_contact_id", sa.Integer(), nullable=True),
    )
    op.create_index("ix_outreach_sends_bank_id", "outreach_sends", ["bank_id"])
    op.create_index(
        "ix_outreach_sends_bank_contact_id", "outreach_sends", ["bank_contact_id"]
    )
    op.create_foreign_key(
        "fk_outreach_sends_bank_id",
        "outreach_sends",
        "banks",
        ["bank_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_outreach_sends_bank_contact_id",
        "outreach_sends",
        "bank_contacts",
        ["bank_contact_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # Re-cast the three partial CHECKs to include the bank pair.
    op.drop_constraint("ck_outreach_sends_at_most_one_firm", "outreach_sends", type_="check")
    op.create_check_constraint(
        "ck_outreach_sends_at_most_one_firm",
        "outreach_sends",
        "(CASE WHEN broker_dealer_id IS NOT NULL THEN 1 ELSE 0 END "
        "+ CASE WHEN advisor_id IS NOT NULL THEN 1 ELSE 0 END "
        "+ CASE WHEN institutional_investor_id IS NOT NULL THEN 1 ELSE 0 END "
        "+ CASE WHEN bank_id IS NOT NULL THEN 1 ELSE 0 END) <= 1",
    )

    op.drop_constraint("ck_outreach_sends_at_most_one_contact", "outreach_sends", type_="check")
    op.create_check_constraint(
        "ck_outreach_sends_at_most_one_contact",
        "outreach_sends",
        "(CASE WHEN contact_id IS NOT NULL THEN 1 ELSE 0 END "
        "+ CASE WHEN advisor_contact_id IS NOT NULL THEN 1 ELSE 0 END "
        "+ CASE WHEN investor_contact_id IS NOT NULL THEN 1 ELSE 0 END "
        "+ CASE WHEN bank_contact_id IS NOT NULL THEN 1 ELSE 0 END) <= 1",
    )

    op.drop_constraint("ck_outreach_sends_adhoc_has_recipient", "outreach_sends", type_="check")
    op.create_check_constraint(
        "ck_outreach_sends_adhoc_has_recipient",
        "outreach_sends",
        "(contact_id IS NOT NULL "
        "OR advisor_contact_id IS NOT NULL "
        "OR investor_contact_id IS NOT NULL "
        "OR bank_contact_id IS NOT NULL "
        "OR recipient_email IS NOT NULL)",
    )


def downgrade() -> None:
    # Restore the pre-bank CHECK definitions (three-way). A downgrade with bank
    # sends on file (bank_contact_id set, no recipient_email) will FAIL here by
    # design when the three-way ``adhoc_has_recipient`` CHECK is re-created on
    # rows it doesn't admit — see the DOWNGRADE HAZARD note above. This is
    # deliberate: outreach_sends is an audit trail, so reverting requires a
    # human to consciously handle those rows first rather than lose them.
    op.drop_constraint("ck_outreach_sends_adhoc_has_recipient", "outreach_sends", type_="check")
    op.create_check_constraint(
        "ck_outreach_sends_adhoc_has_recipient",
        "outreach_sends",
        "(contact_id IS NOT NULL "
        "OR advisor_contact_id IS NOT NULL "
        "OR investor_contact_id IS NOT NULL "
        "OR recipient_email IS NOT NULL)",
    )

    op.drop_constraint("ck_outreach_sends_at_most_one_contact", "outreach_sends", type_="check")
    op.create_check_constraint(
        "ck_outreach_sends_at_most_one_contact",
        "outreach_sends",
        "(CASE WHEN contact_id IS NOT NULL THEN 1 ELSE 0 END "
        "+ CASE WHEN advisor_contact_id IS NOT NULL THEN 1 ELSE 0 END "
        "+ CASE WHEN investor_contact_id IS NOT NULL THEN 1 ELSE 0 END) <= 1",
    )

    op.drop_constraint("ck_outreach_sends_at_most_one_firm", "outreach_sends", type_="check")
    op.create_check_constraint(
        "ck_outreach_sends_at_most_one_firm",
        "outreach_sends",
        "(CASE WHEN broker_dealer_id IS NOT NULL THEN 1 ELSE 0 END "
        "+ CASE WHEN advisor_id IS NOT NULL THEN 1 ELSE 0 END "
        "+ CASE WHEN institutional_investor_id IS NOT NULL THEN 1 ELSE 0 END) <= 1",
    )

    op.drop_constraint("fk_outreach_sends_bank_contact_id", "outreach_sends", type_="foreignkey")
    op.drop_constraint("fk_outreach_sends_bank_id", "outreach_sends", type_="foreignkey")
    op.drop_index("ix_outreach_sends_bank_contact_id", table_name="outreach_sends")
    op.drop_index("ix_outreach_sends_bank_id", table_name="outreach_sends")
    op.drop_column("outreach_sends", "bank_contact_id")
    op.drop_column("outreach_sends", "bank_id")
