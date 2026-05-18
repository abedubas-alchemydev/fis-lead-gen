from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class InstitutionalInvestor(Base):
    """One row per 13F-HR filer (institutional investment manager).

    Sibling of ``InvestmentAdvisor``. Many 13F filers ARE registered
    RIAs (same CIK appears in IAPD); the ``advisor_id`` FK is populated
    for that overlap subset, and rows with ``advisor_id IS NULL`` are
    pure 13F filers (private funds, pensions, insurance companies) that
    do not file Form ADV.

    Default sort scope is ``total_aum DESC NULLS LAST`` (matches the
    advisor list's AUM-ranked default on ``regulatory_aum``). The
    ``total_aum`` value is parsed from the latest 13F-HR
    ``<tableValueTotal>`` so pure-13F filers have a number even when
    no IAPD ``regulatory_aum`` exists.
    """

    __tablename__ = "institutional_investors"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # CIK is unique-but-nullable: a manually curated investor (e.g.
    # unregistered family office) may have no EDGAR CIK assigned yet.
    # The unique index is partial (WHERE cik IS NOT NULL) -- see
    # migration 0044.
    cik: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Cross-link to the advisor row when the same CIK also appears in
    # IAPD. ON DELETE SET NULL so removing an advisor doesn't cascade
    # the investor record (the 13F history is independent).
    advisor_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("investment_advisors.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    legal_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    state: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    website: Mapped[str | None] = mapped_column(String(512), nullable=True)
    website_source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    latest_13f_filing_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    total_aum: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    holdings_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    filings_index_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    matched_source: Mapped[str] = mapped_column(
        String(16), default="edgar_13f", nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(64), default="pending", index=True, nullable=False
    )
    last_enrich_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
