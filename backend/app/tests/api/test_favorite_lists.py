"""API-layer tests for /favorite-lists (#17 phase 1, GET only).

Integration-marked — touches a real Postgres so the FK + UNIQUE constraints
exercise. Auth is mocked via ``app.dependency_overrides`` (same pattern as
``test_favorites.py``); the 401 case runs the real ``get_current_user`` to
prove it rejects pre-DB.
"""

from __future__ import annotations

import secrets
from datetime import datetime

import httpx
import pytest
from sqlalchemy import delete

from app.db.session import SessionLocal
from app.main import app
from app.models.auth import AuthUser
from app.models.broker_dealer import BrokerDealer
from app.models.favorite_list import FavoriteList, FavoriteListItem
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


async def _cleanup(user_ids: list[str], bd_ids: list[int]) -> None:
    async with SessionLocal() as session:
        if user_ids:
            # FavoriteList CASCADE → favorite_list_item rows.
            await session.execute(delete(FavoriteList).where(FavoriteList.user_id.in_(user_ids)))
            await session.execute(delete(AuthUser).where(AuthUser.id.in_(user_ids)))
        if bd_ids:
            await session.execute(delete(BrokerDealer).where(BrokerDealer.id.in_(bd_ids)))
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
