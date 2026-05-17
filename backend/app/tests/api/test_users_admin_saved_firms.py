"""API-layer tests for /users/{user_id}/saved-firms (admin-only).

Integration-marked — touches a real Postgres so the FK + polymorphic XOR
on ``favorite_list_item`` is exercised. Auth is mocked via
``app.dependency_overrides`` (same pattern as ``test_favorite_lists.py``);
the 401/403 cases run the real ``get_current_user`` or a viewer override
to prove the admin gate fires.
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
from app.models.investment_advisor import InvestmentAdvisor
from app.schemas.auth import AuthenticatedUser
from app.services.auth import get_current_user

pytestmark = pytest.mark.integration


def _override_user(user_id: str, role: str = "admin") -> AuthenticatedUser:
    return AuthenticatedUser(
        id=user_id,
        name="Admin Caller",
        email=f"{user_id}@example.com",
        role=role,
        session_expires_at=datetime(2099, 1, 1),
    )


async def _seed_user(role: str = "viewer", suffix: str = "") -> str:
    user_id = f"test-user-{secrets.token_hex(6)}{suffix}"
    async with SessionLocal() as session:
        session.add(
            AuthUser(
                id=user_id,
                name=f"Test {suffix or 'User'}",
                email=f"{user_id}@example.com",
                email_verified=False,
                role=role,
                status="active",
            )
        )
        await session.commit()
    return user_id


async def _seed_bd(name: str) -> int:
    async with SessionLocal() as session:
        bd = BrokerDealer(name=name, matched_source="edgar", is_deficient=False, status="active")
        session.add(bd)
        await session.commit()
        await session.refresh(bd)
        return bd.id


async def _seed_advisor(name: str) -> int:
    async with SessionLocal() as session:
        ad = InvestmentAdvisor(name=name, matched_source="iapd", status="active")
        session.add(ad)
        await session.commit()
        await session.refresh(ad)
        return ad.id


async def _seed_list(user_id: str, name: str = "Favorites", is_default: bool = True) -> int:
    async with SessionLocal() as session:
        fl = FavoriteList(user_id=user_id, name=name, is_default=is_default)
        session.add(fl)
        await session.commit()
        await session.refresh(fl)
        return fl.id


async def _seed_bd_item(list_id: int, bd_id: int) -> None:
    async with SessionLocal() as session:
        session.add(FavoriteListItem(list_id=list_id, broker_dealer_id=bd_id))
        await session.commit()


async def _seed_advisor_item(list_id: int, advisor_id: int) -> None:
    async with SessionLocal() as session:
        session.add(FavoriteListItem(list_id=list_id, advisor_id=advisor_id))
        await session.commit()


async def _cleanup(user_ids: list[str], bd_ids: list[int], advisor_ids: list[int]) -> None:
    async with SessionLocal() as session:
        if user_ids:
            await session.execute(delete(FavoriteList).where(FavoriteList.user_id.in_(user_ids)))
            await session.execute(delete(AuthUser).where(AuthUser.id.in_(user_ids)))
        if bd_ids:
            await session.execute(delete(BrokerDealer).where(BrokerDealer.id.in_(bd_ids)))
        if advisor_ids:
            await session.execute(delete(InvestmentAdvisor).where(InvestmentAdvisor.id.in_(advisor_ids)))
        await session.commit()


def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


async def test_saved_firms_401_without_session_cookie() -> None:
    async with _client() as client:
        response = await client.get("/api/v1/users/anyone/saved-firms")
    assert response.status_code == 401


async def test_saved_firms_403_when_caller_is_not_admin() -> None:
    caller_id = await _seed_user(role="viewer", suffix="-caller")
    target_id = await _seed_user(suffix="-target")
    app.dependency_overrides[get_current_user] = lambda: _override_user(caller_id, role="viewer")
    try:
        async with _client() as client:
            response = await client.get(f"/api/v1/users/{target_id}/saved-firms")
        assert response.status_code == 403
    finally:
        app.dependency_overrides.clear()
        await _cleanup([caller_id, target_id], [], [])


async def test_saved_firms_404_when_target_user_unknown() -> None:
    admin_id = await _seed_user(role="admin", suffix="-admin")
    app.dependency_overrides[get_current_user] = lambda: _override_user(admin_id, role="admin")
    try:
        async with _client() as client:
            response = await client.get("/api/v1/users/does-not-exist/saved-firms")
        assert response.status_code == 404
        assert response.json()["detail"] == "user_not_found"
    finally:
        app.dependency_overrides.clear()
        await _cleanup([admin_id], [], [])


async def test_saved_firms_returns_bds_and_advisors_across_lists() -> None:
    admin_id = await _seed_user(role="admin", suffix="-admin")
    target_id = await _seed_user(suffix="-target")

    default_list = await _seed_list(target_id, name="Favorites", is_default=True)
    custom_list = await _seed_list(target_id, name="Targets", is_default=False)

    bd_a = await _seed_bd("ACME Securities")
    bd_b = await _seed_bd("Bridge Capital LLC")
    advisor_a = await _seed_advisor("Coronet Advisors LP")

    await _seed_bd_item(default_list, bd_a)
    await _seed_bd_item(custom_list, bd_b)
    await _seed_advisor_item(default_list, advisor_a)

    app.dependency_overrides[get_current_user] = lambda: _override_user(admin_id, role="admin")
    try:
        async with _client() as client:
            response = await client.get(f"/api/v1/users/{target_id}/saved-firms")
        assert response.status_code == 200
        body = response.json()

        assert body["total"] == 3
        assert body["user"]["id"] == target_id
        assert {row["target_name"] for row in body["items"]} == {
            "ACME Securities",
            "Bridge Capital LLC",
            "Coronet Advisors LP",
        }
        types = {row["item_type"] for row in body["items"]}
        assert types == {"broker_dealer", "advisor"}

        list_summary = {row["name"]: row for row in body["lists"]}
        assert list_summary["Favorites"]["item_count"] == 2
        assert list_summary["Favorites"]["is_default"] is True
        assert list_summary["Targets"]["item_count"] == 1
        assert list_summary["Targets"]["is_default"] is False
    finally:
        app.dependency_overrides.clear()
        await _cleanup([admin_id, target_id], [bd_a, bd_b], [advisor_a])


async def test_saved_firms_list_id_filter_narrows_results() -> None:
    admin_id = await _seed_user(role="admin", suffix="-admin")
    target_id = await _seed_user(suffix="-target")

    default_list = await _seed_list(target_id, name="Favorites", is_default=True)
    custom_list = await _seed_list(target_id, name="Targets", is_default=False)

    bd_a = await _seed_bd("ACME Securities")
    bd_b = await _seed_bd("Bridge Capital LLC")

    await _seed_bd_item(default_list, bd_a)
    await _seed_bd_item(custom_list, bd_b)

    app.dependency_overrides[get_current_user] = lambda: _override_user(admin_id, role="admin")
    try:
        async with _client() as client:
            response = await client.get(
                f"/api/v1/users/{target_id}/saved-firms",
                params={"list_id": custom_list},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["items"][0]["target_name"] == "Bridge Capital LLC"
        # ``lists`` summary is unaffected by the filter so the pill row
        # always reflects the full picture.
        assert {row["name"] for row in body["lists"]} == {"Favorites", "Targets"}
    finally:
        app.dependency_overrides.clear()
        await _cleanup([admin_id, target_id], [bd_a, bd_b], [])


async def test_saved_firms_pagination_respects_limit_and_offset() -> None:
    admin_id = await _seed_user(role="admin", suffix="-admin")
    target_id = await _seed_user(suffix="-target")
    default_list = await _seed_list(target_id, name="Favorites", is_default=True)

    bd_ids = [await _seed_bd(f"BD-{i:03d}") for i in range(5)]
    for bd_id in bd_ids:
        await _seed_bd_item(default_list, bd_id)

    app.dependency_overrides[get_current_user] = lambda: _override_user(admin_id, role="admin")
    try:
        async with _client() as client:
            page1 = await client.get(
                f"/api/v1/users/{target_id}/saved-firms",
                params={"limit": 2, "offset": 0},
            )
            page2 = await client.get(
                f"/api/v1/users/{target_id}/saved-firms",
                params={"limit": 2, "offset": 2},
            )
        assert page1.status_code == 200 and page2.status_code == 200
        body1 = page1.json()
        body2 = page2.json()
        assert body1["total"] == 5 and body2["total"] == 5
        assert len(body1["items"]) == 2 and len(body2["items"]) == 2
        assert {row["target_id"] for row in body1["items"]}.isdisjoint(
            {row["target_id"] for row in body2["items"]}
        )
    finally:
        app.dependency_overrides.clear()
        await _cleanup([admin_id, target_id], bd_ids, [])
