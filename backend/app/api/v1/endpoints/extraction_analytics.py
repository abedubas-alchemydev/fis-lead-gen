"""Admin-only Extraction Analytics endpoints (`/extraction-analytics`).

Read-only observability over what each external enrichment provider
extracted, grouped by firm. Three GETs — a per-provider summary, a
paginated firm-grouped list with per-provider counts, and a per-firm
drill-down to the actual extracted values. All gated to admins; the
page lives under Settings on the FE.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.schemas.auth import AuthenticatedUser
from app.schemas.extraction_analytics import (
    FirmExtractionDetailResponse,
    FirmExtractionListResponse,
    ProviderSummaryResponse,
)
from app.services.auth import get_current_user
from app.services.extraction_analytics import (
    ALL_PROVIDERS,
    ExtractionAnalyticsRepository,
)

router = APIRouter(prefix="/extraction-analytics")
repository = ExtractionAnalyticsRepository()

FirmType = Literal["broker_dealer", "advisor", "institutional_investor"]


def _ensure_admin(current_user: AuthenticatedUser) -> None:
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required."
        )


@router.get("/summary", response_model=ProviderSummaryResponse)
async def get_extraction_summary(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> ProviderSummaryResponse:
    """Per-provider rollup across contacts, discovered emails, and websites."""
    _ensure_admin(current_user)
    return await repository.summary(db)


@router.get("/firms", response_model=FirmExtractionListResponse)
async def list_extraction_firms(
    firm_type: FirmType | None = Query(None),
    provider: str | None = Query(None),
    q: str | None = Query(None, max_length=255),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> FirmExtractionListResponse:
    """Paginated firms with at least one extraction, each carrying
    per-provider counts. Optionally filtered to one provider / firm kind /
    name search."""
    _ensure_admin(current_user)
    if provider is not None and provider not in ALL_PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown provider. Expected one of: {sorted(ALL_PROVIDERS)}.",
        )
    return await repository.list_firms(
        db,
        firm_type=firm_type,
        provider=provider,
        q=q,
        page=page,
        limit=limit,
    )


@router.get(
    "/firms/{firm_type}/{firm_id}/values",
    response_model=FirmExtractionDetailResponse,
)
async def get_firm_extraction_values(
    firm_type: FirmType,
    firm_id: int,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> FirmExtractionDetailResponse:
    """Every extracted value for one firm, each tagged with its provider."""
    _ensure_admin(current_user)
    result = await repository.firm_values(db, firm_type=firm_type, firm_id=firm_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="firm_not_found"
        )
    return result
