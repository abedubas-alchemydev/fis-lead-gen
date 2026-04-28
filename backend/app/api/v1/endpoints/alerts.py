from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.schemas.alerts import (
    AlertListResponse,
    AlertReadResponse,
    AlertsBulkReadResponse,
    FilingMonitorRunResponse,
)
from app.schemas.auth import AuthenticatedUser
from app.services.alerts import AlertRepository
from app.services.auth import get_current_user
from app.services.filing_monitor import FilingMonitorService

router = APIRouter(prefix="/alerts")
repository = AlertRepository()
filing_monitor_service = FilingMonitorService()


def _parse_values(values: list[str] | None) -> list[str]:
    if not values:
        return []

    parsed: list[str] = []
    for value in values:
        parsed.extend(part.strip() for part in value.split(",") if part.strip())
    return parsed


@router.get("", response_model=AlertListResponse)
async def list_alerts(
    form_type: list[str] | None = Query(default=None),
    priority: list[str] | None = Query(default=None),
    read: bool | None = Query(default=None),
    broker_dealer_id: int | None = Query(default=None),
    category: Literal["form_bd", "deficiency", "all"] | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    _: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> AlertListResponse:
    return await repository.list_alerts(
        db,
        form_types=_parse_values(form_type),
        priorities=_parse_values(priority),
        is_read=read,
        broker_dealer_id=broker_dealer_id,
        category=category,
        page=page,
        limit=limit,
    )


@router.patch("/{alert_id}/read", response_model=AlertReadResponse)
async def mark_alert_read(
    alert_id: int,
    _: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> AlertReadResponse:
    alert = await repository.mark_alert_read(db, alert_id, is_read=True)
    if alert is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found.")
    return AlertReadResponse(id=alert.id, is_read=alert.is_read)


@router.post("/mark-all-read", response_model=AlertsBulkReadResponse)
async def mark_all_alerts_read(
    form_type: list[str] | None = Query(default=None),
    priority: list[str] | None = Query(default=None),
    broker_dealer_id: int | None = Query(default=None),
    _: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> AlertsBulkReadResponse:
    updated_count = await repository.mark_all_read(
        db,
        form_types=_parse_values(form_type),
        priorities=_parse_values(priority),
        broker_dealer_id=broker_dealer_id,
    )
    return AlertsBulkReadResponse(updated_count=updated_count)


@router.post("/monitor/run", response_model=FilingMonitorRunResponse)
async def run_filing_monitor(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> FilingMonitorRunResponse:
    if current_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required.")

    run = await filing_monitor_service.run(db, trigger_source="manual")
    return FilingMonitorRunResponse(
        run_id=run.id,
        total_items=run.total_items,
        success_count=run.success_count,
        failure_count=run.failure_count,
        status=run.status,
    )
