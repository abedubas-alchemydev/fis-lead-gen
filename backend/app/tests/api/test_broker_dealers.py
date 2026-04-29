"""API-layer tests for broker_dealers endpoints (#17 phase 3 BE).

Currently scoped to ``GET /broker-dealers/{firm_id}/favorite-lists`` — the
list-picker endpoint added in phase 3 BE that returns the calling user's
lists each augmented with ``is_member`` for ``firm_id``.

Integration-marked: hits a real Postgres so the FK constraints and the
EXISTS sub-select in the query exercise. Auth is mocked via
``app.dependency_overrides`` (same pattern as ``test_favorite_lists.py``);
the 401 case runs the real ``get_current_user`` to prove it rejects pre-DB.
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


async def _seed_list(user_id: str, name: str, is_default: bool = False) -> int:
    async with SessionLocal() as session:
        fl = FavoriteList(user_id=user_id, name=name, is_default=is_default)
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
            # FavoriteList CASCADE -> favorite_list_item rows.
            await session.execute(delete(FavoriteList).where(FavoriteList.user_id.in_(user_ids)))
            await session.execute(delete(AuthUser).where(AuthUser.id.in_(user_ids)))
        if bd_ids:
            await session.execute(delete(BrokerDealer).where(BrokerDealer.id.in_(bd_ids)))
        await session.commit()


def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


async def test_get_firm_favorite_lists_401_without_session_cookie() -> None:
    """No dependency override -> real get_current_user runs and rejects."""
    bd_id = await _seed_bd()
    try:
        async with _client() as client:
            response = await client.get(f"/api/v1/broker-dealers/{bd_id}/favorite-lists")
        assert response.status_code == 401
    finally:
        await _cleanup([], [bd_id])


async def test_get_firm_favorite_lists_404_when_firm_missing() -> None:
    user_id = await _seed_user()
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.get(
                "/api/v1/broker-dealers/99999999/favorite-lists"
            )
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [])


async def test_get_firm_favorite_lists_returns_membership_per_list() -> None:
    """Firm in default list -> is_member=true on default, false elsewhere.

    The default list has the firm pinned; a second custom list does not.
    Default ordering rule (default first, then created_at asc) also exercised.
    """
    user_id = await _seed_user()
    bd_id = await _seed_bd(name="Target BD")

    default_list_id = await _seed_list(user_id, "Favorites", is_default=True)
    custom_list_id = await _seed_list(user_id, "Watchlist A", is_default=False)
    await _seed_list_item(default_list_id, bd_id)

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.get(
                f"/api/v1/broker-dealers/{bd_id}/favorite-lists"
            )
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 2

        # Default list ordered first, contains the firm.
        assert body[0]["id"] == default_list_id
        assert body[0]["name"] == "Favorites"
        assert body[0]["is_default"] is True
        assert body[0]["is_member"] is True
        assert body[0]["item_count"] == 1

        # Custom list second, does not contain the firm.
        assert body[1]["id"] == custom_list_id
        assert body[1]["name"] == "Watchlist A"
        assert body[1]["is_default"] is False
        assert body[1]["is_member"] is False
        assert body[1]["item_count"] == 0
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [bd_id])


async def test_get_firm_favorite_lists_returns_empty_when_user_has_no_lists() -> None:
    user_id = await _seed_user()
    bd_id = await _seed_bd()

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.get(
                f"/api/v1/broker-dealers/{bd_id}/favorite-lists"
            )
        assert response.status_code == 200
        assert response.json() == []
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [bd_id])


async def test_get_firm_favorite_lists_is_user_scoped() -> None:
    """Lists from another user must not appear in the response."""
    owner = await _seed_user()
    intruder = await _seed_user()
    bd_id = await _seed_bd()

    owner_list = await _seed_list(owner, "Owner Favorites", is_default=True)
    await _seed_list_item(owner_list, bd_id)

    app.dependency_overrides[get_current_user] = lambda: _override_user(intruder)
    try:
        async with _client() as client:
            response = await client.get(
                f"/api/v1/broker-dealers/{bd_id}/favorite-lists"
            )
        assert response.status_code == 200
        assert response.json() == []
    finally:
        app.dependency_overrides.clear()
        await _cleanup([owner, intruder], [bd_id])
