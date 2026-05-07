from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AdvisorFiling(Base):
    """Sibling of ``FilingAlert`` for the Investment Advisor list.

    Tracks Form ADV / Form ADV-W / Form 13F-HR amendments per advisor.
    Same dedupe-key + priority + filed_at shape as ``filing_alerts`` so
    the FE alert renderer can extend with a single conditional branch
    rather than a parallel UI.
    """

    __tablename__ = "advisor_filings"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    advisor_id: Mapped[int] = mapped_column(
        ForeignKey("investment_advisors.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    dedupe_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    form_type: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    priority: Mapped[str] = mapped_column(String(16), nullable=False)
    filed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    source_filing_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
