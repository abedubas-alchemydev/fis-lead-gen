from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class InvestmentAdvisor(Base):
    """One row per SEC-registered Investment Adviser (Form ADV filer).

    Sibling of ``BrokerDealer``. Form ADV diverges sharply from Form BD —
    Item 5 (advisory activities, client mix, AUM) and Schedules A/B
    (direct + indirect owners) replace the BD-side ``types_of_business``,
    ``net_capital``, ``clearing_partner``, etc. The two tables share a few
    surface columns (cik, crd_number, name, state, website) but the rest
    are advisor-specific.

    The ``files_13f`` boolean is denormalized on the row so the master-list
    endpoint can apply the hard 13F scope (``WHERE files_13f = TRUE``)
    without a join. Refreshed by the daily 13F monitor pipeline.
    """

    __tablename__ = "investment_advisors"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # CIK is unique-but-nullable: some IAPD-only RIAs have no EDGAR CIK
    # (filed Form ADV but never anything that requires a CIK). The unique
    # index is partial (WHERE cik IS NOT NULL) — see migration 0030.
    cik: Mapped[str | None] = mapped_column(String(32), nullable=True)
    crd_number: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    sec_file_number: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    name: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    legal_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    state: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    registration_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    formation_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_filing_date: Mapped[date | None] = mapped_column(Date, index=True, nullable=True)
    filings_index_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    matched_source: Mapped[str] = mapped_column(String(16), default="iapd", nullable=False)
    status: Mapped[str] = mapped_column(String(64), default="pending", index=True, nullable=False)
    website: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # 'iapd' | 'apollo' | 'serper' | 'serpapi' | None. Mirrors the BD
    # ``website_source`` column shape so the FE can disclose provenance
    # without a separate provider lookup.
    website_source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Form ADV Item 5.F — regulatory AUM and its split. Numeric(20,2) gives
    # 4 extra digits of precision past BD's Numeric(18,2) since RIAs can
    # report tens of trillions in regulatory AUM.
    regulatory_aum: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    discretionary_aum: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    non_discretionary_aum: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    total_clients: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # ``none_as_null=True`` — same guardrail BD uses to keep Python None
    # writes from landing as the JSONB scalar 'null', which would crash
    # set-returning aggregators (jsonb_array_elements_text) downstream.
    advisory_activities: Mapped[list | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    client_types: Mapped[list | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    client_counts: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    direct_owners: Mapped[list | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    indirect_owners: Mapped[list | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    executive_officers: Mapped[list | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    firm_operations_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 13F denormalized flags. ``files_13f`` is the hard filter scope on
    # the master-list endpoint; updated by the daily 13F monitor pipeline.
    files_13f: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    latest_13f_filing_date: Mapped[date | None] = mapped_column(Date, nullable=True)
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
