"""Add user_favorites and user_visits for per-user lists on broker-dealers.

Revision ID: 20260424_0016
Revises: 20260424_0015
Create Date: 2026-04-24

Two pure-additive tables that back the "My Favorites" and "Visited Firms"
sidebar pages. Both are keyed on ``(user_id, bd_id)`` with ON DELETE CASCADE
on each FK so user- or broker-dealer-deletion cleans up orphans automatically.
No data backfill: these tables are empty on first deploy and fill as users
interact with the detail page.

See plans/favorites-and-visits-2026-04-24.md for the full contract.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "20260424_0016"
down_revision: str | None = "20260424_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
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
    # DESC because GET /favorites sorts newest-first; the index matches the
    # dominant read path so the planner can skip a sort step.
    op.create_index(
        "ix_user_favorite_created_at",
        "user_favorite",
        [sa.text("created_at DESC")],
        unique=False,
    )

    op.create_table(
        "user_visit",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("bd_id", sa.Integer(), nullable=False),
        sa.Column(
            "visit_count",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column(
            "first_visited_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_visited_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["bd_id"], ["broker_dealers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "bd_id", name="uq_user_visit_user_bd"),
    )
    op.create_index("ix_user_visit_user_id", "user_visit", ["user_id"], unique=False)
    op.create_index("ix_user_visit_bd_id", "user_visit", ["bd_id"], unique=False)
    # DESC because GET /visits sorts last_visited_at DESC -- same rationale.
    op.create_index(
        "ix_user_visit_last_visited_at",
        "user_visit",
        [sa.text("last_visited_at DESC")],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_user_visit_last_visited_at", table_name="user_visit")
    op.drop_index("ix_user_visit_bd_id", table_name="user_visit")
    op.drop_index("ix_user_visit_user_id", table_name="user_visit")
    op.drop_table("user_visit")

    op.drop_index("ix_user_favorite_created_at", table_name="user_favorite")
    op.drop_index("ix_user_favorite_bd_id", table_name="user_favorite")
    op.drop_index("ix_user_favorite_user_id", table_name="user_favorite")
    op.drop_table("user_favorite")
