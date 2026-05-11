"""add outreach_sends table

Per-attempt record of an outreach email transmitted via the logged-in
user's Gmail account. Written by ``POST /api/v1/outreach/send`` on both
success and failure so admin audits can answer "what was sent to whom,
and when" without scanning ``audit_log`` JSON.

Revision ID: 20260511_0038
Revises: 20260511_0037
Create Date: 2026-05-11

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "20260511_0038"
down_revision: str | None = "20260511_0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "outreach_sends",
        sa.Column(
            "id", sa.BigInteger(), primary_key=True, autoincrement=True
        ),
        sa.Column(
            "user_id",
            sa.String(length=255),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "broker_dealer_id",
            sa.Integer(),
            sa.ForeignKey("broker_dealers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "contact_id",
            sa.Integer(),
            sa.ForeignKey("executive_contacts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "folder_id",
            sa.BigInteger(),
            sa.ForeignKey("vault_folder.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("subject", sa.String(length=998), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("gmail_message_id", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "sent_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_outreach_sends_user_id", "outreach_sends", ["user_id"]
    )
    op.create_index(
        "ix_outreach_sends_broker_dealer_id",
        "outreach_sends",
        ["broker_dealer_id"],
    )
    op.create_index(
        "ix_outreach_sends_contact_id", "outreach_sends", ["contact_id"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_outreach_sends_contact_id", table_name="outreach_sends"
    )
    op.drop_index(
        "ix_outreach_sends_broker_dealer_id", table_name="outreach_sends"
    )
    op.drop_index(
        "ix_outreach_sends_user_id", table_name="outreach_sends"
    )
    op.drop_table("outreach_sends")
