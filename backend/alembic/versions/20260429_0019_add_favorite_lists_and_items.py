"""Add favorite_list + favorite_list_item; backfill from user_favorite.

Revision ID: 20260429_0019
Revises: 20260429_0018
Create Date: 2026-04-29

Phase 1 of the custom favorites lists feature (#17). Two new tables back the
playlist-style "lists" UI:

* ``favorite_list``      — per-user named list. ``is_default=true`` flags the
  one list every existing favorites-having user gets seeded into during this
  migration; phase 2 will use that flag to protect it from rename/delete.
* ``favorite_list_item`` — M:N between ``favorite_list`` and
  ``broker_dealers``.

Safety-net rationale — DO NOT drop ``user_favorite`` here:
the legacy single-table ``user_favorite`` and its endpoints (POST/DELETE
/broker-dealers/{id}/favorite, GET /favorites) keep working after this PR.
Phase 2 swaps the FE over to the new tables; phase 3 (one release cycle
later) drops the old table once we're sure no rollback is needed. Leaving
``user_favorite`` in place means rollback = drop new tables only.

Backfill logic:
1. One default ``favorite_list`` row per distinct ``user_favorite.user_id``,
   named 'Favorites', ``is_default=true``.
2. One ``favorite_list_item`` per ``user_favorite`` row, joined back to the
   default list by ``user_id``. Original ``created_at`` is preserved so
   "added X days ago" timestamps survive the move.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "20260429_0019"
down_revision: str | None = "20260429_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "favorite_list",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column(
            "is_default",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "name", name="uq_favorite_list_user_name"),
    )
    op.create_index(
        "ix_favorite_list_user_id", "favorite_list", ["user_id"], unique=False
    )

    op.create_table(
        "favorite_list_item",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("list_id", sa.BigInteger(), nullable=False),
        sa.Column("broker_dealer_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["list_id"], ["favorite_list.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["broker_dealer_id"], ["broker_dealers.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "list_id", "broker_dealer_id", name="uq_favorite_list_item_list_bd"
        ),
    )
    op.create_index(
        "ix_favorite_list_item_list_id",
        "favorite_list_item",
        ["list_id"],
        unique=False,
    )
    op.create_index(
        "ix_favorite_list_item_broker_dealer_id",
        "favorite_list_item",
        ["broker_dealer_id"],
        unique=False,
    )

    # Backfill: one default list per existing favoriting user.
    op.execute(
        """
        INSERT INTO favorite_list (user_id, name, is_default)
        SELECT DISTINCT user_id, 'Favorites', true
        FROM user_favorite
        ON CONFLICT (user_id, name) DO NOTHING
        """
    )

    # Backfill: copy each user_favorite row into items pointing at that user's
    # default list. created_at preserved so the FE's "added 3 days ago"
    # rendering survives the migration.
    op.execute(
        """
        INSERT INTO favorite_list_item (list_id, broker_dealer_id, created_at)
        SELECT fl.id, uf.bd_id, uf.created_at
        FROM user_favorite uf
        JOIN favorite_list fl
          ON fl.user_id = uf.user_id AND fl.is_default = true
        ON CONFLICT (list_id, broker_dealer_id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index(
        "ix_favorite_list_item_broker_dealer_id", table_name="favorite_list_item"
    )
    op.drop_index("ix_favorite_list_item_list_id", table_name="favorite_list_item")
    op.drop_table("favorite_list_item")

    op.drop_index("ix_favorite_list_user_id", table_name="favorite_list")
    op.drop_table("favorite_list")
    # user_favorite is intentionally left untouched (safety net).
