"""Cross-entity contact search endpoints.

Search by email address or organization domain across all three contact
tables (executive_contacts, advisor_contacts, investor_contacts) plus
the discovered_emails buffer from the email extractor. Backs the
"Search contacts" surface that sits on top of every list page so the
user can find a contact regardless of which firm-type list they're
currently looking at.

Apollo ``/people/match`` is an optional fallback for find-by-email --
when ``enrich_via_apollo: true`` is on the body, an email with no
local hit triggers a single Apollo round trip and returns the enriched
result with ``source: 'apollo'``. The fallback is opt-in so callers
control spend.
"""

from __future__ import annotations

from urllib.parse import urlparse

from fastapi import APIRouter, Depends
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.models.advisor_contact import AdvisorContact
from app.models.broker_dealer import BrokerDealer
from app.models.discovered_email import DiscoveredEmail
from app.models.executive_contact import ExecutiveContact
from app.models.institutional_investor import InstitutionalInvestor
from app.models.investment_advisor import InvestmentAdvisor
from app.models.investor_contact import InvestorContact
from app.schemas.auth import AuthenticatedUser
from app.schemas.contact_search import (
    ContactSearchByDomainRequest,
    ContactSearchByEmailRequest,
    ContactSearchHit,
    ContactSearchResponse,
)
from app.services.auth import get_current_user


router = APIRouter(prefix="/contacts")


def _normalize_domain(raw: str) -> str:
    """Strip scheme + path + leading 'www.' so callers can paste URLs.

    Mirrors the lighter-weight resolver used by find-emails-button --
    we want "https://www.example.com/foo" and "example.com" to match
    the same firm rows.
    """

    raw = raw.strip().lower()
    if "://" not in raw:
        raw = "http://" + raw
    netloc = urlparse(raw).netloc or raw
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc.split(":")[0]


@router.post("/find-by-email", response_model=ContactSearchResponse)
async def find_contacts_by_email(
    payload: ContactSearchByEmailRequest,
    _: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> ContactSearchResponse:
    """Search all three contact tables + discovered_emails for ``email``.

    Returns one hit per matching row (a contact appearing in multiple
    tables -- which shouldn't happen in practice but is possible across
    enrichment runs -- surfaces as multiple hits).

    Apollo fallback is opt-in via ``enrich_via_apollo: true`` and runs
    only when no local table has a hit. Apollo failures are silent
    (returns empty results) so the search endpoint stays responsive
    even when Apollo is degraded.
    """

    needle = payload.email.lower()
    hits: list[ContactSearchHit] = []

    # ExecutiveContact -> joined with BD for firm_name
    exec_stmt = (
        select(
            ExecutiveContact.id,
            ExecutiveContact.bd_id,
            ExecutiveContact.name,
            ExecutiveContact.title,
            ExecutiveContact.email,
            ExecutiveContact.phone,
            ExecutiveContact.linkedin_url,
            BrokerDealer.name.label("firm_name"),
        )
        .join(BrokerDealer, BrokerDealer.id == ExecutiveContact.bd_id)
        .where(func.lower(ExecutiveContact.email) == needle)
    )
    for row in (await db.execute(exec_stmt)).all():
        hits.append(
            ContactSearchHit(
                source="executive_contact",
                firm_type="broker_dealer",
                firm_id=row.bd_id,
                firm_name=row.firm_name,
                contact_id=row.id,
                name=row.name,
                title=row.title,
                email=row.email,
                phone=row.phone,
                linkedin_url=row.linkedin_url,
            )
        )

    # AdvisorContact
    advisor_stmt = (
        select(
            AdvisorContact.id,
            AdvisorContact.advisor_id,
            AdvisorContact.name,
            AdvisorContact.title,
            AdvisorContact.email,
            AdvisorContact.phone,
            AdvisorContact.linkedin_url,
            InvestmentAdvisor.name.label("firm_name"),
        )
        .join(InvestmentAdvisor, InvestmentAdvisor.id == AdvisorContact.advisor_id)
        .where(func.lower(AdvisorContact.email) == needle)
    )
    for row in (await db.execute(advisor_stmt)).all():
        hits.append(
            ContactSearchHit(
                source="advisor_contact",
                firm_type="advisor",
                firm_id=row.advisor_id,
                firm_name=row.firm_name,
                contact_id=row.id,
                name=row.name,
                title=row.title,
                email=row.email,
                phone=row.phone,
                linkedin_url=row.linkedin_url,
            )
        )

    # InvestorContact
    investor_stmt = (
        select(
            InvestorContact.id,
            InvestorContact.investor_id,
            InvestorContact.name,
            InvestorContact.title,
            InvestorContact.email,
            InvestorContact.phone,
            InvestorContact.linkedin_url,
            InstitutionalInvestor.name.label("firm_name"),
        )
        .join(
            InstitutionalInvestor,
            InstitutionalInvestor.id == InvestorContact.investor_id,
        )
        .where(func.lower(InvestorContact.email) == needle)
    )
    for row in (await db.execute(investor_stmt)).all():
        hits.append(
            ContactSearchHit(
                source="investor_contact",
                firm_type="institutional_investor",
                firm_id=row.investor_id,
                firm_name=row.firm_name,
                contact_id=row.id,
                name=row.name,
                title=row.title,
                email=row.email,
                phone=row.phone,
                linkedin_url=row.linkedin_url,
            )
        )

    # DiscoveredEmail buffer -- email-extractor's domain scan results.
    # Carries enrichment fields filled in by Apollo / Hunter when a scan
    # has been enriched; firm context only when the scan was triggered
    # from a specific BD detail page (bd_id set).
    discovered_stmt = select(DiscoveredEmail).where(
        func.lower(DiscoveredEmail.email) == needle
    )
    for row in (await db.execute(discovered_stmt)).scalars().all():
        hits.append(
            ContactSearchHit(
                source="discovered_email",
                firm_type="broker_dealer" if row.bd_id else None,
                firm_id=row.bd_id,
                firm_name=row.enriched_company,
                contact_id=row.id,
                name=row.enriched_name,
                title=row.enriched_title,
                email=row.email,
                phone=row.enriched_phone,
                linkedin_url=row.enriched_linkedin_url,
            )
        )

    # Apollo opt-in fallback. Skipped when any local hit exists -- the
    # caller already has what they need and the round trip is wasted.
    if payload.enrich_via_apollo and not hits:
        try:
            from app.services.apollo_people_match import match_person_by_email

            apollo_hit = await match_person_by_email(needle)
            if apollo_hit is not None:
                hits.append(apollo_hit)
        except Exception:  # noqa: BLE001
            # Silent degrade -- Apollo issues shouldn't surface as a
            # search failure. The empty result conveys "no hit".
            pass

    return ContactSearchResponse(hits=hits, count=len(hits))


@router.post("/find-by-domain", response_model=ContactSearchResponse)
async def find_contacts_by_domain(
    payload: ContactSearchByDomainRequest,
    _: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> ContactSearchResponse:
    """Return contacts across all three firm types whose firm matches ``domain``.

    Match strategy: a firm matches the domain if ``website`` contains
    the normalized domain. Substring rather than exact-eq because firms
    register sub-paths (``example.com/about``) and we don't want to
    miss those.
    """

    domain = _normalize_domain(payload.domain)
    if not domain:
        return ContactSearchResponse(hits=[], count=0)

    needle = f"%{domain}%"
    hits: list[ContactSearchHit] = []

    # BD firms by website domain -> include their executive contacts
    bd_stmt = (
        select(
            ExecutiveContact.id,
            ExecutiveContact.bd_id,
            ExecutiveContact.name,
            ExecutiveContact.title,
            ExecutiveContact.email,
            ExecutiveContact.phone,
            ExecutiveContact.linkedin_url,
            BrokerDealer.name.label("firm_name"),
        )
        .join(BrokerDealer, BrokerDealer.id == ExecutiveContact.bd_id)
        .where(BrokerDealer.website.ilike(needle))
    )
    for row in (await db.execute(bd_stmt)).all():
        hits.append(
            ContactSearchHit(
                source="executive_contact",
                firm_type="broker_dealer",
                firm_id=row.bd_id,
                firm_name=row.firm_name,
                contact_id=row.id,
                name=row.name,
                title=row.title,
                email=row.email,
                phone=row.phone,
                linkedin_url=row.linkedin_url,
            )
        )

    # Advisor firms by website domain -> include their advisor contacts
    advisor_stmt = (
        select(
            AdvisorContact.id,
            AdvisorContact.advisor_id,
            AdvisorContact.name,
            AdvisorContact.title,
            AdvisorContact.email,
            AdvisorContact.phone,
            AdvisorContact.linkedin_url,
            InvestmentAdvisor.name.label("firm_name"),
        )
        .join(InvestmentAdvisor, InvestmentAdvisor.id == AdvisorContact.advisor_id)
        .where(InvestmentAdvisor.website.ilike(needle))
    )
    for row in (await db.execute(advisor_stmt)).all():
        hits.append(
            ContactSearchHit(
                source="advisor_contact",
                firm_type="advisor",
                firm_id=row.advisor_id,
                firm_name=row.firm_name,
                contact_id=row.id,
                name=row.name,
                title=row.title,
                email=row.email,
                phone=row.phone,
                linkedin_url=row.linkedin_url,
            )
        )

    # Investor firms by website domain -> include their investor contacts
    investor_stmt = (
        select(
            InvestorContact.id,
            InvestorContact.investor_id,
            InvestorContact.name,
            InvestorContact.title,
            InvestorContact.email,
            InvestorContact.phone,
            InvestorContact.linkedin_url,
            InstitutionalInvestor.name.label("firm_name"),
        )
        .join(
            InstitutionalInvestor,
            InstitutionalInvestor.id == InvestorContact.investor_id,
        )
        .where(InstitutionalInvestor.website.ilike(needle))
    )
    for row in (await db.execute(investor_stmt)).all():
        hits.append(
            ContactSearchHit(
                source="investor_contact",
                firm_type="institutional_investor",
                firm_id=row.investor_id,
                firm_name=row.firm_name,
                contact_id=row.id,
                name=row.name,
                title=row.title,
                email=row.email,
                phone=row.phone,
                linkedin_url=row.linkedin_url,
            )
        )

    # Surface firms with the matching domain even when no contacts are
    # on file yet -- the user typically wants both "who do we know at
    # this firm?" and "do we even have this firm tracked?". Each firm
    # row contributes one synthetic hit with contact fields nulled out.
    firms_without_contacts: list[tuple] = []

    bd_firms = (
        await db.execute(
            select(BrokerDealer.id, BrokerDealer.name)
            .where(BrokerDealer.website.ilike(needle))
            .where(
                ~select(ExecutiveContact.id)
                .where(ExecutiveContact.bd_id == BrokerDealer.id)
                .exists()
            )
        )
    ).all()
    for fid, fname in bd_firms:
        firms_without_contacts.append(("broker_dealer", fid, fname))

    advisor_firms = (
        await db.execute(
            select(InvestmentAdvisor.id, InvestmentAdvisor.name)
            .where(InvestmentAdvisor.website.ilike(needle))
            .where(
                ~select(AdvisorContact.id)
                .where(AdvisorContact.advisor_id == InvestmentAdvisor.id)
                .exists()
            )
        )
    ).all()
    for fid, fname in advisor_firms:
        firms_without_contacts.append(("advisor", fid, fname))

    investor_firms = (
        await db.execute(
            select(InstitutionalInvestor.id, InstitutionalInvestor.name)
            .where(InstitutionalInvestor.website.ilike(needle))
            .where(
                ~select(InvestorContact.id)
                .where(InvestorContact.investor_id == InstitutionalInvestor.id)
                .exists()
            )
        )
    ).all()
    for fid, fname in investor_firms:
        firms_without_contacts.append(("institutional_investor", fid, fname))

    for firm_type, fid, fname in firms_without_contacts:
        hits.append(
            ContactSearchHit(
                source="discovered_email",
                firm_type=firm_type,  # type: ignore[arg-type]
                firm_id=fid,
                firm_name=fname,
                contact_id=None,
                name=None,
                title=None,
                email=None,
                phone=None,
                linkedin_url=None,
            )
        )

    return ContactSearchResponse(hits=hits, count=len(hits))
