"""Admin-only Doxie Usage / cost endpoints (`/doxie-usage`).

Read-only observability over the Doxie chatbot's token spend, tool-call
volume, latency, and an *estimated* USD cost. Two GETs — an account-wide
summary and a paginated per-user aggregate. Both gated to admins; the
dashboard lives under Settings on the FE. Mirrors the
``extraction_analytics`` endpoint shape (module-singleton repository,
local ``_ensure_admin``).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.schemas.auth import AuthenticatedUser
from app.schemas.doxie_usage import (
    DoxieUsageSummary,
    DoxieUserUsageListResponse,
)
from app.services.auth import get_current_user
from app.services.doxie_usage import DoxieUsageRepository

router = APIRouter(prefix="/doxie-usage")
repository = DoxieUsageRepository()


def _ensure_admin(current_user: AuthenticatedUser) -> None:
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required."
        )


@router.get("/summary", response_model=DoxieUsageSummary)
async def get_doxie_usage_summary(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> DoxieUsageSummary:
    """Account-wide Doxie token totals, tool-call volume, and estimated cost."""
    _ensure_admin(current_user)
    return await repository.summary(db)


@router.get("/users", response_model=DoxieUserUsageListResponse)
async def list_doxie_usage_users(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> DoxieUserUsageListResponse:
    """Paginated per-user Doxie usage — token sums, estimated cost, latency."""
    _ensure_admin(current_user)
    return await repository.list_users(db, page=page, limit=limit)
