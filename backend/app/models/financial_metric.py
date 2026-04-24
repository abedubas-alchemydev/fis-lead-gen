from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.services.extraction_status import STATUS_PENDING


class FinancialMetric(Base):
    __tablename__ = "financial_metrics"
    __table_args__ = (
        UniqueConstraint("bd_id", "report_date", name="uq_financial_metrics_bd_report_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    bd_id: Mapped[int] = mapped_column(ForeignKey("broker_dealers.id", ondelete="CASCADE"), index=True)
    report_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    net_capital: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    excess_net_capital: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    total_assets: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    required_min_capital: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    source_filing_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Review-queue tag. Mirrors ``clearing_arrangements.extraction_status``:
    # same type, same default, same index. See ``app.services.extraction_status``
    # for the allowed values and the classify helper.
    extraction_status: Mapped[str] = mapped_column(
        String(32),
        default=STATUS_PENDING,
        server_default=STATUS_PENDING,
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
