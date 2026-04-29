from __future__ import annotations

import base64
from datetime import date, datetime, time, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db_session
from app.models.broker_dealer import BrokerDealer
from app.models.favorite_list import FavoriteList, FavoriteListItem
from app.schemas.auth import AuthenticatedUser
from app.schemas.broker_dealer import (
    BrokerDealerDetail,
    BrokerDealerListResponse,
    BrokerDealerProfileResponse,
    DeficiencyStatusSummary,
    ExecutiveContactItem,
    FocusCeoExtractionResponse,
    IntroducingArrangementItem,
    FilingHistoryItem,
    FinancialMetricItem,
    FinancialMetricsResponse,
    RegistrationComplianceSummary,
)
from app.schemas.favorite_list import FavoriteListWithMembership
from app.schemas.favorites import FavoriteResponse
from app.services.contacts import ExecutiveContactService
from app.schemas.pipeline import ClearingArrangementsResponse
from app.services.alerts import AlertRepository
from app.services.auth import get_current_user
from app.services.broker_dealers import BrokerDealerRepository
from app.services.user_lists import (
    add_favorite,
    is_favorited,
    record_visit,
    remove_favorite,
)
from app.services.classification import apply_classification_to_all
from app.services.contact_discovery.orchestrator import discover_contact
from app.services.contacts import ContactEnrichmentUnavailableError, ExecutiveContactService
from app.services.finra import FinraService
from app.services.finra_pdf_service import (
    FinraPdfFetchError,
    FinraPdfNotFound,
    fetch_brokercheck_pdf,
)
from app.services.focus_ceo_extraction import FocusCeoExtractionService
from app.services.service_models import FinraBrokerDealerRecord

router = APIRouter(prefix="/broker-dealers")
repository = BrokerDealerRepository()
alert_repository = AlertRepository()
contact_service = ExecutiveContactService()
finra_service = FinraService()
focus_ceo_service = FocusCeoExtractionService()


def _parse_states(state: list[str] | None) -> list[str]:
    if not state:
        return []

    parsed: list[str] = []
    for value in state:
        parsed.extend(part.strip() for part in value.split(",") if part.strip())
    return parsed


@router.get("", response_model=BrokerDealerListResponse)
async def list_broker_dealers(
    search: str | None = Query(default=None),
    state: list[str] | None = Query(default=None),
    status_filter: list[str] | None = Query(default=None, alias="status"),
    health_filter: list[str] | None = Query(default=None, alias="health"),
    lead_priority_filter: list[str] | None = Query(default=None, alias="lead_priority"),
    clearing_partner_filter: list[str] | None = Query(default=None, alias="clearing_partner"),
    clearing_type_filter: list[str] | None = Query(default=None, alias="clearing_type"),
    types_of_business_filter: list[str] | None = Query(default=None, alias="types_of_business"),
    min_net_capital: float | None = Query(default=None, ge=0),
    max_net_capital: float | None = Query(default=None, ge=0),
    registered_after: date | None = Query(default=None),
    registered_before: date | None = Query(default=None),
    list_mode: str = Query(default="primary", alias="list", pattern="^(primary|alternative|all)$"),
    sort_by: str = Query(default="name"),
    sort_dir: str = Query(default="asc", pattern="^(asc|desc)$"),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=25, ge=1, le=100),
    _: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> BrokerDealerListResponse:
    if (
        min_net_capital is not None
        and max_net_capital is not None
        and min_net_capital > max_net_capital
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="min_net_capital must be less than or equal to max_net_capital.",
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

    return await repository.list_broker_dealers(
        db,
        search=search,
        states=_parse_states(state),
        statuses=_parse_states(status_filter),
        health_statuses=_parse_states(health_filter),
        lead_priorities=_parse_states(lead_priority_filter),
        clearing_partners=_parse_states(clearing_partner_filter),
        clearing_types=_parse_states(clearing_type_filter),
        types_of_business=_parse_states(types_of_business_filter),
        min_net_capital=min_net_capital,
        max_net_capital=max_net_capital,
        registered_after=registered_after,
        registered_before=registered_before,
        list_mode=list_mode,
        sort_by=sort_by,
        sort_dir=sort_dir,
        page=page,
        limit=limit,
    )


@router.get("/states", response_model=list[str])
async def list_broker_dealer_states(
    _: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> list[str]:
    return await repository.list_states(db)


@router.get("/clearing-partners", response_model=list[str])
async def list_clearing_partners(
    _: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> list[str]:
    return await repository.list_clearing_partners(db)


@router.get("/types-of-business", response_model=list[dict])
async def list_types_of_business(
    _: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> list[dict]:
    """Distinct types-of-business across all firms with per-type counts.

    Fuels the multi-select filter on the master list. Shape: `[{type, count}, ...]`.
    """
    return await repository.list_types_of_business(db)


@router.get("/{broker_dealer_id}/focus-report.pdf")
async def download_focus_report_pdf(
    broker_dealer_id: int,
    _: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> Response:
    """Stream the firm's latest X-17A-5 (FOCUS) PDF.

    Downloads the latest filing from SEC EDGAR into a per-request tempdir
    and serves the bytes directly back to the browser via ``Response``. The
    persistent PDF cache that previously sat at ``PDF_CACHE_DIR`` was
    removed in Sprint 2 task #20 (it had grown to ~9 GB on the container).
    Each click costs one fresh SEC fetch (~5-10s) — acceptable UX for a
    rare on-demand action.
    """
    from app.services.pdf_downloader import PdfDownloaderService, pdf_tempdir  # deferred to avoid circular

    broker_dealer = await repository.get_broker_dealer(db, broker_dealer_id)
    if broker_dealer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Broker-dealer not found.")

    downloader = PdfDownloaderService()
    with pdf_tempdir(prefix="focus_report_endpoint_") as tmp_dir:
        try:
            record = await downloader.download_latest_x17a5_pdf(broker_dealer, tmp_dir)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Could not fetch FOCUS report from SEC: {exc}",
            ) from exc

        if record is None or not record.bytes_base64:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No FOCUS report available for this firm.",
            )

        # Decode while still inside the tempdir context. The Response carries
        # the bytes in memory, so the file on disk can be wiped on ``with``
        # exit without breaking the response stream the way FileResponse
        # would (FileResponse opens the file at write-time, not at endpoint
        # return-time).
        pdf_bytes = base64.b64decode(record.bytes_base64)

    filename = f"{broker_dealer.crd_number or broker_dealer.id}-focus-report.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/{broker_dealer_id}/brokercheck.pdf")
async def download_brokercheck_pdf(
    broker_dealer_id: int,
    _: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> Response:
    """Stream the firm's FINRA BrokerCheck Detailed Report PDF.

    On-demand fetch from files.brokercheck.finra.org (~2-5s). The bytes
    flow straight from the upstream response into the browser via
    ``Response`` — no disk involved. The persistent FINRA cache that
    previously sat at ``PDF_CACHE_DIR/finra`` was removed in Sprint 2
    task #20.
    """
    broker_dealer = await repository.get_broker_dealer(db, broker_dealer_id)
    if broker_dealer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Broker-dealer not found.")
    if not broker_dealer.crd_number:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This firm has no CRD number on file; BrokerCheck PDF is not fetchable.",
        )

    try:
        pdf_bytes = await fetch_brokercheck_pdf(broker_dealer.crd_number)
    except FinraPdfNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="FINRA has no Detailed Report PDF for this CRD.",
        ) from exc
    except FinraPdfFetchError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not fetch BrokerCheck PDF from FINRA: {exc}",
        ) from exc

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename={broker_dealer.crd_number}-brokercheck.pdf"
        },
    )


@router.get("/{broker_dealer_id}", response_model=BrokerDealerDetail)
async def get_broker_dealer(
    broker_dealer_id: int,
    _: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> BrokerDealerDetail:
    broker_dealer = await repository.get_broker_dealer(db, broker_dealer_id)
    if broker_dealer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Broker-dealer not found.")
    return broker_dealer


@router.get("/{broker_dealer_id}/financials", response_model=FinancialMetricsResponse)
async def get_broker_dealer_financials(
    broker_dealer_id: int,
    _: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> FinancialMetricsResponse:
    broker_dealer = await repository.get_broker_dealer(db, broker_dealer_id)
    if broker_dealer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Broker-dealer not found.")

    return FinancialMetricsResponse(items=await repository.get_financial_metrics(db, broker_dealer_id))


@router.get("/{broker_dealer_id}/clearing-arrangements", response_model=ClearingArrangementsResponse)
async def get_broker_dealer_clearing_arrangements(
    broker_dealer_id: int,
    _: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> ClearingArrangementsResponse:
    broker_dealer = await repository.get_broker_dealer(db, broker_dealer_id)
    if broker_dealer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Broker-dealer not found.")

    return ClearingArrangementsResponse(items=await repository.list_clearing_arrangements(db, broker_dealer_id))


@router.get("/{broker_dealer_id}/adjacent")
async def get_adjacent_broker_dealers(
    broker_dealer_id: int,
    _: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, int | None]:
    """Return the previous and next broker-dealer IDs for navigation arrows."""
    from sqlalchemy import select as sel

    prev_stmt = (
        sel(BrokerDealer.id)
        .where(BrokerDealer.id < broker_dealer_id)
        .order_by(BrokerDealer.id.desc())
        .limit(1)
    )
    next_stmt = (
        sel(BrokerDealer.id)
        .where(BrokerDealer.id > broker_dealer_id)
        .order_by(BrokerDealer.id.asc())
        .limit(1)
    )
    prev_id = (await db.execute(prev_stmt)).scalar_one_or_none()
    next_id = (await db.execute(next_stmt)).scalar_one_or_none()
    return {"prev_id": prev_id, "next_id": next_id}


class EnrichOfficerRequest(BaseModel):
    """One officer the frontend wants discovered via the multi-provider chain.

    ``type="person"`` requires ``first_name`` and ``last_name``; ``title`` is
    optional but preserved on the resulting row so the UI can render the
    FINRA-derived role alongside the provider-found email / phone.

    ``type="organization"`` requires ``org_name`` (defaults to the firm's
    own name if omitted). Used for sole-member / parent-holding officer rows
    that aren't human beings.
    """

    type: str = Field(pattern="^(person|organization)$")
    first_name: str | None = None
    last_name: str | None = None
    org_name: str | None = None
    title: str | None = None


class EnrichRequestBody(BaseModel):
    officers: list[EnrichOfficerRequest] = Field(default_factory=list)


@router.post("/{broker_dealer_id}/enrich", response_model=list[ExecutiveContactItem])
async def enrich_broker_dealer_contacts(
    broker_dealer_id: int,
    body: EnrichRequestBody | None = Body(default=None),
    _: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> list[ExecutiveContactItem]:
    """Enrich executive contacts for a firm.

    Phase 1: run the existing Apollo-based company search (cheap and often
    catches officers Apollo already has). Phase 2: for each officer the
    frontend sent that didn't get matched in phase 1, run the multi-provider
    discovery chain (Apollo match -> Hunter -> Snov) anchored on the firm's
    website domain.

    Backward compat: when no ``officers`` list is provided the endpoint
    behaves exactly as before -- pure company-level search, no per-officer
    fan-out. That lets the frontend roll out the richer body incrementally.
    """
    broker_dealer = await repository.get_broker_dealer(db, broker_dealer_id)
    if broker_dealer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Broker-dealer not found.")

    try:
        contacts = await contact_service.enrich_contacts(db, broker_dealer)
    except ContactEnrichmentUnavailableError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    officers = list(body.officers) if body else []
    if officers:
        domain = _resolve_domain(broker_dealer)
        existing_names = {_normalise_name(contact.name) for contact in contacts}
        discovered = 0
        for officer in officers:
            entity = _officer_to_entity(officer, broker_dealer, domain)
            if entity is None:
                continue
            if _normalise_name(entity["cache_name"]) in existing_names:
                continue
            row = await discover_contact(entity, bd_id=broker_dealer.id, session=db)
            if row is not None:
                discovered += 1
                existing_names.add(_normalise_name(row.name))
        if discovered:
            await db.commit()
            contacts = await contact_service.list_contacts(db, broker_dealer.id)

    return [ExecutiveContactItem.model_validate(item) for item in contacts]


def _resolve_domain(broker_dealer: BrokerDealer) -> str | None:
    """Extract a bare ``example.com`` domain from the firm's website.

    Handles ``https://www.example.com/path?x=1`` -> ``example.com`` and
    leaves an already-bare ``example.com`` alone. Returns ``None`` when the
    firm has no website on file so downstream providers can skip cleanly.
    """
    website = (broker_dealer.website or "").strip()
    if not website:
        return None
    candidate = website
    if "://" in candidate:
        candidate = candidate.split("://", 1)[1]
    candidate = candidate.split("/", 1)[0].strip().lower()
    if candidate.startswith("www."):
        candidate = candidate[4:]
    return candidate or None


def _officer_to_entity(
    officer: EnrichOfficerRequest,
    broker_dealer: BrokerDealer,
    domain: str | None,
) -> dict[str, object] | None:
    """Translate an EnrichOfficerRequest into the orchestrator's entity shape.

    Returns ``None`` when the officer is missing the fields its type requires
    (a person without first+last, an org entry that resolves to an empty
    name) so the endpoint can skip it without a provider round-trip.
    """
    if officer.type == "person":
        first = (officer.first_name or "").strip()
        last = (officer.last_name or "").strip()
        if not first or not last:
            return None
        return {
            "type": "person",
            "first_name": first,
            "last_name": last,
            "org_name": broker_dealer.name,
            "title": officer.title,
            "domain": domain,
            "cache_name": f"{first} {last}",
        }

    # organisation
    org_name = (officer.org_name or broker_dealer.name or "").strip()
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


def _normalise_name(name: str | None) -> str:
    return (name or "").strip().lower()


@router.post("/{broker_dealer_id}/extract-focus-ceo", response_model=FocusCeoExtractionResponse)
async def extract_focus_ceo(
    broker_dealer_id: int,
    _: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> FocusCeoExtractionResponse:
    """On-demand extraction of CEO contact info and net capital from the latest FOCUS Report PDF.

    Downloads the most recent X-17A-5 filing for this broker-dealer, sends it to
    Gemini for structured extraction, and persists the CEO as an ExecutiveContact
    with source="focus_report".
    """
    broker_dealer = await repository.get_broker_dealer(db, broker_dealer_id)
    if broker_dealer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Broker-dealer not found.")

    result = await focus_ceo_service.extract(db, broker_dealer)
    await db.commit()

    return FocusCeoExtractionResponse(
        ceo_name=result.ceo_name,
        ceo_title=result.ceo_title,
        ceo_phone=result.ceo_phone,
        ceo_email=result.ceo_email,
        net_capital=result.net_capital,
        report_date=result.report_date,
        source_pdf_url=result.source_pdf_url,
        confidence_score=result.confidence_score,
        extraction_status=result.extraction_status,
        extraction_notes=result.extraction_notes,
    )


@router.post("/{broker_dealer_id}/favorite", response_model=FavoriteResponse)
async def favorite_broker_dealer(
    broker_dealer_id: int,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> FavoriteResponse:
    """Favorite a broker-dealer for the calling user.

    Idempotent: a second call returns 200 with the original ``favorited_at``.
    """
    broker_dealer = await repository.get_broker_dealer(db, broker_dealer_id)
    if broker_dealer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Broker-dealer not found.")

    row = await add_favorite(db, current_user.id, broker_dealer_id)
    return FavoriteResponse(favorited=True, favorited_at=row.created_at)


@router.delete("/{broker_dealer_id}/favorite", status_code=status.HTTP_204_NO_CONTENT)
async def unfavorite_broker_dealer(
    broker_dealer_id: int,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> None:
    """Remove a favorite. Idempotent: 204 even when the row wasn't there."""
    broker_dealer = await repository.get_broker_dealer(db, broker_dealer_id)
    if broker_dealer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Broker-dealer not found.")

    await remove_favorite(db, current_user.id, broker_dealer_id)


@router.get(
    "/{firm_id}/favorite-lists",
    response_model=list[FavoriteListWithMembership],
)
async def get_firm_favorite_lists(
    firm_id: int,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> list[FavoriteListWithMembership]:
    """Return the calling user's lists with an ``is_member`` flag for ``firm_id``.

    Powers the FE list-picker so each list can render a checked state without
    a per-list round-trip. ``item_count`` is computed via the same outer-join
    sub-aggregate that ``GET /favorite-lists`` uses so the list-picker can
    show ``Watchlist A · 3 firms`` next to the checkbox. Default list first,
    then by ``created_at`` ascending — matches the sidebar ordering.
    """
    firm_check = await db.execute(
        select(BrokerDealer.id).where(BrokerDealer.id == firm_id)
    )
    if firm_check.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Firm not found.")

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
            FavoriteListItem.broker_dealer_id == firm_id,
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


@router.post("/{broker_dealer_id}/visit", status_code=status.HTTP_204_NO_CONTENT)
async def visit_broker_dealer(
    broker_dealer_id: int,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> None:
    """Record a detail-page visit.

    Fired fire-and-forget by the frontend on mount. Upserts the ``user_visit``
    row: first call sets ``visit_count=1`` and both timestamps to ``now()``;
    subsequent calls bump ``visit_count`` and ``last_visited_at`` while the
    original ``first_visited_at`` is preserved.
    """
    broker_dealer = await repository.get_broker_dealer(db, broker_dealer_id)
    if broker_dealer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Broker-dealer not found.")

    await record_visit(db, current_user.id, broker_dealer_id)


@router.get("/{broker_dealer_id}/profile", response_model=BrokerDealerProfileResponse)
async def get_broker_dealer_profile(
    broker_dealer_id: int,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> BrokerDealerProfileResponse:
    broker_dealer = await repository.get_broker_dealer(db, broker_dealer_id)
    if broker_dealer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Broker-dealer not found.")

    financials = await repository.get_financial_metrics(db, broker_dealer_id)
    clearing_arrangements = await repository.list_clearing_arrangements(db, broker_dealer_id)
    introducing_arrangements = await repository.list_introducing_arrangements(db, broker_dealer_id)
    industry_arrangements = await repository.list_industry_arrangements(db, broker_dealer_id)
    executive_contacts = await repository.get_executive_contacts(db, broker_dealer_id)
    recent_alerts = (
        await alert_repository.list_alerts(
            db,
            form_types=[],
            priorities=[],
            is_read=None,
            broker_dealer_id=broker_dealer_id,
            page=1,
            limit=8,
        )
    ).items

    filing_history: list[FilingHistoryItem] = []
    for alert in recent_alerts:
        filing_history.append(
            FilingHistoryItem(
                label=alert.form_type,
                filed_at=alert.filed_at,
                summary=alert.summary,
                source_filing_url=alert.source_filing_url,
                priority=alert.priority,
            )
        )

    for metric in financials:
        filing_history.append(
            FilingHistoryItem(
                label="FOCUS Report",
                filed_at=datetime.combine(metric.report_date, time(hour=17), tzinfo=timezone.utc),
                summary="Financial report used for net capital and YoY growth calculations.",
                source_filing_url=metric.source_filing_url,
                priority="medium",
            )
        )

    for arrangement in clearing_arrangements:
        report_date = arrangement.report_date
        if report_date is None:
            continue
        filing_history.append(
            FilingHistoryItem(
                label="X-17A-5 Annual Report",
                filed_at=datetime.combine(report_date, time(hour=16), tzinfo=timezone.utc),
                summary=(
                    f"Clearing arrangement extracted as {arrangement.clearing_partner or 'Unknown'} "
                    f"({arrangement.clearing_type or 'unknown'})."
                ),
                source_filing_url=arrangement.source_filing_url,
                priority="medium",
            )
        )

    filing_history.sort(key=lambda item: item.filed_at, reverse=True)

    favorited, favorited_at = await is_favorited(db, current_user.id, broker_dealer_id)

    return BrokerDealerProfileResponse(
        broker_dealer=BrokerDealerDetail.model_validate(broker_dealer),
        financials=[FinancialMetricItem.model_validate(item) for item in financials],
        clearing_arrangements=clearing_arrangements,
        introducing_arrangements=introducing_arrangements,
        industry_arrangements=industry_arrangements,
        recent_alerts=recent_alerts,
        filing_history=filing_history[:20],
        executive_contacts=[ExecutiveContactItem.model_validate(item) for item in executive_contacts],
        is_favorited=favorited,
        favorited_at=favorited_at,
        registration_compliance=RegistrationComplianceSummary(
            registration_status=broker_dealer.status,
            registration_date=broker_dealer.registration_date,
            sec_file_number=broker_dealer.sec_file_number,
            crd_number=broker_dealer.crd_number,
            branch_count=broker_dealer.branch_count,
            business_type=broker_dealer.business_type,
            filings_index_url=broker_dealer.filings_index_url,
        ),
        deficiency_status=DeficiencyStatusSummary(
            is_deficient=broker_dealer.is_deficient,
            latest_deficiency_filed_at=broker_dealer.latest_deficiency_filed_at,
            message=(
                "Form 17a-11 deficiency notice detected. This firm belongs in the Alternative List."
                if broker_dealer.is_deficient
                else "No active Form 17a-11 deficiency notice is currently tracked."
            ),
        ),
    )


@router.post("/{broker_dealer_id}/health-check")
async def trigger_health_check(
    broker_dealer_id: int,
    _: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, object]:
    """Triggered Enrichment / Health Check (Revision 2.2).

    When a user clicks a firm, the system performs a real-time health check
    to determine whether the contact information, FINRA detail data, or net
    capital must be refreshed from the latest filing.
    """
    broker_dealer = await repository.get_broker_dealer(db, broker_dealer_id)
    if broker_dealer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Broker-dealer not found.")

    changes: list[str] = []

    # Re-fetch FINRA detail if the firm has a CRD number
    if broker_dealer.crd_number:
        record = FinraBrokerDealerRecord(
            crd_number=broker_dealer.crd_number,
            name=broker_dealer.name,
            sec_file_number=broker_dealer.sec_file_number,
            registration_status=broker_dealer.status,
            branch_count=broker_dealer.branch_count,
            address_city=broker_dealer.city,
            address_state=broker_dealer.state,
            business_type=broker_dealer.business_type,
        )
        enriched = await finra_service.enrich_with_detail([record])
        if enriched:
            enriched_record = enriched[0]
            if enriched_record.types_of_business and enriched_record.types_of_business != broker_dealer.types_of_business:
                broker_dealer.types_of_business = enriched_record.types_of_business
                changes.append("types_of_business")
            if enriched_record.direct_owners and enriched_record.direct_owners != broker_dealer.direct_owners:
                broker_dealer.direct_owners = enriched_record.direct_owners
                changes.append("direct_owners")
            if enriched_record.executive_officers and enriched_record.executive_officers != broker_dealer.executive_officers:
                broker_dealer.executive_officers = enriched_record.executive_officers
                changes.append("executive_officers")
            if enriched_record.firm_operations_text and enriched_record.firm_operations_text != broker_dealer.firm_operations_text:
                broker_dealer.firm_operations_text = enriched_record.firm_operations_text
                changes.append("firm_operations_text")
            if enriched_record.website and enriched_record.website != broker_dealer.website:
                broker_dealer.website = enriched_record.website
                changes.append("website")

    # Re-apply classification logic
    from app.services.classification import determine_clearing_classification, classify_niche_restricted

    new_classification = determine_clearing_classification(broker_dealer.firm_operations_text)
    if broker_dealer.clearing_classification != new_classification:
        broker_dealer.clearing_classification = new_classification
        changes.append("clearing_classification")

    new_niche = classify_niche_restricted(broker_dealer.types_of_business)
    if broker_dealer.is_niche_restricted != new_niche:
        broker_dealer.is_niche_restricted = new_niche
        changes.append("is_niche_restricted")

    await db.commit()

    return {
        "broker_dealer_id": broker_dealer_id,
        "fields_refreshed": changes,
        "total_changes": len(changes),
    }
