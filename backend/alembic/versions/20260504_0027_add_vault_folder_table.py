"""add vault_folder table for outreach service descriptions

Backs the per-user "Vault" feature (``frontend/app/(app)/vault``) which
holds named services + descriptions used by the Outreach modal on
``/master-list/{id}``. The MVP is description-only — file uploads and
RAG embeddings are deferred to a follow-up migration.

Revision ID: 20260504_0027
Revises: 20260503_0026
Create Date: 2026-05-04

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260504_0027"
down_revision: str | None = "20260503_0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vault_folder",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.String(length=255),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    # The list endpoint always filters by user_id; a name dedupe per user
    # is a soft product preference (the FE prevents duplicate folder
    # names per user but DB-level uniqueness keeps it honest).
    op.create_unique_constraint(
        "uq_vault_folder_user_name",
        "vault_folder",
        ["user_id", "name"],
    )
    op.create_index(
        "ix_vault_folder_user_id",
        "vault_folder",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_vault_folder_user_id", table_name="vault_folder")
    op.drop_constraint(
        "uq_vault_folder_user_name", "vault_folder", type_="unique"
    )
    op.drop_table("vault_folder")
