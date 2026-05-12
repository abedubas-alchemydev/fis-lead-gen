"""Auth-gate + payload-validation tests for /clearing-partner-suggestions.

Mirrors the unit-style pattern in ``test_settings_competitors.py`` —
DB session is stubbed via dependency override so the suite stays out of
the integration run. Full happy-path tests for accept/reject (which
ripple through ``refresh_competitor_flags`` + ``refresh_lead_scores``)
will live in an integration-marked suite once we have a fixture for
that — out of scope for this PR.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.api.v1.endpoints import settings as settings_endpoints
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
def empty_db_session():
    app.dependency_overrides[get_db_session] = _fake_db_session
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_db_session, None)


async def test_run_clustering_blocks_viewer(empty_db_session) -> None:
    """POST /run is admin-only — viewer must 403."""

    app.dependency_overrides[get_current_user] = lambda: _user("viewer")
    try:
        async with _client() as client:
            response = await client.post(
                "/api/v1/settings/clearing-partner-suggestions/run"
            )
        assert response.status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)


async def test_accept_blocks_viewer(empty_db_session) -> None:
    """POST /:id/accept is admin-only."""

    app.dependency_overrides[get_current_user] = lambda: _user("viewer")
    try:
        async with _client() as client:
            response = await client.post(
                "/api/v1/settings/clearing-partner-suggestions/1/accept",
                json={
                    "canonical_name": "RBC Capital Markets",
                    "display_name": "RBC",
                    "variants": ["RBC Capital Markets, LLC", "RBC Capital Markets LLC"],
                    "priority": 90,
                },
            )
        assert response.status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)


async def test_reject_blocks_viewer(empty_db_session) -> None:
    app.dependency_overrides[get_current_user] = lambda: _user("viewer")
    try:
        async with _client() as client:
            response = await client.post(
                "/api/v1/settings/clearing-partner-suggestions/1/reject"
            )
        assert response.status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)


async def test_accept_rejects_empty_variants(empty_db_session) -> None:
    """Pydantic-level validation: ``variants`` must contain ≥1 entry.
    422 because the validation fails before ``_ensure_admin`` runs.
    """

    app.dependency_overrides[get_current_user] = lambda: _user("admin")
    try:
        async with _client() as client:
            response = await client.post(
                "/api/v1/settings/clearing-partner-suggestions/1/accept",
                json={
                    "canonical_name": "RBC Capital Markets",
                    "variants": [],
                    "priority": 90,
                },
            )
        assert response.status_code == 422
    finally:
        app.dependency_overrides.pop(get_current_user, None)


async def test_run_clustering_admin_happy_path() -> None:
    """Admin POST /run returns the response shape the FE depends on.

    Replaces ``get_db_session`` with a mock session whose ``commit`` is
    awaitable (the endpoint calls ``await db.commit()``), and stubs the
    service-layer + helper so no Postgres is needed.
    """

    mock_db = AsyncMock()
    mock_db.commit = AsyncMock()

    async def _mock_db_session():
        yield mock_db

    app.dependency_overrides[get_current_user] = lambda: _user("admin")
    app.dependency_overrides[get_db_session] = _mock_db_session
    try:
        with (
            patch.object(
                settings_endpoints,
                "run_clustering_pass",
                new=AsyncMock(return_value=3),
            ),
            patch.object(
                settings_endpoints,
                "_pending_count",
                new=AsyncMock(return_value=7),
            ),
        ):
            async with _client() as client:
                response = await client.post(
                    "/api/v1/settings/clearing-partner-suggestions/run"
                )
        assert response.status_code == 200
        body = response.json()
        assert body == {"new_pending_count": 3, "total_pending_count": 7}
        mock_db.commit.assert_awaited_once()
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)
