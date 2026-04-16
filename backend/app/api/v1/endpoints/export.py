from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.schemas.auth import AuthenticatedUser
from app.schemas.export import ExportCsvResponse, ExportPreviewResponse
from app.services.auth import get_current_user
from app.services.broker_dealers import BrokerDealerRepository
from app.services.export_service import EXPORT_ROW_LIMIT, ExportService

router = APIRouter(prefix="/export")
repository = BrokerDealerRepository()
export_service = ExportService()


def _parse_values(values: list[str] | None) -> list[str]:
    if not values:
        return []
    parsed: list[str] = []
    for value in values:
        parsed.extend(part.strip() for part in value.split(",") if part.strip())
    return parsed


@router.get("/preview", response_model=ExportPreviewResponse)
async def get_export_preview(
    search: str | None = Query(default=None),
    state: list[str] | None = Query(default=None),
    status_filter: list[str] | None = Query(default=None, alias="status"),
    health_filter: list[str] | None = Query(default=None, alias="health"),
    lead_priority_filter: list[str] | None = Query(default=None, alias="lead_priority"),
    clearing_partner_filter: list[str] | None = Query(default=None, alias="clearing_partner"),
    clearing_type_filter: list[str] | None = Query(default=None, alias="clearing_type"),
    list_mode: str = Query(default="primary", alias="list", pattern="^(primary|alternative|all)$"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> ExportPreviewResponse:
    response = await repository.list_broker_dealers(
        db,
        search=search,
        states=_parse_values(state),
        statuses=_parse_values(status_filter),
        health_statuses=_parse_values(health_filter),
        lead_priorities=_parse_values(lead_priority_filter),
        clearing_partners=_parse_values(clearing_partner_filter),
        clearing_types=_parse_values(clearing_type_filter),
        list_mode=list_mode,
        sort_by="lead_score",
        sort_dir="desc",
        page=1,
        limit=1,
    )
    return ExportPreviewResponse(
        matching_records=response.meta.total,
        export_limit=EXPORT_ROW_LIMIT,
        remaining_exports_today=await export_service.get_remaining_exports_today(db, current_user.id),
        requested_records=min(response.meta.total, EXPORT_ROW_LIMIT),
    )


@router.post("", response_model=ExportCsvResponse)
async def export_csv(
    search: str | None = Query(default=None),
    state: list[str] | None = Query(default=None),
    status_filter: list[str] | None = Query(default=None, alias="status"),
    health_filter: list[str] | None = Query(default=None, alias="health"),
    lead_priority_filter: list[str] | None = Query(default=None, alias="lead_priority"),
    clearing_partner_filter: list[str] | None = Query(default=None, alias="clearing_partner"),
    clearing_type_filter: list[str] | None = Query(default=None, alias="clearing_type"),
    list_mode: str = Query(default="primary", alias="list", pattern="^(primary|alternative|all)$"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> ExportCsvResponse:
    try:
        content, exported_records, remaining = await export_service.build_export(
            db,
            current_user=current_user,
            search=search,
            states=_parse_values(state),
            statuses=_parse_values(status_filter),
            health_statuses=_parse_values(health_filter),
            lead_priorities=_parse_values(lead_priority_filter),
            clearing_partners=_parse_values(clearing_partner_filter),
            clearing_types=_parse_values(clearing_type_filter),
            list_mode=list_mode,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return ExportCsvResponse(
        filename=f"deshorn-export-{timestamp}.csv",
        content=content,
        exported_records=exported_records,
        remaining_exports_today=remaining,
    )
