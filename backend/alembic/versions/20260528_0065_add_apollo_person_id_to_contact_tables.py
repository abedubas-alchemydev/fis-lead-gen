"""Add apollo_person_id to advisor_contacts, executive_contacts, investor_contacts.

Revision ID: 20260528_0065
Revises: 20260528_0064
Create Date: 2026-05-28

Wires Apollo's async phone-number reveal flow. ``/people/match`` with
``reveal_phone_number=true`` returns the person object synchronously
(email + linkedin) but phone_numbers land later via webhook POST.
Apollo's sync response carries the MongoDB-style ``person.id`` we need
to correlate the async callback back to the row we wrote.

Indexed because the webhook handler does
``SELECT ... WHERE apollo_person_id = :id`` across all three tables on
every payload.

Additive, nullable, no constraint changes — safe to ship without a
backfill (existing rows land with NULL = "no Apollo reveal in flight").
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "20260528_0065"
down_revision: str | None = "20260528_0064"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TABLES = ("advisor_contacts", "executive_contacts", "investor_contacts")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column("apollo_person_id", sa.String(length=64), nullable=True),
        )
        op.create_index(
            f"ix_{table}_apollo_person_id",
            table,
            ["apollo_person_id"],
        )


def downgrade() -> None:
    for table in _TABLES:
        op.drop_index(
            f"ix_{table}_apollo_person_id",
            table_name=table,
        )
        op.drop_column(table, "apollo_person_id")
