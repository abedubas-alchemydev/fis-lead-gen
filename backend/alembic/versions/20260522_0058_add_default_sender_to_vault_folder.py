"""Add vault_folder.default_sender_account_id for per-service sender defaults.

Revision ID: 20260522_0058
Revises: 20260522_0057
Create Date: 2026-05-22

Each Vault folder represents one service the user sells (Custody, Stock
Loan, etc.). Multi-sender outreach lets a folder pin a default sender
so that, e.g., the Custody folder always defaults to custody@firm.com
in the Outreach modal -- the user can still override per-send.

Soft reference (no FK to account.id) because Better Auth's unlink
endpoint doesn't expose a hook for cleaning up referencing rows, and
we'd rather the modal handle the "default sender no longer exists"
case via its three-tier fallback than have Alembic fight with Better
Auth over cascade semantics.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260522_0058"
down_revision: str | None = "20260522_0057"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "vault_folder",
        sa.Column(
            "default_sender_account_id",
            sa.String(length=255),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("vault_folder", "default_sender_account_id")
