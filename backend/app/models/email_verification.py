from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.discovered_email import DiscoveredEmail


class SmtpStatus(StrEnum):
    not_checked = "not_checked"
    deliverable = "deliverable"
    undeliverable = "undeliverable"
    inconclusive = "inconclusive"
    blocked = "blocked"


class EmailVerification(Base):
    """Result of validating one DiscoveredEmail.

    syntax_valid + mx_record_present run inline on every discovery.
    smtp_status is set only when the user explicitly requests SMTP verification
    (a follow-up prompt wires that endpoint).
    """

    __tablename__ = "email_verification"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    discovered_email_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("discovered_email.id", ondelete="CASCADE"), nullable=False, index=True
    )
    syntax_valid: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    mx_record_present: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    smtp_status: Mapped[str] = mapped_column(String(32), default=SmtpStatus.not_checked.value, nullable=False)
    smtp_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    discovered_email: Mapped[DiscoveredEmail] = relationship(back_populates="verifications")
