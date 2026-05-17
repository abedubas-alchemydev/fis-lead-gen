"""Endpoints for custom favorites lists (#17, all phases shipped).

Manages user-owned ``favorite_list`` rows and their ``favorite_list_item``
membership: GET/POST/PUT/DELETE on the lists, plus POST/DELETE on items.
The companion ``GET /broker-dealers/{id}/favorite-lists`` (in
``broker_dealers.py``) is the FE list-picker's data source. The legacy
``user_favorite`` table was dropped in 20260429_0021 (and again,
idempotently, in 20260429_0022); writes from the legacy POST path were
rewired onto ``favorite_list_item`` in PR #172.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, status
from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.models.broker_dealer import BrokerDealer
from app.models.favorite_list import FavoriteList, FavoriteListItem
from app.models.investment_advisor import InvestmentAdvisor
from app.schemas.auth import AuthenticatedUser
from app.schemas.favorite_list import (
    FavoriteListAdvisorItemBatchCreate,
    FavoriteListAdvisorItemCreate,
    FavoriteListAdvisorItemResponse,
    FavoriteListCreate,
    FavoriteListItemBatchCreate,
    FavoriteListItemBatchResponse,
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

    Returns broker-dealer and investment-advisor items in one stream, ordered
    by ``created_at desc`` then ``id desc``. Each row carries an
    ``entity_type`` discriminator plus the populated id/name pair for its
    kind (the other kind's columns are ``None``).

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
            FavoriteListItem.id,
            FavoriteListItem.broker_dealer_id,
            BrokerDealer.name.label("broker_dealer_name"),
            FavoriteListItem.advisor_id,
            InvestmentAdvisor.name.label("advisor_name"),
            FavoriteListItem.created_at,
        )
        .outerjoin(
            BrokerDealer, BrokerDealer.id == FavoriteListItem.broker_dealer_id
        )
        .outerjoin(
            InvestmentAdvisor,
            InvestmentAdvisor.id == FavoriteListItem.advisor_id,
        )
        .where(FavoriteListItem.list_id == list_id)
        .order_by(FavoriteListItem.created_at.desc(), FavoriteListItem.id.desc())
        .offset(offset)
        .limit(page_size)
    )
    rows = (await db.execute(data_stmt)).all()

    items: list[FavoriteListItemResponse] = []
    for _item_id, bd_id, bd_name, advisor_id, advisor_name, added_at in rows:
        if bd_id is not None:
            items.append(
                FavoriteListItemResponse(
                    entity_type="broker_dealer",
                    broker_dealer_id=bd_id,
                    broker_dealer_name=bd_name,
                    added_at=added_at,
                )
            )
        else:
            items.append(
                FavoriteListItemResponse(
                    entity_type="advisor",
                    advisor_id=advisor_id,
                    advisor_name=advisor_name,
                    added_at=added_at,
                )
            )
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
        entity_type="broker_dealer",
        broker_dealer_id=bd_id,
        broker_dealer_name=bd_name,
        added_at=added_at,
    )


@router.post(
    "/{list_id}/items/batch",
    response_model=FavoriteListItemBatchResponse,
)
async def batch_add_items_to_favorite_list(
    payload: FavoriteListItemBatchCreate,
    list_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> FavoriteListItemBatchResponse:
    """Add many broker-dealers to a list in one transaction.

    Idempotent — already-present ids land in ``skipped_existing``; non-existent
    firm ids land in ``skipped_unknown`` rather than aborting the batch. Capped
    at 200 ids/call by the request schema. ``advisor_id`` is set explicitly so
    the polymorphic XOR check (``ck_favorite_list_item_exactly_one_target``)
    is satisfied.
    """
    await _get_owned_list(db, list_id, current_user.id)

    requested_ids = payload.broker_dealer_ids
    known_rows = await db.execute(
        select(BrokerDealer.id).where(BrokerDealer.id.in_(requested_ids))
    )
    known_ids = {row[0] for row in known_rows}
    skipped_unknown = [bd_id for bd_id in requested_ids if bd_id not in known_ids]

    added_count = 0
    if known_ids:
        upsert = (
            pg_insert(FavoriteListItem)
            .values(
                [
                    {
                        "list_id": list_id,
                        "broker_dealer_id": bd_id,
                        "advisor_id": None,
                    }
                    for bd_id in known_ids
                ]
            )
            .on_conflict_do_nothing(index_elements=["list_id", "broker_dealer_id"])
            .returning(FavoriteListItem.id)
        )
        result = await db.execute(upsert)
        added_count = len(result.fetchall())

    await db.commit()
    return FavoriteListItemBatchResponse(
        added=added_count,
        skipped_existing=len(known_ids) - added_count,
        skipped_unknown=skipped_unknown,
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


# ── Investment-advisor parallels ──────────────────────────────────────────
# Same shape as the broker-dealer endpoints above, but they write/delete
# rows where ``advisor_id`` is set and ``broker_dealer_id IS NULL``. The
# XOR check constraint (``ck_favorite_list_item_exactly_one_target``,
# migration 0031) is the DB-side backstop; the explicit ``broker_dealer_id
# = None`` on inserts satisfies it.


@router.post(
    "/{list_id}/advisor-items",
    response_model=FavoriteListAdvisorItemResponse,
)
async def add_advisor_to_favorite_list(
    payload: FavoriteListAdvisorItemCreate,
    list_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> FavoriteListAdvisorItemResponse:
    """Add an investment advisor to a list owned by the calling user.

    Idempotent — re-POSTing the same ``advisor_id`` returns the existing
    row's ``added_at`` instead of raising on the unique constraint.
    """
    await _get_owned_list(db, list_id, current_user.id)

    advisor_check = await db.execute(
        select(InvestmentAdvisor.id, InvestmentAdvisor.name).where(
            InvestmentAdvisor.id == payload.advisor_id
        )
    )
    advisor_row = advisor_check.first()
    if advisor_row is None:
        raise HTTPException(status_code=404, detail="Advisor not found")
    advisor_id, advisor_name = advisor_row

    # uq_favorite_list_item_list_advisor is a PARTIAL unique index
    # (WHERE advisor_id IS NOT NULL) from migration 0031. ON CONFLICT
    # needs the matching index_where predicate to bind to the right index.
    upsert = (
        pg_insert(FavoriteListItem)
        .values(list_id=list_id, advisor_id=advisor_id, broker_dealer_id=None)
        .on_conflict_do_nothing(
            index_elements=["list_id", "advisor_id"],
            index_where=text("advisor_id IS NOT NULL"),
        )
        .returning(FavoriteListItem.id, FavoriteListItem.created_at)
    )
    inserted = (await db.execute(upsert)).first()
    if inserted is None:
        existing = await db.execute(
            select(FavoriteListItem.created_at).where(
                FavoriteListItem.list_id == list_id,
                FavoriteListItem.advisor_id == advisor_id,
            )
        )
        added_at = existing.scalar_one()
    else:
        added_at = inserted[1]
    await db.commit()

    return FavoriteListAdvisorItemResponse(
        advisor_id=advisor_id,
        advisor_name=advisor_name,
        added_at=added_at,
    )


@router.post(
    "/{list_id}/advisor-items/batch",
    response_model=FavoriteListItemBatchResponse,
)
async def batch_add_advisors_to_favorite_list(
    payload: FavoriteListAdvisorItemBatchCreate,
    list_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> FavoriteListItemBatchResponse:
    """Add many investment advisors to a list in one transaction.

    Mirrors the BD batch endpoint: idempotent, with ``skipped_existing`` /
    ``skipped_unknown`` accounting. ``broker_dealer_id`` is set explicitly
    to ``None`` so the XOR check constraint is satisfied.
    """
    await _get_owned_list(db, list_id, current_user.id)

    requested_ids = payload.advisor_ids
    known_rows = await db.execute(
        select(InvestmentAdvisor.id).where(InvestmentAdvisor.id.in_(requested_ids))
    )
    known_ids = {row[0] for row in known_rows}
    skipped_unknown = [aid for aid in requested_ids if aid not in known_ids]

    added_count = 0
    if known_ids:
        # See comment on the single-advisor upsert above re: partial index.
        upsert = (
            pg_insert(FavoriteListItem)
            .values(
                [
                    {
                        "list_id": list_id,
                        "broker_dealer_id": None,
                        "advisor_id": aid,
                    }
                    for aid in known_ids
                ]
            )
            .on_conflict_do_nothing(
                index_elements=["list_id", "advisor_id"],
                index_where=text("advisor_id IS NOT NULL"),
            )
            .returning(FavoriteListItem.id)
        )
        result = await db.execute(upsert)
        added_count = len(result.fetchall())

    await db.commit()
    return FavoriteListItemBatchResponse(
        added=added_count,
        skipped_existing=len(known_ids) - added_count,
        skipped_unknown=skipped_unknown,
    )


@router.delete(
    "/{list_id}/advisor-items/{advisor_id}",
    status_code=204,
)
async def remove_advisor_from_favorite_list(
    list_id: int = Path(..., ge=1),
    advisor_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> Response:
    """Remove an advisor from the list. 404 if it wasn't in the list."""
    await _get_owned_list(db, list_id, current_user.id)

    result = await db.execute(
        delete(FavoriteListItem).where(
            FavoriteListItem.list_id == list_id,
            FavoriteListItem.advisor_id == advisor_id,
        )
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="favorite_list_item_not_found")
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
