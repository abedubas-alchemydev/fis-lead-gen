"""Endpoints for custom favorites lists (#17 phases 1-2).

Phase 1 shipped GET only. Phase 2 adds the writable side
(POST/PUT/DELETE on the list itself plus POST/DELETE on its items).

The legacy ``POST /broker-dealers/{id}/favorite`` path still writes to the
``user_favorite`` safety-net table; that path is retained for one more
release cycle of soak before being dropped in a separate cleanup PR.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, status
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.models.broker_dealer import BrokerDealer
from app.models.favorite_list import FavoriteList, FavoriteListItem
from app.schemas.auth import AuthenticatedUser
from app.schemas.favorite_list import (
    FavoriteListCreate,
    FavoriteListItemCreate,
    FavoriteListItemResponse,
    FavoriteListResponse,
    FavoriteListUpdate,
    PaginatedFavoriteListItems,
)
from app.services.auth import get_current_user

router = APIRouter(prefix="/favorite-lists")

_DUPLICATE_NAME_DETAIL = "A list with that name already exists"


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


async def _get_owned_list(
    db: AsyncSession, list_id: int, user_id: str
) -> FavoriteList:
    """Return the list iff it belongs to ``user_id``; 404 otherwise.

    Same opaque ``favorite_list_not_found`` detail as phase 1 so a leaked
    list_id can't be used to enumerate other users' lists.
    """
    result = await db.execute(
        select(FavoriteList).where(
            FavoriteList.id == list_id,
            FavoriteList.user_id == user_id,
        )
    )
    favorite_list = result.scalar_one_or_none()
    if favorite_list is None:
        raise HTTPException(status_code=404, detail="favorite_list_not_found")
    return favorite_list


@router.post("", response_model=FavoriteListResponse, status_code=201)
async def create_favorite_list(
    payload: FavoriteListCreate,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> FavoriteListResponse:
    """Create a new (non-default) list owned by the calling user."""
    favorite_list = FavoriteList(
        user_id=current_user.id,
        name=payload.name,
        is_default=False,
    )
    db.add(favorite_list)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=400, detail=_DUPLICATE_NAME_DETAIL)
    await db.refresh(favorite_list)
    return FavoriteListResponse(
        id=favorite_list.id,
        name=favorite_list.name,
        is_default=favorite_list.is_default,
        item_count=0,
        created_at=favorite_list.created_at,
    )


@router.put("/{list_id}", response_model=FavoriteListResponse)
async def update_favorite_list(
    payload: FavoriteListUpdate,
    list_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> FavoriteListResponse:
    """Rename a non-default list owned by the calling user."""
    favorite_list = await _get_owned_list(db, list_id, current_user.id)
    if favorite_list.is_default:
        raise HTTPException(
            status_code=400, detail="The default list cannot be renamed"
        )

    favorite_list.name = payload.name
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=400, detail=_DUPLICATE_NAME_DETAIL)
    await db.refresh(favorite_list)

    count_stmt = select(func.count(FavoriteListItem.id)).where(
        FavoriteListItem.list_id == favorite_list.id
    )
    item_count = int((await db.execute(count_stmt)).scalar_one())
    return FavoriteListResponse(
        id=favorite_list.id,
        name=favorite_list.name,
        is_default=favorite_list.is_default,
        item_count=item_count,
        created_at=favorite_list.created_at,
    )


@router.delete("/{list_id}", status_code=204)
async def delete_favorite_list(
    list_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> Response:
    """Delete a non-default list (cascades items via FK)."""
    favorite_list = await _get_owned_list(db, list_id, current_user.id)
    if favorite_list.is_default:
        raise HTTPException(
            status_code=400, detail="The default list cannot be deleted"
        )
    await db.delete(favorite_list)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{list_id}/items", response_model=FavoriteListItemResponse)
async def add_item_to_favorite_list(
    payload: FavoriteListItemCreate,
    list_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> FavoriteListItemResponse:
    """Add a broker-dealer to a list owned by the calling user.

    Idempotent — re-POSTing the same ``broker_dealer_id`` returns the
    existing row instead of raising on the unique constraint, so the FE
    can fire the same call twice (double-click, retry) without a 500.
    """
    await _get_owned_list(db, list_id, current_user.id)

    bd_check = await db.execute(
        select(BrokerDealer.id, BrokerDealer.name).where(
            BrokerDealer.id == payload.broker_dealer_id
        )
    )
    bd_row = bd_check.first()
    if bd_row is None:
        raise HTTPException(status_code=404, detail="Firm not found")
    bd_id, bd_name = bd_row

    upsert = (
        pg_insert(FavoriteListItem)
        .values(list_id=list_id, broker_dealer_id=bd_id)
        .on_conflict_do_nothing(
            index_elements=["list_id", "broker_dealer_id"]
        )
        .returning(FavoriteListItem.id, FavoriteListItem.created_at)
    )
    inserted = (await db.execute(upsert)).first()
    if inserted is None:
        existing = await db.execute(
            select(FavoriteListItem.created_at).where(
                FavoriteListItem.list_id == list_id,
                FavoriteListItem.broker_dealer_id == bd_id,
            )
        )
        added_at = existing.scalar_one()
    else:
        added_at = inserted[1]
    await db.commit()

    return FavoriteListItemResponse(
        broker_dealer_id=bd_id,
        broker_dealer_name=bd_name,
        added_at=added_at,
    )


@router.delete("/{list_id}/items/{broker_dealer_id}", status_code=204)
async def remove_item_from_favorite_list(
    list_id: int = Path(..., ge=1),
    broker_dealer_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> Response:
    """Remove a firm from the list. 404 if it wasn't in the list."""
    await _get_owned_list(db, list_id, current_user.id)

    result = await db.execute(
        delete(FavoriteListItem).where(
            FavoriteListItem.list_id == list_id,
            FavoriteListItem.broker_dealer_id == broker_dealer_id,
        )
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="favorite_list_item_not_found")
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
