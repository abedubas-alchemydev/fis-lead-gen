from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.schemas.auth import AuthenticatedUser
from app.schemas.favorites import FavoriteListResponse
from app.services.auth import get_current_user
from app.services.user_lists import list_favorites

router = APIRouter(prefix="/favorites")


@router.get("", response_model=FavoriteListResponse)
async def get_favorites(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> FavoriteListResponse:
    """Return the calling user's favorites, newest first.

    Pagination matches the master-list convention (limit/offset). The service
    layer owns the sort order so clients can't request something the covering
    index wasn't built for.
    """
    items, total = await list_favorites(db, current_user.id, limit=limit, offset=offset)
    return FavoriteListResponse(items=items, total=total, limit=limit, offset=offset)
