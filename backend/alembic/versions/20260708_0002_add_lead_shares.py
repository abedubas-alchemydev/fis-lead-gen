"""Add lead_share + lead_share_item (password-protected public share links).

Revision ID: 20260708_0002
Revises: 20260708_0001
Create Date: 2026-07-08

DOX Share: an admin snapshots a curated favorite list into a share, hands a
client a ``/share/{token}`` link plus a one-time password, and the client
browses the shared leads without a DOX account. Two tables back it:

- ``lead_share`` — one row per share link. ``token`` is the unguessable URL
  component (``secrets.token_urlsafe(32)``, stored plaintext so the public
  endpoint can look it up directly — it is a locator, not a secret on its
  own; the password gates the content). ``password_hash`` is a
  self-describing scrypt string; the plaintext is returned exactly once at
  create/rotate time and never stored. ``password_generation`` increments on
  every rotation so previously minted unlock cookies (which embed the
  generation) die instantly. ``failed_unlocks`` +
  ``failed_unlock_window_started_at`` back a fixed-window brute-force
  throttle on the public unlock endpoint. ``created_by`` /
  ``source_favorite_list_id`` are provenance only, so both are ``ON DELETE
  SET NULL`` — a share must outlive its creator's account and its source
  list (the share is a *snapshot*, not a live view).

- ``lead_share_item`` — the snapshot membership, polymorphic across the
  three shareable entity kinds. Constraints:

  * ``ck_lead_share_item_exactly_one_target`` — 3-way XOR: exactly one of
    ``broker_dealer_id`` / ``advisor_id`` / ``bank_id`` is non-null (same
    pattern as ``ck_favorite_list_item_exactly_one_target``).
  * ``uq_lead_share_item_share_position`` — ``position`` (the curated 1..N
    display order) is unique per share.
  * ``uq_lead_share_item_share_bd`` / ``_share_advisor`` / ``_share_bank`` —
    partial unique indexes so the same entity can't appear twice in one
    share (partial, because a normal unique over a nullable column would
    never fire — NULLs are distinct).

  Entity FKs are ``ON DELETE CASCADE``: when a firm is deleted from DOX the
  snapshot row simply disappears from the share (positions keep their gaps;
  they order, they don't number). ``share_id`` is also CASCADE — items never
  outlive their share.

DOWNGRADE IS SAFE (unlike the 20260703_0003 outreach hazard): shares are
derived, regenerable data — every share is a snapshot of a favorite list an
admin can re-create in seconds, and no compliance/audit trail lives here
(share lifecycle events are recorded in ``audit_log``, which this migration
does not touch). Dropping both tables loses only the links themselves.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260708_0002"
down_revision: str | None = "20260708_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lead_share",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column(
            "password_generation",
            sa.Integer(),
            server_default="1",
            nullable=False,
        ),
        sa.Column("password_rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("source_favorite_list_id", sa.BigInteger(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("view_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_viewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_unlocks", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "failed_unlock_window_started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["source_favorite_list_id"], ["favorite_list.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token", name="uq_lead_share_token"),
    )
    op.create_index(
        "ix_lead_share_created_by", "lead_share", ["created_by"], unique=False
    )

    op.create_table(
        "lead_share_item",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("share_id", sa.BigInteger(), nullable=False),
        sa.Column("broker_dealer_id", sa.Integer(), nullable=True),
        sa.Column("advisor_id", sa.Integer(), nullable=True),
        sa.Column("bank_id", sa.Integer(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["share_id"], ["lead_share.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["broker_dealer_id"], ["broker_dealers.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["advisor_id"], ["investment_advisors.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["bank_id"], ["banks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "(CASE WHEN broker_dealer_id IS NOT NULL THEN 1 ELSE 0 END "
            "+ CASE WHEN advisor_id IS NOT NULL THEN 1 ELSE 0 END "
            "+ CASE WHEN bank_id IS NOT NULL THEN 1 ELSE 0 END) = 1",
            name="ck_lead_share_item_exactly_one_target",
        ),
        sa.UniqueConstraint(
            "share_id", "position", name="uq_lead_share_item_share_position"
        ),
    )
    op.create_index(
        "ix_lead_share_item_share_id", "lead_share_item", ["share_id"], unique=False
    )
    op.create_index(
        "ix_lead_share_item_broker_dealer_id",
        "lead_share_item",
        ["broker_dealer_id"],
        unique=False,
    )
    op.create_index(
        "ix_lead_share_item_advisor_id",
        "lead_share_item",
        ["advisor_id"],
        unique=False,
    )
    op.create_index(
        "ix_lead_share_item_bank_id", "lead_share_item", ["bank_id"], unique=False
    )
    # Per-entity dedupe: partial unique indexes (a plain composite unique over
    # a nullable column never fires — NULLs compare distinct).
    op.create_index(
        "uq_lead_share_item_share_bd",
        "lead_share_item",
        ["share_id", "broker_dealer_id"],
        unique=True,
        postgresql_where=sa.text("broker_dealer_id IS NOT NULL"),
    )
    op.create_index(
        "uq_lead_share_item_share_advisor",
        "lead_share_item",
        ["share_id", "advisor_id"],
        unique=True,
        postgresql_where=sa.text("advisor_id IS NOT NULL"),
    )
    op.create_index(
        "uq_lead_share_item_share_bank",
        "lead_share_item",
        ["share_id", "bank_id"],
        unique=True,
        postgresql_where=sa.text("bank_id IS NOT NULL"),
    )


def downgrade() -> None:
    # Safe by design: shares are regenerable snapshots of favorite lists, and
    # their lifecycle audit trail lives in audit_log (untouched here).
    op.drop_index("uq_lead_share_item_share_bank", table_name="lead_share_item")
    op.drop_index("uq_lead_share_item_share_advisor", table_name="lead_share_item")
    op.drop_index("uq_lead_share_item_share_bd", table_name="lead_share_item")
    op.drop_index("ix_lead_share_item_bank_id", table_name="lead_share_item")
    op.drop_index("ix_lead_share_item_advisor_id", table_name="lead_share_item")
    op.drop_index("ix_lead_share_item_broker_dealer_id", table_name="lead_share_item")
    op.drop_index("ix_lead_share_item_share_id", table_name="lead_share_item")
    op.drop_table("lead_share_item")

    op.drop_index("ix_lead_share_created_by", table_name="lead_share")
    op.drop_table("lead_share")
