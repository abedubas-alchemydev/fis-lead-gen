"""API-layer tests for /broker-dealers/{id}/favorite and /favorites.

Integration-marked -- the endpoints touch a real Postgres. Auth is mocked via
``app.dependency_overrides`` so we don't need to hand-craft a BetterAuth
session cookie in every test; the 401-without-cookie case runs the real
``get_current_user`` to prove it rejects pre-DB.

Favorites now persist into ``favorite_list_item`` against the user's default
``favorite_list``; ``add_favorite`` lazily seeds the default list. Cleanup
deletes the user's ``favorite_list`` rows (CASCADE handles the items).
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
from app.models.favorite_list import FavoriteList
from app.models.user_visit import UserVisit
from app.schemas.auth import AuthenticatedUser
from app.services.auth import get_current_user

pytestmark = pytest.mark.integration


def _override_user(user_id: str) -> AuthenticatedUser:
    """Stub AuthenticatedUser used when bypassing the real auth dependency."""
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


async def _cleanup(user_ids: list[str], bd_ids: list[int]) -> None:
    async with SessionLocal() as session:
        if user_ids:
            # FavoriteList CASCADE -> favorite_list_item rows.
            await session.execute(delete(FavoriteList).where(FavoriteList.user_id.in_(user_ids)))
            await session.execute(delete(UserVisit).where(UserVisit.user_id.in_(user_ids)))
            await session.execute(delete(AuthUser).where(AuthUser.id.in_(user_ids)))
        if bd_ids:
            await session.execute(delete(BrokerDealer).where(BrokerDealer.id.in_(bd_ids)))
        await session.commit()


def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


async def test_get_favorites_401_without_session_cookie() -> None:
    # No dependency override -> real get_current_user runs and rejects.
    async with _client() as client:
        response = await client.get("/api/v1/favorites")
    assert response.status_code == 401


async def test_post_favorite_404_when_bd_missing() -> None:
    user_id = await _seed_user()
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.post("/api/v1/broker-dealers/99999999/favorite")
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [])


async def test_delete_favorite_404_when_bd_missing() -> None:
    user_id = await _seed_user()
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.delete("/api/v1/broker-dealers/99999999/favorite")
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [])


async def test_post_favorite_200_returns_timestamp() -> None:
    user_id = await _seed_user()
    bd_id = await _seed_bd()
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.post(f"/api/v1/broker-dealers/{bd_id}/favorite")
        assert response.status_code == 200
        body = response.json()
        assert body["favorited"] is True
        assert isinstance(body["favorited_at"], str)
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [bd_id])


async def test_post_favorite_is_idempotent() -> None:
    user_id = await _seed_user()
    bd_id = await _seed_bd()
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            first = await client.post(f"/api/v1/broker-dealers/{bd_id}/favorite")
            second = await client.post(f"/api/v1/broker-dealers/{bd_id}/favorite")
        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["favorited_at"] == second.json()["favorited_at"]

        async with _client() as client:
            list_response = await client.get("/api/v1/favorites")
        assert list_response.status_code == 200
        assert list_response.json()["total"] == 1
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [bd_id])


async def test_delete_favorite_204_and_idempotent() -> None:
    user_id = await _seed_user()
    bd_id = await _seed_bd()
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            await client.post(f"/api/v1/broker-dealers/{bd_id}/favorite")
            first_delete = await client.delete(f"/api/v1/broker-dealers/{bd_id}/favorite")
            second_delete = await client.delete(f"/api/v1/broker-dealers/{bd_id}/favorite")

        assert first_delete.status_code == 204
        assert second_delete.status_code == 204  # idempotent when absent

        async with _client() as client:
            list_response = await client.get("/api/v1/favorites")
        assert list_response.json()["total"] == 0
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [bd_id])


async def test_get_favorites_sorts_created_at_desc() -> None:
    user_id = await _seed_user()
    bd_a = await _seed_bd(name="BD-A")
    bd_b = await _seed_bd(name="BD-B")
    bd_c = await _seed_bd(name="BD-C")
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            for bd_id in (bd_a, bd_b, bd_c):
                await client.post(f"/api/v1/broker-dealers/{bd_id}/favorite")

            response = await client.get("/api/v1/favorites")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 3
        ids = [item["id"] for item in body["items"]]
        # Newest first.
        assert ids == [bd_c, bd_b, bd_a]
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [bd_a, bd_b, bd_c])


async def test_favorites_are_user_scoped() -> None:
    user_a = await _seed_user()
    user_b = await _seed_user()
    bd_id = await _seed_bd()

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_a)
    try:
        async with _client() as client:
            await client.post(f"/api/v1/broker-dealers/{bd_id}/favorite")

        # Swap to user B and confirm they see nothing.
        app.dependency_overrides[get_current_user] = lambda: _override_user(user_b)
        async with _client() as client:
            response = await client.get("/api/v1/favorites")
        assert response.json() == {"items": [], "total": 0, "limit": 50, "offset": 0}
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_a, user_b], [bd_id])


async def test_profile_reflects_is_favorited_per_user() -> None:
    user_a = await _seed_user()
    user_b = await _seed_user()
    bd_id = await _seed_bd()

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_a)
    try:
        async with _client() as client:
            await client.post(f"/api/v1/broker-dealers/{bd_id}/favorite")
            a_profile = await client.get(f"/api/v1/broker-dealers/{bd_id}/profile")

        assert a_profile.status_code == 200
        a_body = a_profile.json()
        assert a_body["is_favorited"] is True
        assert a_body["favorited_at"] is not None

        # User B's profile view of the same BD reports is_favorited=False.
        app.dependency_overrides[get_current_user] = lambda: _override_user(user_b)
        async with _client() as client:
            b_profile = await client.get(f"/api/v1/broker-dealers/{bd_id}/profile")

        assert b_profile.status_code == 200
        b_body = b_profile.json()
        assert b_body["is_favorited"] is False
        assert b_body["favorited_at"] is None
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_a, user_b], [bd_id])
