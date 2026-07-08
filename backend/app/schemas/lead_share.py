"""Pydantic schemas for DOX Share — password-protected public share links.

Two audiences, two clearly separated sections:

* **Admin** (cookie-authed, admin-role) — create a share from a favorite list,
  list/inspect shares, rotate the password, revoke/unrevoke, delete. The
  plaintext password rides out exactly once (``ShareCreateResponse`` /
  ``RotatePasswordResponse``) and is never echoed again.

* **Public** (unauthenticated, token+password gated) — the ``/share/{token}``
  surface. These are **allowlist** projections of the internal profile
  responses, built through explicit ``from_internal`` / ``from_contact``
  mappers so a field added to an internal schema can never silently leak to a
  client. Everything internal — lead scoring, favorite state, competitor
  flags, extraction/enrichment diagnostics, unknown-reason blocks, internal
  alerts and bookkeeping timestamps — is dropped here.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.bank import (
    BankApplicationEventItem,
    BankDetail,
    BankPdfLink,
    BankSourceLink,
)
from app.schemas.broker_dealer import (
    BrokerDealerProfileResponse,
    DeficiencyStatusSummary,
    IndustryArrangementItem,
    IntroducingArrangementItem,
    RegistrationComplianceSummary,
)
from app.schemas.contact_hits import EmailHit, PhoneHit
from app.schemas.investment_advisor import InvestmentAdvisorProfileResponse

ShareItemKind = Literal["broker_dealer", "bank", "advisor"]


# ══════════════════════════════════════════════════════════════════════════
# Admin section
# ══════════════════════════════════════════════════════════════════════════


class ShareCreateRequest(BaseModel):
    favorite_list_id: int = Field(ge=1)
    # Client-facing display name for the share; defaults to the favorite
    # list's own name when omitted.
    name: str | None = Field(default=None, min_length=1, max_length=120)

    @field_validator("name", mode="before")
    @classmethod
    def _strip_name(cls, v: object) -> object:
        if isinstance(v, str):
            stripped = v.strip()
            return stripped or None
        return v


class ShareSkippedItems(BaseModel):
    """Counts of favorite-list members that can't be shared (v1 supports the
    three firm verticals only)."""

    institutional_investors: int = 0
    reporting_owners: int = 0


class ShareSummary(BaseModel):
    id: int
    name: str
    token: str
    # Absolute share link when ``settings.share_frontend_base_url`` is set,
    # else None (the frontend composes it from window.location.origin).
    url: str | None = None
    item_count: int
    created_by: str | None = None
    created_by_email: str | None = None
    created_at: datetime
    revoked_at: datetime | None = None
    password_rotated_at: datetime | None = None
    view_count: int
    last_viewed_at: datetime | None = None
    source_favorite_list_id: int | None = None


class ShareCreateResponse(BaseModel):
    share: ShareSummary
    # Returned exactly once — never retrievable again.
    password: str
    skipped: ShareSkippedItems
    warnings: list[str] = []


class ShareListResponse(BaseModel):
    items: list[ShareSummary]


class ShareItemAdminRow(BaseModel):
    item_id: int
    kind: ShareItemKind
    entity_id: int
    # Live-joined display name; None if the entity row was deleted after the
    # snapshot was taken.
    name: str | None = None
    position: int


class ShareDetailResponse(BaseModel):
    share: ShareSummary
    items: list[ShareItemAdminRow]


class RotatePasswordResponse(BaseModel):
    share_id: int
    # Returned exactly once.
    password: str
    password_rotated_at: datetime


# ══════════════════════════════════════════════════════════════════════════
# Public section — meta + list
# ══════════════════════════════════════════════════════════════════════════


class PublicShareMeta(BaseModel):
    name: str
    unlocked: bool
    # Populated only when unlocked, so the list size isn't leaked pre-password.
    item_count: int | None = None


class PublicShareItemRow(BaseModel):
    """One row in the unlocked share list. Per-vertical headline fields are
    None for the other kinds."""

    item_id: int
    kind: ShareItemKind
    position: int
    name: str
    city: str | None = None
    state: str | None = None
    crd_number: str | None = None
    # broker-dealer headline
    latest_net_capital: float | None = None
    health_status: str | None = None
    current_clearing_partner: str | None = None
    # advisor headline
    regulatory_aum: float | None = None
    total_clients: int | None = None
    # bank headline
    charter_status: str | None = None
    charter_type: str | None = None
    asset: float | None = None


class PublicShareListResponse(BaseModel):
    name: str
    items: list[PublicShareItemRow]


# ══════════════════════════════════════════════════════════════════════════
# Public section — shared leaf projections
# ══════════════════════════════════════════════════════════════════════════


class PublicContactItem(BaseModel):
    """A person on a shared profile — every email/phone we hold (already
    synthesized + personal-first ordered by the source item's validator).

    Stripped vs the internal ``*ContactItem``: row ids, entity FKs,
    discovery source/confidence, Apollo ids, enrichment timestamps, and the
    PDF-provenance columns (source_url / page_number / context_snippet)."""

    name: str
    title: str | None = None
    role_context: str | None = None
    email: str | None = None
    phone: str | None = None
    linkedin_url: str | None = None
    emails: list[EmailHit] = []
    phones: list[PhoneHit] = []

    @classmethod
    def from_contact(cls, item: object) -> Self:
        return cls(
            name=getattr(item, "name", "") or "",
            title=getattr(item, "title", None),
            role_context=getattr(item, "role_context", None),
            email=getattr(item, "email", None),
            phone=getattr(item, "phone", None),
            linkedin_url=getattr(item, "linkedin_url", None),
            emails=list(getattr(item, "emails", None) or []),
            phones=list(getattr(item, "phones", None) or []),
        )


class PublicDiscoveredEmailRow(BaseModel):
    """An email-extractor discovery on a shared profile — the enriched
    person fields only (no run linkage, status, confidence, or provider ids)."""

    email: str
    name: str | None = None
    title: str | None = None
    phone: str | None = None
    linkedin_url: str | None = None

    @classmethod
    def from_model(cls, row: object) -> Self:
        return cls(
            email=getattr(row, "email", "") or "",
            name=getattr(row, "enriched_name", None),
            title=getattr(row, "enriched_title", None),
            phone=getattr(row, "enriched_phone", None),
            linkedin_url=getattr(row, "enriched_linkedin_url", None),
        )


class PublicFinancialRow(BaseModel):
    """One FOCUS financial period — net capital series for the trend chart.
    Strips extraction_status / unknown_reason / ids."""

    report_date: date
    net_capital: float
    excess_net_capital: float | None = None
    total_assets: float | None = None
    required_min_capital: float | None = None
    source_filing_url: str | None = None

    @classmethod
    def from_internal(cls, item: object) -> Self:
        return cls(
            report_date=item.report_date,
            net_capital=item.net_capital,
            excess_net_capital=getattr(item, "excess_net_capital", None),
            total_assets=getattr(item, "total_assets", None),
            required_min_capital=getattr(item, "required_min_capital", None),
            source_filing_url=getattr(item, "source_filing_url", None),
        )


class PublicClearingArrangementRow(BaseModel):
    """A clearing arrangement as filed. Strips extraction confidence/status/
    notes, the is_competitor / is_verified flags, unknown_reason, and ids."""

    filing_year: int | None = None
    report_date: date | None = None
    clearing_partner: str | None = None
    clearing_type: str | None = None
    agreement_date: date | None = None
    source_filing_url: str | None = None

    @classmethod
    def from_internal(cls, item: object) -> Self:
        return cls(
            filing_year=getattr(item, "filing_year", None),
            report_date=getattr(item, "report_date", None),
            clearing_partner=getattr(item, "clearing_partner", None),
            clearing_type=getattr(item, "clearing_type", None),
            agreement_date=getattr(item, "agreement_date", None),
            source_filing_url=getattr(item, "source_filing_url", None),
        )


class PublicClearingMembershipRow(BaseModel):
    """A clearing-agency / SRO membership. Keeps the public directory facts
    (agency, member number, verbatim name, status); strips the internal match
    method/confidence and source-file provenance."""

    agency: str
    member_number: str | None = None
    member_name_raw: str | None = None
    status: str | None = None

    @classmethod
    def from_internal(cls, item: object) -> Self:
        return cls(
            agency=getattr(item, "agency", "") or "",
            member_number=getattr(item, "member_number", None),
            member_name_raw=getattr(item, "member_name_raw", None),
            status=getattr(item, "status", None),
        )


class PublicAdvisorFilingRow(BaseModel):
    """A Form ADV filing on a shared advisor profile. Strips is_read /
    priority / ids."""

    form_type: str
    filed_at: datetime
    summary: str | None = None
    source_filing_url: str | None = None

    @classmethod
    def from_internal(cls, item: object) -> Self:
        return cls(
            form_type=getattr(item, "form_type", "") or "",
            filed_at=item.filed_at,
            summary=getattr(item, "summary", None),
            source_filing_url=getattr(item, "source_filing_url", None),
        )


# ══════════════════════════════════════════════════════════════════════════
# Public section — per-vertical profiles
# ══════════════════════════════════════════════════════════════════════════


class PublicBrokerDealerProfile(BaseModel):
    # identity / registration
    name: str
    crd_number: str | None = None
    sec_file_number: str | None = None
    cik: str | None = None
    city: str | None = None
    state: str | None = None
    website: str | None = None
    status: str | None = None
    registration_date: date | None = None
    formation_date: date | None = None
    business_type: str | None = None
    branch_count: int | None = None
    dba_names: list[str] | None = None
    types_of_business: list[str] | None = None
    types_of_business_other: str | None = None
    types_of_business_total: int | None = None
    firm_operations_text: str | None = None
    direct_owners: list[dict[str, str]] | None = None
    executive_officers: list[dict[str, str]] | None = None
    member_agencies: list[str] = []
    # clearing (competitor flag deliberately excluded)
    current_clearing_partner: str | None = None
    current_clearing_type: str | None = None
    last_filing_date: date | None = None
    filings_index_url: str | None = None
    # financial health
    latest_net_capital: float | None = None
    latest_excess_net_capital: float | None = None
    latest_total_assets: float | None = None
    required_min_capital: float | None = None
    yoy_growth: float | None = None
    three_year_cagr: float | None = None
    health_status: str | None = None
    is_deficient: bool = False
    latest_deficiency_filed_at: date | None = None
    # nested
    financials: list[PublicFinancialRow] = []
    clearing_arrangements: list[PublicClearingArrangementRow] = []
    clearing_memberships: list[PublicClearingMembershipRow] = []
    introducing_arrangements: list[IntroducingArrangementItem] = []
    industry_arrangements: list[IndustryArrangementItem] = []
    registration_compliance: RegistrationComplianceSummary | None = None
    deficiency_status: DeficiencyStatusSummary | None = None
    executive_contacts: list[PublicContactItem] = []
    discovered_emails: list[PublicDiscoveredEmailRow] = []

    @classmethod
    def from_internal(
        cls,
        profile: BrokerDealerProfileResponse,
        *,
        discovered_emails: list[PublicDiscoveredEmailRow] | None = None,
    ) -> Self:
        bd = profile.broker_dealer
        return cls(
            name=bd.name,
            crd_number=bd.crd_number,
            sec_file_number=bd.sec_file_number,
            cik=bd.cik,
            city=bd.city,
            state=bd.state,
            website=bd.website,
            status=bd.status,
            registration_date=bd.registration_date,
            formation_date=bd.formation_date,
            business_type=bd.business_type,
            branch_count=bd.branch_count,
            dba_names=bd.dba_names,
            types_of_business=bd.types_of_business,
            types_of_business_other=bd.types_of_business_other,
            types_of_business_total=bd.types_of_business_total,
            firm_operations_text=bd.firm_operations_text,
            direct_owners=bd.direct_owners,
            executive_officers=bd.executive_officers,
            member_agencies=list(bd.member_agencies or []),
            current_clearing_partner=bd.current_clearing_partner,
            current_clearing_type=bd.current_clearing_type,
            last_filing_date=bd.last_filing_date,
            filings_index_url=bd.filings_index_url,
            latest_net_capital=bd.latest_net_capital,
            latest_excess_net_capital=bd.latest_excess_net_capital,
            latest_total_assets=bd.latest_total_assets,
            required_min_capital=bd.required_min_capital,
            yoy_growth=bd.yoy_growth,
            three_year_cagr=bd.three_year_cagr,
            health_status=bd.health_status,
            is_deficient=bd.is_deficient,
            latest_deficiency_filed_at=bd.latest_deficiency_filed_at,
            financials=[PublicFinancialRow.from_internal(f) for f in profile.financials],
            clearing_arrangements=[
                PublicClearingArrangementRow.from_internal(c)
                for c in profile.clearing_arrangements
            ],
            clearing_memberships=[
                PublicClearingMembershipRow.from_internal(m)
                for m in profile.clearing_memberships
            ],
            introducing_arrangements=list(profile.introducing_arrangements),
            industry_arrangements=list(profile.industry_arrangements),
            registration_compliance=profile.registration_compliance,
            deficiency_status=profile.deficiency_status,
            executive_contacts=[
                PublicContactItem.from_contact(c) for c in profile.executive_contacts
            ],
            discovered_emails=list(discovered_emails or []),
        )


class PublicAdvisorProfile(BaseModel):
    name: str
    legal_name: str | None = None
    crd_number: str | None = None
    sec_file_number: str | None = None
    cik: str | None = None
    city: str | None = None
    state: str | None = None
    status: str | None = None
    registration_date: date | None = None
    formation_date: date | None = None
    last_filing_date: date | None = None
    filings_index_url: str | None = None
    website: str | None = None
    regulatory_aum: float | None = None
    discretionary_aum: float | None = None
    non_discretionary_aum: float | None = None
    total_clients: int | None = None
    advisory_activities: list[str] | None = None
    client_types: list[str] | None = None
    client_counts: dict[str, int] | None = None
    direct_owners: list[dict[str, str]] | None = None
    indirect_owners: list[dict[str, str]] | None = None
    executive_officers: list[dict[str, str]] | None = None
    firm_operations_text: str | None = None
    other_business_names: list[str] | None = None
    files_13f: bool = False
    latest_13f_filing_date: date | None = None
    member_agencies: list[str] = []
    contacts: list[PublicContactItem] = []
    filings: list[PublicAdvisorFilingRow] = []
    discovered_emails: list[PublicDiscoveredEmailRow] = []

    @classmethod
    def from_internal(
        cls,
        profile: InvestmentAdvisorProfileResponse,
        *,
        discovered_emails: list[PublicDiscoveredEmailRow] | None = None,
    ) -> Self:
        a = profile.advisor
        return cls(
            name=a.name,
            legal_name=a.legal_name,
            crd_number=a.crd_number,
            sec_file_number=a.sec_file_number,
            cik=a.cik,
            city=a.city,
            state=a.state,
            status=a.status,
            registration_date=a.registration_date,
            formation_date=a.formation_date,
            last_filing_date=a.last_filing_date,
            filings_index_url=a.filings_index_url,
            website=a.website,
            regulatory_aum=a.regulatory_aum,
            discretionary_aum=a.discretionary_aum,
            non_discretionary_aum=a.non_discretionary_aum,
            total_clients=a.total_clients,
            advisory_activities=a.advisory_activities,
            client_types=a.client_types,
            client_counts=a.client_counts,
            direct_owners=a.direct_owners,
            indirect_owners=a.indirect_owners,
            executive_officers=a.executive_officers,
            firm_operations_text=a.firm_operations_text,
            other_business_names=getattr(a, "other_business_names", None),
            files_13f=a.files_13f,
            latest_13f_filing_date=a.latest_13f_filing_date,
            member_agencies=list(a.member_agencies or []),
            contacts=[PublicContactItem.from_contact(c) for c in profile.contacts],
            filings=[PublicAdvisorFilingRow.from_internal(f) for f in profile.filings],
            discovered_emails=list(discovered_emails or []),
        )


class PublicBankProfile(BaseModel):
    name: str
    fdic_cert: str | None = None
    fed_rssd: str | None = None
    occ_charter_number: str | None = None
    occ_control_number: str | None = None
    address: str | None = None
    city: str | None = None
    state: str | None = None
    zip: str | None = None
    website: str | None = None
    charter_authority: str | None = None
    charter_type: str | None = None
    charter_status: str | None = None
    bkclass: str | None = None
    regulator: str | None = None
    lei: str | None = None
    established_date: date | None = None
    insured_date: date | None = None
    application_received_date: date | None = None
    last_action_date: date | None = None
    asset: float | None = None
    deposits: float | None = None
    offices: int | None = None
    active: bool | None = None
    digital_assets: bool = False
    digital_asset_pdfs: list[BankPdfLink] = []
    application_events: list[BankApplicationEventItem] = []
    source_links: list[BankSourceLink] = []
    contacts: list[PublicContactItem] = []
    discovered_emails: list[PublicDiscoveredEmailRow] = []

    @classmethod
    def from_internal(
        cls,
        detail: BankDetail,
        *,
        discovered_emails: list[PublicDiscoveredEmailRow] | None = None,
    ) -> Self:
        return cls(
            name=detail.name,
            fdic_cert=detail.fdic_cert,
            fed_rssd=detail.fed_rssd,
            occ_charter_number=detail.occ_charter_number,
            occ_control_number=detail.occ_control_number,
            address=detail.address,
            city=detail.city,
            state=detail.state,
            zip=detail.zip,
            website=detail.website,
            charter_authority=detail.charter_authority,
            charter_type=detail.charter_type,
            charter_status=detail.charter_status,
            bkclass=detail.bkclass,
            regulator=detail.regulator,
            lei=detail.lei,
            established_date=detail.established_date,
            insured_date=detail.insured_date,
            application_received_date=detail.application_received_date,
            last_action_date=detail.last_action_date,
            asset=detail.asset,
            deposits=detail.deposits,
            offices=detail.offices,
            active=detail.active,
            digital_assets=detail.digital_assets,
            digital_asset_pdfs=list(detail.digital_asset_pdfs or []),
            application_events=list(detail.application_events or []),
            source_links=list(detail.source_links or []),
            contacts=[PublicContactItem.from_contact(c) for c in detail.contacts],
            discovered_emails=list(discovered_emails or []),
        )


class PublicProfileResponse(BaseModel):
    """Kind-enveloped public profile — exactly one of the three is populated,
    matching ``kind``."""

    item_id: int
    kind: ShareItemKind
    broker_dealer: PublicBrokerDealerProfile | None = None
    bank: PublicBankProfile | None = None
    advisor: PublicAdvisorProfile | None = None
