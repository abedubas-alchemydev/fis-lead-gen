from __future__ import annotations

from datetime import date, datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from app.schemas.contact_hits import EmailHit, PhoneHit, synthesize_contact_arrays


class InstitutionalInvestorListItem(BaseModel):
    """One row of the Institutional Investor master list.

    Mirrors the ``InvestmentAdvisorListItem`` shape for shared concepts.
    The FE workspace client for investors is a copy of the advisor
    workspace client with field swaps (``total_aum`` for
    ``regulatory_aum``, etc.) and the addition of ``advisor_id`` so the
    "Also a registered investment adviser ->" cross-link can render
    without a separate fetch.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    cik: str | None
    advisor_id: int | None
    name: str
    legal_name: str | None
    city: str | None
    state: str | None
    status: str
    matched_source: str
    website: str | None = None
    website_source: str | None = None
    latest_13f_filing_date: date | None = None
    total_aum: float | None = None
    holdings_count: int | None = None
    filings_index_url: str | None = None
    last_enrich_attempt_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class InstitutionalInvestorDetail(InstitutionalInvestorListItem):
    """Detail view -- same shape as the list item for the skeleton.

    Later iterations will add holdings, top positions, and 13F-extracted
    narrative once the holdings ingestion lands.
    """

    pass


class InstitutionalInvestorListMeta(BaseModel):
    page: int
    limit: int
    total: int
    total_pages: int
    pipeline_refreshed_at: datetime | None = None


class InstitutionalInvestorListResponse(BaseModel):
    items: list[InstitutionalInvestorListItem]
    meta: InstitutionalInvestorListMeta


class InvestorContactItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    investor_id: int
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


class InvestorFilingItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    investor_id: int
    form_type: str
    priority: str
    filed_at: datetime
    summary: str
    source_filing_url: str | None
    is_read: bool


class InstitutionalInvestorProfileResponse(BaseModel):
    """Aggregate response for the detail page.

    ``advisor_id`` on the embedded investor is the cross-link populated
    when the same CIK also appears in IAPD; the FE uses it to render the
    "View as Investment Advisor" link without a separate lookup.
    """

    investor: InstitutionalInvestorDetail
    contacts: list[InvestorContactItem] = []
    filings: list[InvestorFilingItem] = []
    is_favorited: bool = False


class InstitutionalInvestorAdjacentResponse(BaseModel):
    """Prev/next ids for the Next-button continuous navigation."""

    prev_id: int | None = None
    next_id: int | None = None
