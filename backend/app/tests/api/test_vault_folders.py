"""API-layer tests for /vault/folders.

Integration-marked — touches real Postgres so the FK + UNIQUE
constraints exercise. Auth is mocked via ``app.dependency_overrides``
(same pattern as ``test_favorite_lists.py``); the 401 cases run the
real ``get_current_user`` to prove it rejects pre-DB.
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
from app.models.vault_folder import VaultFolder
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


async def _seed_folder(
    user_id: str, name: str = "Custody", description: str = "desc"
) -> int:
    async with SessionLocal() as session:
        folder = VaultFolder(user_id=user_id, name=name, description=description)
        session.add(folder)
        await session.commit()
        await session.refresh(folder)
        return folder.id


async def _cleanup(user_ids: list[str]) -> None:
    async with SessionLocal() as session:
        if user_ids:
            await session.execute(
                delete(VaultFolder).where(VaultFolder.user_id.in_(user_ids))
            )
            await session.execute(delete(AuthUser).where(AuthUser.id.in_(user_ids)))
            await session.commit()


def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


# ── 401 (no session) ────────────────────────────────────────────────────────


async def test_list_folders_401_without_session() -> None:
    async with _client() as client:
        response = await client.get("/api/v1/vault/folders")
    assert response.status_code == 401


async def test_create_folder_401_without_session() -> None:
    async with _client() as client:
        response = await client.post(
            "/api/v1/vault/folders", json={"name": "X", "description": ""}
        )
    assert response.status_code == 401


# ── Happy paths ─────────────────────────────────────────────────────────────


async def test_list_folders_returns_only_callers_rows() -> None:
    user_a = await _seed_user()
    user_b = await _seed_user()
    folder_a = await _seed_folder(user_a, "Custody-A", "for A")
    await _seed_folder(user_b, "Stock Loan", "for B")

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_a)
    try:
        async with _client() as client:
            response = await client.get("/api/v1/vault/folders")
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["id"] == folder_a
        assert body[0]["name"] == "Custody-A"
        assert body[0]["description"] == "for A"
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        await _cleanup([user_a, user_b])


async def test_create_folder_201_then_409_on_duplicate_name() -> None:
    user_id = await _seed_user()
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            r1 = await client.post(
                "/api/v1/vault/folders",
                json={"name": "Margin Financing", "description": "rates etc"},
            )
            assert r1.status_code == 201
            assert r1.json()["name"] == "Margin Financing"

            r2 = await client.post(
                "/api/v1/vault/folders",
                json={"name": "Margin Financing", "description": "different text"},
            )
            assert r2.status_code == 400
            assert "already exists" in r2.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        await _cleanup([user_id])


async def test_create_folder_validates_name_length() -> None:
    user_id = await _seed_user()
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.post(
                "/api/v1/vault/folders", json={"name": "", "description": ""}
            )
        assert response.status_code == 422
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        await _cleanup([user_id])


async def test_update_folder_partial_payload() -> None:
    user_id = await _seed_user()
    folder_id = await _seed_folder(user_id, "Custody", "old desc")

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.put(
                f"/api/v1/vault/folders/{folder_id}",
                json={"description": "new desc"},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["name"] == "Custody"
        assert body["description"] == "new desc"
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        await _cleanup([user_id])


async def test_delete_folder_204() -> None:
    user_id = await _seed_user()
    folder_id = await _seed_folder(user_id)

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.delete(f"/api/v1/vault/folders/{folder_id}")
        assert response.status_code == 204

        async with _client() as client:
            list_response = await client.get("/api/v1/vault/folders")
        assert list_response.status_code == 200
        assert list_response.json() == []
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        await _cleanup([user_id])


# ── Cross-tenant isolation (the security guarantee) ─────────────────────────


async def test_update_folder_owned_by_other_user_returns_404() -> None:
    owner = await _seed_user()
    intruder = await _seed_user()
    folder_id = await _seed_folder(owner, "Custody", "owner data")

    app.dependency_overrides[get_current_user] = lambda: _override_user(intruder)
    try:
        async with _client() as client:
            response = await client.put(
                f"/api/v1/vault/folders/{folder_id}",
                json={"description": "hostile rewrite"},
            )
        assert response.status_code == 404
        assert response.json()["detail"] == "vault_folder_not_found"
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        await _cleanup([owner, intruder])


async def test_delete_folder_owned_by_other_user_returns_404() -> None:
    owner = await _seed_user()
    intruder = await _seed_user()
    folder_id = await _seed_folder(owner, "Custody")

    app.dependency_overrides[get_current_user] = lambda: _override_user(intruder)
    try:
        async with _client() as client:
            response = await client.delete(f"/api/v1/vault/folders/{folder_id}")
        assert response.status_code == 404
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        await _cleanup([owner, intruder])
