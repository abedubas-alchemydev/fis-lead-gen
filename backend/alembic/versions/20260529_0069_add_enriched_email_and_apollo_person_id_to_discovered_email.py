"""Add enriched_email + apollo_person_id to discovered_email.

Revision ID: 20260529_0069
Revises: 20260528_0068
Create Date: 2026-05-29

Extends per-row Apollo enrichment of discovered emails to capture a
revealed email and to participate in the async phone-reveal flow:

- ``enriched_email`` -- the email Apollo surfaces during /people/match
  (the revealed personal email when reveal_personal_emails is on, else
  the matched work email). Distinct from ``email``, which is the
  discovered address that keys the lookup.
- ``apollo_person_id`` -- the MongoDB-style ``person.id`` from the sync
  match response. Indexed because the phone-reveal webhook correlates the
  async callback with ``SELECT ... WHERE apollo_person_id = :id`` (mirrors
  the contact tables in 20260528_0065).

Additive, nullable, no constraint changes -- safe to ship without a
backfill (existing rows land NULL = "not enriched / no reveal in flight").
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "20260529_0069"
down_revision: str | None = "20260528_0068"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TABLE = "discovered_email"


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column("enriched_email", sa.String(length=320), nullable=True),
    )
    op.add_column(
        _TABLE,
        sa.Column("apollo_person_id", sa.String(length=64), nullable=True),
    )
    op.create_index(
        f"ix_{_TABLE}_apollo_person_id",
        _TABLE,
        ["apollo_person_id"],
    )


def downgrade() -> None:
    op.drop_index(
        f"ix_{_TABLE}_apollo_person_id",
        table_name=_TABLE,
    )
    op.drop_column(_TABLE, "apollo_person_id")
    op.drop_column(_TABLE, "enriched_email")
