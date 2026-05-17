"""API-layer tests for /favorite-lists (#17 phases 1-2).

Integration-marked — touches a real Postgres so the FK + UNIQUE constraints
exercise. Auth is mocked via ``app.dependency_overrides`` (same pattern as
``test_favorites.py``); the 401 cases run the real ``get_current_user`` to
prove it rejects pre-DB.
"""

from __future__ import annotations

import secrets
from datetime import datetime

import httpx
import pytest
from sqlalchemy import delete, select

from app.db.session import SessionLocal
from app.main import app
from app.models.auth import AuthUser
from app.models.broker_dealer import BrokerDealer
from app.models.favorite_list import FavoriteList, FavoriteListItem
from app.models.investment_advisor import InvestmentAdvisor
from app.schemas.auth import AuthenticatedUser
from app.services.auth import get_current_user

pytestmark = pytest.mark.integration


def _override_user(user_id: str) -> AuthenticatedUser:
    return AuthenticatedUser(
        id=user_id,
        name="Test User",
        email=f"{user_id}@example.com",
        role="viewer",
        session_expires_at=datetime(2099, 1, 1),
    )


async def _seed_user() -> str:
    user_id = f"test-user-{secrets.token_hex(6)}"
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


async def _seed_bd(name: str = "Test BD") -> int:
    async with SessionLocal() as session:
        bd = BrokerDealer(name=name, matched_source="edgar", is_deficient=False, status="active")
        session.add(bd)
        await session.commit()
        await session.refresh(bd)
        return bd.id


async def _seed_default_list(user_id: str) -> int:
    async with SessionLocal() as session:
        fl = FavoriteList(user_id=user_id, name="Favorites", is_default=True)
        session.add(fl)
        await session.commit()
        await session.refresh(fl)
        return fl.id


async def _seed_list_item(list_id: int, bd_id: int) -> None:
    async with SessionLocal() as session:
        session.add(FavoriteListItem(list_id=list_id, broker_dealer_id=bd_id))
        await session.commit()


async def _cleanup(
    user_ids: list[str],
    bd_ids: list[int],
    advisor_ids: list[int] | None = None,
) -> None:
    async with SessionLocal() as session:
        if user_ids:
            # FavoriteList CASCADE → favorite_list_item rows.
            await session.execute(delete(FavoriteList).where(FavoriteList.user_id.in_(user_ids)))
            await session.execute(delete(AuthUser).where(AuthUser.id.in_(user_ids)))
        if bd_ids:
            await session.execute(delete(BrokerDealer).where(BrokerDealer.id.in_(bd_ids)))
        if advisor_ids:
            await session.execute(
                delete(InvestmentAdvisor).where(InvestmentAdvisor.id.in_(advisor_ids))
            )
        await session.commit()


async def _seed_advisor(name: str = "Test Advisor") -> int:
    async with SessionLocal() as session:
        advisor = InvestmentAdvisor(name=name)
        session.add(advisor)
        await session.commit()
        await session.refresh(advisor)
        return advisor.id


async def _seed_list_advisor_item(list_id: int, advisor_id: int) -> None:
    async with SessionLocal() as session:
        session.add(FavoriteListItem(list_id=list_id, advisor_id=advisor_id))
        await session.commit()


def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


async def test_get_favorite_lists_401_without_session_cookie() -> None:
    async with _client() as client:
        response = await client.get("/api/v1/favorite-lists")
    assert response.status_code == 401


async def test_get_list_items_401_without_session_cookie() -> None:
    async with _client() as client:
        response = await client.get("/api/v1/favorite-lists/1/items")
    assert response.status_code == 401


async def test_get_favorite_lists_returns_users_default_list() -> None:
    user_id = await _seed_user()
    list_id = await _seed_default_list(user_id)
    bd_id = await _seed_bd(name="BD-A")
    await _seed_list_item(list_id, bd_id)

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.get("/api/v1/favorite-lists")
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["name"] == "Favorites"
        assert body[0]["is_default"] is True
        assert body[0]["item_count"] == 1
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [bd_id])


async def test_get_favorite_lists_orders_default_first_then_created_asc() -> None:
    user_id = await _seed_user()
    default_id = await _seed_default_list(user_id)

    async with SessionLocal() as session:
        custom_a = FavoriteList(user_id=user_id, name="Watchlist A", is_default=False)
        custom_b = FavoriteList(user_id=user_id, name="Watchlist B", is_default=False)
        session.add(custom_a)
        await session.commit()
        await session.refresh(custom_a)
        session.add(custom_b)
        await session.commit()
        await session.refresh(custom_b)

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.get("/api/v1/favorite-lists")
        assert response.status_code == 200
        body = response.json()
        names = [row["name"] for row in body]
        assert names[0] == "Favorites"
        assert names[1:] == ["Watchlist A", "Watchlist B"]
        assert body[0]["id"] == default_id
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [])


async def test_get_list_items_returns_paginated_items() -> None:
    user_id = await _seed_user()
    list_id = await _seed_default_list(user_id)
    bd_a = await _seed_bd(name="BD-A")
    bd_b = await _seed_bd(name="BD-B")
    bd_c = await _seed_bd(name="BD-C")
    for bd_id in (bd_a, bd_b, bd_c):
        await _seed_list_item(list_id, bd_id)

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.get(
                f"/api/v1/favorite-lists/{list_id}/items?page=1&page_size=2"
            )
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 3
        assert body["page"] == 1
        assert body["page_size"] == 2
        assert len(body["items"]) == 2
        assert body["items"][0]["broker_dealer_id"] == bd_c
        assert body["items"][0]["broker_dealer_name"] == "BD-C"

        async with _client() as client:
            page2 = await client.get(
                f"/api/v1/favorite-lists/{list_id}/items?page=2&page_size=2"
            )
        assert page2.status_code == 200
        page2_body = page2.json()
        assert page2_body["page"] == 2
        assert len(page2_body["items"]) == 1
        assert page2_body["items"][0]["broker_dealer_id"] == bd_a
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [bd_a, bd_b, bd_c])


async def test_get_list_items_404_for_foreign_list() -> None:
    owner = await _seed_user()
    intruder = await _seed_user()
    list_id = await _seed_default_list(owner)

    app.dependency_overrides[get_current_user] = lambda: _override_user(intruder)
    try:
        async with _client() as client:
            response = await client.get(f"/api/v1/favorite-lists/{list_id}/items")
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()
        await _cleanup([owner, intruder], [])


async def test_get_list_items_404_for_missing_list() -> None:
    user_id = await _seed_user()
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.get("/api/v1/favorite-lists/99999999/items")
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [])


async def test_lists_are_user_scoped() -> None:
    user_a = await _seed_user()
    user_b = await _seed_user()
    await _seed_default_list(user_a)

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_b)
    try:
        async with _client() as client:
            response = await client.get("/api/v1/favorite-lists")
        assert response.status_code == 200
        assert response.json() == []
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_a, user_b], [])


# ---------------------------------------------------------------------------
# Phase 2 — POST/PUT/DELETE
# ---------------------------------------------------------------------------


async def test_create_favorite_list_401_without_session_cookie() -> None:
    async with _client() as client:
        response = await client.post(
            "/api/v1/favorite-lists", json={"name": "X"}
        )
    assert response.status_code == 401


async def test_update_favorite_list_401_without_session_cookie() -> None:
    async with _client() as client:
        response = await client.put(
            "/api/v1/favorite-lists/1", json={"name": "X"}
        )
    assert response.status_code == 401


async def test_delete_favorite_list_401_without_session_cookie() -> None:
    async with _client() as client:
        response = await client.delete("/api/v1/favorite-lists/1")
    assert response.status_code == 401


async def test_add_item_401_without_session_cookie() -> None:
    async with _client() as client:
        response = await client.post(
            "/api/v1/favorite-lists/1/items", json={"broker_dealer_id": 1}
        )
    assert response.status_code == 401


async def test_remove_item_401_without_session_cookie() -> None:
    async with _client() as client:
        response = await client.delete("/api/v1/favorite-lists/1/items/1")
    assert response.status_code == 401


async def test_create_favorite_list_creates_owned_row() -> None:
    user_id = await _seed_user()
    await _seed_default_list(user_id)
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.post(
                "/api/v1/favorite-lists", json={"name": "  Watchlist A  "}
            )
        assert response.status_code == 201
        body = response.json()
        assert body["name"] == "Watchlist A"
        assert body["is_default"] is False
        assert body["item_count"] == 0

        async with _client() as client:
            listing = await client.get("/api/v1/favorite-lists")
        assert listing.status_code == 200
        names = [row["name"] for row in listing.json()]
        assert names == ["Favorites", "Watchlist A"]
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [])


async def test_create_favorite_list_duplicate_name_returns_400() -> None:
    user_id = await _seed_user()
    await _seed_default_list(user_id)
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            first = await client.post(
                "/api/v1/favorite-lists", json={"name": "Watchlist"}
            )
        assert first.status_code == 201

        async with _client() as client:
            dup = await client.post(
                "/api/v1/favorite-lists", json={"name": "Watchlist"}
            )
        assert dup.status_code == 400
        assert "already exists" in dup.json()["detail"]
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [])


async def test_create_favorite_list_blank_name_returns_422() -> None:
    user_id = await _seed_user()
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            empty = await client.post(
                "/api/v1/favorite-lists", json={"name": ""}
            )
        assert empty.status_code == 422

        async with _client() as client:
            whitespace = await client.post(
                "/api/v1/favorite-lists", json={"name": "   "}
            )
        assert whitespace.status_code == 422
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [])


async def test_create_favorite_list_oversize_name_returns_422() -> None:
    user_id = await _seed_user()
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.post(
                "/api/v1/favorite-lists", json={"name": "x" * 81}
            )
        assert response.status_code == 422
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [])


async def test_update_favorite_list_renames_non_default() -> None:
    user_id = await _seed_user()
    await _seed_default_list(user_id)
    async with SessionLocal() as session:
        custom = FavoriteList(user_id=user_id, name="Old", is_default=False)
        session.add(custom)
        await session.commit()
        await session.refresh(custom)
        custom_id = custom.id

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.put(
                f"/api/v1/favorite-lists/{custom_id}", json={"name": "New Name"}
            )
        assert response.status_code == 200
        body = response.json()
        assert body["id"] == custom_id
        assert body["name"] == "New Name"
        assert body["is_default"] is False
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [])


async def test_update_default_favorite_list_returns_400() -> None:
    user_id = await _seed_user()
    default_id = await _seed_default_list(user_id)
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.put(
                f"/api/v1/favorite-lists/{default_id}",
                json={"name": "Renamed"},
            )
        assert response.status_code == 400
        assert "default" in response.json()["detail"].lower()
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [])


async def test_update_favorite_list_duplicate_name_returns_400() -> None:
    user_id = await _seed_user()
    await _seed_default_list(user_id)
    async with SessionLocal() as session:
        first = FavoriteList(user_id=user_id, name="Alpha", is_default=False)
        second = FavoriteList(user_id=user_id, name="Bravo", is_default=False)
        session.add(first)
        session.add(second)
        await session.commit()
        await session.refresh(second)
        second_id = second.id

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.put(
                f"/api/v1/favorite-lists/{second_id}", json={"name": "Alpha"}
            )
        assert response.status_code == 400
        assert "already exists" in response.json()["detail"]
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [])


async def test_update_favorite_list_404_for_foreign_list() -> None:
    owner = await _seed_user()
    intruder = await _seed_user()
    async with SessionLocal() as session:
        fl = FavoriteList(user_id=owner, name="Owners", is_default=False)
        session.add(fl)
        await session.commit()
        await session.refresh(fl)
        fl_id = fl.id

    app.dependency_overrides[get_current_user] = lambda: _override_user(intruder)
    try:
        async with _client() as client:
            response = await client.put(
                f"/api/v1/favorite-lists/{fl_id}", json={"name": "Pwned"}
            )
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()
        await _cleanup([owner, intruder], [])


async def test_delete_favorite_list_removes_list_and_cascades_items() -> None:
    user_id = await _seed_user()
    await _seed_default_list(user_id)
    bd_id = await _seed_bd(name="BD-To-Cascade")
    async with SessionLocal() as session:
        custom = FavoriteList(user_id=user_id, name="ToDelete", is_default=False)
        session.add(custom)
        await session.commit()
        await session.refresh(custom)
        custom_id = custom.id
        session.add(FavoriteListItem(list_id=custom_id, broker_dealer_id=bd_id))
        await session.commit()

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.delete(f"/api/v1/favorite-lists/{custom_id}")
        assert response.status_code == 204

        async with SessionLocal() as session:
            list_remaining = await session.execute(
                select(FavoriteList).where(FavoriteList.id == custom_id)
            )
            assert list_remaining.scalar_one_or_none() is None
            items_remaining = await session.execute(
                select(FavoriteListItem).where(FavoriteListItem.list_id == custom_id)
            )
            assert items_remaining.scalar_one_or_none() is None
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [bd_id])


async def test_delete_default_favorite_list_returns_400() -> None:
    user_id = await _seed_user()
    default_id = await _seed_default_list(user_id)
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.delete(f"/api/v1/favorite-lists/{default_id}")
        assert response.status_code == 400
        assert "default" in response.json()["detail"].lower()
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [])


async def test_delete_favorite_list_404_for_foreign_list() -> None:
    owner = await _seed_user()
    intruder = await _seed_user()
    async with SessionLocal() as session:
        fl = FavoriteList(user_id=owner, name="Owners", is_default=False)
        session.add(fl)
        await session.commit()
        await session.refresh(fl)
        fl_id = fl.id

    app.dependency_overrides[get_current_user] = lambda: _override_user(intruder)
    try:
        async with _client() as client:
            response = await client.delete(f"/api/v1/favorite-lists/{fl_id}")
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()
        await _cleanup([owner, intruder], [])


async def test_add_item_inserts_and_is_idempotent() -> None:
    user_id = await _seed_user()
    list_id = await _seed_default_list(user_id)
    bd_id = await _seed_bd(name="BD-Add")
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            first = await client.post(
                f"/api/v1/favorite-lists/{list_id}/items",
                json={"broker_dealer_id": bd_id},
            )
        assert first.status_code == 200
        first_body = first.json()
        assert first_body["broker_dealer_id"] == bd_id
        assert first_body["broker_dealer_name"] == "BD-Add"
        first_added_at = first_body["added_at"]

        async with _client() as client:
            second = await client.post(
                f"/api/v1/favorite-lists/{list_id}/items",
                json={"broker_dealer_id": bd_id},
            )
        assert second.status_code == 200
        second_body = second.json()
        assert second_body["broker_dealer_id"] == bd_id
        assert second_body["added_at"] == first_added_at

        async with SessionLocal() as session:
            count_result = await session.execute(
                select(FavoriteListItem).where(
                    FavoriteListItem.list_id == list_id,
                    FavoriteListItem.broker_dealer_id == bd_id,
                )
            )
            rows = count_result.scalars().all()
            assert len(rows) == 1
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [bd_id])


async def test_add_item_404_for_unknown_firm() -> None:
    user_id = await _seed_user()
    list_id = await _seed_default_list(user_id)
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.post(
                f"/api/v1/favorite-lists/{list_id}/items",
                json={"broker_dealer_id": 99999999},
            )
        assert response.status_code == 404
        assert response.json()["detail"] == "Firm not found"
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [])


async def test_add_item_404_for_foreign_list() -> None:
    owner = await _seed_user()
    intruder = await _seed_user()
    list_id = await _seed_default_list(owner)
    bd_id = await _seed_bd(name="BD-Foreign-Add")
    app.dependency_overrides[get_current_user] = lambda: _override_user(intruder)
    try:
        async with _client() as client:
            response = await client.post(
                f"/api/v1/favorite-lists/{list_id}/items",
                json={"broker_dealer_id": bd_id},
            )
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()
        await _cleanup([owner, intruder], [bd_id])


async def test_remove_item_removes_present_row() -> None:
    user_id = await _seed_user()
    list_id = await _seed_default_list(user_id)
    bd_id = await _seed_bd(name="BD-Remove")
    await _seed_list_item(list_id, bd_id)

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.delete(
                f"/api/v1/favorite-lists/{list_id}/items/{bd_id}"
            )
        assert response.status_code == 204

        async with SessionLocal() as session:
            remaining = await session.execute(
                select(FavoriteListItem).where(
                    FavoriteListItem.list_id == list_id,
                    FavoriteListItem.broker_dealer_id == bd_id,
                )
            )
            assert remaining.scalar_one_or_none() is None
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [bd_id])


async def test_remove_item_404_when_absent() -> None:
    user_id = await _seed_user()
    list_id = await _seed_default_list(user_id)
    bd_id = await _seed_bd(name="BD-Absent")

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.delete(
                f"/api/v1/favorite-lists/{list_id}/items/{bd_id}"
            )
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [bd_id])


async def test_remove_item_404_for_foreign_list() -> None:
    owner = await _seed_user()
    intruder = await _seed_user()
    list_id = await _seed_default_list(owner)
    bd_id = await _seed_bd(name="BD-Foreign-Remove")
    await _seed_list_item(list_id, bd_id)

    app.dependency_overrides[get_current_user] = lambda: _override_user(intruder)
    try:
        async with _client() as client:
            response = await client.delete(
                f"/api/v1/favorite-lists/{list_id}/items/{bd_id}"
            )
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()
        await _cleanup([owner, intruder], [bd_id])


# ── Batch-add endpoint ────────────────────────────────────────────────────


async def _count_items(list_id: int) -> int:
    async with SessionLocal() as session:
        result = await session.execute(
            select(FavoriteListItem).where(FavoriteListItem.list_id == list_id)
        )
        return len(result.fetchall())


async def test_batch_add_401_without_session_cookie() -> None:
    async with _client() as client:
        response = await client.post(
            "/api/v1/favorite-lists/1/items/batch",
            json={"broker_dealer_ids": [1]},
        )
    assert response.status_code == 401


async def test_batch_add_inserts_all_when_none_existing() -> None:
    user_id = await _seed_user()
    list_id = await _seed_default_list(user_id)
    bd_a = await _seed_bd(name="BD-Batch-A")
    bd_b = await _seed_bd(name="BD-Batch-B")
    bd_c = await _seed_bd(name="BD-Batch-C")

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.post(
                f"/api/v1/favorite-lists/{list_id}/items/batch",
                json={"broker_dealer_ids": [bd_a, bd_b, bd_c]},
            )
        assert response.status_code == 200
        body = response.json()
        assert body == {
            "added": 3,
            "skipped_existing": 0,
            "skipped_unknown": [],
        }
        assert await _count_items(list_id) == 3
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [bd_a, bd_b, bd_c])


async def test_batch_add_skips_existing() -> None:
    user_id = await _seed_user()
    list_id = await _seed_default_list(user_id)
    bd_a = await _seed_bd(name="BD-Batch-Skip-A")
    bd_b = await _seed_bd(name="BD-Batch-Skip-B")
    bd_c = await _seed_bd(name="BD-Batch-Skip-C")
    await _seed_list_item(list_id, bd_a)

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.post(
                f"/api/v1/favorite-lists/{list_id}/items/batch",
                json={"broker_dealer_ids": [bd_a, bd_b, bd_c]},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["added"] == 2
        assert body["skipped_existing"] == 1
        assert body["skipped_unknown"] == []
        assert await _count_items(list_id) == 3
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [bd_a, bd_b, bd_c])


async def test_batch_add_idempotent_on_repeat() -> None:
    user_id = await _seed_user()
    list_id = await _seed_default_list(user_id)
    bd_a = await _seed_bd(name="BD-Batch-Idem-A")
    bd_b = await _seed_bd(name="BD-Batch-Idem-B")

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            first = await client.post(
                f"/api/v1/favorite-lists/{list_id}/items/batch",
                json={"broker_dealer_ids": [bd_a, bd_b]},
            )
            second = await client.post(
                f"/api/v1/favorite-lists/{list_id}/items/batch",
                json={"broker_dealer_ids": [bd_a, bd_b]},
            )
        assert first.status_code == 200
        assert first.json()["added"] == 2
        assert second.status_code == 200
        assert second.json() == {
            "added": 0,
            "skipped_existing": 2,
            "skipped_unknown": [],
        }
        assert await _count_items(list_id) == 2
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [bd_a, bd_b])


async def test_batch_add_reports_unknown_firms() -> None:
    user_id = await _seed_user()
    list_id = await _seed_default_list(user_id)
    bd_a = await _seed_bd(name="BD-Batch-Unknown-A")
    bd_b = await _seed_bd(name="BD-Batch-Unknown-B")

    fake_a = 9_999_999
    fake_b = 9_999_998

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.post(
                f"/api/v1/favorite-lists/{list_id}/items/batch",
                json={"broker_dealer_ids": [bd_a, fake_a, bd_b, fake_b]},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["added"] == 2
        assert body["skipped_existing"] == 0
        assert sorted(body["skipped_unknown"]) == sorted([fake_a, fake_b])
        assert await _count_items(list_id) == 2
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [bd_a, bd_b])


async def test_batch_add_404_for_foreign_list() -> None:
    owner = await _seed_user()
    intruder = await _seed_user()
    list_id = await _seed_default_list(owner)
    bd_id = await _seed_bd(name="BD-Batch-Foreign")

    app.dependency_overrides[get_current_user] = lambda: _override_user(intruder)
    try:
        async with _client() as client:
            response = await client.post(
                f"/api/v1/favorite-lists/{list_id}/items/batch",
                json={"broker_dealer_ids": [bd_id]},
            )
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()
        await _cleanup([owner, intruder], [bd_id])


async def test_batch_add_404_for_missing_list() -> None:
    user_id = await _seed_user()
    bd_id = await _seed_bd(name="BD-Batch-MissingList")

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.post(
                "/api/v1/favorite-lists/99999999/items/batch",
                json={"broker_dealer_ids": [bd_id]},
            )
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [bd_id])


async def test_batch_add_422_for_empty_list() -> None:
    user_id = await _seed_user()
    list_id = await _seed_default_list(user_id)

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.post(
                f"/api/v1/favorite-lists/{list_id}/items/batch",
                json={"broker_dealer_ids": []},
            )
        assert response.status_code == 422
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [])


async def test_batch_add_422_for_oversize_payload() -> None:
    user_id = await _seed_user()
    list_id = await _seed_default_list(user_id)

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.post(
                f"/api/v1/favorite-lists/{list_id}/items/batch",
                json={"broker_dealer_ids": list(range(1, 202))},
            )
        assert response.status_code == 422
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [])


async def test_batch_add_dedupes_request() -> None:
    user_id = await _seed_user()
    list_id = await _seed_default_list(user_id)
    bd_a = await _seed_bd(name="BD-Batch-Dedupe-A")
    bd_b = await _seed_bd(name="BD-Batch-Dedupe-B")

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.post(
                f"/api/v1/favorite-lists/{list_id}/items/batch",
                json={"broker_dealer_ids": [bd_a, bd_a, bd_b, bd_b, bd_a]},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["added"] == 2
        assert body["skipped_existing"] == 0
        assert body["skipped_unknown"] == []
        assert await _count_items(list_id) == 2
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [bd_a, bd_b])


# ── Investment-advisor parallels ──────────────────────────────────────────


async def test_add_advisor_item_401_without_session_cookie() -> None:
    async with _client() as client:
        response = await client.post(
            "/api/v1/favorite-lists/1/advisor-items", json={"advisor_id": 1}
        )
    assert response.status_code == 401


async def test_add_advisor_item_inserts_and_is_idempotent() -> None:
    user_id = await _seed_user()
    list_id = await _seed_default_list(user_id)
    advisor_id = await _seed_advisor(name="Advisor-Add")
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            first = await client.post(
                f"/api/v1/favorite-lists/{list_id}/advisor-items",
                json={"advisor_id": advisor_id},
            )
        assert first.status_code == 200
        first_body = first.json()
        assert first_body["advisor_id"] == advisor_id
        assert first_body["advisor_name"] == "Advisor-Add"
        first_added_at = first_body["added_at"]

        async with _client() as client:
            second = await client.post(
                f"/api/v1/favorite-lists/{list_id}/advisor-items",
                json={"advisor_id": advisor_id},
            )
        assert second.status_code == 200
        assert second.json()["added_at"] == first_added_at

        async with SessionLocal() as session:
            count_result = await session.execute(
                select(FavoriteListItem).where(
                    FavoriteListItem.list_id == list_id,
                    FavoriteListItem.advisor_id == advisor_id,
                )
            )
            rows = count_result.scalars().all()
            assert len(rows) == 1
            assert rows[0].broker_dealer_id is None
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [], [advisor_id])


async def test_add_advisor_item_404_for_unknown_advisor() -> None:
    user_id = await _seed_user()
    list_id = await _seed_default_list(user_id)
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.post(
                f"/api/v1/favorite-lists/{list_id}/advisor-items",
                json={"advisor_id": 99999999},
            )
        assert response.status_code == 404
        assert response.json()["detail"] == "Advisor not found"
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [])


async def test_add_advisor_item_404_for_foreign_list() -> None:
    owner = await _seed_user()
    intruder = await _seed_user()
    list_id = await _seed_default_list(owner)
    advisor_id = await _seed_advisor(name="Advisor-Foreign")
    app.dependency_overrides[get_current_user] = lambda: _override_user(intruder)
    try:
        async with _client() as client:
            response = await client.post(
                f"/api/v1/favorite-lists/{list_id}/advisor-items",
                json={"advisor_id": advisor_id},
            )
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()
        await _cleanup([owner, intruder], [], [advisor_id])


async def test_remove_advisor_item_401_without_session_cookie() -> None:
    async with _client() as client:
        response = await client.delete("/api/v1/favorite-lists/1/advisor-items/1")
    assert response.status_code == 401


async def test_remove_advisor_item_removes_present_row() -> None:
    user_id = await _seed_user()
    list_id = await _seed_default_list(user_id)
    advisor_id = await _seed_advisor(name="Advisor-Remove")
    await _seed_list_advisor_item(list_id, advisor_id)

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.delete(
                f"/api/v1/favorite-lists/{list_id}/advisor-items/{advisor_id}"
            )
        assert response.status_code == 204

        async with SessionLocal() as session:
            remaining = await session.execute(
                select(FavoriteListItem).where(
                    FavoriteListItem.list_id == list_id,
                    FavoriteListItem.advisor_id == advisor_id,
                )
            )
            assert remaining.scalar_one_or_none() is None
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [], [advisor_id])


async def test_remove_advisor_item_404_when_absent() -> None:
    user_id = await _seed_user()
    list_id = await _seed_default_list(user_id)
    advisor_id = await _seed_advisor(name="Advisor-Absent")

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.delete(
                f"/api/v1/favorite-lists/{list_id}/advisor-items/{advisor_id}"
            )
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [], [advisor_id])


async def test_advisor_batch_add_401_without_session_cookie() -> None:
    async with _client() as client:
        response = await client.post(
            "/api/v1/favorite-lists/1/advisor-items/batch",
            json={"advisor_ids": [1]},
        )
    assert response.status_code == 401


async def test_advisor_batch_add_inserts_all_when_none_existing() -> None:
    user_id = await _seed_user()
    list_id = await _seed_default_list(user_id)
    a = await _seed_advisor(name="Advisor-Batch-A")
    b = await _seed_advisor(name="Advisor-Batch-B")
    c = await _seed_advisor(name="Advisor-Batch-C")

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.post(
                f"/api/v1/favorite-lists/{list_id}/advisor-items/batch",
                json={"advisor_ids": [a, b, c]},
            )
        assert response.status_code == 200
        body = response.json()
        assert body == {
            "added": 3,
            "skipped_existing": 0,
            "skipped_unknown": [],
        }
        assert await _count_items(list_id) == 3
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [], [a, b, c])


async def test_advisor_batch_add_skips_existing_and_unknown() -> None:
    user_id = await _seed_user()
    list_id = await _seed_default_list(user_id)
    a = await _seed_advisor(name="Advisor-Batch-Skip-A")
    b = await _seed_advisor(name="Advisor-Batch-Skip-B")
    await _seed_list_advisor_item(list_id, a)
    fake = 9_999_999

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.post(
                f"/api/v1/favorite-lists/{list_id}/advisor-items/batch",
                json={"advisor_ids": [a, b, fake]},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["added"] == 1
        assert body["skipped_existing"] == 1
        assert body["skipped_unknown"] == [fake]
        assert await _count_items(list_id) == 2
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [], [a, b])


async def test_advisor_batch_add_404_for_foreign_list() -> None:
    owner = await _seed_user()
    intruder = await _seed_user()
    list_id = await _seed_default_list(owner)
    advisor_id = await _seed_advisor(name="Advisor-Batch-Foreign")

    app.dependency_overrides[get_current_user] = lambda: _override_user(intruder)
    try:
        async with _client() as client:
            response = await client.post(
                f"/api/v1/favorite-lists/{list_id}/advisor-items/batch",
                json={"advisor_ids": [advisor_id]},
            )
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()
        await _cleanup([owner, intruder], [], [advisor_id])


async def test_advisor_batch_add_422_for_oversize_payload() -> None:
    user_id = await _seed_user()
    list_id = await _seed_default_list(user_id)

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.post(
                f"/api/v1/favorite-lists/{list_id}/advisor-items/batch",
                json={"advisor_ids": list(range(1, 202))},
            )
        assert response.status_code == 422
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [])


async def test_get_items_returns_mixed_bd_and_advisor_items() -> None:
    """The polymorphic GET surfaces both kinds with entity_type discriminator."""
    user_id = await _seed_user()
    list_id = await _seed_default_list(user_id)
    bd_id = await _seed_bd(name="Mixed-BD")
    advisor_id = await _seed_advisor(name="Mixed-Advisor")
    await _seed_list_item(list_id, bd_id)
    await _seed_list_advisor_item(list_id, advisor_id)

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.get(f"/api/v1/favorite-lists/{list_id}/items")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 2
        kinds = {item["entity_type"] for item in body["items"]}
        assert kinds == {"broker_dealer", "advisor"}
        for item in body["items"]:
            if item["entity_type"] == "broker_dealer":
                assert item["broker_dealer_id"] == bd_id
                assert item["broker_dealer_name"] == "Mixed-BD"
                assert item["advisor_id"] is None
                assert item["advisor_name"] is None
            else:
                assert item["advisor_id"] == advisor_id
                assert item["advisor_name"] == "Mixed-Advisor"
                assert item["broker_dealer_id"] is None
                assert item["broker_dealer_name"] is None
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [bd_id], [advisor_id])


async def test_get_advisor_favorite_lists_returns_membership() -> None:
    user_id = await _seed_user()
    list_id = await _seed_default_list(user_id)
    advisor_id = await _seed_advisor(name="Membership-Advisor")
    await _seed_list_advisor_item(list_id, advisor_id)

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.get(
                f"/api/v1/investment-advisors/{advisor_id}/favorite-lists"
            )
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["is_member"] is True
        assert body[0]["is_default"] is True
        assert body[0]["item_count"] == 1
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [], [advisor_id])


async def test_get_advisor_favorite_lists_404_for_unknown_advisor() -> None:
    user_id = await _seed_user()
    await _seed_default_list(user_id)
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.get(
                "/api/v1/investment-advisors/99999999/favorite-lists"
            )
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [])
