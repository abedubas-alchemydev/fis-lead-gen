"""Add outreach_sends.sender_account_id + sender_email for multi-sender audits.

Revision ID: 20260522_0057
Revises: 20260522_0056
Create Date: 2026-05-22

Per-send sender selection (see 0056) means a single user can route
outreach through any of their linked accounts. The audit row must
record the actual sender so admin sorting / "last contacted from X"
works post-feature. ``sender_account_id`` is a soft reference to
``account.id`` (no FK -- Better Auth's unlink path doesn't cascade
nicely and we'd rather keep orphan rows readable than lose history).

``sender_email`` is the point-in-time email at send time; backfilled
from ``user.email`` so the admin Sent-Outreach view stays populated
for legacy rows. ``sender_account_id`` stays NULL on legacy rows --
joining ``(user_id, provider)`` to an account is ambiguous once a user
has linked a second account of the same provider.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260522_0057"
down_revision: str | None = "20260522_0056"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "outreach_sends",
        sa.Column("sender_account_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "outreach_sends",
        sa.Column("sender_email", sa.String(length=320), nullable=True),
    )
    op.create_index(
        "ix_outreach_sends_sender_account_id",
        "outreach_sends",
        ["sender_account_id"],
        unique=False,
    )
    # Backfill sender_email from the user's login email so legacy
    # audit rows still render a sender in the admin view. This mirrors
    # the pre-feature behaviour where every send was "from" the login
    # email regardless of which OAuth account actually transmitted it.
    op.execute(
        """
        UPDATE outreach_sends os
        SET sender_email = u.email
        FROM "user" u
        WHERE os.user_id = u.id AND os.sender_email IS NULL
        """
    )


def downgrade() -> None:
    op.drop_index(
        "ix_outreach_sends_sender_account_id", table_name="outreach_sends"
    )
    op.drop_column("outreach_sends", "sender_email")
    op.drop_column("outreach_sends", "sender_account_id")
