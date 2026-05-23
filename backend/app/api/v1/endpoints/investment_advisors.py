from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.models.favorite_list import FavoriteList, FavoriteListItem
from app.models.investment_advisor import InvestmentAdvisor
from app.models.pipeline_run import PipelineRun
from app.schemas.auth import AuthenticatedUser
from app.schemas.favorite_list import FavoriteListWithMembership
from app.schemas.investment_advisor import (
    AdvisoryActivityCount,
    ClientTypeCount,
    InvestmentAdvisorDetail,
    InvestmentAdvisorListResponse,
    InvestmentAdvisorProfileResponse,
    RefreshAdvisorRequest,
    RefreshAdvisorResponse,
)
from app.core.feature_permissions import INVESTMENT_ADVISORS
from app.services.advisor_refresh_orchestrator import (
    REFRESH_ADVISOR_ALL_PIPELINE_NAME,
    decide_pipelines,
    required_provider_keys,
    run_advisor_refresh,
)
from app.services.auth import ensure_feature, get_current_user
from app.services.edgar import EdgarService, build_edgar_filing_url
from app.services.investment_advisors import InvestmentAdvisorRepository
from app.services.pipeline_reaper import STALE_REFRESH_RUN_AGE
from app.services.user_lists import is_advisor_favorited

logger = logging.getLogger(__name__)

# Per-(user, advisor) cooldown for rage-click protection. Mirrors the
# BD endpoint's _REFRESH_ALL_COOLDOWN_SECONDS.
_REFRESH_ADVISOR_COOLDOWN_SECONDS = 30


def _is_recent_run(started_at: datetime | None) -> bool:
    """True if a run started recently enough to still be considered alive.

    Anything older than STALE_REFRESH_RUN_AGE is presumed orphaned (its
    in-process BackgroundTask was killed by an instance swap) and must not be
    treated as in-flight.
    """

    if started_at is None:
        return False
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    return started_at >= datetime.now(timezone.utc) - STALE_REFRESH_RUN_AGE


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


@router.get("/{advisor_id}/13f/latest")
async def redirect_to_latest_13f(
    advisor_id: int,
    _: AuthenticatedUser = Depends(_require_investment_advisors),
    db: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    """302 to the most recent 13F-HR primary document on SEC EDGAR.

    Resolves the latest 13F-HR at request time via ``EdgarService``
    (1-hour in-process cache) so we don't have to persist accession
    numbers. Mirrors the vault download pattern in ``vault_files.py``:
    redirect to the upstream URL and let the browser handle the bytes.
    """

    advisor = await db.get(InvestmentAdvisor, advisor_id)
    if advisor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Advisor not found."
        )
    if not advisor.files_13f or not advisor.cik:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No 13F filings on record for this advisor.",
        )

    filings = await EdgarService().list_all_filings_for_cik(advisor.cik)
    latest = next((f for f in filings if f.get("form") == "13F-HR"), None)
    if latest is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No 13F-HR filing found on EDGAR for this advisor.",
        )

    accession = latest.get("accession_number")
    primary_document = latest.get("primary_document")
    url = build_edgar_filing_url(
        advisor.cik,
        str(accession) if accession else None,
        str(primary_document) if primary_document else None,
    )
    if not url:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not build EDGAR filing URL.",
        )
    return RedirectResponse(url, status_code=302)


@router.get("/{advisor_id}/adjacent")
async def get_adjacent_advisors(
    advisor_id: int,
    _: AuthenticatedUser = Depends(_require_investment_advisors),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, int | None]:
    """Return the previous and next advisor IDs for the Next button.

    Walks the advisor master-list's default view: ``files_13f = TRUE``
    ordered by ``name ASC NULLS LAST, id ASC``. The frontend has a
    separate return-envelope path that walks whatever filter/sort the
    user actually had on screen; this endpoint is the fallback for
    direct-link / bookmark visits, so it should match what the user
    would see if they navigated to ``/advisor-list`` fresh.

    Mirror of ``GET /broker-dealers/{id}/adjacent`` -- same shape.
    """

    ordered_ids = (
        await db.execute(
            select(InvestmentAdvisor.id)
            .where(InvestmentAdvisor.files_13f.is_(True))
            .order_by(InvestmentAdvisor.name.asc().nullslast(), InvestmentAdvisor.id.asc())
        )
    ).scalars().all()

    try:
        idx = ordered_ids.index(advisor_id)
    except ValueError:
        # Advisor exists but is filtered out of the default 13F-only view.
        # Surface no neighbours rather than guess.
        return {"prev_id": None, "next_id": None}

    prev_id = ordered_ids[idx - 1] if idx > 0 else None
    next_id = ordered_ids[idx + 1] if idx < len(ordered_ids) - 1 else None
    return {"prev_id": prev_id, "next_id": next_id}


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
    current_user: AuthenticatedUser = Depends(_require_investment_advisors),
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
        is_favorited=await is_advisor_favorited(db, current_user.id, advisor_id),
    )


# ─── Per-advisor refresh-all orchestrator ─────────────────────────────────────
# Mirrors the BD endpoint at
# backend/app/api/v1/endpoints/broker_dealers.py:1481-1635. The FE calls this
# on every /advisor-list/{id} page load; the orchestrator's per-pipeline gates
# skip sub-pipelines whose target columns are already populated, so steady-
# state cost is near-zero. See
# backend/app/services/advisor_refresh_orchestrator.py for the per-pipeline
# semantics.

async def _run_advisor_refresh_background(
    parent_run_id: int,
    advisor_id: int,
    trigger_source: str,
    pipelines_to_run: tuple[str, ...],
    pipelines_to_skip: tuple[str, ...],
) -> None:
    """Defensive wrapper so an unhandled exception in the background task
    doesn't silently leave the parent PipelineRun stuck on ``queued``."""
    try:
        await run_advisor_refresh(
            parent_run_id,
            advisor_id,
            trigger_source=trigger_source,
            pipelines_to_run=pipelines_to_run,
            pipelines_to_skip=pipelines_to_skip,
        )
    except Exception:
        logger.exception(
            "advisor-refresh background task failed (parent_run_id=%s advisor_id=%s)",
            parent_run_id,
            advisor_id,
        )


@router.post(
    "/{advisor_id}/refresh-all",
    response_model=RefreshAdvisorResponse,
)
async def refresh_advisor_all(
    advisor_id: int,
    background_tasks: BackgroundTasks,
    response: Response,
    body: RefreshAdvisorRequest = Body(default_factory=RefreshAdvisorRequest),
    current_user: AuthenticatedUser = Depends(_require_investment_advisors),
    db: AsyncSession = Depends(get_db_session),
) -> RefreshAdvisorResponse:
    """One button per row -> fan out to whatever sub-pipelines this advisor
    actually needs.

    Inspects the IA record at request start and runs only the sub-pipelines
    whose target fields are still missing. If every gate fails, returns
    200 + ``status="skipped"`` immediately with no PipelineRun row and
    zero provider cost.

    Async: when at least one gate is open, returns 202 with the parent
    ``run_id`` and schedules a FastAPI BackgroundTask. The FE polls
    ``GET /api/v1/pipeline/run/{run_id}`` until terminal.
    """
    advisor = await db.get(InvestmentAdvisor, advisor_id)
    if advisor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Investment advisor not found.",
        )

    decision = decide_pipelines(advisor)

    if not decision.to_run:
        return RefreshAdvisorResponse(
            run_id=None,
            status="skipped",
            advisor_id=advisor_id,
            reason="Already complete.",
        )

    missing_keys = required_provider_keys(decision.to_run)
    if missing_keys:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Provider not configured: {', '.join(missing_keys)}.",
        )

    advisor_marker = f'"advisor_id": {advisor_id}'

    # A genuinely in-flight refresh-all already exists for this advisor.
    # Rather than 409 (which the browser surfaces as a red console error even
    # though the FE handles it cleanly), attach to the existing run and return
    # 202 with that run_id. The FE polls run_id identically whether the run was
    # just queued or already in flight, so the behavior is unchanged.
    #
    # A run only counts as in-flight if it started within STALE_REFRESH_RUN_AGE.
    # An older running/queued row is an orphan — its in-process BackgroundTask
    # was killed by an instance swap and it will never finalize — so we ignore
    # it and start a fresh run instead of trapping the page on a dead run. The
    # startup reaper marks such orphans failed; this guard handles the window
    # before the next restart.
    in_flight_stmt = (
        select(PipelineRun)
        .where(PipelineRun.pipeline_name == REFRESH_ADVISOR_ALL_PIPELINE_NAME)
        .where(PipelineRun.status.in_(("queued", "running")))
        .where(PipelineRun.notes.ilike(f"%{advisor_marker}%"))
        .order_by(PipelineRun.id.desc())
        .limit(1)
    )
    in_flight = (await db.execute(in_flight_stmt)).scalar_one_or_none()
    if in_flight is not None and _is_recent_run(in_flight.started_at):
        response.status_code = status.HTTP_202_ACCEPTED
        return RefreshAdvisorResponse(
            run_id=in_flight.id,
            status="in_flight",
            advisor_id=advisor_id,
            reason="A refresh-all run is already in flight for this advisor.",
        )

    # 429 — per-(user, advisor) cooldown so rage-clicks don't burn provider
    # credits twice. Same shape as BD's cooldown guard.
    cooldown_cutoff = datetime.now(timezone.utc) - timedelta(seconds=_REFRESH_ADVISOR_COOLDOWN_SECONDS)
    user_marker = f"manual_single:{current_user.email}"
    cooldown_stmt = (
        select(PipelineRun)
        .where(PipelineRun.pipeline_name == REFRESH_ADVISOR_ALL_PIPELINE_NAME)
        .where(PipelineRun.trigger_source == user_marker)
        .where(PipelineRun.notes.ilike(f"%{advisor_marker}%"))
        .where(PipelineRun.started_at >= cooldown_cutoff)
        .order_by(PipelineRun.id.desc())
        .limit(1)
    )
    recent = (await db.execute(cooldown_stmt)).scalar_one_or_none()
    if recent is not None:
        elapsed = (datetime.now(timezone.utc) - recent.started_at).total_seconds()
        retry_after = max(1, int(_REFRESH_ADVISOR_COOLDOWN_SECONDS - elapsed))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Refresh-all cooldown active. Try again in {retry_after}s.",
            headers={"Retry-After": str(retry_after)},
        )

    trigger_source = user_marker
    parent_run = PipelineRun(
        pipeline_name=REFRESH_ADVISOR_ALL_PIPELINE_NAME,
        trigger_source=trigger_source,
        status="queued",
        total_items=len(decision.to_run),
        processed_items=0,
        success_count=0,
        failure_count=0,
        notes=json.dumps(
            {
                "advisor_id": advisor_id,
                "stage": "queued",
                "scope": body.scope,
                "ran": list(decision.to_run),
                "skipped": list(decision.to_skip),
            }
        ),
    )
    db.add(parent_run)
    await db.commit()
    await db.refresh(parent_run)

    background_tasks.add_task(
        _run_advisor_refresh_background,
        parent_run.id,
        advisor_id,
        trigger_source,
        decision.to_run,
        decision.to_skip,
    )

    response.status_code = status.HTTP_202_ACCEPTED
    return RefreshAdvisorResponse(
        run_id=parent_run.id,
        status=parent_run.status,
        advisor_id=advisor_id,
        reason=None,
    )
