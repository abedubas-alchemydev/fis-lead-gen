from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ClearingArrangement(Base):
    __tablename__ = "clearing_arrangements"
    __table_args__ = (UniqueConstraint("bd_id", "filing_year", name="uq_clearing_arrangements_bd_year"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    bd_id: Mapped[int] = mapped_column(ForeignKey("broker_dealers.id", ondelete="CASCADE"), index=True)
    pipeline_run_id: Mapped[int | None] = mapped_column(ForeignKey("pipeline_runs.id", ondelete="SET NULL"), nullable=True)
    filing_year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    report_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    source_filing_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_pdf_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    local_document_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    clearing_partner: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    normalized_partner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    clearing_type: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    agreement_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    extraction_confidence: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    extraction_status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False, index=True)
    extraction_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_competitor: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
