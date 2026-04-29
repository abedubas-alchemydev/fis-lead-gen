"""API-layer tests for /broker-dealers/{id}/visit and /visits.

Integration-marked -- touches real Postgres. Auth is mocked via
``app.dependency_overrides`` to skip the BetterAuth cookie layer.
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
from app.models.favorite_list import FavoriteList
from app.models.user_visit import UserVisit
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


async def test_get_visits_401_without_session_cookie() -> None:
    async with _client() as client:
        response = await client.get("/api/v1/visits")
    assert response.status_code == 401


async def test_post_visit_404_when_bd_missing() -> None:
    user_id = await _seed_user()
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.post("/api/v1/broker-dealers/99999999/visit")
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [])


async def test_post_visit_first_call_creates_row_with_count_one() -> None:
    user_id = await _seed_user()
    bd_id = await _seed_bd()
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.post(f"/api/v1/broker-dealers/{bd_id}/visit")
        assert response.status_code == 204

        async with SessionLocal() as session:
            row = (
                await session.execute(
                    select(UserVisit).where(
                        UserVisit.user_id == user_id, UserVisit.bd_id == bd_id
                    )
                )
            ).scalar_one()
        assert row.visit_count == 1
        assert row.first_visited_at is not None
        assert row.first_visited_at == row.last_visited_at
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [bd_id])


async def test_post_visit_second_call_increments_count() -> None:
    user_id = await _seed_user()
    bd_id = await _seed_bd()
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            await client.post(f"/api/v1/broker-dealers/{bd_id}/visit")
            await client.post(f"/api/v1/broker-dealers/{bd_id}/visit")

        async with SessionLocal() as session:
            row = (
                await session.execute(
                    select(UserVisit).where(
                        UserVisit.user_id == user_id, UserVisit.bd_id == bd_id
                    )
                )
            ).scalar_one()
        assert row.visit_count == 2
        assert row.last_visited_at >= row.first_visited_at
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [bd_id])


async def test_get_visits_sorts_last_visited_desc() -> None:
    user_id = await _seed_user()
    bd_a = await _seed_bd(name="BD-A")
    bd_b = await _seed_bd(name="BD-B")
    bd_c = await _seed_bd(name="BD-C")
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            await client.post(f"/api/v1/broker-dealers/{bd_a}/visit")
            await client.post(f"/api/v1/broker-dealers/{bd_b}/visit")
            await client.post(f"/api/v1/broker-dealers/{bd_c}/visit")
            # Bump A forward so it's now the most-recent.
            await client.post(f"/api/v1/broker-dealers/{bd_a}/visit")

            response = await client.get("/api/v1/visits")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 3
        ids = [item["id"] for item in body["items"]]
        assert ids[0] == bd_a
        a_item = next(item for item in body["items"] if item["id"] == bd_a)
        assert a_item["visit_count"] == 2
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [bd_a, bd_b, bd_c])


async def test_visits_are_user_scoped() -> None:
    user_a = await _seed_user()
    user_b = await _seed_user()
    bd_id = await _seed_bd()

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_a)
    try:
        async with _client() as client:
            await client.post(f"/api/v1/broker-dealers/{bd_id}/visit")

        app.dependency_overrides[get_current_user] = lambda: _override_user(user_b)
        async with _client() as client:
            response = await client.get("/api/v1/visits")
        assert response.json() == {"items": [], "total": 0, "limit": 50, "offset": 0}
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_a, user_b], [bd_id])
