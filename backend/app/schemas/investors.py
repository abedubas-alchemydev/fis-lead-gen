from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel


class InvestorItem(BaseModel):
    """One row in the Investors tab.

    Shape mirrors what the FE renders directly — the BE collapses the
    Form 4 transaction + reporting-person + issuer triple into a single
    flat record so the grouped-by-ticker UI can stream rows without
    second-level joins.
    """

    id: int
    accession_number: str
    is_derivative: bool

    issuer_cik: str
    issuer_name: str
    issuer_ticker: str | None

    reporting_owner_cik: str
    reporting_owner_name: str
    reporting_owner_title: str | None
    reporting_owner_is_director: bool
    reporting_owner_is_officer: bool
    reporting_owner_is_ten_pct: bool
    reporting_owner_street1: str | None
    reporting_owner_street2: str | None
    reporting_owner_city: str | None
    reporting_owner_state: str | None
    reporting_owner_zip: str | None

    security_title: str | None
    transaction_date: date
    transaction_code: str | None
    ad_code: str
    shares: float | None
    price_per_share: float | None
    transaction_value: float | None
    txn_count: int

    enriched_phone: str | None
    enriched_email: str | None
    enriched_at: datetime | None

    source_filing_url: str | None
    filed_at: datetime


class InvestorListMeta(BaseModel):
    page: int
    limit: int
    total: int
    total_pages: int


class InvestorListResponse(BaseModel):
    items: list[InvestorItem]
    meta: InvestorListMeta


class InvestorEnrichResponse(BaseModel):
    """Returned by ``POST /investors/{id}/enrich``.

    Returns just the enrichment fields — the FE merges them into the
    consolidated row it already has. ``enriched_at`` is always populated
    after a successful Apollo call (even when Apollo returns no match)
    so the FE can distinguish "never enriched" from "enriched, came back
    empty" and avoid re-triggering on every render. ``txn_id`` echoes the
    leader transaction id the FE called with so the FE knows which row
    in its list to patch.
    """

    txn_id: int
    enriched_phone: str | None
    enriched_email: str | None
    enriched_at: datetime
    matched: bool


class InvestorPipelineRunResponse(BaseModel):
    """Returned by ``POST /pipeline/run/form4-watcher``.

    Same shape as the existing watchers' triggers (filing-monitor,
    registration-monitor, deficiency-monitor) — keeps the FE pipelines
    admin page generic.
    """

    run_id: int
    status: str
    total_items: int
    processed_items: int
    success_count: int
    failure_count: int
