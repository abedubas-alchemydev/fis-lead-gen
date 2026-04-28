"""Auth-gate tests for /api/v1/settings/competitors.

GET is read-only seed data (Pershing, Apex, Hilltop, etc.) that the
master-list FE bootstrap fetches on every page load — it must work for
any authenticated user, not just admins. POST/PUT/DELETE remain
admin-only because they mutate the seed list.

These are unit tests: the DB session and repository call are stubbed via
``app.dependency_overrides`` and ``unittest.mock.patch`` so the suite
stays in the default (non-integration) run and does not require a real
Postgres.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.api.v1.endpoints.settings import repository
from app.db.session import get_db_session
from app.main import app
from app.schemas.auth import AuthenticatedUser
from app.services.auth import get_current_user


def _user(role: str) -> AuthenticatedUser:
    return AuthenticatedUser(
        id=f"test-{role}",
        name=f"Test {role.title()}",
        email=f"{role}@example.com",
        role=role,
        session_expires_at=datetime(2099, 1, 1),
    )


async def _fake_db_session():
    yield None


def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


@pytest.fixture
def stubbed_competitors():
    """Stub repository.list_competitor_providers + DB session so the
    handler runs without touching Postgres."""

    async def _fake_list(_db):
        return [
            {
                "id": 1,
                "name": "Pershing",
                "aliases": ["Pershing LLC"],
                "priority": 100,
                "is_active": True,
            }
        ]

    app.dependency_overrides[get_db_session] = _fake_db_session
    with patch.object(
        repository,
        "list_competitor_providers",
        new=AsyncMock(side_effect=_fake_list),
    ):
        try:
            yield
        finally:
            app.dependency_overrides.pop(get_db_session, None)


async def test_get_competitors_allows_viewer_role(stubbed_competitors) -> None:
    """Viewer-role user gets 200 — read access is no longer admin-gated."""
    app.dependency_overrides[get_current_user] = lambda: _user("viewer")
    try:
        async with _client() as client:
            response = await client.get("/api/v1/settings/competitors")
        assert response.status_code == 200
        body = response.json()
        assert body["items"][0]["name"] == "Pershing"
    finally:
        app.dependency_overrides.pop(get_current_user, None)


async def test_get_competitors_allows_admin_role(stubbed_competitors) -> None:
    """Admin still gets 200 — regression check that the existing path works."""
    app.dependency_overrides[get_current_user] = lambda: _user("admin")
    try:
        async with _client() as client:
            response = await client.get("/api/v1/settings/competitors")
        assert response.status_code == 200
    finally:
        app.dependency_overrides.pop(get_current_user, None)


async def test_get_competitors_rejects_unauthenticated() -> None:
    """Without a session cookie, the real get_current_user runs and 401s.
    No dependency override here — we want the actual auth dep to fire."""
    async with _client() as client:
        response = await client.get("/api/v1/settings/competitors")
    assert response.status_code == 401


async def test_post_competitors_still_blocks_viewer_role() -> None:
    """Writes remain admin-only — viewer POST must 403.

    Regression guard: removing _ensure_admin from GET must not leak
    onto POST.
    """
    app.dependency_overrides[get_current_user] = lambda: _user("viewer")
    app.dependency_overrides[get_db_session] = _fake_db_session
    try:
        async with _client() as client:
            response = await client.post(
                "/api/v1/settings/competitors",
                json={"name": "TestCo", "aliases": ["tc"], "priority": 50},
            )
        assert response.status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)
