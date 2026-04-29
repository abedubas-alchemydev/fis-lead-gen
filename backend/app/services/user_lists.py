"""Service layer for per-user favorites and visit history.

Favorites now live in ``favorite_list_item`` filtered to the user's default
``favorite_list`` (named "Favorites", ``is_default=true``). The legacy
``user_favorite`` single-table flow was dropped in migration ``20260429_0021``;
this module bridges the legacy ``POST /broker-dealers/{id}/favorite`` /
``GET /favorites`` endpoints onto the new playlist-style schema while phase 4
of #17 swaps those endpoints over to the explicit list APIs.

Public function signatures are preserved so endpoint callers don't change:
``add_favorite``, ``remove_favorite``, ``is_favorited``, ``list_favorites``.

All writes go through ``INSERT ... ON CONFLICT`` so the UI can fire the same
POST twice (double-click, retry) without tripping a 500. The endpoint layer
is therefore free of its own idempotency guard: the DB is the source of truth.

Reads (``list_favorites`` / ``list_visits``) join ``broker_dealers`` so the
response carries the master-list row shape plus the favourite/visit metadata
needed to render "added 3 days ago" / "last visited 2 hours ago · 4 visits".
"""

from __future__ import annotations

from datetime import datetime
from typing import Sequence

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.broker_dealer import BrokerDealer
from app.models.favorite_list import FavoriteList, FavoriteListItem
from app.models.user_visit import UserVisit
from app.schemas.favorites import FavoriteListItem as FavoriteListItemSchema
from app.schemas.visits import VisitListItem


_DEFAULT_LIST_NAME = "Favorites"


async def _get_or_create_default_list_id(db: AsyncSession, user_id: str) -> int:
    """Return the user's default ``favorite_list`` id, creating it if absent.

    Migration ``20260429_0019`` backfilled a default list for every user who
    already had legacy favorites at upgrade time. Users who signed up after
    the migration won't have one until they favorite something for the first
    time, so the write path bootstraps it lazily here. Idempotent via
    ``ON CONFLICT (user_id, name) DO NOTHING`` plus a follow-up SELECT for
    the lost-race path.

    Args:
        db: Async DB session.
        user_id: BetterAuth user id.

    Returns:
        Primary key of the user's default favorite list.
    """
    stmt = select(FavoriteList.id).where(
        FavoriteList.user_id == user_id,
        FavoriteList.is_default.is_(True),
    )
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing is not None:
        return existing

    insert_stmt = (
        insert(FavoriteList)
        .values(user_id=user_id, name=_DEFAULT_LIST_NAME, is_default=True)
        .on_conflict_do_nothing(index_elements=["user_id", "name"])
        .returning(FavoriteList.id)
    )
    inserted = (await db.execute(insert_stmt)).scalar_one_or_none()
    if inserted is not None:
        return inserted
    return (await db.execute(stmt)).scalar_one()


async def _get_default_list_id(db: AsyncSession, user_id: str) -> int | None:
    """Read-only lookup of the user's default favorite-list id.

    Returns ``None`` when the user has no default list yet -- the expected
    happy path for users who haven't favorited anything; the write path
    creates the list lazily on the first favorite. Read-side callers
    (``remove_favorite`` / ``list_favorites`` / ``is_favorited``) treat
    ``None`` as an empty result, not an error.

    Args:
        db: Async DB session.
        user_id: BetterAuth user id.

    Returns:
        Default list's primary key, or ``None`` when the user has not
        favorited anything yet.
    """
    stmt = select(FavoriteList.id).where(
        FavoriteList.user_id == user_id,
        FavoriteList.is_default.is_(True),
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def add_favorite(
    db: AsyncSession, user_id: str, bd_id: int
) -> FavoriteListItem:
    """Favorite a broker-dealer for a user via their default list.

    Idempotent: if the ``(list_id, broker_dealer_id)`` pair already exists the
    existing row is returned unchanged. ``ON CONFLICT DO NOTHING`` gives us
    that semantics without races; we fall back to a plain SELECT when the
    INSERT is a no-op so the caller always gets the canonical row back.

    Args:
        db: Async DB session.
        user_id: BetterAuth user id.
        bd_id: Broker-dealer primary key.

    Returns:
        Persisted ``FavoriteListItem`` row (existing or newly inserted).
    """
    list_id = await _get_or_create_default_list_id(db, user_id)
    insert_stmt = (
        insert(FavoriteListItem)
        .values(list_id=list_id, broker_dealer_id=bd_id)
        .on_conflict_do_nothing(index_elements=["list_id", "broker_dealer_id"])
        .returning(FavoriteListItem)
    )
    row = (await db.execute(insert_stmt)).scalar_one_or_none()
    if row is None:
        existing = await db.execute(
            select(FavoriteListItem).where(
                FavoriteListItem.list_id == list_id,
                FavoriteListItem.broker_dealer_id == bd_id,
            )
        )
        row = existing.scalar_one()
    await db.commit()
    return row


async def remove_favorite(db: AsyncSession, user_id: str, bd_id: int) -> None:
    """Unfavorite a broker-dealer from the user's default list.

    Idempotent: a DELETE against a row that doesn't exist is a no-op, which
    matches the HTTP 204 contract on ``DELETE /broker-dealers/{id}/favorite``.
    A user with no default list at all is also a no-op (nothing to remove).

    Args:
        db: Async DB session.
        user_id: BetterAuth user id.
        bd_id: Broker-dealer primary key.

    Returns:
        ``None``.
    """
    list_id = await _get_default_list_id(db, user_id)
    if list_id is None:
        return
    await db.execute(
        delete(FavoriteListItem).where(
            FavoriteListItem.list_id == list_id,
            FavoriteListItem.broker_dealer_id == bd_id,
        )
    )
    await db.commit()


async def list_favorites(
    db: AsyncSession,
    user_id: str,
    limit: int,
    offset: int,
) -> tuple[list[FavoriteListItemSchema], int]:
    """Return a page of the user's default-list favorites plus the total count.

    Sorted ``created_at DESC`` (newest first) per plan §2.1. A user with no
    default list yet returns an empty page.

    Args:
        db: Async DB session.
        user_id: BetterAuth user id.
        limit: Page size (already validated by the endpoint layer).
        offset: Page offset.

    Returns:
        ``(items, total)`` -- ``items`` is the page of summary rows and
        ``total`` is the unpaginated count for the user's default list.
    """
    list_id = await _get_default_list_id(db, user_id)
    if list_id is None:
        return [], 0

    total_stmt = select(func.count(FavoriteListItem.id)).where(
        FavoriteListItem.list_id == list_id
    )
    total = int((await db.execute(total_stmt)).scalar_one())

    data_stmt = (
        select(BrokerDealer, FavoriteListItem.created_at)
        .join(
            FavoriteListItem,
            FavoriteListItem.broker_dealer_id == BrokerDealer.id,
        )
        .where(FavoriteListItem.list_id == list_id)
        .order_by(FavoriteListItem.created_at.desc(), FavoriteListItem.id.desc())
        .offset(offset)
        .limit(limit)
    )
    rows: Sequence = (await db.execute(data_stmt)).all()

    items = [
        FavoriteListItemSchema.model_validate(
            {**_bd_to_summary(broker_dealer), "favorited_at": favorited_at}
        )
        for broker_dealer, favorited_at in rows
    ]
    return items, total


async def record_visit(db: AsyncSession, user_id: str, bd_id: int) -> UserVisit:
    """Record a detail-page visit.

    First call for a given ``(user_id, bd_id)`` inserts the row with
    ``visit_count=1`` and both timestamps at ``now()``. Subsequent calls bump
    ``visit_count`` and slide ``last_visited_at`` forward while preserving
    ``first_visited_at``. A single statement keeps the write atomic -- no
    read-then-write race.

    Args:
        db: Async DB session.
        user_id: BetterAuth user id.
        bd_id: Broker-dealer primary key.

    Returns:
        Persisted ``UserVisit`` row reflecting the post-upsert state.
    """
    stmt = insert(UserVisit).values(user_id=user_id, bd_id=bd_id)
    upsert = stmt.on_conflict_do_update(
        index_elements=["user_id", "bd_id"],
        set_={
            "visit_count": UserVisit.visit_count + 1,
            "last_visited_at": func.now(),
        },
    ).returning(UserVisit)
    row = (await db.execute(upsert)).scalar_one()
    await db.commit()
    return row


async def list_visits(
    db: AsyncSession,
    user_id: str,
    limit: int,
    offset: int,
) -> tuple[list[VisitListItem], int]:
    """Return a page of the user's visit history plus the total count.

    Sorted ``last_visited_at DESC`` (most recently viewed first) per plan §2.2.

    Args:
        db: Async DB session.
        user_id: BetterAuth user id.
        limit: Page size (already validated by the endpoint layer).
        offset: Page offset.

    Returns:
        ``(items, total)`` -- ``items`` is the page of summary rows annotated
        with ``last_visited_at`` and ``visit_count``; ``total`` is the user's
        full visit count.
    """
    total_stmt = select(func.count(UserVisit.id)).where(UserVisit.user_id == user_id)
    total = int((await db.execute(total_stmt)).scalar_one())

    data_stmt = (
        select(BrokerDealer, UserVisit.last_visited_at, UserVisit.visit_count)
        .join(UserVisit, UserVisit.bd_id == BrokerDealer.id)
        .where(UserVisit.user_id == user_id)
        .order_by(UserVisit.last_visited_at.desc(), UserVisit.id.desc())
        .offset(offset)
        .limit(limit)
    )
    rows: Sequence = (await db.execute(data_stmt)).all()

    items = [
        VisitListItem.model_validate(
            {
                **_bd_to_summary(broker_dealer),
                "last_visited_at": last_visited_at,
                "visit_count": visit_count,
            }
        )
        for broker_dealer, last_visited_at, visit_count in rows
    ]
    return items, total


async def is_favorited(
    db: AsyncSession,
    user_id: str,
    bd_id: int,
) -> tuple[bool, datetime | None]:
    """Check whether the user has favorited this broker-dealer.

    Returns ``(False, None)`` when the user has no default list yet, or when
    the firm isn't pinned to it. The profile endpoint uses this to stamp
    ``is_favorited`` + ``favorited_at`` onto the response so the heart toggle
    renders in the correct state on first paint.

    Args:
        db: Async DB session.
        user_id: BetterAuth user id.
        bd_id: Broker-dealer primary key.

    Returns:
        ``(is_favorited, favorited_at)``: ``(True, datetime)`` when the firm
        is on the user's default list; ``(False, None)`` otherwise.
    """
    list_id = await _get_default_list_id(db, user_id)
    if list_id is None:
        return False, None
    stmt = select(FavoriteListItem.created_at).where(
        FavoriteListItem.list_id == list_id,
        FavoriteListItem.broker_dealer_id == bd_id,
    )
    created_at = (await db.execute(stmt)).scalar_one_or_none()
    if created_at is None:
        return False, None
    return True, created_at


def _bd_to_summary(broker_dealer: BrokerDealer) -> dict[str, object]:
    """Project a ``BrokerDealer`` row onto the 12-field summary shape.

    Kept local (not on the model) so the summary schema stays the only
    authority over which fields ship in the favorites / visits lists.

    Args:
        broker_dealer: ORM row to project.

    Returns:
        Dict with the 12 summary fields used by ``FavoriteListItemSchema``
        and ``VisitListItem``.
    """
    return {
        "id": broker_dealer.id,
        "name": broker_dealer.name,
        "city": broker_dealer.city,
        "state": broker_dealer.state,
        "lead_score": (
            float(broker_dealer.lead_score) if broker_dealer.lead_score is not None else None
        ),
        "lead_priority": broker_dealer.lead_priority,
        "current_clearing_partner": broker_dealer.current_clearing_partner,
        "health_status": broker_dealer.health_status,
        "is_deficient": broker_dealer.is_deficient,
        "last_filing_date": broker_dealer.last_filing_date,
        "latest_net_capital": (
            float(broker_dealer.latest_net_capital)
            if broker_dealer.latest_net_capital is not None
            else None
        ),
        "yoy_growth": (
            float(broker_dealer.yoy_growth) if broker_dealer.yoy_growth is not None else None
        ),
    }
