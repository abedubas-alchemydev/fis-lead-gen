"""API-layer tests for the per-user outreach signature (footer) endpoints.

Integration-marked -- both endpoints touch a real Postgres. Covers
``GET``/``PUT /outreach/signature``: empty default, round-trip, upsert
of an existing row, whitespace stripping + empty-clear, and the
5000-char cap. Mirrors the seed/override/cleanup style used by
``test_outreach_create_tab.py``.
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
from app.schemas.auth import AuthenticatedUser
from app.services.auth import get_current_user

pytestmark = pytest.mark.integration


def _override_user(user_id: str) -> AuthenticatedUser:
    return AuthenticatedUser(
        id=user_id,
        name="Test User",
        email=f"{user_id}@example.com",
        role="viewer",
        feature_permissions=[],
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


async def _cleanup(user_id: str) -> None:
    # user_outreach_settings cascades from the user row (ON DELETE CASCADE).
    async with SessionLocal() as session:
        await session.execute(delete(AuthUser).where(AuthUser.id == user_id))
        await session.commit()


def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


async def test_signature_empty_when_unset() -> None:
    user_id = await _seed_user()
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.get("/api/v1/outreach/signature")
        assert response.status_code == 200, response.text
        assert response.json() == {"signature": ""}
    finally:
        app.dependency_overrides.clear()
        await _cleanup(user_id)


async def test_put_then_get_round_trips() -> None:
    user_id = await _seed_user()
    sig = "Jane Doe\nDirector, Business Development\nAcme Clearing"
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            put = await client.put(
                "/api/v1/outreach/signature", json={"signature": sig}
            )
            assert put.status_code == 200, put.text
            assert put.json() == {"signature": sig}

            got = await client.get("/api/v1/outreach/signature")
            assert got.json() == {"signature": sig}
    finally:
        app.dependency_overrides.clear()
        await _cleanup(user_id)


async def test_put_upserts_existing_row() -> None:
    user_id = await _seed_user()
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            await client.put(
                "/api/v1/outreach/signature", json={"signature": "first"}
            )
            second = await client.put(
                "/api/v1/outreach/signature", json={"signature": "second"}
            )
            assert second.json() == {"signature": "second"}

            got = await client.get("/api/v1/outreach/signature")
            assert got.json() == {"signature": "second"}
    finally:
        app.dependency_overrides.clear()
        await _cleanup(user_id)


async def test_put_strips_whitespace_and_empty_clears() -> None:
    user_id = await _seed_user()
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            await client.put(
                "/api/v1/outreach/signature", json={"signature": "  hello  "}
            )
            got = await client.get("/api/v1/outreach/signature")
            assert got.json() == {"signature": "hello"}

            cleared = await client.put(
                "/api/v1/outreach/signature", json={"signature": "   "}
            )
            assert cleared.json() == {"signature": ""}
            got_after = await client.get("/api/v1/outreach/signature")
            assert got_after.json() == {"signature": ""}
    finally:
        app.dependency_overrides.clear()
        await _cleanup(user_id)


async def test_put_rejects_oversized_signature() -> None:
    user_id = await _seed_user()
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.put(
                "/api/v1/outreach/signature", json={"signature": "x" * 5001}
            )
        assert response.status_code == 422
    finally:
        app.dependency_overrides.clear()
        await _cleanup(user_id)
