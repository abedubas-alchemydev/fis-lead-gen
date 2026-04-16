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
    is_niche_restricted: bool = False
    created_at: datetime


class BrokerDealerDetail(BrokerDealerListItem):
    pass


class BrokerDealerListMeta(BaseModel):
    page: int
    limit: int
    total: int
    total_pages: int


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


class BrokerDealerProfileResponse(BaseModel):
    broker_dealer: BrokerDealerDetail
    financials: list[FinancialMetricItem]
    clearing_arrangements: list[ClearingArrangementItem]
    recent_alerts: list[AlertListItem]
    filing_history: list[FilingHistoryItem]
    executive_contacts: list[ExecutiveContactItem]
    registration_compliance: RegistrationComplianceSummary
    deficiency_status: DeficiencyStatusSummary
