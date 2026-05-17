from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.models.favorite_list import FavoriteList, FavoriteListItem
from app.models.investment_advisor import InvestmentAdvisor
from app.schemas.auth import AuthenticatedUser
from app.schemas.favorite_list import FavoriteListWithMembership
from app.schemas.investment_advisor import (
    AdvisoryActivityCount,
    ClientTypeCount,
    InvestmentAdvisorDetail,
    InvestmentAdvisorListResponse,
    InvestmentAdvisorProfileResponse,
)
from app.core.feature_permissions import INVESTMENT_ADVISORS
from app.services.auth import ensure_feature, get_current_user
from app.services.investment_advisors import InvestmentAdvisorRepository


router = APIRouter(prefix="/investment-advisors")
repository = InvestmentAdvisorRepository()


def _require_investment_advisors(
    user: AuthenticatedUser = Depends(get_current_user),
) -> AuthenticatedUser:
    ensure_feature(user, INVESTMENT_ADVISORS)
    return user


def _parse_csv_list(values: list[str] | None) -> list[str]:
    """Split repeated query params with embedded CSVs into a flat list.

    Mirrors ``broker_dealers._parse_states`` so the FE can use either
    ``?states=NY&states=CA`` or ``?states=NY,CA`` interchangeably.
    """

    if not values:
        return []
    parsed: list[str] = []
    for value in values:
        parsed.extend(part.strip() for part in value.split(",") if part.strip())
    return parsed


@router.get("", response_model=InvestmentAdvisorListResponse)
async def list_investment_advisors(
    search: str | None = Query(default=None, alias="q"),
    state: list[str] | None = Query(default=None),
    status_filter: list[str] | None = Query(default=None, alias="status"),
    advisory_activities_filter: list[str] | None = Query(
        default=None, alias="advisory_activities"
    ),
    client_types_filter: list[str] | None = Query(default=None, alias="client_types"),
    files_13f: bool | None = Query(default=True),
    min_regulatory_aum: float | None = Query(default=None, ge=0),
    max_regulatory_aum: float | None = Query(default=None, ge=0),
    registered_after: date | None = Query(default=None),
    registered_before: date | None = Query(default=None),
    sort_by: str = Query(default="regulatory_aum"),
    sort_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=25, ge=1, le=100),
    _: AuthenticatedUser = Depends(_require_investment_advisors),
    db: AsyncSession = Depends(get_db_session),
) -> InvestmentAdvisorListResponse:
    if (
        min_regulatory_aum is not None
        and max_regulatory_aum is not None
        and min_regulatory_aum > max_regulatory_aum
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="min_regulatory_aum must be less than or equal to max_regulatory_aum.",
        )
    if (
        registered_after is not None
        and registered_before is not None
        and registered_after > registered_before
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="registered_after must be on or before registered_before.",
        )

    return await repository.list_investment_advisors(
        db,
        search=search,
        states=_parse_csv_list(state),
        statuses=_parse_csv_list(status_filter),
        advisory_activities=_parse_csv_list(advisory_activities_filter),
        client_types=_parse_csv_list(client_types_filter),
        files_13f=files_13f,
        min_regulatory_aum=min_regulatory_aum,
        max_regulatory_aum=max_regulatory_aum,
        registered_after=registered_after,
        registered_before=registered_before,
        sort_by=sort_by,
        sort_dir=sort_dir,
        page=page,
        limit=limit,
    )


@router.get("/states", response_model=list[str])
async def list_investment_advisor_states(
    _: AuthenticatedUser = Depends(_require_investment_advisors),
    db: AsyncSession = Depends(get_db_session),
) -> list[str]:
    return await repository.list_states(db)


@router.get("/advisory-activities", response_model=list[AdvisoryActivityCount])
async def list_advisory_activities(
    _: AuthenticatedUser = Depends(_require_investment_advisors),
    db: AsyncSession = Depends(get_db_session),
) -> list[AdvisoryActivityCount]:
    """Distinct Form ADV Item 5.G activities with per-type counts.

    Fuels the multi-select filter on the advisor master list — same
    shape and semantics as ``GET /broker-dealers/types-of-business``.
    """

    rows = await repository.list_advisory_activities(db)
    return [AdvisoryActivityCount(type=row["type"], count=int(row["count"])) for row in rows]


@router.get("/client-types", response_model=list[ClientTypeCount])
async def list_client_types(
    _: AuthenticatedUser = Depends(_require_investment_advisors),
    db: AsyncSession = Depends(get_db_session),
) -> list[ClientTypeCount]:
    """Distinct Form ADV Item 5.D client categories with per-type counts."""

    rows = await repository.list_client_types(db)
    return [ClientTypeCount(type=row["type"], count=int(row["count"])) for row in rows]


@router.get(
    "/{advisor_id}/favorite-lists",
    response_model=list[FavoriteListWithMembership],
)
async def get_advisor_favorite_lists(
    advisor_id: int,
    current_user: AuthenticatedUser = Depends(_require_investment_advisors),
    db: AsyncSession = Depends(get_db_session),
) -> list[FavoriteListWithMembership]:
    """Return the calling user's lists with an ``is_member`` flag for ``advisor_id``.

    Mirror of ``GET /broker-dealers/{firm_id}/favorite-lists`` — same query
    shape, just keyed on ``advisor_id`` instead of ``broker_dealer_id``.
    Powers the FE list-picker on advisor-list rows + the advisor-detail
    page.
    """

    advisor_check = await db.execute(
        select(InvestmentAdvisor.id).where(InvestmentAdvisor.id == advisor_id)
    )
    if advisor_check.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Advisor not found."
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
            FavoriteListItem.advisor_id == advisor_id,
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


@router.get("/{advisor_id}", response_model=InvestmentAdvisorDetail)
async def get_investment_advisor(
    advisor_id: int,
    _: AuthenticatedUser = Depends(_require_investment_advisors),
    db: AsyncSession = Depends(get_db_session),
) -> InvestmentAdvisorDetail:
    advisor = await repository.get_investment_advisor(db, advisor_id)
    if advisor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Investment advisor {advisor_id} not found.",
        )
    return InvestmentAdvisorDetail.model_validate(advisor)


@router.get("/{advisor_id}/profile", response_model=InvestmentAdvisorProfileResponse)
async def get_investment_advisor_profile(
    advisor_id: int,
    _: AuthenticatedUser = Depends(_require_investment_advisors),
    db: AsyncSession = Depends(get_db_session),
) -> InvestmentAdvisorProfileResponse:
    """Aggregate detail-page response.

    PR 1 ships only ``advisor`` populated; ``contacts`` and ``filings``
    return as empty arrays until the PR 3 / PR 4 ingestion pipelines
    write to ``advisor_contacts`` and ``advisor_filings``.
    """

    advisor = await repository.get_investment_advisor(db, advisor_id)
    if advisor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Investment advisor {advisor_id} not found.",
        )

    contacts = await repository.list_advisor_contacts(db, advisor_id)
    filings = await repository.list_advisor_filings(db, advisor_id)

    return InvestmentAdvisorProfileResponse(
        advisor=InvestmentAdvisorDetail.model_validate(advisor),
        contacts=contacts,
        filings=filings,
        is_favorited=False,
    )
