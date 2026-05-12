"""Add clearing_partner_merge_suggestions table.

Revision ID: 20260508_0034
Revises: 20260508_0033
Create Date: 2026-05-08

Persists clusters of raw ``current_clearing_partner`` variants that the
clustering pass thinks belong to one firm (e.g. "RBC Capital Markets,
LLC" / "RBC Capital Markets LLC" / "RBC Capital Markets Corp"). Admin
reviews each pending row at /settings and either accepts (creates a
CompetitorProvider with the variants as aliases) or rejects.

The unique ``cluster_signature`` enables idempotent reruns — a cluster
with the same exact members produces the same signature and is skipped
on insert, so an admin who rejects a suggestion never sees that exact
cluster again. A different cluster (even one that supersets a rejected
one) gets a new signature and surfaces as new pending.

Greenfield table; no backfill, no production data risk.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "20260508_0034"
down_revision: str | None = "20260508_0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "clearing_partner_merge_suggestions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("cluster_signature", sa.String(length=64), nullable=False),
        sa.Column("variants", sa.JSON(), nullable=False),
        sa.Column("suggested_name", sa.String(length=255), nullable=False),
        sa.Column("min_score", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column(
            "accepted_provider_id",
            sa.Integer(),
            sa.ForeignKey("competitor_providers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_clearing_partner_merge_suggestions_cluster_signature",
        "clearing_partner_merge_suggestions",
        ["cluster_signature"],
        unique=True,
    )
    op.create_index(
        "ix_clearing_partner_merge_suggestions_status",
        "clearing_partner_merge_suggestions",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_clearing_partner_merge_suggestions_status",
        table_name="clearing_partner_merge_suggestions",
    )
    op.drop_index(
        "ix_clearing_partner_merge_suggestions_cluster_signature",
        table_name="clearing_partner_merge_suggestions",
    )
    op.drop_table("clearing_partner_merge_suggestions")
