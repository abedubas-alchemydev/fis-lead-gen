"""Drop user_favorite for real, after code refactor.

Revision ID: 20260429_0022
Revises: 20260429_0021
Create Date: 2026-04-29

Hotfix follow-on to PR #157 / migration ``20260429_0021``. The previous drop
went out before the BE code referencing ``user_favorite`` had been removed,
which 500'd the firm-detail endpoint (``schemas.broker_dealer.is_favorited``
queried the dropped table). Mitigation was to re-create the table by hand
on prod so the page recovered; this migration finishes the job by dropping
the table cleanly once the code is no longer reading from or writing to it.

Idempotency note — by the time this runs, ``user_favorite`` may or may not
exist depending on environment:

* prod: re-created manually as a hotfix mitigation -> table EXISTS
* staging / fresh dev: dropped cleanly by ``20260429_0021`` -> table DOES
  NOT exist
* CI on a fresh DB: created by ``20260424_0016`` then dropped by
  ``20260429_0021`` -> table DOES NOT exist when this runs

``DROP TABLE IF EXISTS ... CASCADE`` handles all three. CASCADE is defensive
against any straggler FK that wasn't explicitly captured here.

Reversibility — ``downgrade`` recreates the table empty so the alembic chain
stays walkable. Data is NOT restored: the original favorites already live in
``favorite_list_item`` (backfilled by ``20260429_0019``), so a rollback path
is effectively a no-op for users. Same approach as ``20260429_0021``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "20260429_0022"
down_revision: str | None = "20260429_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Idempotent: the table was dropped by 20260429_0021 and may or may not
    # have been re-created manually as a hotfix mitigation. Drop again,
    # cleanly, only if present. CASCADE clears any straggler FKs.
    op.execute("DROP TABLE IF EXISTS user_favorite CASCADE")


def downgrade() -> None:
    # Recreate user_favorite empty with the EXACT schema from 20260424_0016.
    # Data is NOT restored — favorites live in favorite_list_item; restoring
    # rows here would require a Neon point-in-time restore.
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
    op.create_index(
        "ix_user_favorite_user_id", "user_favorite", ["user_id"], unique=False
    )
    op.create_index(
        "ix_user_favorite_bd_id", "user_favorite", ["bd_id"], unique=False
    )
    op.create_index(
        "ix_user_favorite_created_at",
        "user_favorite",
        [sa.text("created_at DESC")],
        unique=False,
    )
