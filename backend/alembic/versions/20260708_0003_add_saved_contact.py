"""Add saved_contact (per-user "Save Contact" snapshot store).

Revision ID: 20260708_0003
Revises: 20260708_0002
Create Date: 2026-07-08

Backs the "Save Contact" feature: a user pins an individual contact discovered
by the email extractor and revisits it later. The contact's fields are
SNAPSHOTTED onto the row (name/title/email/company/phone/linkedin_url) so the
save survives deletion of the originating scan.

``contact_id`` is a bare integer reference with NO foreign key on purpose:

* the source row (``discovered_email``) cascade-deletes with its
  ``extraction_run``; an FK here would either vanish the save or block the
  prune, so the snapshot + FK-less reference keeps saves self-contained; and
* ``source`` is a discriminator so the store can pin other origins later
  (polymorphic by ``(source, contact_id)``) without a per-source FK column.

Ownership + PK style mirror ``favorite_list`` (migration 0019): ``user_id``
``String(255)`` FK -> ``user.id`` ``ON DELETE CASCADE`` + indexed, and a
``BigInteger`` surrogate PK (the schema uses no UUIDs). The
``(user_id, source, contact_id)`` unique constraint backs the idempotent
``INSERT ... ON CONFLICT DO NOTHING`` save path.

Additive; no data backfill.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "20260708_0003"
down_revision: str | None = "20260708_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "saved_contact",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        # Bare reference to the source row's id -- intentionally NOT a foreign
        # key so the snapshot outlives the source's cascade-delete.
        sa.Column("contact_id", sa.Integer(), nullable=False),
        # Snapshot columns -- widths match their discovered_email sources so a
        # save never truncates the value it copied.
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("company", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=64), nullable=True),
        sa.Column("linkedin_url", sa.String(length=512), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "source",
            "contact_id",
            name="uq_saved_contact_user_source_contact",
        ),
    )
    op.create_index(
        "ix_saved_contact_user_id", "saved_contact", ["user_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_saved_contact_user_id", table_name="saved_contact")
    op.drop_table("saved_contact")
