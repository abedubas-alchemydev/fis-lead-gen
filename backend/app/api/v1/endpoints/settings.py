from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.schemas.auth import AuthenticatedUser
from app.schemas.pipeline import CompetitorProviderItem, CompetitorProvidersResponse
from app.schemas.settings import (
    CompetitorProviderCreate,
    CompetitorProviderUpdate,
    DataRefreshResponse,
    ScoringSettingsItem,
    ScoringSettingsUpdate,
)
from app.services.auth import get_current_user
from app.services.broker_dealers import BrokerDealerRepository
from app.services.classification import apply_classification_to_all
from app.services.competitors import CompetitorProviderService
from app.services.filing_monitor import FilingMonitorService
from app.services.finra import FinraService
from app.services.pipeline import ClearingPipelineService
from app.services.settings import SettingsService

router = APIRouter(prefix="/settings")
repository = BrokerDealerRepository()
competitor_service = CompetitorProviderService()
settings_service = SettingsService()
filing_monitor_service = FilingMonitorService()
pipeline_service = ClearingPipelineService()
finra_service = FinraService()


def _ensure_admin(current_user: AuthenticatedUser) -> None:
    if current_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required.")


@router.get("/scoring", response_model=ScoringSettingsItem)
async def get_scoring_settings(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> ScoringSettingsItem:
    _ensure_admin(current_user)
    return ScoringSettingsItem.model_validate(await settings_service.get_scoring_settings(db))


@router.put("/scoring", response_model=ScoringSettingsItem)
async def update_scoring_settings(
    payload: ScoringSettingsUpdate,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> ScoringSettingsItem:
    _ensure_admin(current_user)
    total = (
        payload.net_capital_growth_weight
        + payload.clearing_arrangement_weight
        + payload.financial_health_weight
        + payload.registration_recency_weight
    )
    if total != 100:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Scoring weights must total 100.")
    setting = await settings_service.update_scoring_settings(
        db,
        net_capital_growth_weight=payload.net_capital_growth_weight,
        clearing_arrangement_weight=payload.clearing_arrangement_weight,
        financial_health_weight=payload.financial_health_weight,
        registration_recency_weight=payload.registration_recency_weight,
    )
    await repository.refresh_lead_scores(db)
    await db.commit()
    return ScoringSettingsItem.model_validate(setting)


@router.get("/competitors", response_model=CompetitorProvidersResponse)
async def list_competitors(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> CompetitorProvidersResponse:
    return CompetitorProvidersResponse(items=await repository.list_competitor_providers(db))


@router.post("/competitors", response_model=CompetitorProviderItem)
async def create_competitor(
    payload: CompetitorProviderCreate,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> CompetitorProviderItem:
    _ensure_admin(current_user)
    competitor = await settings_service.create_competitor(
        db,
        name=payload.name,
        aliases=payload.aliases,
        priority=payload.priority,
    )
    await repository.refresh_competitor_flags(db)
    await repository.refresh_lead_scores(db)
    await db.commit()
    return CompetitorProviderItem.model_validate(competitor)


@router.put("/competitors/{competitor_id}", response_model=CompetitorProviderItem)
async def update_competitor(
    competitor_id: int,
    payload: CompetitorProviderUpdate,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> CompetitorProviderItem:
    _ensure_admin(current_user)
    competitor = await settings_service.update_competitor(
        db,
        competitor_id,
        aliases=payload.aliases,
        priority=payload.priority,
        is_active=payload.is_active,
    )
    if competitor is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Competitor not found.")
    await repository.refresh_competitor_flags(db)
    await repository.refresh_lead_scores(db)
    await db.commit()
    return CompetitorProviderItem.model_validate(competitor)


@router.post("/refresh-data", response_model=DataRefreshResponse)
async def refresh_data(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> DataRefreshResponse:
    _ensure_admin(current_user)
    await competitor_service.seed_defaults(db)
    clearing_run = await pipeline_service.run(db, trigger_source=f"settings_refresh:{current_user.email}")
    filing_run = await filing_monitor_service.run(db, trigger_source=f"settings_refresh:{current_user.email}")
    await repository.refresh_lead_scores(db)
    await db.commit()
    refreshed_count = await repository.count_all(db)
    return DataRefreshResponse(
        filing_monitor_run_id=filing_run.id,
        clearing_pipeline_run_id=clearing_run.id,
        refreshed_broker_dealers=refreshed_count,
    )


@router.post("/refresh-finra-details")
async def refresh_finra_details(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, object]:
    """Bi-Monthly FINRA refresh (Revision 2.2).

    Re-runs the FINRA detail enrichment to capture changes in Direct Owners,
    Executive Officers, and Business Types.  Also re-applies the classification
    logic gates (self-clearing, introducing, niche/restricted).
    """
    _ensure_admin(current_user)

    # Fetch all existing broker-dealers that have CRD numbers
    from sqlalchemy import select
    from app.models.broker_dealer import BrokerDealer

    broker_dealers = (
        await db.execute(
            select(BrokerDealer)
            .where(BrokerDealer.crd_number.is_not(None))
            .order_by(BrokerDealer.id.asc())
        )
    ).scalars().all()

    # Build lightweight FINRA records for enrichment
    from app.services.service_models import FinraBrokerDealerRecord

    finra_records = [
        FinraBrokerDealerRecord(
            crd_number=bd.crd_number,
            name=bd.name,
            sec_file_number=bd.sec_file_number,
            registration_status=bd.status,
            branch_count=bd.branch_count,
            address_city=bd.city,
            address_state=bd.state,
            business_type=bd.business_type,
        )
        for bd in broker_dealers
        if bd.crd_number
    ]

    # Enrich from FINRA detail endpoint
    enriched = await finra_service.enrich_with_detail(finra_records)

    # Map enriched data back to DB records
    crd_to_enriched = {r.crd_number: r for r in enriched}
    updated_count = 0
    for bd in broker_dealers:
        enriched_record = crd_to_enriched.get(bd.crd_number)
        if enriched_record is None:
            continue
        changed = False
        if enriched_record.types_of_business and enriched_record.types_of_business != bd.types_of_business:
            bd.types_of_business = enriched_record.types_of_business
            changed = True
        if enriched_record.direct_owners and enriched_record.direct_owners != bd.direct_owners:
            bd.direct_owners = enriched_record.direct_owners
            changed = True
        if enriched_record.executive_officers and enriched_record.executive_officers != bd.executive_officers:
            bd.executive_officers = enriched_record.executive_officers
            changed = True
        if enriched_record.firm_operations_text and enriched_record.firm_operations_text != bd.firm_operations_text:
            bd.firm_operations_text = enriched_record.firm_operations_text
            changed = True
        if enriched_record.website and enriched_record.website != bd.website:
            bd.website = enriched_record.website
            changed = True
        if changed:
            updated_count += 1

    await db.flush()

    # Re-apply classification gates
    classified_count = await apply_classification_to_all(db)
    await repository.refresh_lead_scores(db)
    await db.commit()

    return {
        "total_firms_scanned": len(finra_records),
        "firms_updated": updated_count,
        "firms_reclassified": classified_count,
    }
