"""add enrich_cancelled_at to extraction_run

Backs the Stop button on the Email Extractor's "Enrich All" flow. The
bulk-enrichment background task polls this timestamp before each row and
exits early when it's set; the cancel endpoint stamps it; the enrich-all
endpoint clears it on start so a re-run begins from a clean slate.

Nullable on purpose -- the unset value (NULL) is the signal "not
cancelled, run normally."

Revision ID: 20260518_0043
Revises: 20260518_0042
Create Date: 2026-05-18

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "20260518_0043"
down_revision: str | None = "20260518_0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "extraction_run",
        sa.Column("enrich_cancelled_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("extraction_run", "enrich_cancelled_at")
