"""Auth-gate + shape tests for /api/v1/doxie-usage.

The whole surface is admin-only (read-only observability over the Doxie
chatbot's token spend). These are unit tests: the DB session and
repository methods are stubbed via ``app.dependency_overrides`` +
``unittest.mock.patch`` so the suite stays in the default (non-integration)
run and needs no Postgres — the ``percentile_cont`` SQL in the repository
never executes here.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import httpx

from app.api.v1.endpoints.doxie_usage import repository
from app.db.session import get_db_session
from app.main import app
from app.schemas.auth import AuthenticatedUser
from app.schemas.doxie_usage import (
    DoxieUsageSummary,
    DoxieUserUsageListResponse,
    DoxieUserUsageRow,
)
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


def _fake_summary() -> DoxieUsageSummary:
    return DoxieUsageSummary(
        total_assistant_turns=120,
        assistant_turns_with_tokens=98,
        total_prompt_tokens=240_000,
        total_completion_tokens=60_000,
        total_tokens=300_000,
        total_tool_calls=37,
        estimated_cost_usd=0.222,
        cost_input_per_1m_usd=0.30,
        cost_output_per_1m_usd=2.50,
    )


def _fake_users() -> DoxieUserUsageListResponse:
    return DoxieUserUsageListResponse(
        items=[
            DoxieUserUsageRow(
                user_id="user-a",
                name="Ada Power",
                email="ada@example.com",
                assistant_turns=80,
                total_prompt_tokens=160_000,
                total_completion_tokens=40_000,
                total_tokens=200_000,
                total_tool_calls=25,
                estimated_cost_usd=0.148,
                avg_latency_ms=812.4,
                p50_latency_ms=640.0,
            )
        ],
        total=1,
    )


# ── summary ────────────────────────────────────────────────────────────


async def test_summary_rejects_unauthenticated() -> None:
    """No session cookie -> the real get_current_user fires and 401s."""
    async with _client() as client:
        response = await client.get("/api/v1/doxie-usage/summary")
    assert response.status_code == 401


async def test_summary_blocks_viewer_role() -> None:
    app.dependency_overrides[get_current_user] = lambda: _user("viewer")
    app.dependency_overrides[get_db_session] = _fake_db_session
    try:
        async with _client() as client:
            response = await client.get("/api/v1/doxie-usage/summary")
        assert response.status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)


async def test_summary_allows_admin() -> None:
    app.dependency_overrides[get_current_user] = lambda: _user("admin")
    app.dependency_overrides[get_db_session] = _fake_db_session
    try:
        with patch.object(
            repository, "summary", new=AsyncMock(return_value=_fake_summary())
        ):
            async with _client() as client:
                response = await client.get("/api/v1/doxie-usage/summary")
        assert response.status_code == 200
        body = response.json()
        assert body["total_tokens"] == 300_000
        assert body["assistant_turns_with_tokens"] == 98
        assert body["estimated_cost_usd"] == 0.222
        assert body["cost_input_per_1m_usd"] == 0.30
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)


# ── users list ─────────────────────────────────────────────────────────


async def test_users_rejects_unauthenticated() -> None:
    async with _client() as client:
        response = await client.get("/api/v1/doxie-usage/users")
    assert response.status_code == 401


async def test_users_blocks_viewer_role() -> None:
    app.dependency_overrides[get_current_user] = lambda: _user("viewer")
    app.dependency_overrides[get_db_session] = _fake_db_session
    try:
        async with _client() as client:
            response = await client.get("/api/v1/doxie-usage/users")
        assert response.status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)


async def test_users_returns_rows_for_admin() -> None:
    app.dependency_overrides[get_current_user] = lambda: _user("admin")
    app.dependency_overrides[get_db_session] = _fake_db_session
    try:
        with patch.object(
            repository, "list_users", new=AsyncMock(return_value=_fake_users())
        ):
            async with _client() as client:
                response = await client.get("/api/v1/doxie-usage/users")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["items"][0]["user_id"] == "user-a"
        assert body["items"][0]["total_tokens"] == 200_000
        assert body["items"][0]["p50_latency_ms"] == 640.0
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)


async def test_users_forwards_pagination_params() -> None:
    """Handler passes page / limit through to the repository."""
    app.dependency_overrides[get_current_user] = lambda: _user("admin")
    app.dependency_overrides[get_db_session] = _fake_db_session
    empty = DoxieUserUsageListResponse(items=[], total=0)
    try:
        with patch.object(
            repository, "list_users", new=AsyncMock(return_value=empty)
        ) as mock_list:
            async with _client() as client:
                response = await client.get(
                    "/api/v1/doxie-usage/users",
                    params={"page": 3, "limit": 10},
                )
        assert response.status_code == 200
        kwargs = mock_list.await_args.kwargs
        assert kwargs["page"] == 3
        assert kwargs["limit"] == 10
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)


async def test_users_rejects_out_of_range_limit() -> None:
    """limit is bounded ge=1 le=100 -> FastAPI 422s an over-cap value before
    the repository is touched."""
    app.dependency_overrides[get_current_user] = lambda: _user("admin")
    app.dependency_overrides[get_db_session] = _fake_db_session
    try:
        with patch.object(
            repository, "list_users", new=AsyncMock()
        ) as mock_list:
            async with _client() as client:
                response = await client.get(
                    "/api/v1/doxie-usage/users?limit=500"
                )
        assert response.status_code == 422
        mock_list.assert_not_awaited()
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)
