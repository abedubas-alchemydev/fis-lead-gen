"""Drop user_favorite safety-net table (post #17 phase 1+2 soak).

Revision ID: 20260429_0021
Revises: 20260429_0020
Create Date: 2026-04-29

Phase 3 of #17. ``user_favorite`` was retained as a rollback safety-net by the
phase-1 migration (``20260424_0016``) so the legacy single-table favorites
flow could be reverted to without data loss while the new ``favorite_list`` /
``favorite_list_item`` tables soaked. Phases 1+2 have now ridden one full
release cycle in production (PRs #140, #144, #150, #152) without incident, so
the table is no longer needed as a fallback.

Renumbering note — this migration was originally authored as ``20260429_0020``
and merged via PR #157 / release PR #158, but a parallel CLI's data-only
migration (``20260429_0020_update_default_scoring_weights``, PR #159 / #160)
raced into ``develop`` with the same revision id. Both deploys failed with
"Multiple head revisions are present". Renumbered to ``20260429_0021`` so the
chain stays linear: 0019 -> 0020 (scoring weights) -> 0021 (drop safety-net).
The ordering is safe — the two migrations touch disjoint tables.

Reversibility note — ``downgrade`` recreates the table with the EXACT schema
defined by ``20260424_0016`` (same column types, FK targets, indexes, and
``uq_user_favorite_user_bd`` unique constraint) so the alembic chain stays
walkable. Data is NOT restored on downgrade: the prior favorites already
live in ``favorite_list_item`` (backfilled by ``20260429_0019``), so a
rollback path is effectively a no-op for users. Restoring the original rows
would require a Neon point-in-time restore.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "20260429_0021"
down_revision: str | None = "20260429_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_user_favorite_created_at", table_name="user_favorite")
    op.drop_index("ix_user_favorite_bd_id", table_name="user_favorite")
    op.drop_index("ix_user_favorite_user_id", table_name="user_favorite")
    op.drop_table("user_favorite")


def downgrade() -> None:
    # Recreate user_favorite with the EXACT schema from 20260424_0016 so the
    # alembic chain stays walkable. Data is NOT restored — favorites already
    # live in favorite_list_item; restoring rows here would require a DB
    # point-in-time restore.
    op.create_table(
        "user_favorite",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("bd_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["bd_id"], ["broker_dealers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "bd_id", name="uq_user_favorite_user_bd"),
    )
    op.create_index("ix_user_favorite_user_id", "user_favorite", ["user_id"], unique=False)
    op.create_index("ix_user_favorite_bd_id", "user_favorite", ["bd_id"], unique=False)
    op.create_index(
        "ix_user_favorite_created_at",
        "user_favorite",
        [sa.text("created_at DESC")],
        unique=False,
    )
