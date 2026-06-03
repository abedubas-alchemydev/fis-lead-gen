"""add to_emails / cc_emails / bcc_emails to outreach_sends

The Create Outreach composer can now send a single email to multiple
recipients with CC and BCC (like a normal mail client). One
``outreach_sends`` row is written per compose-send; these three columns
capture the full recipient set as comma-joined address lists so the audit
record reflects exactly who received the message:

  - ``to_emails``  : full visible To list (populated only when > 1 To;
                     single-To rows keep using ``recipient_email``).
  - ``cc_emails``  : visible Cc list.
  - ``bcc_emails`` : hidden Bcc list (recorded for audit/compliance even
                     though recipients never see it).

All nullable; legacy rows and single-recipient / contact-based sends
leave them NULL.

Revision ID: 20260603_0003
Revises: 20260603_0002
Create Date: 2026-06-03
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "20260603_0003"
down_revision: str | None = "20260603_0002"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "outreach_sends",
        sa.Column("to_emails", sa.Text(), nullable=True),
    )
    op.add_column(
        "outreach_sends",
        sa.Column("cc_emails", sa.Text(), nullable=True),
    )
    op.add_column(
        "outreach_sends",
        sa.Column("bcc_emails", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("outreach_sends", "bcc_emails")
    op.drop_column("outreach_sends", "cc_emails")
    op.drop_column("outreach_sends", "to_emails")
