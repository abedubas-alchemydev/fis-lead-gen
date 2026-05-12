from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.schemas.auth import AuthenticatedUser
from app.schemas.investment_advisor import (
    AdvisoryActivityCount,
    ClientTypeCount,
    InvestmentAdvisorDetail,
    InvestmentAdvisorListResponse,
    InvestmentAdvisorProfileResponse,
)
from app.services.auth import get_current_user
from app.services.investment_advisors import InvestmentAdvisorRepository


router = APIRouter(prefix="/investment-advisors")
repository = InvestmentAdvisorRepository()


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
    _: AuthenticatedUser = Depends(get_current_user),
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
    _: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> list[str]:
    return await repository.list_states(db)


@router.get("/advisory-activities", response_model=list[AdvisoryActivityCount])
async def list_advisory_activities(
    _: AuthenticatedUser = Depends(get_current_user),
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
    _: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> list[ClientTypeCount]:
    """Distinct Form ADV Item 5.D client categories with per-type counts."""

    rows = await repository.list_client_types(db)
    return [ClientTypeCount(type=row["type"], count=int(row["count"])) for row in rows]


@router.get("/{advisor_id}", response_model=InvestmentAdvisorDetail)
async def get_investment_advisor(
    advisor_id: int,
    _: AuthenticatedUser = Depends(get_current_user),
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
    _: AuthenticatedUser = Depends(get_current_user),
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
