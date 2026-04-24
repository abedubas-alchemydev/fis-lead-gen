"""Service layer for per-user favorites and visit history.

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
from app.models.user_favorite import UserFavorite
from app.models.user_visit import UserVisit
from app.schemas.favorites import FavoriteListItem
from app.schemas.visits import VisitListItem


async def add_favorite(db: AsyncSession, user_id: str, bd_id: int) -> UserFavorite:
    """Favorite a broker-dealer for a user.

    Idempotent: if the ``(user_id, bd_id)`` pair already exists the existing
    row is returned unchanged. ``ON CONFLICT DO NOTHING`` gives us that
    semantics without races; we fall back to a plain SELECT when the INSERT
    is a no-op so the caller always gets the canonical row back.
    """
    stmt = (
        insert(UserFavorite)
        .values(user_id=user_id, bd_id=bd_id)
        .on_conflict_do_nothing(index_elements=["user_id", "bd_id"])
        .returning(UserFavorite)
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        existing = await db.execute(
            select(UserFavorite).where(
                UserFavorite.user_id == user_id,
                UserFavorite.bd_id == bd_id,
            )
        )
        row = existing.scalar_one()
    await db.commit()
    return row


async def remove_favorite(db: AsyncSession, user_id: str, bd_id: int) -> None:
    """Unfavorite a broker-dealer for a user.

    Idempotent: a DELETE against a row that doesn't exist is a no-op, which
    matches the HTTP 204 contract on ``DELETE /broker-dealers/{id}/favorite``.
    """
    await db.execute(
        delete(UserFavorite).where(
            UserFavorite.user_id == user_id,
            UserFavorite.bd_id == bd_id,
        )
    )
    await db.commit()


async def list_favorites(
    db: AsyncSession,
    user_id: str,
    limit: int,
    offset: int,
) -> tuple[list[FavoriteListItem], int]:
    """Return a page of the user's favorites plus the total count.

    Sorted ``created_at DESC`` (newest first) per plan §2.1. The covering
    index ``ix_user_favorite_created_at`` matches the sort direction.
    """
    total_stmt = select(func.count(UserFavorite.id)).where(UserFavorite.user_id == user_id)
    total = int((await db.execute(total_stmt)).scalar_one())

    data_stmt = (
        select(BrokerDealer, UserFavorite.created_at)
        .join(UserFavorite, UserFavorite.bd_id == BrokerDealer.id)
        .where(UserFavorite.user_id == user_id)
        .order_by(UserFavorite.created_at.desc(), UserFavorite.id.desc())
        .offset(offset)
        .limit(limit)
    )
    rows: Sequence = (await db.execute(data_stmt)).all()

    items = [
        FavoriteListItem.model_validate(
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

    Returns ``(False, None)`` when no row exists. The profile endpoint uses
    this to stamp ``is_favorited`` + ``favorited_at`` onto the response so
    the heart toggle renders in the correct state on first paint.
    """
    stmt = select(UserFavorite.created_at).where(
        UserFavorite.user_id == user_id,
        UserFavorite.bd_id == bd_id,
    )
    created_at = (await db.execute(stmt)).scalar_one_or_none()
    if created_at is None:
        return False, None
    return True, created_at


def _bd_to_summary(broker_dealer: BrokerDealer) -> dict[str, object]:
    """Project a ``BrokerDealer`` row onto the 12-field summary shape.

    Kept local (not on the model) so the summary schema stays the only
    authority over which fields ship in the favorites / visits lists.
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
