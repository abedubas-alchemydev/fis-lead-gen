"""Add outreach_sends.provider so multi-provider sends record which transport ran.

Revision ID: 20260519_0049
Revises: 20260518_0047
Create Date: 2026-05-19

PR C adds Outlook (Microsoft Graph) and Yahoo (SMTP+XOAUTH2) as
outreach transports alongside the existing Gmail path. The audit row
must record which provider actually ran the send so admins can sort the
Sent Outreach view by provider and so post-incident debugging can map
failures to the right transport.

``gmail_message_id`` stays as-is (not renamed) to keep the diff small
and keep existing API consumers working. Microsoft Graph's
``users.sendMail`` returns 202 with no message id, and Yahoo SMTP
returns no id either, so non-Gmail rows store a synthetic
``<provider>-<isoformat>`` placeholder in that column. The name lies
slightly; cleanup can rename later.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260519_0049"
down_revision: str | None = "20260518_0047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # NOT NULL with DEFAULT 'google' so existing rows backfill correctly
    # without a separate UPDATE pass. New rows are written with the
    # explicit provider per the OutreachSend kwargs in the endpoint.
    op.add_column(
        "outreach_sends",
        sa.Column(
            "provider",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'google'"),
        ),
    )
    # Drop the default after the backfill so application code is the
    # only writer (defensive — keeps test fixtures honest about which
    # provider they're exercising).
    op.alter_column("outreach_sends", "provider", server_default=None)


def downgrade() -> None:
    op.drop_column("outreach_sends", "provider")
