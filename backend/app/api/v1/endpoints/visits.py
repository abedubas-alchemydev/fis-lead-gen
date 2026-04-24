from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.schemas.auth import AuthenticatedUser
from app.schemas.visits import VisitListResponse
from app.services.auth import get_current_user
from app.services.user_lists import list_visits

router = APIRouter(prefix="/visits")


@router.get("", response_model=VisitListResponse)
async def get_visits(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> VisitListResponse:
    """Return the calling user's visit history, most-recent-first.

    Same pagination contract as ``GET /favorites``. Sort (``last_visited_at
    DESC``) is owned by the service layer so it can always honour the
    covering index.
    """
    items, total = await list_visits(db, current_user.id, limit=limit, offset=offset)
    return VisitListResponse(items=items, total=total, limit=limit, offset=offset)
