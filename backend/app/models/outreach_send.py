"""Per-attempt record of an outreach email transmitted via the user's Gmail.

Written by ``POST /api/v1/outreach/send`` on both success and failure so
admin audits, future "last contacted on ..." hints on the contact row,
and operator debugging of OAuth/Gmail failures all have a typed table to
query rather than scanning ``audit_log`` JSON.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class OutreachSend(Base):
    __tablename__ = "outreach_sends"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    user_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Polymorphic firm FKs: at most one of broker_dealer_id, advisor_id,
    # institutional_investor_id is non-null per row. Enforced at the DB
    # via ``ck_outreach_sends_at_most_one_firm`` (relaxed from = 1 to
    # <= 1 in migration 0063 so adhoc rows can set zero firm FKs).
    broker_dealer_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("broker_dealers.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    advisor_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("investment_advisors.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    institutional_investor_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("institutional_investors.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    # Polymorphic contact FKs: at most one of contact_id (executive),
    # advisor_contact_id, investor_contact_id is non-null per row.
    # Enforced via ``ck_outreach_sends_at_most_one_contact`` (relaxed
    # from = 1 to <= 1 in migration 0063 so adhoc rows can set zero
    # contact FKs and carry ``recipient_email`` instead).
    contact_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("executive_contacts.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    advisor_contact_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("advisor_contacts.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    investor_contact_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("investor_contacts.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    # Folder is nullable so a later folder deletion doesn't lose the
    # send history. ``ON DELETE SET NULL`` matches that intent at the
    # DB layer.
    folder_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("vault_folder.id", ondelete="SET NULL"),
        nullable=True,
    )
    subject: Mapped[str] = mapped_column(String(998), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    # Which transport actually ran the send: "google" (Gmail API),
    # "microsoft" (MS Graph users.sendMail), or "yahoo" (SMTP+XOAUTH2
    # via aiosmtplib). Backfilled to "google" on the migration 0049
    # upgrade. New rows MUST set this explicitly.
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    # Soft reference to ``account.id`` (no FK; see migration 0057).
    # NULL on legacy pre-multi-sender rows; new rows always populate.
    sender_account_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )
    # Point-in-time sender address. For legacy rows this was backfilled
    # from ``user.email`` so admin sort/filter on sender still works.
    sender_email: Mapped[str | None] = mapped_column(
        String(320), nullable=True
    )
    # Provider-side message id. For Gmail this is the real
    # users.messages.send response id; for Microsoft/Yahoo it's a
    # synthetic "{provider}-{iso8601}" placeholder (those transports
    # don't return a server-side id we can rely on). Column name is
    # legacy from the Gmail-only era — cleanup can rename later.
    gmail_message_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    # ``sent`` on Gmail API 2xx, ``failed`` otherwise. Kept as a free
    # string (not a Postgres enum) to keep schema changes cheap if we
    # later split ``failed`` into ``failed_scope`` / ``failed_api`` /
    # ``failed_recipient``.
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Adhoc recipient fields. Populated only when all six contact/firm
    # FKs are NULL (the user sent to a free-form email with no firm
    # record in our system). For contact-based rows these stay NULL and
    # the read path pulls the address from the joined contact row.
    # Added in migration 0063.
    recipient_email: Mapped[str | None] = mapped_column(
        String(320), nullable=True
    )
    recipient_name: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    # Soft-delete tombstone. NULL = live; a timestamp means the user
    # deleted the row from /outreach/sent. The list + detail read paths
    # filter ``deleted_at IS NULL`` so deleted rows vanish from the UI,
    # but the row stays on disk so the send audit trail isn't lost.
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
