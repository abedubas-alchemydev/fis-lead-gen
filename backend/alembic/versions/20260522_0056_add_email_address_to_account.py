"""Add account.email_address so each linked OAuth account's email is queryable.

Revision ID: 20260522_0056
Revises: 20260521_0055
Create Date: 2026-05-22

Multi-sender outreach lets a user link multiple Google/Outlook/Yahoo
accounts and pick which one to send from. Today the ``account`` table
stores OAuth tokens but not the email each account represents, so the
send dispatcher in ``outreach.py`` fell back to ``current_user.email`` --
which is the *login* email, not the linked account's email. That's
broken any time a user links an OAuth identity that isn't their login.

The column is nullable because Better Auth's account row is created
before we can run the post-link hook that decodes the ``id_token`` and
writes the address; backfill races with linkSocial otherwise. New rows
are populated by a ``databaseHooks.account.create.after`` in
``frontend/lib/auth.ts``; legacy rows lazy-backfill on first send.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260522_0056"
down_revision: str | None = "20260521_0055"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "account",
        sa.Column("email_address", sa.String(length=320), nullable=True),
    )
    op.create_index(
        "ix_account_email_address",
        "account",
        ["email_address"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_account_email_address", table_name="account")
    op.drop_column("account", "email_address")
