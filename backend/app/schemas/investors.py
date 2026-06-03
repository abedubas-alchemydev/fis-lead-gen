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
    enriched_linkedin_url: str | None = None
    enriched_at: datetime | None

    # True when an Apollo phone-reveal was requested for this insider
    # (apollo_person_id stamped) but the number hasn't landed via the async
    # webhook yet. Lets the FE show a "phone arriving" hint in place of a
    # blank slot. Always False once a phone is present or when no reveal was
    # ever requested (PDL-only match, total miss, or entity filer).
    phone_pending: bool = False

    source_filing_url: str | None
    filed_at: datetime

    # Favorites (insider). ``reporting_owner_id`` is the surrogate id of
    # the insider's ``reporting_owners`` row, or None until first
    # favorited (the FE then adds by ``reporting_owner_cik``).
    # ``is_favorited`` reflects membership in the caller's default list.
    reporting_owner_id: int | None = None
    is_favorited: bool = False

    # True when the reporting owner's name looks like an entity (LLC, LP,
    # GP, Fund, Holdings, ...) rather than a natural person. Computed
    # per-response from the name; lets the FE disable the Enrich button
    # for rows the person-only Apollo/PDL match can never resolve.
    is_entity: bool = False


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
    consolidated row it already has. ``enriched_at`` is populated after a
    real Apollo/PDL attempt (even when both come back empty) so the FE can
    distinguish "never enriched" from "enriched, came back empty" and
    avoid re-triggering on every render. ``enriched_at`` is **NULL** when
    ``skip_reason`` is set (e.g. entity filer) — the lookup never actually
    ran, so caching a timestamp would be misleading. ``txn_id`` echoes the
    leader transaction id the FE called with so the FE knows which row in
    its list to patch. ``skip_reason`` is non-NULL only for deliberate
    short-circuits (currently just ``"entity_filer"``); a real upstream
    miss returns ``matched=False`` with ``skip_reason=None``.
    """

    txn_id: int
    enriched_phone: str | None
    enriched_email: str | None
    enriched_linkedin_url: str | None = None
    enriched_at: datetime | None
    matched: bool
    skip_reason: str | None = None
    # True when Apollo returned a record and an async phone-reveal was
    # requested but the number isn't in the sync response — the FE merges
    # this into the row so the "phone arriving" hint shows immediately after
    # a click, not just on the next list load.
    phone_pending: bool = False


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
