from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from app.schemas.contact_hits import EmailHit, PhoneHit, synthesize_contact_arrays


class InvestmentAdvisorListItem(BaseModel):
    """One row of the Investment Advisor master list.

    Mirrors the BD ``BrokerDealerListItem`` shape for shared concepts —
    the FE workspace client for advisors is a copy of the BD workspace
    client with field swaps. ``regulatory_aum`` is the analog of
    ``latest_net_capital``; ``advisory_activities`` is the analog of
    ``types_of_business``.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    cik: str | None
    crd_number: str | None
    sec_file_number: str | None
    name: str
    legal_name: str | None
    city: str | None
    state: str | None
    status: str
    matched_source: str
    registration_date: date | None
    formation_date: date | None
    last_filing_date: date | None
    filings_index_url: str | None
    website: str | None = None
    website_source: str | None = None
    # Form ADV Item 5.F — regulatory AUM and its split.
    regulatory_aum: float | None = None
    discretionary_aum: float | None = None
    non_discretionary_aum: float | None = None
    total_clients: int | None = None
    # Form ADV multi-select fields. ``advisory_activities`` is Item 5.G,
    # ``client_types`` is Item 5.D, ``client_counts`` is the per-category
    # number of clients from Item 5.D.3.
    advisory_activities: list[str] | None = None
    client_types: list[str] | None = None
    client_counts: dict[str, int] | None = None
    direct_owners: list[dict[str, str]] | None = None
    indirect_owners: list[dict[str, str]] | None = None
    executive_officers: list[dict[str, str]] | None = None
    firm_operations_text: str | None = None
    # Hard-scope filter flag. ``files_13f=true`` is the default WHERE
    # clause on the master-list endpoint.
    files_13f: bool = False
    latest_13f_filing_date: date | None = None
    last_enrich_attempt_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class InvestmentAdvisorDetail(InvestmentAdvisorListItem):
    """Detail view — same shape as the list item for now.

    PR 3 will extend this with ``advisory_summary``, narrative text from
    Form ADV Part 2, and richer Schedule A/B owner data once the LLM
    extractor is in place. Keeping the detail/list shapes identical for
    PR 1 keeps the FE detail-page placeholder simple.
    """

    pass


class InvestmentAdvisorListMeta(BaseModel):
    page: int
    limit: int
    total: int
    total_pages: int
    # Most recent ``pipeline_run.completed_at`` (or ``started_at`` for an
    # in-flight run) for the advisor pipelines, surfaced so the FE topbar
    # can render "Pipeline refreshed Xm ago" — same shape BD uses.
    pipeline_refreshed_at: datetime | None = None


class InvestmentAdvisorListResponse(BaseModel):
    items: list[InvestmentAdvisorListItem]
    meta: InvestmentAdvisorListMeta


class AdvisorContactItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    advisor_id: int
    name: str
    title: str
    email: str | None
    phone: str | None
    linkedin_url: str | None
    source: str
    discovery_source: str | None = None
    discovery_confidence: float | None = None
    enriched_at: datetime
    emails: list[EmailHit] = []
    phones: list[PhoneHit] = []

    @field_validator("emails", "phones", mode="before")
    @classmethod
    def _coerce_null_to_empty(cls, v: object) -> object:
        return v if v is not None else []

    @model_validator(mode="after")
    def _synthesize_arrays(self) -> Self:
        self.emails, self.phones = synthesize_contact_arrays(self)
        return self


class AdvisorFilingItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    advisor_id: int
    form_type: str
    priority: str
    filed_at: datetime
    summary: str
    source_filing_url: str | None
    is_read: bool


class AdvisoryActivityCount(BaseModel):
    """One entry in the multi-select filter dropdown for advisory activities.

    Mirrors the BD ``TypeOfBusinessOption`` shape — ``{type, count}``
    sorted by count descending then alphabetically.
    """

    type: str
    count: int


class ClientTypeCount(BaseModel):
    """One entry in the client-type filter dropdown.

    Form ADV Item 5.D categories: individuals, high net worth individuals,
    investment companies, pension plans, charitable orgs, etc.
    """

    type: str
    count: int


class InvestmentAdvisorProfileResponse(BaseModel):
    """Aggregate response for the detail page.

    PR 1 ships only ``advisor`` populated; PR 3 fills in ``contacts``,
    ``filings``, ``recent_alerts`` once the ingestion + extraction
    pipelines are in place.
    """

    advisor: InvestmentAdvisorDetail
    contacts: list[AdvisorContactItem] = []
    filings: list[AdvisorFilingItem] = []
    is_favorited: bool = False


# ─── Per-advisor refresh-all (IA analog of RefreshAllRequest/Response) ───────
# Mirrors backend/app/schemas/broker_dealer.py:346-379 so the FE can reuse the
# same polling + 409-conflict + 429-cooldown handling pattern. See plan in
# C:/Users/DSWDSRV-CARAGA/.claude/plans/hos-is-the-new-gleaming-toast.md.

class RefreshAdvisorRequest(BaseModel):
    """Request body for ``POST /investment-advisors/{id}/refresh-all``.

    ``scope="all"`` is the only initial scope. List-only scope can be added
    later if the IA grid grows refresh-relevant columns; today the grid
    surfaces only the always-populated AUM/filing fields, so list_only
    would have nothing to skip.
    """

    scope: Literal["all"] = "all"


class RefreshAdvisorResponse(BaseModel):
    """Response shape for ``POST /investment-advisors/{id}/refresh-all``.

    Two terminal shapes mirroring the BD endpoint:

    - ``run_id=int, status="queued"`` — at least one sub-pipeline's gate
      passed; FE polls ``GET /pipeline/run/{run_id}`` for the parent run's
      terminal state and ``notes.summary`` toast string.
    - ``run_id=None, status="skipped", reason="Already complete."`` — every
      gate failed. No PipelineRun row, no provider calls, no cost.

    When a parent run is already in flight for this advisor, the handler
    returns 202 + ``status="in_flight"`` carrying that run's id (rather
    than a 409) so the FE attaches to it without the browser logging a
    cosmetic console error. The FE polls ``run_id`` identically in the
    queued and in-flight cases.
    """

    run_id: int | None = None
    status: str
    advisor_id: int
    reason: str | None = None
