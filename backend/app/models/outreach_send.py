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
    broker_dealer_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("broker_dealers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    contact_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("executive_contacts.id", ondelete="CASCADE"),
        nullable=False,
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
    gmail_message_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    # ``sent`` on Gmail API 2xx, ``failed`` otherwise. Kept as a free
    # string (not a Postgres enum) to keep schema changes cheap if we
    # later split ``failed`` into ``failed_scope`` / ``failed_api`` /
    # ``failed_recipient``.
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
