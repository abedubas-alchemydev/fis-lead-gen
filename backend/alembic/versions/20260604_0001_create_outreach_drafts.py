"""create outreach_drafts (saved-but-unsent composer drafts)

Backs the Outreach "Drafts" tab. A draft is the mutable counterpart to the
immutable ``outreach_sends`` audit row: the Create-Outreach composer can save
an in-progress To/Cc/Bcc message here (surviving a reload), and Doxie-authored
emails land here for human review before sending. Private to the owner and
hard-deleted on send/discard — no audit-retention requirement (the send itself
is recorded in ``outreach_sends``).

Recipient columns mirror the ``POST /outreach/compose-send`` payload so a draft
round-trips losslessly back into the composer.

Revision ID: 20260604_0001
Revises: 20260603_0003
Create Date: 2026-06-04
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260604_0001"
down_revision: str | None = "20260603_0003"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "outreach_drafts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        # Subject/body nullable so an in-progress draft saves before anything
        # is typed. 998 mirrors the send-path subject cap.
        sa.Column("subject", sa.String(length=998), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        # Recipient buckets mirror the compose-send payload: to_recipients is a
        # JSON list of {"email", "name"}; cc/bcc are lists of address strings.
        sa.Column("to_recipients", postgresql.JSONB(), nullable=True),
        sa.Column("cc_emails", postgresql.JSONB(), nullable=True),
        sa.Column("bcc_emails", postgresql.JSONB(), nullable=True),
        sa.Column("folder_id", sa.BigInteger(), nullable=True),
        # Soft reference to account.id (no FK; mirrors outreach_sends).
        sa.Column("sender_account_id", sa.String(length=255), nullable=True),
        sa.Column(
            "source",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'manual'"),
        ),
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
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        # Folder deletion nulls the reference rather than dropping the draft.
        sa.ForeignKeyConstraint(
            ["folder_id"], ["vault_folder.id"], ondelete="SET NULL"
        ),
    )
    # Drafts are always read scoped to the owner (and ordered by updated_at);
    # name matches the model's ``index=True`` so create_all + alembic agree.
    op.create_index(
        "ix_outreach_drafts_user_id",
        "outreach_drafts",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_outreach_drafts_user_id", table_name="outreach_drafts")
    op.drop_table("outreach_drafts")
