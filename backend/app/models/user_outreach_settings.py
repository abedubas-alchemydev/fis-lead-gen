from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class UserOutreachSettings(Base):
    """Per-user outreach preferences. One row per user, keyed by ``user_id``.

    Currently holds just ``signature`` — the footer block the compose
    surfaces (per-contact Outreach modal + the ``/outreach`` Create tab)
    prefill beneath the email body and append to the message on send.
    Kept as a dedicated app-owned table rather than a column on the
    Better-Auth-managed ``user`` table so outreach concerns stay out of
    the auth schema; FK cascade mirrors ``user_visit``.
    """

    __tablename__ = "user_outreach_settings"

    user_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("user.id", ondelete="CASCADE"),
        primary_key=True,
    )
    signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
