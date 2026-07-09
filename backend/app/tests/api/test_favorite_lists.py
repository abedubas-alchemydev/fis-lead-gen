"""API-layer tests for /favorite-lists (#17 phases 1-2).

Integration-marked — touches a real Postgres so the FK + UNIQUE constraints
exercise. Auth is mocked via ``app.dependency_overrides`` (same pattern as
``test_favorites.py``); the 401 cases run the real ``get_current_user`` to
prove it rejects pre-DB.
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import delete, select

from app.db.session import SessionLocal
from app.main import app
from app.models.auth import AuthUser
from app.models.bank import Bank
from app.models.broker_dealer import BrokerDealer
from app.models.favorite_list import FavoriteList, FavoriteListItem
from app.models.investment_advisor import InvestmentAdvisor
from app.models.pipeline_run import PipelineRun
from app.schemas.auth import AuthenticatedUser
from app.services import favorite_list_enrich_orchestrator as fl_enrich
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


def _override_user_with_advisors_feature(user_id: str) -> AuthenticatedUser:
    """Override variant for /investment-advisors/{id}/favorite-lists.

    That endpoint goes through ensure_feature(INVESTMENT_ADVISORS); the
    base _override_user returns a viewer with no grants, which 403s.
    Mirrors test_broker_dealers.py where the override carries
    feature_permissions=["master_list"] for the same reason.
    """
    return AuthenticatedUser(
        id=user_id,
        name="Test User",
        email=f"{user_id}@example.com",
        role="viewer",
        feature_permissions=["investment_advisors"],
        session_expires_at=datetime(2099, 1, 1),
    )


def _override_user_with_banks_feature(user_id: str) -> AuthenticatedUser:
    """Override variant for /banks/{bank_id}/favorite-lists.

    That endpoint goes through ensure_feature(BANKS) — same reasoning as
    the advisors override above.
    """
    return AuthenticatedUser(
        id=user_id,
        name="Test User",
        email=f"{user_id}@example.com",
        role="viewer",
        feature_permissions=["banks"],
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
    bank_ids: list[int] | None = None,
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
        if bank_ids:
            await session.execute(delete(Bank).where(Bank.id.in_(bank_ids)))
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


async def _seed_bank(
    name: str = "Test Bank",
    city: str | None = "Testville",
    state: str | None = "TX",
) -> int:
    async with SessionLocal() as session:
        bank = Bank(name=name, city=city, state=state)
        session.add(bank)
        await session.commit()
        await session.refresh(bank)
        return bank.id


async def _seed_list_bank_item(list_id: int, bank_id: int) -> None:
    async with SessionLocal() as session:
        session.add(FavoriteListItem(list_id=list_id, bank_id=bank_id))
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

    app.dependency_overrides[get_current_user] = (
        lambda: _override_user_with_advisors_feature(user_id)
    )
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
    app.dependency_overrides[get_current_user] = (
        lambda: _override_user_with_advisors_feature(user_id)
    )
    try:
        async with _client() as client:
            response = await client.get(
                "/api/v1/investment-advisors/99999999/favorite-lists"
            )
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [])


# ── Bank parallels ─────────────────────────────────────────────────────────


async def test_add_bank_item_401_without_session_cookie() -> None:
    async with _client() as client:
        response = await client.post(
            "/api/v1/favorite-lists/1/bank-items", json={"bank_id": 1}
        )
    assert response.status_code == 401


async def test_add_bank_item_inserts_and_is_idempotent() -> None:
    user_id = await _seed_user()
    list_id = await _seed_default_list(user_id)
    bank_id = await _seed_bank(name="Bank-Add")
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            first = await client.post(
                f"/api/v1/favorite-lists/{list_id}/bank-items",
                json={"bank_id": bank_id},
            )
        assert first.status_code == 200
        first_body = first.json()
        assert first_body["bank_id"] == bank_id
        assert first_body["bank_name"] == "Bank-Add"
        first_added_at = first_body["added_at"]

        async with _client() as client:
            second = await client.post(
                f"/api/v1/favorite-lists/{list_id}/bank-items",
                json={"bank_id": bank_id},
            )
        assert second.status_code == 200
        assert second.json()["added_at"] == first_added_at

        async with SessionLocal() as session:
            count_result = await session.execute(
                select(FavoriteListItem).where(
                    FavoriteListItem.list_id == list_id,
                    FavoriteListItem.bank_id == bank_id,
                )
            )
            rows = count_result.scalars().all()
            assert len(rows) == 1
            assert rows[0].broker_dealer_id is None
            assert rows[0].advisor_id is None
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [], bank_ids=[bank_id])


async def test_add_bank_item_404_for_unknown_bank() -> None:
    user_id = await _seed_user()
    list_id = await _seed_default_list(user_id)
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.post(
                f"/api/v1/favorite-lists/{list_id}/bank-items",
                json={"bank_id": 99999999},
            )
        assert response.status_code == 404
        assert response.json()["detail"] == "Bank not found"
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [])


async def test_add_bank_item_404_for_foreign_list() -> None:
    owner = await _seed_user()
    intruder = await _seed_user()
    list_id = await _seed_default_list(owner)
    bank_id = await _seed_bank(name="Bank-Foreign")
    app.dependency_overrides[get_current_user] = lambda: _override_user(intruder)
    try:
        async with _client() as client:
            response = await client.post(
                f"/api/v1/favorite-lists/{list_id}/bank-items",
                json={"bank_id": bank_id},
            )
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()
        await _cleanup([owner, intruder], [], bank_ids=[bank_id])


async def test_remove_bank_item_401_without_session_cookie() -> None:
    async with _client() as client:
        response = await client.delete("/api/v1/favorite-lists/1/bank-items/1")
    assert response.status_code == 401


async def test_remove_bank_item_removes_present_row() -> None:
    user_id = await _seed_user()
    list_id = await _seed_default_list(user_id)
    bank_id = await _seed_bank(name="Bank-Remove")
    await _seed_list_bank_item(list_id, bank_id)

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.delete(
                f"/api/v1/favorite-lists/{list_id}/bank-items/{bank_id}"
            )
        assert response.status_code == 204

        async with SessionLocal() as session:
            remaining = await session.execute(
                select(FavoriteListItem).where(
                    FavoriteListItem.list_id == list_id,
                    FavoriteListItem.bank_id == bank_id,
                )
            )
            assert remaining.scalar_one_or_none() is None
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [], bank_ids=[bank_id])


async def test_remove_bank_item_404_when_absent() -> None:
    user_id = await _seed_user()
    list_id = await _seed_default_list(user_id)
    bank_id = await _seed_bank(name="Bank-Absent")

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.delete(
                f"/api/v1/favorite-lists/{list_id}/bank-items/{bank_id}"
            )
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [], bank_ids=[bank_id])


async def test_bank_batch_add_401_without_session_cookie() -> None:
    async with _client() as client:
        response = await client.post(
            "/api/v1/favorite-lists/1/bank-items/batch",
            json={"bank_ids": [1]},
        )
    assert response.status_code == 401


async def test_bank_batch_add_inserts_all_when_none_existing() -> None:
    user_id = await _seed_user()
    list_id = await _seed_default_list(user_id)
    a = await _seed_bank(name="Bank-Batch-A")
    b = await _seed_bank(name="Bank-Batch-B")
    c = await _seed_bank(name="Bank-Batch-C")

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.post(
                f"/api/v1/favorite-lists/{list_id}/bank-items/batch",
                json={"bank_ids": [a, b, c]},
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
        await _cleanup([user_id], [], bank_ids=[a, b, c])


async def test_bank_batch_add_skips_existing_and_unknown() -> None:
    user_id = await _seed_user()
    list_id = await _seed_default_list(user_id)
    a = await _seed_bank(name="Bank-Batch-Skip-A")
    b = await _seed_bank(name="Bank-Batch-Skip-B")
    await _seed_list_bank_item(list_id, a)
    fake = 9_999_999

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.post(
                f"/api/v1/favorite-lists/{list_id}/bank-items/batch",
                json={"bank_ids": [a, b, fake]},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["added"] == 1
        assert body["skipped_existing"] == 1
        assert body["skipped_unknown"] == [fake]
        assert await _count_items(list_id) == 2
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [], bank_ids=[a, b])


async def test_bank_batch_add_dedupes_request() -> None:
    user_id = await _seed_user()
    list_id = await _seed_default_list(user_id)
    a = await _seed_bank(name="Bank-Batch-Dedupe-A")
    b = await _seed_bank(name="Bank-Batch-Dedupe-B")

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.post(
                f"/api/v1/favorite-lists/{list_id}/bank-items/batch",
                json={"bank_ids": [a, a, b, b, a]},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["added"] == 2
        assert body["skipped_existing"] == 0
        assert body["skipped_unknown"] == []
        assert await _count_items(list_id) == 2
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [], bank_ids=[a, b])


async def test_bank_batch_add_404_for_foreign_list() -> None:
    owner = await _seed_user()
    intruder = await _seed_user()
    list_id = await _seed_default_list(owner)
    bank_id = await _seed_bank(name="Bank-Batch-Foreign")

    app.dependency_overrides[get_current_user] = lambda: _override_user(intruder)
    try:
        async with _client() as client:
            response = await client.post(
                f"/api/v1/favorite-lists/{list_id}/bank-items/batch",
                json={"bank_ids": [bank_id]},
            )
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()
        await _cleanup([owner, intruder], [], bank_ids=[bank_id])


async def test_bank_batch_add_422_for_oversize_payload() -> None:
    user_id = await _seed_user()
    list_id = await _seed_default_list(user_id)

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.post(
                f"/api/v1/favorite-lists/{list_id}/bank-items/batch",
                json={"bank_ids": list(range(1, 202))},
            )
        assert response.status_code == 422
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [])


async def test_get_items_returns_mixed_items_including_bank() -> None:
    """Bank rows join the polymorphic GET stream with city/state hydration."""
    user_id = await _seed_user()
    list_id = await _seed_default_list(user_id)
    bd_id = await _seed_bd(name="Mixed-BD-2")
    bank_id = await _seed_bank(name="Mixed-Bank", city="Austin", state="TX")
    await _seed_list_item(list_id, bd_id)
    await _seed_list_bank_item(list_id, bank_id)

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.get(f"/api/v1/favorite-lists/{list_id}/items")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 2
        kinds = {item["entity_type"] for item in body["items"]}
        assert kinds == {"broker_dealer", "bank"}
        for item in body["items"]:
            if item["entity_type"] == "bank":
                assert item["bank_id"] == bank_id
                assert item["bank_name"] == "Mixed-Bank"
                assert item["bank_city"] == "Austin"
                assert item["bank_state"] == "TX"
                assert item["broker_dealer_id"] is None
                assert item["broker_dealer_name"] is None
            else:
                assert item["broker_dealer_id"] == bd_id
                assert item["broker_dealer_name"] == "Mixed-BD-2"
                assert item["bank_id"] is None
                assert item["bank_name"] is None
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [bd_id], bank_ids=[bank_id])


async def test_get_bank_favorite_lists_returns_membership() -> None:
    user_id = await _seed_user()
    list_id = await _seed_default_list(user_id)
    bank_id = await _seed_bank(name="Membership-Bank")
    await _seed_list_bank_item(list_id, bank_id)

    app.dependency_overrides[get_current_user] = (
        lambda: _override_user_with_banks_feature(user_id)
    )
    try:
        async with _client() as client:
            response = await client.get(f"/api/v1/banks/{bank_id}/favorite-lists")
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["is_member"] is True
        assert body[0]["is_default"] is True
        assert body[0]["item_count"] == 1
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [], bank_ids=[bank_id])


async def test_get_bank_favorite_lists_404_for_unknown_bank() -> None:
    user_id = await _seed_user()
    await _seed_default_list(user_id)
    app.dependency_overrides[get_current_user] = (
        lambda: _override_user_with_banks_feature(user_id)
    )
    try:
        async with _client() as client:
            response = await client.get("/api/v1/banks/99999999/favorite-lists")
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [])


# ── POST /favorite-lists/{list_id}/enrich (bulk-enrich dispatch) ──────────────
# These stub the background runner so no providers are hit: the endpoint's job
# is owner-scope + enumerate + dispatch, which is what we assert here.


async def _cleanup_runs(run_ids: list[int]) -> None:
    if not run_ids:
        return
    async with SessionLocal() as session:
        await session.execute(delete(PipelineRun).where(PipelineRun.id.in_(run_ids)))
        await session.commit()


async def test_enrich_favorite_list_401_without_session_cookie() -> None:
    async with _client() as client:
        response = await client.post("/api/v1/favorite-lists/1/enrich")
    assert response.status_code == 401


async def test_enrich_favorite_list_404_when_not_owner() -> None:
    owner_id = await _seed_user()
    other_id = await _seed_user()
    list_id = await _seed_default_list(owner_id)

    # Authenticated as ``other_id`` — must not be able to enrich owner's list.
    app.dependency_overrides[get_current_user] = lambda: _override_user(other_id)
    try:
        async with _client() as client:
            response = await client.post(f"/api/v1/favorite-lists/{list_id}/enrich")
        assert response.status_code == 404
        assert response.json()["detail"] == "favorite_list_not_found"
    finally:
        app.dependency_overrides.clear()
        await _cleanup([owner_id, other_id], [])


async def test_enrich_empty_list_returns_skipped_no_run() -> None:
    user_id = await _seed_user()
    list_id = await _seed_default_list(user_id)

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.post(f"/api/v1/favorite-lists/{list_id}/enrich")
        assert response.status_code == 200
        body = response.json()
        assert body == {
            "run_id": None,
            "status": "skipped",
            "list_id": list_id,
            "total": 0,
            "reason": "List is empty.",
        }
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [])


async def test_enrich_dispatches_job_and_creates_parent_run(monkeypatch) -> None:
    user_id = await _seed_user()
    list_id = await _seed_default_list(user_id)
    bd_id = await _seed_bd(name="Enrich BD")
    await _seed_list_item(list_id, bd_id)

    dispatched: dict = {}

    async def _stub_runner(parent_run_id, lid, items, *, user):  # noqa: ANN001
        dispatched["parent_run_id"] = parent_run_id
        dispatched["list_id"] = lid
        dispatched["item_count"] = len(items)
        dispatched["entity_types"] = [it.entity_type for it in items]

    # Stub the module-level name the endpoint dispatches so no providers run.
    monkeypatch.setattr(fl_enrich, "run_favorite_list_enrich_background", _stub_runner)
    import app.api.v1.endpoints.favorite_lists as fl_endpoint

    monkeypatch.setattr(fl_endpoint, "run_favorite_list_enrich_background", _stub_runner)

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    run_id = None
    try:
        async with _client() as client:
            response = await client.post(f"/api/v1/favorite-lists/{list_id}/enrich")
        assert response.status_code == 202
        body = response.json()
        run_id = body["run_id"]
        assert run_id is not None
        assert body["status"] == "queued"
        assert body["total"] == 1

        # The background job was scheduled with the enumerated item.
        assert dispatched["item_count"] == 1
        assert dispatched["entity_types"] == ["broker_dealer"]
        assert dispatched["parent_run_id"] == run_id

        # A parent PipelineRun row was persisted with the progress skeleton.
        async with SessionLocal() as session:
            run = await session.get(PipelineRun, run_id)
            assert run is not None
            assert run.pipeline_name == fl_enrich.FAVORITE_LIST_ENRICH_PIPELINE_NAME
            assert run.status == "queued"
            assert run.total_items == 1
            notes = json.loads(run.notes)
            assert notes["list_id"] == list_id
            assert notes["total"] == 1
            assert notes["items"] == []
    finally:
        app.dependency_overrides.clear()
        await _cleanup_runs([run_id] if run_id else [])
        await _cleanup([user_id], [bd_id])


async def test_enrich_attaches_to_in_flight_run(monkeypatch) -> None:
    user_id = await _seed_user()
    list_id = await _seed_default_list(user_id)
    bd_id = await _seed_bd(name="In-flight BD")
    await _seed_list_item(list_id, bd_id)

    async def _stub_runner(*args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        return None

    monkeypatch.setattr(fl_enrich, "run_favorite_list_enrich_background", _stub_runner)
    import app.api.v1.endpoints.favorite_lists as fl_endpoint

    monkeypatch.setattr(fl_endpoint, "run_favorite_list_enrich_background", _stub_runner)

    # Pre-seed a running bulk-enrich run for THIS (user, list): same trigger
    # source + notes marker the endpoint writes, started just now.
    trigger = f"favorite_list_enrich:{user_id}@example.com"
    seeded_run_id = None
    async with SessionLocal() as session:
        run = PipelineRun(
            pipeline_name=fl_enrich.FAVORITE_LIST_ENRICH_PIPELINE_NAME,
            trigger_source=trigger,
            status="running",
            total_items=1,
            notes=json.dumps({"list_id": list_id, "stage": "running", "items": []}),
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        seeded_run_id = run.id

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.post(f"/api/v1/favorite-lists/{list_id}/enrich")
        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "in_flight"
        assert body["run_id"] == seeded_run_id  # attached, no duplicate queued
    finally:
        app.dependency_overrides.clear()
        await _cleanup_runs([seeded_run_id] if seeded_run_id else [])
        await _cleanup([user_id], [bd_id])


async def test_enrich_attaches_to_long_run_with_fresh_heartbeat(monkeypatch) -> None:
    """The double-spend fix: a bulk run whose ``started_at`` is well past the
    10-min staleness threshold but whose heartbeat (notes.last_progress_at) is
    fresh must still read as in-flight — so the FE's re-attach POST attaches
    instead of spawning a SECOND worker that re-bills every provider."""
    user_id = await _seed_user()
    list_id = await _seed_default_list(user_id)
    bd_id = await _seed_bd(name="Heartbeat BD")
    await _seed_list_item(list_id, bd_id)

    async def _stub_runner(*args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        return None

    monkeypatch.setattr(fl_enrich, "run_favorite_list_enrich_background", _stub_runner)
    import app.api.v1.endpoints.favorite_lists as fl_endpoint

    monkeypatch.setattr(fl_endpoint, "run_favorite_list_enrich_background", _stub_runner)

    trigger = f"favorite_list_enrich:{user_id}@example.com"
    seeded_run_id = None
    async with SessionLocal() as session:
        run = PipelineRun(
            pipeline_name=fl_enrich.FAVORITE_LIST_ENRICH_PIPELINE_NAME,
            trigger_source=trigger,
            status="running",
            total_items=1,
            notes=json.dumps(
                {
                    "list_id": list_id,
                    "stage": "running",
                    # Started 30 min ago (>> 10 min stale threshold) …
                    "last_progress_at": datetime.now(timezone.utc).isoformat(),  # … but advancing NOW.
                    "items": [],
                }
            ),
            # started_at ancient — a started_at-based guard would wrongly treat
            # this as an orphan and start a duplicate run.
            started_at=datetime.now(timezone.utc) - timedelta(minutes=30),
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        seeded_run_id = run.id

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.post(f"/api/v1/favorite-lists/{list_id}/enrich")
        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "in_flight"
        assert body["run_id"] == seeded_run_id  # attached, not duplicated

        # No second parent run was created for this (user, list).
        async with SessionLocal() as session:
            runs = (
                (
                    await session.execute(
                        select(PipelineRun).where(
                            PipelineRun.trigger_source == trigger,
                            PipelineRun.pipeline_name
                            == fl_enrich.FAVORITE_LIST_ENRICH_PIPELINE_NAME,
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(runs) == 1
    finally:
        app.dependency_overrides.clear()
        await _cleanup_runs([seeded_run_id] if seeded_run_id else [])
        await _cleanup([user_id], [bd_id])


async def test_enrich_attaches_when_per_firm_progress_quiet_but_lifetime_beat_fresh(
    monkeypatch,
) -> None:
    """Core double-spend fix (finding M1) at the endpoint: a run whose per-firm
    pointer has been QUIET for >10 min (a firm stuck mid-dispatch) but whose
    LIFETIME heartbeat is fresh must still read in-flight, so the FE re-click
    ("Close and click Bulk enrich again") ATTACHES instead of spawning a second
    worker that re-bills every provider for the overlapping firms."""
    user_id = await _seed_user()
    list_id = await _seed_default_list(user_id)
    bd_id = await _seed_bd(name="Stuck-Firm BD")
    await _seed_list_item(list_id, bd_id)

    async def _stub_runner(*args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        return None

    monkeypatch.setattr(fl_enrich, "run_favorite_list_enrich_background", _stub_runner)
    import app.api.v1.endpoints.favorite_lists as fl_endpoint

    monkeypatch.setattr(fl_endpoint, "run_favorite_list_enrich_background", _stub_runner)

    trigger = f"favorite_list_enrich:{user_id}@example.com"
    seeded_run_id = None
    async with SessionLocal() as session:
        run = PipelineRun(
            pipeline_name=fl_enrich.FAVORITE_LIST_ENRICH_PIPELINE_NAME,
            trigger_source=trigger,
            status="running",
            total_items=9,
            notes=json.dumps(
                {
                    "list_id": list_id,
                    "stage": "running",
                    "total": 9,
                    # Per-firm pointer FROZEN (firm #3 stuck mid-chain); no per-firm
                    # write has landed in 20 min …
                    "current_index": 2,
                    "current_name": "Stuck-Firm BD",
                    "items": [],
                    # … but the lifetime beat re-stamped liveness just now, which is
                    # the only signal is_run_stale reads.
                    "last_progress_at": datetime.now(timezone.utc).isoformat(),
                }
            ),
            started_at=datetime.now(timezone.utc) - timedelta(minutes=40),
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        seeded_run_id = run.id

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.post(f"/api/v1/favorite-lists/{list_id}/enrich")
        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "in_flight"
        assert body["run_id"] == seeded_run_id  # attached, not duplicated

        async with SessionLocal() as session:
            runs = (
                (
                    await session.execute(
                        select(PipelineRun).where(
                            PipelineRun.trigger_source == trigger,
                            PipelineRun.pipeline_name
                            == fl_enrich.FAVORITE_LIST_ENRICH_PIPELINE_NAME,
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(runs) == 1  # no second worker spawned
    finally:
        app.dependency_overrides.clear()
        await _cleanup_runs([seeded_run_id] if seeded_run_id else [])
        await _cleanup([user_id], [bd_id])


async def test_enrich_starts_fresh_when_prior_run_heartbeat_is_stale(monkeypatch) -> None:
    """Conversely, a prior run whose heartbeat has gone quiet past the threshold
    is a genuine orphan: the endpoint ignores it and queues a fresh run."""
    user_id = await _seed_user()
    list_id = await _seed_default_list(user_id)
    bd_id = await _seed_bd(name="Orphan BD")
    await _seed_list_item(list_id, bd_id)

    async def _stub_runner(*args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        return None

    monkeypatch.setattr(fl_enrich, "run_favorite_list_enrich_background", _stub_runner)
    import app.api.v1.endpoints.favorite_lists as fl_endpoint

    monkeypatch.setattr(fl_endpoint, "run_favorite_list_enrich_background", _stub_runner)

    trigger = f"favorite_list_enrich:{user_id}@example.com"
    orphan_id = None
    async with SessionLocal() as session:
        run = PipelineRun(
            pipeline_name=fl_enrich.FAVORITE_LIST_ENRICH_PIPELINE_NAME,
            trigger_source=trigger,
            status="running",
            total_items=1,
            notes=json.dumps(
                {
                    "list_id": list_id,
                    "stage": "running",
                    # Heartbeat 25 min old → worker is dead.
                    "last_progress_at": (
                        datetime.now(timezone.utc) - timedelta(minutes=25)
                    ).isoformat(),
                    "items": [],
                }
            ),
            started_at=datetime.now(timezone.utc) - timedelta(minutes=30),
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        orphan_id = run.id

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    new_run_id = None
    try:
        async with _client() as client:
            response = await client.post(f"/api/v1/favorite-lists/{list_id}/enrich")
        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "queued"
        new_run_id = body["run_id"]
        assert new_run_id != orphan_id  # a fresh run, not the orphan
    finally:
        app.dependency_overrides.clear()
        await _cleanup_runs([r for r in (orphan_id, new_run_id) if r])
        await _cleanup([user_id], [bd_id])


async def test_enrich_concurrent_dispatch_attaches_instead_of_duplicating(
    monkeypatch,
) -> None:
    """Dispatch-race guard: two simultaneous clicks must yield exactly ONE
    parent run. The advisory lock serializes them so the second sees the first's
    committed run and attaches (in_flight) rather than double-spending."""
    import asyncio

    user_id = await _seed_user()
    list_id = await _seed_default_list(user_id)
    bd_id = await _seed_bd(name="Race BD")
    await _seed_list_item(list_id, bd_id)

    async def _stub_runner(*args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        return None

    monkeypatch.setattr(fl_enrich, "run_favorite_list_enrich_background", _stub_runner)
    import app.api.v1.endpoints.favorite_lists as fl_endpoint

    monkeypatch.setattr(fl_endpoint, "run_favorite_list_enrich_background", _stub_runner)

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    trigger = f"favorite_list_enrich:{user_id}@example.com"
    run_ids: list[int] = []
    try:
        # Two independent clients so each request gets its own connection —
        # required for the advisory lock to actually serialize them.
        async with _client() as c1, _client() as c2:
            r1, r2 = await asyncio.gather(
                c1.post(f"/api/v1/favorite-lists/{list_id}/enrich"),
                c2.post(f"/api/v1/favorite-lists/{list_id}/enrich"),
            )
        assert r1.status_code == 202 and r2.status_code == 202
        statuses = {r1.json()["status"], r2.json()["status"]}
        # One queued the run, the other attached to it.
        assert statuses == {"queued", "in_flight"}
        assert r1.json()["run_id"] == r2.json()["run_id"]

        async with SessionLocal() as session:
            rows = (
                (
                    await session.execute(
                        select(PipelineRun).where(
                            PipelineRun.trigger_source == trigger,
                            PipelineRun.pipeline_name
                            == fl_enrich.FAVORITE_LIST_ENRICH_PIPELINE_NAME,
                        )
                    )
                )
                .scalars()
                .all()
            )
        run_ids = [r.id for r in rows]
        assert len(run_ids) == 1  # no duplicate parent run
    finally:
        app.dependency_overrides.clear()
        await _cleanup_runs(run_ids)
        await _cleanup([user_id], [bd_id])
