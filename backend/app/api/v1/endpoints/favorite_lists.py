"""Read-only endpoints for custom favorites lists (#17 phase 1).

Phase 1 ships GET only. POST/PUT/DELETE land in phase 2 — until then the
default 'Favorites' list (created by the 20260429_0019 migration) is the only
list a user can write to, and it does so via the legacy
``POST /broker-dealers/{id}/favorite`` path that still talks to ``user_favorite``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.models.broker_dealer import BrokerDealer
from app.models.favorite_list import FavoriteList, FavoriteListItem
from app.schemas.auth import AuthenticatedUser
from app.schemas.favorite_list import (
    FavoriteListItemResponse,
    FavoriteListResponse,
    PaginatedFavoriteListItems,
)
from app.services.auth import get_current_user

router = APIRouter(prefix="/favorite-lists")


@router.get("", response_model=list[FavoriteListResponse])
async def list_favorite_lists(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> list[FavoriteListResponse]:
    """Return the calling user's lists.

    Default list first, then the rest by ``created_at`` ascending so newly
    created lists land at the bottom of the FE sidebar.
    """
    item_count = (
        select(
            FavoriteListItem.list_id.label("list_id"),
            func.count(FavoriteListItem.id).label("count"),
        )
        .group_by(FavoriteListItem.list_id)
        .subquery()
    )
    stmt = (
        select(FavoriteList, func.coalesce(item_count.c.count, 0).label("item_count"))
        .outerjoin(item_count, FavoriteList.id == item_count.c.list_id)
        .where(FavoriteList.user_id == current_user.id)
        .order_by(FavoriteList.is_default.desc(), FavoriteList.created_at.asc())
    )
    rows = (await db.execute(stmt)).all()
    return [
        FavoriteListResponse(
            id=fl.id,
            name=fl.name,
            is_default=fl.is_default,
            item_count=int(count),
            created_at=fl.created_at,
        )
        for fl, count in rows
    ]


@router.get("/{list_id}/items", response_model=PaginatedFavoriteListItems)
async def list_favorite_list_items(
    list_id: int = Path(..., ge=1),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> PaginatedFavoriteListItems:
    """Return a page of items in a list owned by the calling user.

    404 if the list doesn't exist or belongs to another user — same shape so
    a leaked list_id doesn't reveal whether it's "missing" vs. "yours".
    """
    owner_check = await db.execute(
        select(FavoriteList.id).where(
            FavoriteList.id == list_id,
            FavoriteList.user_id == current_user.id,
        )
    )
    if owner_check.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="favorite_list_not_found")

    total_stmt = select(func.count(FavoriteListItem.id)).where(
        FavoriteListItem.list_id == list_id
    )
    total = int((await db.execute(total_stmt)).scalar_one())

    offset = (page - 1) * page_size
    data_stmt = (
        select(
            FavoriteListItem.broker_dealer_id,
            BrokerDealer.name,
            FavoriteListItem.created_at,
        )
        .join(BrokerDealer, BrokerDealer.id == FavoriteListItem.broker_dealer_id)
        .where(FavoriteListItem.list_id == list_id)
        .order_by(FavoriteListItem.created_at.desc(), FavoriteListItem.id.desc())
        .offset(offset)
        .limit(page_size)
    )
    rows = (await db.execute(data_stmt)).all()

    items = [
        FavoriteListItemResponse(
            broker_dealer_id=bd_id,
            broker_dealer_name=name,
            added_at=added_at,
        )
        for bd_id, name, added_at in rows
    ]
    return PaginatedFavoriteListItems(
        items=items, total=total, page=page, page_size=page_size
    )
