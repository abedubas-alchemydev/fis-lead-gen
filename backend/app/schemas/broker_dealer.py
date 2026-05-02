from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from app.schemas.alerts import AlertListItem
from app.schemas.pipeline import ClearingArrangementItem
from app.schemas.unknown_reason import UnknownReason

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
    # 'finra' | 'apollo' | 'hunter' | 'serpapi' | None. Lets the FE disclose
    # the source when needed and keeps the audit trail visible from the
    # broker-dealer detail response without a separate trip to a discovery-
    # history table. The string column is bounded at VARCHAR(16) on the
    # model side; widen there if a future provider's name exceeds that.
    website_source: str | None = None
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
    # Populated when ``current_clearing_partner`` is None — derived from the
    # latest ``ClearingArrangement`` row's extraction_status / extraction_notes
    # by ``app.services.unknown_reasons.derive_clearing_unknown_reason``. The
    # FE keys off the presence of this object to render the info-icon tooltip.
    current_clearing_unknown_reason: UnknownReason | None = None
    # Populated when the rolled-up financial summary (``latest_net_capital``,
    # ``yoy_growth``) is missing — derived from the latest ``FinancialMetric``
    # row's extraction_status. None when the firm has parsed financials.
    financial_unknown_reason: UnknownReason | None = None


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
    # Populated when the row's ``extraction_status`` !=  "parsed". For
    # financials a row exists ⇒ ``net_capital`` and ``report_date`` are
    # NOT NULL, so this only fires for needs_review / provider_error /
    # pipeline_error / missing_pdf rows. None on parsed rows.
    unknown_reason: UnknownReason | None = None


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
    # Per-row ``unknown_reason`` is always None on a populated contact —
    # the field exists so consumers can read every nullable surface through
    # the same shape. The list-level "no contacts at all" reason ships as
    # ``BrokerDealerProfileResponse.executive_contacts_unknown_reason``.
    unknown_reason: UnknownReason | None = None


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
    # Per-user favorite state for the firm detail page. Populated from the
    # calling user's default ``favorite_list`` (its ``favorite_list_item``
    # rows) so the heart toggle renders in its correct state on the first
    # paint. The legacy ``user_favorite`` table was dropped in 20260429_0021
    # (and again, idempotently, in 20260429_0022).
    is_favorited: bool = False
    favorited_at: datetime | None = None
    # List-level reason for an empty ``executive_contacts``. The contact
    # table has no extraction_status column (Apollo/Hunter/Snov-driven), so
    # the only signal is "row exists or doesn't" — when the list is empty
    # this gets populated with ``not_yet_extracted`` so the FE can render
    # an info icon next to the "No contacts" empty state.
    executive_contacts_unknown_reason: UnknownReason | None = None


class ResolveWebsiteResponse(BaseModel):
    """Response shape for ``POST /broker-dealers/{id}/resolve-website``.

    On a hit (newly resolved or already cached): ``website`` and
    ``website_source`` are populated and ``reason`` is ``None``. On a
    clean miss the chain ran but produced no valid candidate; both
    fields are ``None`` and ``reason`` is ``no_valid_candidate``. On
    total provider failure both fields stay ``None`` and ``reason`` is
    ``all_providers_errored: ...`` — the FE keys off ``reason`` to know
    whether to retry on a later visit.
    """

    website: str | None = None
    website_source: str | None = None
    reason: str | None = None


class RefreshFinancialsResponse(BaseModel):
    """Response shape for ``POST /broker-dealers/{id}/refresh-financials``.

    The handler returns 202 Accepted and kicks off the X-17A-5 → Gemini
    extraction in a FastAPI BackgroundTask. ``run_id`` points at the
    PipelineRun row the FE polls via ``GET /pipeline/run/{run_id}`` to
    learn when the extraction finishes. ``status`` is ``"queued"`` on a
    fresh trigger; on a 409 conflict the handler returns the in-flight
    run's current status (``"queued"`` or ``"running"``) so the FE can
    pick up polling without erroring.
    """

    run_id: int
    status: str
    broker_dealer_id: int
