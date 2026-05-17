"""Admin-only per-user views.

Currently exposes ``GET /api/v1/users/{user_id}/saved-firms`` — a flat,
paginated view of every firm a target user has bookmarked across all of
their favorite lists (default + custom), used by the
``/settings/users/{id}/saved-firms`` admin sub-page so an admin can see
what each client is tracking.

The endpoint deliberately lives outside ``/favorite-lists`` (which is
strictly per-caller and would otherwise grow a polluting
``?admin_for=<user_id>`` param) so admin-scoped reads and self-scoped
reads stay cleanly separated.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy import String, func, literal, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.models.auth import AuthUser
from app.models.broker_dealer import BrokerDealer
from app.models.favorite_list import FavoriteList, FavoriteListItem
from app.models.investment_advisor import InvestmentAdvisor
from app.schemas.auth import AuthenticatedUser
from app.schemas.users_admin import (
    AdminSavedFirmListSummary,
    AdminSavedFirmRow,
    AdminUserBrief,
    AdminUserSavedFirmsResponse,
)
from app.services.auth import get_current_user

router = APIRouter(prefix="/users")


def _ensure_admin(current_user: AuthenticatedUser) -> None:
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )


async def _load_user_or_404(db: AsyncSession, user_id: str) -> AuthUser:
    row = await db.execute(select(AuthUser).where(AuthUser.id == user_id))
    user = row.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="user_not_found")
    return user


@router.get(
    "/{user_id}/saved-firms",
    response_model=AdminUserSavedFirmsResponse,
)
async def list_user_saved_firms(
    user_id: str = Path(..., min_length=1, max_length=255),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    list_id: int | None = Query(None, ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> AdminUserSavedFirmsResponse:
    """Admin-only flat view of one user's saved firms (BDs + advisors).

    Joins ``favorite_list_item`` against both ``broker_dealers`` and
    ``investment_advisors`` (the item is polymorphic XOR via the
    ``ck_favorite_list_item_exactly_one_target`` DB-side check), UNION ALLs
    them into a single ordered stream, and paginates. The ``lists`` summary
    is computed separately so the FE can render the filter-pill row with
    accurate counts even when the current page is filtered.
    """
    _ensure_admin(current_user)
    user = await _load_user_or_404(db, user_id)

    bd_items = (
        select(
            literal("broker_dealer", String).label("item_type"),
            BrokerDealer.id.label("target_id"),
            BrokerDealer.name.label("target_name"),
            FavoriteList.id.label("list_id"),
            FavoriteList.name.label("list_name"),
            FavoriteList.is_default.label("list_is_default"),
            FavoriteListItem.created_at.label("saved_at"),
        )
        .select_from(FavoriteListItem)
        .join(FavoriteList, FavoriteList.id == FavoriteListItem.list_id)
        .join(BrokerDealer, BrokerDealer.id == FavoriteListItem.broker_dealer_id)
        .where(
            FavoriteList.user_id == user_id,
            FavoriteListItem.broker_dealer_id.is_not(None),
        )
    )

    advisor_items = (
        select(
            literal("advisor", String).label("item_type"),
            InvestmentAdvisor.id.label("target_id"),
            InvestmentAdvisor.name.label("target_name"),
            FavoriteList.id.label("list_id"),
            FavoriteList.name.label("list_name"),
            FavoriteList.is_default.label("list_is_default"),
            FavoriteListItem.created_at.label("saved_at"),
        )
        .select_from(FavoriteListItem)
        .join(FavoriteList, FavoriteList.id == FavoriteListItem.list_id)
        .join(
            InvestmentAdvisor,
            InvestmentAdvisor.id == FavoriteListItem.advisor_id,
        )
        .where(
            FavoriteList.user_id == user_id,
            FavoriteListItem.advisor_id.is_not(None),
        )
    )

    if list_id is not None:
        bd_items = bd_items.where(FavoriteList.id == list_id)
        advisor_items = advisor_items.where(FavoriteList.id == list_id)

    unioned = union_all(bd_items, advisor_items).subquery()

    total_stmt = select(func.count()).select_from(unioned)
    total = int((await db.execute(total_stmt)).scalar_one())

    page_stmt = (
        select(unioned)
        .order_by(unioned.c.saved_at.desc(), unioned.c.target_id.desc())
        .limit(limit)
        .offset(offset)
    )
    page_rows = (await db.execute(page_stmt)).all()

    items = [
        AdminSavedFirmRow(
            item_type=row.item_type,
            target_id=row.target_id,
            target_name=row.target_name,
            list_id=row.list_id,
            list_name=row.list_name,
            list_is_default=row.list_is_default,
            saved_at=row.saved_at,
        )
        for row in page_rows
    ]

    # Lists summary: one row per favorite_list the user owns, with the
    # combined BD + advisor item count. Unrelated to the ``list_id`` filter
    # above so the filter-pill row always shows every list.
    summary_stmt = (
        select(
            FavoriteList.id,
            FavoriteList.name,
            FavoriteList.is_default,
            func.count(FavoriteListItem.id).label("item_count"),
        )
        .select_from(FavoriteList)
        .outerjoin(FavoriteListItem, FavoriteListItem.list_id == FavoriteList.id)
        .where(FavoriteList.user_id == user_id)
        .group_by(FavoriteList.id, FavoriteList.name, FavoriteList.is_default)
        .order_by(FavoriteList.is_default.desc(), FavoriteList.created_at.asc())
    )
    summary_rows = (await db.execute(summary_stmt)).all()
    lists = [
        AdminSavedFirmListSummary(
            id=row.id,
            name=row.name,
            is_default=row.is_default,
            item_count=int(row.item_count),
        )
        for row in summary_rows
    ]

    return AdminUserSavedFirmsResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        lists=lists,
        user=AdminUserBrief(id=user.id, email=user.email, name=user.name),
    )
