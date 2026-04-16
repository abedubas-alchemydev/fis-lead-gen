from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.models.broker_dealer import BrokerDealer
from app.schemas.auth import AuthenticatedUser
from app.schemas.stats import ClearingDistributionResponse, DashboardStatsResponse, TotalBrokerDealersResponse
from app.services.alerts import AlertRepository
from app.services.auth import get_current_user
from app.services.broker_dealers import BrokerDealerRepository

router = APIRouter(prefix="/stats")
repository = BrokerDealerRepository()
alert_repository = AlertRepository()


@router.get("/total-bds", response_model=TotalBrokerDealersResponse)
async def get_total_broker_dealers(
    _: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> TotalBrokerDealersResponse:
    return TotalBrokerDealersResponse(total_bds=await repository.count_all(db))


@router.get("", response_model=DashboardStatsResponse)
async def get_dashboard_stats(
    _: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> DashboardStatsResponse:
    total_active_bds = await repository.count_all(db)
    thirty_days_ago = date.today() - timedelta(days=30)

    new_bds_stmt = select(func.count(BrokerDealer.id)).where(BrokerDealer.registration_date >= thirty_days_ago)
    new_bds_30_days = int((await db.execute(new_bds_stmt)).scalar_one())
    deficiency_alerts = await alert_repository.count_deficiency_firms(db)
    high_value_leads = await repository.count_hot_leads(db)

    return DashboardStatsResponse(
        total_active_bds=total_active_bds,
        new_bds_30_days=new_bds_30_days,
        deficiency_alerts=deficiency_alerts,
        high_value_leads=high_value_leads,
    )


@router.get("/clearing-distribution", response_model=ClearingDistributionResponse)
async def get_clearing_distribution(
    _: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> ClearingDistributionResponse:
    return ClearingDistributionResponse(items=await repository.get_clearing_distribution(db))
