from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.feature_permissions import INSTITUTIONAL_INVESTORS
from app.db.session import get_db_session
from app.models.favorite_list import FavoriteList, FavoriteListItem
from app.models.institutional_investor import InstitutionalInvestor
from app.models.investor_contact import InvestorContact
from app.schemas.auth import AuthenticatedUser
from app.schemas.favorite_list import FavoriteListWithMembership
from app.schemas.institutional_investor import (
    InstitutionalInvestorAdjacentResponse,
    InstitutionalInvestorDetail,
    InstitutionalInvestorListResponse,
    InstitutionalInvestorProfileResponse,
    InvestorContactItem,
)
from app.services.auth import ensure_feature, get_current_user
from app.services.contact_discovery.orchestrator import discover_investor_contact
from app.services.contacts import (
    ApolloLookupError,
    ContactEnrichmentUnavailableError,
    ContactNotFoundError,
    NoEmailForLookupError,
)
from app.services.institutional_investors import InstitutionalInvestorRepository
from app.services.investor_contacts import InvestorContactService


router = APIRouter(prefix="/institutional-investors")
repository = InstitutionalInvestorRepository()
investor_contact_service = InvestorContactService()


def _require_institutional_investors(
    user: AuthenticatedUser = Depends(get_current_user),
) -> AuthenticatedUser:
    ensure_feature(user, INSTITUTIONAL_INVESTORS)
    return user


def _parse_csv_list(values: list[str] | None) -> list[str]:
    """Split repeated query params with embedded CSVs into a flat list.

    Mirrors ``investment_advisors._parse_csv_list``.
    """

    if not values:
        return []
    parsed: list[str] = []
    for value in values:
        parsed.extend(part.strip() for part in value.split(",") if part.strip())
    return parsed


@router.get("", response_model=InstitutionalInvestorListResponse)
async def list_institutional_investors(
    search: str | None = Query(default=None, alias="q"),
    state: list[str] | None = Query(default=None),
    status_filter: list[str] | None = Query(default=None, alias="status"),
    min_total_aum: float | None = Query(default=None, ge=0),
    max_total_aum: float | None = Query(default=None, ge=0),
    filed_13f_after: date | None = Query(default=None),
    filed_13f_before: date | None = Query(default=None),
    only_with_advisor_link: bool | None = Query(default=None),
    sort_by: str = Query(default="total_aum"),
    sort_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=25, ge=1, le=100),
    _: AuthenticatedUser = Depends(_require_institutional_investors),
    db: AsyncSession = Depends(get_db_session),
) -> InstitutionalInvestorListResponse:
    if (
        min_total_aum is not None
        and max_total_aum is not None
        and min_total_aum > max_total_aum
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="min_total_aum must be less than or equal to max_total_aum.",
        )
    if (
        filed_13f_after is not None
        and filed_13f_before is not None
        and filed_13f_after > filed_13f_before
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="filed_13f_after must be on or before filed_13f_before.",
        )

    return await repository.list_institutional_investors(
        db,
        search=search,
        states=_parse_csv_list(state),
        statuses=_parse_csv_list(status_filter),
        min_total_aum=min_total_aum,
        max_total_aum=max_total_aum,
        filed_13f_after=filed_13f_after,
        filed_13f_before=filed_13f_before,
        only_with_advisor_link=only_with_advisor_link,
        sort_by=sort_by,
        sort_dir=sort_dir,
        page=page,
        limit=limit,
    )


@router.get("/states", response_model=list[str])
async def list_institutional_investor_states(
    _: AuthenticatedUser = Depends(_require_institutional_investors),
    db: AsyncSession = Depends(get_db_session),
) -> list[str]:
    return await repository.list_states(db)


@router.get(
    "/{investor_id}/favorite-lists",
    response_model=list[FavoriteListWithMembership],
)
async def get_institutional_investor_favorite_lists(
    investor_id: int,
    current_user: AuthenticatedUser = Depends(_require_institutional_investors),
    db: AsyncSession = Depends(get_db_session),
) -> list[FavoriteListWithMembership]:
    """Return the calling user's lists with an ``is_member`` flag for ``investor_id``.

    Mirror of ``GET /investment-advisors/{advisor_id}/favorite-lists``.
    Depends on the polymorphism extension migration that adds
    ``institutional_investor_id`` to ``favorite_list_item``.
    """

    investor_check = await db.execute(
        select(InstitutionalInvestor.id).where(InstitutionalInvestor.id == investor_id)
    )
    if investor_check.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Investor not found."
        )

    item_count_sq = (
        select(
            FavoriteListItem.list_id.label("list_id"),
            func.count(FavoriteListItem.id).label("count"),
        )
        .group_by(FavoriteListItem.list_id)
        .subquery()
    )
    is_member_expr = (
        exists()
        .where(
            FavoriteListItem.list_id == FavoriteList.id,
            FavoriteListItem.institutional_investor_id == investor_id,
        )
        .label("is_member")
    )
    stmt = (
        select(
            FavoriteList,
            func.coalesce(item_count_sq.c.count, 0).label("item_count"),
            is_member_expr,
        )
        .outerjoin(item_count_sq, FavoriteList.id == item_count_sq.c.list_id)
        .where(FavoriteList.user_id == current_user.id)
        .order_by(FavoriteList.is_default.desc(), FavoriteList.created_at.asc())
    )
    rows = (await db.execute(stmt)).all()
    return [
        FavoriteListWithMembership(
            id=fl.id,
            name=fl.name,
            is_default=fl.is_default,
            item_count=int(count),
            created_at=fl.created_at,
            is_member=bool(is_member),
        )
        for fl, count, is_member in rows
    ]


@router.get(
    "/{investor_id}/adjacent",
    response_model=InstitutionalInvestorAdjacentResponse,
)
async def get_institutional_investor_adjacent(
    investor_id: int,
    _: AuthenticatedUser = Depends(_require_institutional_investors),
    db: AsyncSession = Depends(get_db_session),
) -> InstitutionalInvestorAdjacentResponse:
    """Prev/next investor id for the detail page's Next button.

    Fallback path when the FE has no return-envelope reconstructing the
    user's filtered/sorted list. Walks the default ``name ASC NULLS
    LAST, id ASC`` order.
    """

    prev_id, next_id = await repository.get_adjacent(db, investor_id)
    return InstitutionalInvestorAdjacentResponse(prev_id=prev_id, next_id=next_id)


@router.get("/{investor_id}", response_model=InstitutionalInvestorDetail)
async def get_institutional_investor(
    investor_id: int,
    _: AuthenticatedUser = Depends(_require_institutional_investors),
    db: AsyncSession = Depends(get_db_session),
) -> InstitutionalInvestorDetail:
    investor = await repository.get_institutional_investor(db, investor_id)
    if investor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Institutional investor {investor_id} not found.",
        )
    return InstitutionalInvestorDetail.model_validate(investor)


@router.get(
    "/{investor_id}/profile",
    response_model=InstitutionalInvestorProfileResponse,
)
async def get_institutional_investor_profile(
    investor_id: int,
    _: AuthenticatedUser = Depends(_require_institutional_investors),
    db: AsyncSession = Depends(get_db_session),
) -> InstitutionalInvestorProfileResponse:
    """Aggregate detail-page response.

    Skeleton ships ``contacts`` and ``filings`` as empty arrays until the
    ingestion pipelines write to ``investor_contacts`` and
    ``investor_filings``. ``advisor_id`` on the embedded investor is the
    cross-link the FE uses to render the "View as Investment Advisor"
    deep link.
    """

    investor = await repository.get_institutional_investor(db, investor_id)
    if investor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Institutional investor {investor_id} not found.",
        )

    contacts = await repository.list_investor_contacts(db, investor_id)
    filings = await repository.list_investor_filings(db, investor_id)

    return InstitutionalInvestorProfileResponse(
        investor=InstitutionalInvestorDetail.model_validate(investor),
        contacts=contacts,
        filings=filings,
        is_favorited=False,
    )


# ── Enrichment (PDL-driven, mirrors the BD endpoint) ──────────────────


class InvestorEnrichOfficerRequest(BaseModel):
    """One officer the FE wants discovered via the multi-provider chain.

    ``type="person"`` requires ``first_name`` + ``last_name``; ``title``
    is optional but preserved on the resulting row so the UI can render
    the source role alongside the provider-found email / phone.

    ``type="organization"`` requires ``org_name`` (defaults to the
    investor's own name when omitted).
    """

    type: str = Field(pattern="^(person|organization)$")
    first_name: str | None = None
    last_name: str | None = None
    org_name: str | None = None
    title: str | None = None


class InvestorEnrichRequestBody(BaseModel):
    officers: list[InvestorEnrichOfficerRequest] = Field(default_factory=list)


@router.post("/{investor_id}/enrich", response_model=list[InvestorContactItem])
async def enrich_institutional_investor_contacts(
    investor_id: int,
    body: InvestorEnrichRequestBody | None = Body(default=None),
    _: AuthenticatedUser = Depends(_require_institutional_investors),
    db: AsyncSession = Depends(get_db_session),
) -> list[InvestorContactItem]:
    """Enrich investor contacts via the multi-provider discovery chain.

    Unlike the BD endpoint there's no Phase-1 Apollo company search for
    investors -- Apollo coverage for pure-13F filers is weak and we don't
    have a precedent company-search service. Phase 2 runs each requested
    officer through ``pdl -> apollo_match -> hunter -> snov`` in order
    via ``discover_investor_contact``. An empty officers list returns the
    existing contacts unchanged (no enrich attempted).
    """
    investor = await repository.get_institutional_investor(db, investor_id)
    if investor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Institutional investor not found.",
        )

    existing = await repository.list_investor_contacts(db, investor_id)
    officers = list(body.officers) if body else []
    if not officers:
        return [InvestorContactItem.model_validate(c) for c in existing]

    domain = _resolve_investor_domain(investor)
    existing_names = {_normalise_name(c.name) for c in existing}
    discovered = 0
    for officer in officers:
        entity = _officer_to_investor_entity(officer, investor, domain)
        if entity is None:
            continue
        if _normalise_name(entity["cache_name"]) in existing_names:
            continue
        row = await discover_investor_contact(
            entity, investor_id=investor.id, session=db
        )
        if row is not None:
            discovered += 1
            existing_names.add(_normalise_name(row.name))

    if discovered:
        await db.commit()
        existing = await repository.list_investor_contacts(db, investor_id)
    return [InvestorContactItem.model_validate(c) for c in existing]


@router.post(
    "/{investor_id}/contacts/{contact_id}/find-phone",
    response_model=InvestorContactItem,
)
async def find_phone_for_investor_contact(
    investor_id: int,
    contact_id: int,
    _: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> InvestorContactItem:
    """Per-row PDL (Apollo fallback) phone lookup for an InvestorContact.

    Mirrors ``POST /api/v1/broker-dealers/{id}/contacts/{contact_id}/find-phone``:
    PDL is tried first; on miss / hard error / no key the Apollo path
    runs as a fallback. PDL errors are silenced (graceful degradation);
    Apollo's transient errors surface as 502.
    """
    contact = await db.get(InvestorContact, contact_id)
    if contact is None or contact.investor_id != investor_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Contact not found."
        )

    try:
        updated = await investor_contact_service.find_phone_for_contact(db, contact_id)
    except ContactNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except NoEmailForLookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except ContactEnrichmentUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except ApolloLookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=f"apollo: {exc}"
        ) from exc

    return InvestorContactItem.model_validate(updated)


def _normalise_name(name: str | None) -> str:
    return (name or "").strip().lower()


def _resolve_investor_domain(investor: InstitutionalInvestor) -> str | None:
    """Extract a bare ``example.com`` domain from the firm's website."""
    website = (investor.website or "").strip()
    if not website:
        return None
    candidate = website
    if "://" in candidate:
        candidate = candidate.split("://", 1)[1]
    candidate = candidate.split("/", 1)[0].strip().lower()
    if candidate.startswith("www."):
        candidate = candidate[4:]
    return candidate or None


def _officer_to_investor_entity(
    officer: InvestorEnrichOfficerRequest,
    investor: InstitutionalInvestor,
    domain: str | None,
) -> dict[str, object] | None:
    """Translate the FE officer payload into the orchestrator's entity shape."""
    if officer.type == "person":
        first = (officer.first_name or "").strip()
        last = (officer.last_name or "").strip()
        if not first or not last:
            return None
        return {
            "type": "person",
            "first_name": first,
            "last_name": last,
            "org_name": investor.name,
            "title": officer.title,
            "domain": domain,
            "cache_name": f"{first} {last}",
        }
    org_name = (officer.org_name or investor.name or "").strip()
    if not org_name:
        return None
    return {
        "type": "organization",
        "first_name": None,
        "last_name": None,
        "org_name": org_name,
        "title": officer.title,
        "domain": domain,
        "cache_name": org_name,
    }
