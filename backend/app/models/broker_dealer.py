from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class BrokerDealer(Base):
    __tablename__ = "broker_dealers"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    cik: Mapped[str | None] = mapped_column(String(32), unique=True, index=True, nullable=True)
    crd_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    sec_file_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    state: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    branch_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    business_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    registration_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    matched_source: Mapped[str] = mapped_column(String(16), default="edgar", nullable=False)
    last_filing_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    filings_index_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    required_min_capital: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    latest_net_capital: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    latest_excess_net_capital: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    latest_total_assets: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    yoy_growth: Mapped[float | None] = mapped_column(Numeric(8, 2), nullable=True)
    health_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_deficient: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    latest_deficiency_filed_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    lead_score: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True, index=True)
    lead_priority: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    current_clearing_partner: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    current_clearing_type: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    current_clearing_is_competitor: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    current_clearing_source_filing_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_clearing_extraction_confidence: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    last_audit_report_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # ── Tri-Stream fields (Revision 1) ──
    website: Mapped[str | None] = mapped_column(String(512), nullable=True)
    types_of_business: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    types_of_business_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    types_of_business_other: Mapped[str | None] = mapped_column(Text, nullable=True)
    direct_owners: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    executive_officers: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    firm_operations_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    clearing_classification: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    clearing_raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_niche_restricted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    formation_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    total_assets_yoy: Mapped[float | None] = mapped_column(Numeric(8, 2), nullable=True)

    status: Mapped[str] = mapped_column(String(64), default="pending", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
