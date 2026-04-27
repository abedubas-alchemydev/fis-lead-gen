"""add last_enrich_attempt_at to broker_dealers

Revision ID: 20260427_0017
Revises: 20260424_0016
Create Date: 2026-04-27

The /master-list/{id} detail page's useEffect fires
``POST /api/v1/broker-dealers/{id}/enrich`` on every visit. The existing
weak guard reads off ``ExecutiveContact.enriched_at``, which means firms
where Apollo previously returned no result have no rows -> the guard
reads as "never enriched" and the Apollo call re-fires forever.

This migration adds a single nullable indexed timestamp column on
``broker_dealers``. The service-side cooldown stamps it on every
Apollo-owned outcome (success + no-result) and short-circuits subsequent
calls within ``APOLLO_ENRICH_COOLDOWN_HOURS`` (default 24).

NULL on existing rows is correct: NULL means "never attempted", which
the cooldown guard treats as "no cooldown active". So no backfill needed
-- first-time calls behave exactly as before.

The btree index keeps the eventual ``WHERE last_enrich_attempt_at <
$cutoff`` housekeeping queries cheap, mirroring the indexing convention
on ``broker_dealers.lead_score`` / ``lead_priority``.

Downgrade drops the index first, then the column.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "20260427_0017"
down_revision: str | None = "20260424_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "broker_dealers",
        sa.Column(
            "last_enrich_attempt_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_broker_dealers_last_enrich_attempt_at",
        "broker_dealers",
        ["last_enrich_attempt_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_broker_dealers_last_enrich_attempt_at",
        table_name="broker_dealers",
    )
    op.drop_column("broker_dealers", "last_enrich_attempt_at")
