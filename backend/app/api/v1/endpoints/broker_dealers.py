from __future__ import annotations

from datetime import datetime, time, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db_session
from app.models.broker_dealer import BrokerDealer
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
from app.services.contacts import ExecutiveContactService
from app.schemas.pipeline import ClearingArrangementsResponse
from app.services.alerts import AlertRepository
from app.services.auth import get_current_user
from app.services.broker_dealers import BrokerDealerRepository
from app.services.classification import apply_classification_to_all
from app.services.contacts import ContactEnrichmentUnavailableError, ExecutiveContactService
from app.services.finra import FinraService
from app.services.finra_pdf_service import (
    FinraPdfFetchError,
    FinraPdfNotFound,
    fetch_and_cache_brokercheck_pdf,
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
    list_mode: str = Query(default="primary", alias="list", pattern="^(primary|alternative|all)$"),
    sort_by: str = Query(default="name"),
    sort_dir: str = Query(default="asc", pattern="^(asc|desc)$"),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=25, ge=1, le=100),
    _: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> BrokerDealerListResponse:
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
) -> FileResponse:
    """Stream the firm's latest X-17A-5 (FOCUS) PDF.

    Reuses the existing PdfDownloaderService cache at PDF_CACHE_DIR/{cik}-{accession}.pdf.
    First request may take ~5-10s to fetch from SEC; subsequent requests are instant.
    """
    from app.services.pdf_downloader import PdfDownloaderService  # deferred to avoid circular

    broker_dealer = await repository.get_broker_dealer(db, broker_dealer_id)
    if broker_dealer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Broker-dealer not found.")

    downloader = PdfDownloaderService()
    try:
        record = await downloader.download_latest_x17a5_pdf(broker_dealer)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not fetch FOCUS report from SEC: {exc}",
        ) from exc

    if record is None or not record.local_document_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No FOCUS report available for this firm.",
        )

    filename = f"{broker_dealer.crd_number or broker_dealer.id}-focus-report.pdf"
    return FileResponse(
        path=record.local_document_path,
        media_type="application/pdf",
        filename=filename,
    )


@router.get("/{broker_dealer_id}/brokercheck.pdf")
async def download_brokercheck_pdf(
    broker_dealer_id: int,
    _: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> FileResponse:
    """Stream the firm's FINRA BrokerCheck Detailed Report PDF.

    On-demand fetch with disk cache at PDF_CACHE_DIR/finra/{crd}.pdf. First
    click takes ~2-5s to hit files.brokercheck.finra.org; subsequent clicks
    serve from cache.
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
        cache_path = await fetch_and_cache_brokercheck_pdf(broker_dealer.crd_number)
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

    return FileResponse(
        path=str(cache_path),
        media_type="application/pdf",
        filename=f"{broker_dealer.crd_number}-brokercheck.pdf",
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


@router.post("/{broker_dealer_id}/enrich", response_model=list[ExecutiveContactItem])
async def enrich_broker_dealer_contacts(
    broker_dealer_id: int,
    _: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> list[ExecutiveContactItem]:
    broker_dealer = await repository.get_broker_dealer(db, broker_dealer_id)
    if broker_dealer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Broker-dealer not found.")
    try:
        contacts = await contact_service.enrich_contacts(db, broker_dealer)
    except ContactEnrichmentUnavailableError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    return [ExecutiveContactItem.model_validate(item) for item in contacts]


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


@router.get("/{broker_dealer_id}/profile", response_model=BrokerDealerProfileResponse)
async def get_broker_dealer_profile(
    broker_dealer_id: int,
    _: AuthenticatedUser = Depends(get_current_user),
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

    return BrokerDealerProfileResponse(
        broker_dealer=BrokerDealerDetail.model_validate(broker_dealer),
        financials=[FinancialMetricItem.model_validate(item) for item in financials],
        clearing_arrangements=clearing_arrangements,
        introducing_arrangements=introducing_arrangements,
        industry_arrangements=industry_arrangements,
        recent_alerts=recent_alerts,
        filing_history=filing_history[:20],
        executive_contacts=[ExecutiveContactItem.model_validate(item) for item in executive_contacts],
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
