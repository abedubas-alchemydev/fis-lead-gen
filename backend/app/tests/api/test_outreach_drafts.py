"""API-layer tests for the Outreach Drafts CRUD surface.

Integration-marked -- the endpoints touch a real Postgres. Covers the
round-trip (create -> get), partial-save tolerance, list ordering/scoping,
update, delete, owner isolation (404 cross-user), and folder-ownership
validation. No Gemini / email-provider mocking: drafts are pure persistence,
so nothing here exercises a transport.
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
        feature_permissions=["sent_outreach"],
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


async def _seed_vault_folder(user_id: str, name: str) -> int:
    async with SessionLocal() as session:
        folder = VaultFolder(user_id=user_id, name=name, description="")
        session.add(folder)
        await session.commit()
        await session.refresh(folder)
        return folder.id


async def _cleanup_folder(folder_id: int) -> None:
    async with SessionLocal() as session:
        await session.execute(
            delete(VaultFolder).where(VaultFolder.id == folder_id)
        )
        await session.commit()


async def _cleanup_users(user_ids: list[str]) -> None:
    # Drafts FK user_id ON DELETE CASCADE, so dropping the user clears them.
    async with SessionLocal() as session:
        if user_ids:
            await session.execute(
                delete(AuthUser).where(AuthUser.id.in_(user_ids))
            )
        await session.commit()


def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


def _sample_body(**overrides) -> dict:
    payload = {
        "subject": "Quick intro",
        "body": "Hi there,\n\nValue para.\n\n- Sender",
        "to": [{"email": "sarah@example.com", "name": "Sarah"}],
        "cc": ["colleague@example.com"],
        "bcc": ["crm@example.com"],
        "source": "manual",
    }
    payload.update(overrides)
    return payload


async def test_create_and_get_draft_round_trips() -> None:
    user_id = await _seed_user()
    folder_id = await _seed_vault_folder(user_id, "Stock Loan")
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            created = await client.post(
                "/api/v1/outreach/drafts",
                json=_sample_body(folder_id=folder_id),
            )
            assert created.status_code == 201, created.text
            draft = created.json()
            draft_id = draft["id"]
            assert draft["subject"] == "Quick intro"
            assert draft["body"].startswith("Hi there")
            assert draft["to"] == [{"email": "sarah@example.com", "name": "Sarah"}]
            assert draft["cc"] == ["colleague@example.com"]
            assert draft["bcc"] == ["crm@example.com"]
            assert draft["folder_id"] == folder_id
            assert draft["folder_name"] == "Stock Loan"
            assert draft["source"] == "manual"

            fetched = await client.get(f"/api/v1/outreach/drafts/{draft_id}")
            assert fetched.status_code == 200, fetched.text
            assert fetched.json()["body"].startswith("Hi there")
    finally:
        app.dependency_overrides.clear()
        await _cleanup_folder(folder_id)
        await _cleanup_users([user_id])


async def test_create_empty_draft_saves() -> None:
    """A blank draft (nothing typed yet) must still save -- the composer's
    Save Draft button should work mid-compose."""
    user_id = await _seed_user()
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            created = await client.post("/api/v1/outreach/drafts", json={})
            assert created.status_code == 201, created.text
            draft = created.json()
            assert draft["subject"] == ""
            assert draft["body"] == ""
            assert draft["to"] == []
            assert draft["cc"] == []
            assert draft["bcc"] == []
            assert draft["folder_id"] is None
            assert draft["source"] == "manual"
    finally:
        app.dependency_overrides.clear()
        await _cleanup_users([user_id])


async def test_create_draft_with_doxie_source() -> None:
    user_id = await _seed_user()
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            created = await client.post(
                "/api/v1/outreach/drafts",
                json=_sample_body(source="doxie"),
            )
            assert created.status_code == 201, created.text
            assert created.json()["source"] == "doxie"
    finally:
        app.dependency_overrides.clear()
        await _cleanup_users([user_id])


async def test_list_returns_only_callers_drafts_without_body() -> None:
    owner_id = await _seed_user()
    other_id = await _seed_user()
    app.dependency_overrides[get_current_user] = lambda: _override_user(other_id)
    try:
        async with _client() as client:
            # Another user's draft must not leak into the caller's list.
            app.dependency_overrides[get_current_user] = (
                lambda: _override_user(owner_id)
            )
            await client.post("/api/v1/outreach/drafts", json=_sample_body())
            app.dependency_overrides[get_current_user] = (
                lambda: _override_user(other_id)
            )
            mine = await client.post(
                "/api/v1/outreach/drafts", json=_sample_body(subject="Mine")
            )
            mine_id = mine.json()["id"]

            listed = await client.get("/api/v1/outreach/drafts")
            assert listed.status_code == 200, listed.text
            payload = listed.json()
            ids = [item["id"] for item in payload["items"]]
            assert mine_id in ids
            assert payload["total"] == len(payload["items"]) == 1
            # Body is omitted from list rows to keep them light.
            assert "body" not in payload["items"][0]
    finally:
        app.dependency_overrides.clear()
        await _cleanup_users([owner_id, other_id])


async def test_update_overwrites_and_bumps_to_top() -> None:
    user_id = await _seed_user()
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            first = (
                await client.post(
                    "/api/v1/outreach/drafts", json=_sample_body(subject="First")
                )
            ).json()
            await client.post(
                "/api/v1/outreach/drafts", json=_sample_body(subject="Second")
            )

            updated = await client.put(
                f"/api/v1/outreach/drafts/{first['id']}",
                json=_sample_body(
                    subject="First (edited)",
                    to=[{"email": "new@example.com", "name": "New"}],
                    cc=[],
                    bcc=[],
                ),
            )
            assert updated.status_code == 200, updated.text
            body = updated.json()
            assert body["subject"] == "First (edited)"
            assert body["to"] == [{"email": "new@example.com", "name": "New"}]
            assert body["cc"] == []

            # Editing bumps updated_at, so the edited draft sorts first.
            listed = (await client.get("/api/v1/outreach/drafts")).json()
            assert listed["items"][0]["id"] == first["id"]
    finally:
        app.dependency_overrides.clear()
        await _cleanup_users([user_id])


async def test_delete_draft_then_404() -> None:
    user_id = await _seed_user()
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            created = await client.post(
                "/api/v1/outreach/drafts", json=_sample_body()
            )
            draft_id = created.json()["id"]
            deleted = await client.delete(f"/api/v1/outreach/drafts/{draft_id}")
            assert deleted.status_code == 204
            gone = await client.get(f"/api/v1/outreach/drafts/{draft_id}")
            assert gone.status_code == 404
    finally:
        app.dependency_overrides.clear()
        await _cleanup_users([user_id])


async def test_owner_isolation_404_cross_user() -> None:
    owner_id = await _seed_user()
    other_id = await _seed_user()
    app.dependency_overrides[get_current_user] = lambda: _override_user(owner_id)
    try:
        async with _client() as client:
            created = await client.post(
                "/api/v1/outreach/drafts", json=_sample_body()
            )
            draft_id = created.json()["id"]

            app.dependency_overrides[get_current_user] = (
                lambda: _override_user(other_id)
            )
            assert (
                await client.get(f"/api/v1/outreach/drafts/{draft_id}")
            ).status_code == 404
            assert (
                await client.put(
                    f"/api/v1/outreach/drafts/{draft_id}", json=_sample_body()
                )
            ).status_code == 404
            assert (
                await client.delete(f"/api/v1/outreach/drafts/{draft_id}")
            ).status_code == 404
    finally:
        app.dependency_overrides.clear()
        await _cleanup_users([owner_id, other_id])


async def test_create_draft_404_on_folder_owned_by_other_user() -> None:
    owner_id = await _seed_user()
    caller_id = await _seed_user()
    folder_id = await _seed_vault_folder(owner_id, "Owner Service")
    app.dependency_overrides[get_current_user] = lambda: _override_user(caller_id)
    try:
        async with _client() as client:
            response = await client.post(
                "/api/v1/outreach/drafts",
                json=_sample_body(folder_id=folder_id),
            )
        assert response.status_code == 404
        assert response.json()["detail"] == "outreach_inputs_not_found"
    finally:
        app.dependency_overrides.clear()
        await _cleanup_folder(folder_id)
        await _cleanup_users([owner_id, caller_id])


async def test_drafts_401_without_session_cookie() -> None:
    # No dependency override -> real get_current_user runs and rejects.
    async with _client() as client:
        response = await client.get("/api/v1/outreach/drafts")
    assert response.status_code == 401
