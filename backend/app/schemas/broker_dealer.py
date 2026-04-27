from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from app.schemas.alerts import AlertListItem
from app.schemas.pipeline import ClearingArrangementItem

class BrokerDealerListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    cik: str | None
    crd_number: str | None
    sec_file_number: str | None
    name: str
    city: str | None
    state: str | None
    status: str
    branch_count: int | None
    business_type: str | None
    registration_date: date | None
    matched_source: str
    last_filing_date: date | None
    filings_index_url: str | None
    required_min_capital: float | None
    latest_net_capital: float | None
    latest_excess_net_capital: float | None
    latest_total_assets: float | None
    yoy_growth: float | None
    health_status: str | None
    is_deficient: bool
    latest_deficiency_filed_at: date | None
    lead_score: float | None
    lead_priority: str | None
    current_clearing_partner: str | None
    current_clearing_type: str | None
    current_clearing_is_competitor: bool
    current_clearing_source_filing_url: str | None
    current_clearing_extraction_confidence: float | None
    last_audit_report_date: date | None
    # ── Tri-Stream fields (Revision 1) ──
    website: str | None = None
    types_of_business: list[str] | None = None
    direct_owners: list[dict[str, str]] | None = None
    executive_officers: list[dict[str, str]] | None = None
    firm_operations_text: str | None = None
    clearing_classification: str | None = None
    clearing_raw_text: str | None = None
    is_niche_restricted: bool = False
    formation_date: date | None = None
    total_assets_yoy: float | None = None
    types_of_business_total: int | None = None
    types_of_business_other: str | None = None
    # Stamped server-side on every Apollo /enrich attempt the API "owns"
    # (success or no-result). NULL = never attempted. Surfaced here so a
    # follow-up FE PR can use it to gate the detail-page enrich call.
    last_enrich_attempt_at: datetime | None = None
    created_at: datetime


class BrokerDealerDetail(BrokerDealerListItem):
    pass


class BrokerDealerListMeta(BaseModel):
    page: int
    limit: int
    total: int
    total_pages: int
    # Populated from the most recent `pipeline_run` row so every authenticated
    # user (not just admins) can render a "Pipeline refreshed Xm ago" stamp
    # next to the master-list tabs. Null when no runs have landed yet.
    pipeline_refreshed_at: datetime | None = None


class BrokerDealerListResponse(BaseModel):
    items: list[BrokerDealerListItem]
    meta: BrokerDealerListMeta


class FinancialMetricItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    bd_id: int
    report_date: date
    net_capital: float
    excess_net_capital: float | None
    total_assets: float | None
    required_min_capital: float | None
    source_filing_url: str | None
    # Read-only tag. Always set server-side by the financial pipeline
    # (app.services.extraction_status). Clients must not POST or PATCH
    # this field; the backend has no write path for it.
    extraction_status: str = "parsed"
    created_at: datetime


class FinancialMetricsResponse(BaseModel):
    items: list[FinancialMetricItem]


class FilingHistoryItem(BaseModel):
    label: str
    filed_at: datetime
    summary: str
    source_filing_url: str | None
    priority: str | None


class ExecutiveContactItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    bd_id: int
    name: str
    title: str
    email: str | None
    phone: str | None
    linkedin_url: str | None
    source: str
    discovery_source: str | None = None
    discovery_confidence: float | None = None
    enriched_at: datetime


class RegistrationComplianceSummary(BaseModel):
    registration_status: str
    registration_date: date | None
    sec_file_number: str | None
    crd_number: str | None
    branch_count: int | None
    business_type: str | None
    filings_index_url: str | None


class DeficiencyStatusSummary(BaseModel):
    is_deficient: bool
    latest_deficiency_filed_at: date | None
    message: str


class IntroducingArrangementItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    bd_id: int
    statement: str | None = None
    business_name: str | None = None
    effective_date: date | None = None
    description: str | None = None


class IndustryArrangementItem(BaseModel):
    """FINRA 'Firm Operations → Industry Arrangements' row.

    One of three kinds (books_records / accounts_funds / customer_accounts),
    each a yes/no plus optional partner block. Together they say whether the
    firm is truly self-clearing vs using a third party.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    bd_id: int
    kind: str
    has_arrangement: bool
    partner_name: str | None = None
    partner_crd: str | None = None
    partner_address: str | None = None
    effective_date: date | None = None
    description: str | None = None


class FocusCeoExtractionResponse(BaseModel):
    """Response from the on-demand FOCUS Report CEO extraction."""
    ceo_name: str | None = None
    ceo_title: str | None = None
    ceo_phone: str | None = None
    ceo_email: str | None = None
    net_capital: float | None = None
    report_date: date | None = None
    source_pdf_url: str | None = None
    confidence_score: float
    extraction_status: str
    extraction_notes: str | None = None


class BrokerDealerSummary(BaseModel):
    """Slim master-list projection reused by /favorites and /visits.

    Mirrors the 12 fields plan §2 calls out as "BrokerDealerSummary" so the
    sidebar list pages can render the same row shape as the master list
    without pulling the full detail envelope.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    city: str | None
    state: str | None
    lead_score: float | None
    lead_priority: str | None
    current_clearing_partner: str | None
    health_status: str | None
    is_deficient: bool
    last_filing_date: date | None
    latest_net_capital: float | None
    yoy_growth: float | None


class BrokerDealerProfileResponse(BaseModel):
    broker_dealer: BrokerDealerDetail
    financials: list[FinancialMetricItem]
    clearing_arrangements: list[ClearingArrangementItem]
    introducing_arrangements: list[IntroducingArrangementItem]
    industry_arrangements: list[IndustryArrangementItem] = []
    recent_alerts: list[AlertListItem]
    filing_history: list[FilingHistoryItem]
    executive_contacts: list[ExecutiveContactItem]
    registration_compliance: RegistrationComplianceSummary
    deficiency_status: DeficiencyStatusSummary
    # Per-user favorite state for the firm detail page. Populated from
    # `user_favorite` against the calling session's user_id so the heart
    # toggle renders in its correct state on the first paint.
    is_favorited: bool = False
    favorited_at: datetime | None = None
