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
    three_year_cagr: Mapped[float | None] = mapped_column(Numeric(8, 2), nullable=True)
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
    # 'finra' (Form BD Web Address) | 'apollo' (organizations/search fallback)
    # | None when the firm has no website on either source. NULL is the legacy
    # default for rows that predate the firm-website backfill.
    website_source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # ``none_as_null=True`` makes Python ``None`` writes land as SQL NULL
    # instead of the JSONB scalar ``'null'`` (the default). Without this,
    # ``WHERE col IS NOT NULL`` lets JSONB-null rows through to set-returning
    # functions like ``jsonb_array_elements_text``, which then crash with
    # "cannot extract elements from a scalar". Sibling-list columns get the
    # same treatment for consistency, even though ``list_types_of_business``
    # is the only aggregator today.
    types_of_business: Mapped[list | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    types_of_business_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    types_of_business_other: Mapped[str | None] = mapped_column(Text, nullable=True)
    direct_owners: Mapped[list | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    executive_officers: Mapped[list | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    firm_operations_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    clearing_classification: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    clearing_raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_niche_restricted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    formation_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    total_assets_yoy: Mapped[float | None] = mapped_column(Numeric(8, 2), nullable=True)
    # FINRA "Doing Business As" / alternate trade names. Parsed from
    # ``firm_other_names`` in the BrokerCheck firm-search payload. Used by
    # the website resolver so a firm registered as ``303 ALTERNATIVES,
    # LLC`` but operating at ``303capitalmarkets.com`` (DBA "303Capital
    # Markets") can still anchor a candidate URL on the trade-name token.
    dba_names: Mapped[list | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    # LLM-generated alias list — parent-company brand expansions, acronym
    # expansions, and common stylized variants that FINRA's ``firm_other_names``
    # rarely captures. Augments the resolver's token pool so subsidiary firms
    # whose web presence lives on a parent domain (e.g., ``BOFA SECURITIES,
    # INC.`` operating at ``bankofamerica.com``) can anchor a candidate via
    # an LLM-supplied alias like "Bank of America Securities". Kept separate
    # from ``dba_names`` to preserve provenance — FINRA-sourced trade names
    # stay in their authoritative column.
    resolver_aliases: Mapped[list | None] = mapped_column(JSONB(none_as_null=True), nullable=True)

    status: Mapped[str] = mapped_column(String(64), default="pending", nullable=False)
    # Stamped on every Apollo /enrich attempt that the API "owns" (success or
    # no-result). Used by ExecutiveContactService.enrich_contacts as a
    # server-side cooldown so empty-result firms don't re-fire Apollo on
    # every detail-page visit. NULL means "never attempted".
    last_enrich_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    # Stamped after every bulk gap-fill pass over this BD (regardless of
    # which sub-pipelines fired or what the result was). The bulk runner
    # in scripts/gap_fill_broker_dealers.py uses this as a 30-day cooldown
    # so a firm whose source genuinely has no value isn't re-queried on
    # every run. NULL means "never attempted by gap-fill".
    last_gap_fill_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    # Stamped by the clearing-agency-membership importer for every firm it
    # evaluates against the OCC/DTCC directories. NULL means "never checked"
    # (FE renders nothing); non-NULL with no active membership row means
    # "evaluated, not a member" (FE renders "Not a member"). This sentinel is
    # what distinguishes a confirmed non-member from an unprocessed firm.
    clearing_membership_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
