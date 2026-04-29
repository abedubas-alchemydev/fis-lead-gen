"""Service-layer tests for user favorites and visit history.

Integration-marked: exercises real Postgres so the ``INSERT ... ON CONFLICT``
branches (which are Postgres-dialect-specific) actually execute and we catch
any schema drift between the model and the migration. The default run
(``pytest -m "not integration"``) skips these; run with ``pytest -m
integration`` locally against the Docker stack or staging.

Each test seeds its own ``AuthUser`` + ``BrokerDealer`` rows, does its work,
then cleans up in reverse-dependency order. That keeps tests independent
without the cost of a full DB reset between cases.

Favorites now live in ``favorite_list_item`` keyed off the user's default
``favorite_list``; ``add_favorite`` lazily seeds that default list. Cleanup
deletes the user's ``favorite_list`` rows (CASCADE handles the items).
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, select

from app.db.session import SessionLocal
from app.models.auth import AuthUser
from app.models.broker_dealer import BrokerDealer
from app.models.favorite_list import FavoriteList, FavoriteListItem
from app.models.user_visit import UserVisit
from app.services.user_lists import (
    add_favorite,
    is_favorited,
    list_favorites,
    list_visits,
    record_visit,
    remove_favorite,
)

pytestmark = pytest.mark.integration


async def _make_user(suffix: str = "") -> str:
    """Insert a throwaway user; returns its id."""
    user_id = f"test-user-{secrets.token_hex(6)}{suffix}"
    async with SessionLocal() as session:
        session.add(
            AuthUser(
                id=user_id,
                name="Test User",
                email=f"{user_id}@example.com",
                email_verified=False,
                role="viewer",
                status="active",
            )
        )
        await session.commit()
    return user_id


async def _make_broker_dealer(name: str = "Test BD") -> int:
    """Insert a throwaway broker-dealer; returns its id."""
    async with SessionLocal() as session:
        bd = BrokerDealer(name=name, matched_source="edgar", is_deficient=False, status="active")
        session.add(bd)
        await session.commit()
        await session.refresh(bd)
        return bd.id


async def _default_list_items(user_id: str) -> list[FavoriteListItem]:
    """Return the user's default-list items, or [] when no default list yet."""
    async with SessionLocal() as session:
        return list(
            (
                await session.execute(
                    select(FavoriteListItem)
                    .join(FavoriteList, FavoriteList.id == FavoriteListItem.list_id)
                    .where(
                        FavoriteList.user_id == user_id,
                        FavoriteList.is_default.is_(True),
                    )
                )
            ).scalars()
        )


async def _cleanup(user_ids: list[str], bd_ids: list[int]) -> None:
    """Delete favorite-lists/visits/user/bd rows in dependency order.

    Deleting ``favorite_list`` cascades to ``favorite_list_item`` via the
    ``ON DELETE CASCADE`` on its FK, so we don't need a separate items pass.
    """
    async with SessionLocal() as session:
        if user_ids:
            await session.execute(delete(FavoriteList).where(FavoriteList.user_id.in_(user_ids)))
            await session.execute(delete(UserVisit).where(UserVisit.user_id.in_(user_ids)))
            await session.execute(delete(AuthUser).where(AuthUser.id.in_(user_ids)))
        if bd_ids:
            await session.execute(delete(BrokerDealer).where(BrokerDealer.id.in_(bd_ids)))
        await session.commit()


async def test_add_favorite_is_idempotent() -> None:
    user_id = await _make_user()
    bd_id = await _make_broker_dealer()
    try:
        async with SessionLocal() as session:
            first = await add_favorite(session, user_id, bd_id)
            second = await add_favorite(session, user_id, bd_id)

        assert first.id == second.id
        assert first.created_at == second.created_at

        rows = await _default_list_items(user_id)
        assert len(rows) == 1
        assert rows[0].broker_dealer_id == bd_id
    finally:
        await _cleanup([user_id], [bd_id])


async def test_remove_favorite_is_idempotent_when_absent() -> None:
    user_id = await _make_user()
    bd_id = await _make_broker_dealer()
    try:
        async with SessionLocal() as session:
            await remove_favorite(session, user_id, bd_id)  # never favorited
            await remove_favorite(session, user_id, bd_id)  # still a no-op

        rows = await _default_list_items(user_id)
        assert rows == []
    finally:
        await _cleanup([user_id], [bd_id])


async def test_remove_favorite_deletes_existing_row() -> None:
    user_id = await _make_user()
    bd_id = await _make_broker_dealer()
    try:
        async with SessionLocal() as session:
            await add_favorite(session, user_id, bd_id)

        async with SessionLocal() as session:
            await remove_favorite(session, user_id, bd_id)

        rows = await _default_list_items(user_id)
        assert rows == []
    finally:
        await _cleanup([user_id], [bd_id])


async def test_record_visit_first_call_inserts_with_count_one() -> None:
    user_id = await _make_user()
    bd_id = await _make_broker_dealer()
    try:
        async with SessionLocal() as session:
            row = await record_visit(session, user_id, bd_id)

        assert row.visit_count == 1
        assert row.first_visited_at is not None
        assert row.last_visited_at is not None
        assert row.first_visited_at == row.last_visited_at
    finally:
        await _cleanup([user_id], [bd_id])


async def test_record_visit_second_call_increments_count_and_advances_last_visited() -> None:
    user_id = await _make_user()
    bd_id = await _make_broker_dealer()
    try:
        async with SessionLocal() as session:
            first = await record_visit(session, user_id, bd_id)
            first_first_visited = first.first_visited_at
            first_last_visited = first.last_visited_at

        async with SessionLocal() as session:
            second = await record_visit(session, user_id, bd_id)

        assert second.visit_count == 2
        # first_visited_at is preserved across calls (telemetry intent).
        assert second.first_visited_at == first_first_visited
        # last_visited_at is bumped forward. >= because clocks can tick
        # identically within a Neon transaction boundary in test fixtures.
        assert second.last_visited_at >= first_last_visited
    finally:
        await _cleanup([user_id], [bd_id])


async def test_list_favorites_sorted_created_at_desc() -> None:
    user_id = await _make_user()
    bd_ids = [
        await _make_broker_dealer(name="BD-A"),
        await _make_broker_dealer(name="BD-B"),
        await _make_broker_dealer(name="BD-C"),
    ]
    try:
        async with SessionLocal() as session:
            for bd_id in bd_ids:
                await add_favorite(session, user_id, bd_id)

        async with SessionLocal() as session:
            items, total = await list_favorites(session, user_id, limit=50, offset=0)

        assert total == 3
        returned_ids = [item.id for item in items]
        # Newest insert first -> BD-C, BD-B, BD-A.
        assert returned_ids == [bd_ids[2], bd_ids[1], bd_ids[0]]
        for item in items:
            assert isinstance(item.favorited_at, datetime)
    finally:
        await _cleanup([user_id], bd_ids)


async def test_list_visits_sorted_last_visited_desc() -> None:
    user_id = await _make_user()
    bd_ids = [
        await _make_broker_dealer(name="BD-D"),
        await _make_broker_dealer(name="BD-E"),
        await _make_broker_dealer(name="BD-F"),
    ]
    try:
        async with SessionLocal() as session:
            for bd_id in bd_ids:
                await record_visit(session, user_id, bd_id)

        # Bump BD-D forward so it's now the most-recently-visited.
        async with SessionLocal() as session:
            await record_visit(session, user_id, bd_ids[0])

        async with SessionLocal() as session:
            items, total = await list_visits(session, user_id, limit=50, offset=0)

        assert total == 3
        returned_ids = [item.id for item in items]
        assert returned_ids[0] == bd_ids[0]
        # BD-D got visited twice.
        first_item = next(item for item in items if item.id == bd_ids[0])
        assert first_item.visit_count == 2
    finally:
        await _cleanup([user_id], bd_ids)


async def test_cross_user_isolation_on_favorites() -> None:
    user_a = await _make_user(suffix="-a")
    user_b = await _make_user(suffix="-b")
    bd_id = await _make_broker_dealer()
    try:
        async with SessionLocal() as session:
            await add_favorite(session, user_a, bd_id)

        async with SessionLocal() as session:
            a_items, a_total = await list_favorites(session, user_a, limit=50, offset=0)
            b_items, b_total = await list_favorites(session, user_b, limit=50, offset=0)

        assert a_total == 1
        assert [item.id for item in a_items] == [bd_id]
        assert b_total == 0
        assert b_items == []

        async with SessionLocal() as session:
            a_state = await is_favorited(session, user_a, bd_id)
            b_state = await is_favorited(session, user_b, bd_id)
        assert a_state[0] is True and a_state[1] is not None
        assert b_state == (False, None)
    finally:
        await _cleanup([user_a, user_b], [bd_id])


async def test_cross_user_isolation_on_visits() -> None:
    user_a = await _make_user(suffix="-a")
    user_b = await _make_user(suffix="-b")
    bd_id = await _make_broker_dealer()
    try:
        async with SessionLocal() as session:
            await record_visit(session, user_a, bd_id)

        async with SessionLocal() as session:
            _a_items, a_total = await list_visits(session, user_a, limit=50, offset=0)
            b_items, b_total = await list_visits(session, user_b, limit=50, offset=0)

        assert a_total == 1
        assert b_total == 0
        assert b_items == []
    finally:
        await _cleanup([user_a, user_b], [bd_id])


async def test_is_favorited_returns_false_when_absent() -> None:
    user_id = await _make_user()
    bd_id = await _make_broker_dealer()
    try:
        async with SessionLocal() as session:
            favorited, favorited_at = await is_favorited(session, user_id, bd_id)
        assert favorited is False
        assert favorited_at is None
    finally:
        await _cleanup([user_id], [bd_id])


async def test_is_favorited_returns_true_with_created_at() -> None:
    user_id = await _make_user()
    bd_id = await _make_broker_dealer()
    try:
        async with SessionLocal() as session:
            await add_favorite(session, user_id, bd_id)

        async with SessionLocal() as session:
            favorited, favorited_at = await is_favorited(session, user_id, bd_id)

        assert favorited is True
        assert isinstance(favorited_at, datetime)
        assert favorited_at.tzinfo is not None
        # Should be roughly "now" (within the last minute).
        delta = (datetime.now(timezone.utc) - favorited_at).total_seconds()
        assert delta < 60
    finally:
        await _cleanup([user_id], [bd_id])


async def test_list_favorites_pagination() -> None:
    user_id = await _make_user()
    bd_ids = [await _make_broker_dealer(name=f"BD-{i}") for i in range(5)]
    try:
        async with SessionLocal() as session:
            for bd_id in bd_ids:
                await add_favorite(session, user_id, bd_id)

        async with SessionLocal() as session:
            page1_items, total = await list_favorites(session, user_id, limit=2, offset=0)
            page2_items, _ = await list_favorites(session, user_id, limit=2, offset=2)

        assert total == 5
        assert len(page1_items) == 2
        assert len(page2_items) == 2
        assert {i.id for i in page1_items}.isdisjoint({i.id for i in page2_items})
    finally:
        await _cleanup([user_id], bd_ids)
