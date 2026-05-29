"""add apollo_person_id to form4_transactions

Lets the async Apollo phone-reveal webhook find a Form 4 insider's rows by
the MongoDB-style ``person.id`` we capture on the sync ``/people/match``
response. Mirrors the ``apollo_person_id`` column already on the three
contact tables (migration 20260528_0065). Nullable + indexed: most rows are
NULL until a "Find contact" click stamps the id, and the webhook looks rows
up by an equality probe on this column.

Revision ID: 20260529_0071
Revises: 20260529_0070
Create Date: 2026-05-29
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "20260529_0071"
down_revision: str | None = "20260529_0070"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "form4_transactions",
        sa.Column("apollo_person_id", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_form4_transactions_apollo_person_id",
        "form4_transactions",
        ["apollo_person_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_form4_transactions_apollo_person_id",
        table_name="form4_transactions",
    )
    op.drop_column("form4_transactions", "apollo_person_id")
