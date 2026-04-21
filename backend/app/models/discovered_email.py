from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.email_verification import EmailVerification
    from app.models.extraction_run import ExtractionRun


class DiscoverySource(StrEnum):
    hunter = "hunter"
    apollo = "apollo"
    snov = "snov"
    site_crawler = "site_crawler"
    theharvester = "theharvester"


class DiscoveredEmail(Base):
    """A single email discovered during a scan, attributed to its source provider."""

    __tablename__ = "discovered_email"
    __table_args__ = (UniqueConstraint("run_id", "email", name="uq_discovered_email_run_email"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("extraction_run.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    attribution: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    run: Mapped[ExtractionRun] = relationship(back_populates="discovered_emails")
    verifications: Mapped[list[EmailVerification]] = relationship(
        back_populates="discovered_email", cascade="all, delete-orphan", lazy="selectin"
    )
