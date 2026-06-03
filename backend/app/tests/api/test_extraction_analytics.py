"""Auth-gate + shape tests for /api/v1/extraction-analytics.

The whole surface is admin-only (read-only observability over provider
extraction). These are unit tests: the DB session and repository methods
are stubbed via ``app.dependency_overrides`` + ``unittest.mock.patch`` so
the suite stays in the default (non-integration) run and needs no Postgres.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import httpx

from app.api.v1.endpoints.extraction_analytics import repository
from app.db.session import get_db_session
from app.main import app
from app.schemas.auth import AuthenticatedUser
from app.schemas.extraction_analytics import (
    FirmExtractionListResponse,
    FirmExtractionRow,
    ProviderCount,
    ProviderSummaryResponse,
    ProviderSummaryTotals,
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


def _fake_summary() -> ProviderSummaryResponse:
    return ProviderSummaryResponse(
        contacts=[
            ProviderCount(
                provider="apollo_match",
                label="Apollo (person match)",
                surface="contact",
                firm_count=12,
                value_count=160,
            )
        ],
        discovered_emails=[],
        websites=[],
        totals=ProviderSummaryTotals(
            total_contacts_attributed=160,
            total_contacts_unattributed=2901,
            total_discovered_emails=4672,
            total_enriched_emails=1132,
            total_firms_with_website=406,
        ),
    )


# ── summary ────────────────────────────────────────────────────────────


async def test_summary_rejects_unauthenticated() -> None:
    """No session cookie -> the real get_current_user fires and 401s."""
    async with _client() as client:
        response = await client.get("/api/v1/extraction-analytics/summary")
    assert response.status_code == 401


async def test_summary_blocks_viewer_role() -> None:
    app.dependency_overrides[get_current_user] = lambda: _user("viewer")
    app.dependency_overrides[get_db_session] = _fake_db_session
    try:
        async with _client() as client:
            response = await client.get("/api/v1/extraction-analytics/summary")
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
                response = await client.get("/api/v1/extraction-analytics/summary")
        assert response.status_code == 200
        body = response.json()
        assert body["totals"]["total_enriched_emails"] == 1132
        assert body["totals"]["total_contacts_unattributed"] == 2901
        assert body["contacts"][0]["provider"] == "apollo_match"
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)


# ── firms list ─────────────────────────────────────────────────────────


async def test_firms_blocks_viewer_role() -> None:
    app.dependency_overrides[get_current_user] = lambda: _user("viewer")
    app.dependency_overrides[get_db_session] = _fake_db_session
    try:
        async with _client() as client:
            response = await client.get("/api/v1/extraction-analytics/firms")
        assert response.status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)


async def test_firms_rejects_unknown_provider() -> None:
    """The ALL_PROVIDERS guard 400s before touching the repository."""
    app.dependency_overrides[get_current_user] = lambda: _user("admin")
    app.dependency_overrides[get_db_session] = _fake_db_session
    try:
        with patch.object(repository, "list_firms", new=AsyncMock()) as mock_list:
            async with _client() as client:
                response = await client.get(
                    "/api/v1/extraction-analytics/firms?provider=bogus"
                )
        assert response.status_code == 400
        mock_list.assert_not_awaited()
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)


async def test_firms_forwards_query_params() -> None:
    """Handler passes firm_type / provider / q / page / limit through."""
    app.dependency_overrides[get_current_user] = lambda: _user("admin")
    app.dependency_overrides[get_db_session] = _fake_db_session
    empty = FirmExtractionListResponse(items=[], total=0)
    try:
        with patch.object(
            repository, "list_firms", new=AsyncMock(return_value=empty)
        ) as mock_list:
            async with _client() as client:
                response = await client.get(
                    "/api/v1/extraction-analytics/firms",
                    params={
                        "firm_type": "advisor",
                        "provider": "hunter",
                        "q": "acme",
                        "page": 2,
                        "limit": 10,
                    },
                )
        assert response.status_code == 200
        kwargs = mock_list.await_args.kwargs
        assert kwargs["firm_type"] == "advisor"
        assert kwargs["provider"] == "hunter"
        assert kwargs["q"] == "acme"
        assert kwargs["page"] == 2
        assert kwargs["limit"] == 10
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)


async def test_firms_returns_rows_for_admin() -> None:
    app.dependency_overrides[get_current_user] = lambda: _user("admin")
    app.dependency_overrides[get_db_session] = _fake_db_session
    payload = FirmExtractionListResponse(
        items=[
            FirmExtractionRow(
                firm_type="broker_dealer",
                firm_id=42,
                name="Acme Securities",
                crd_number="12345",
                website_source="finra",
                contact_total=3,
                discovered_email_total=5,
                provider_counts=[],
                last_enriched_at=None,
            )
        ],
        total=1,
    )
    try:
        with patch.object(
            repository, "list_firms", new=AsyncMock(return_value=payload)
        ):
            async with _client() as client:
                response = await client.get("/api/v1/extraction-analytics/firms")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["items"][0]["firm_id"] == 42
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)


# ── firm values (drill-down) ───────────────────────────────────────────


async def test_firm_values_blocks_viewer_role() -> None:
    app.dependency_overrides[get_current_user] = lambda: _user("viewer")
    app.dependency_overrides[get_db_session] = _fake_db_session
    try:
        async with _client() as client:
            response = await client.get(
                "/api/v1/extraction-analytics/firms/broker_dealer/1/values"
            )
        assert response.status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)


async def test_firm_values_404_when_firm_missing() -> None:
    app.dependency_overrides[get_current_user] = lambda: _user("admin")
    app.dependency_overrides[get_db_session] = _fake_db_session
    try:
        with patch.object(repository, "firm_values", new=AsyncMock(return_value=None)):
            async with _client() as client:
                response = await client.get(
                    "/api/v1/extraction-analytics/firms/broker_dealer/999999/values"
                )
        assert response.status_code == 404
        assert response.json()["detail"] == "firm_not_found"
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)


async def test_firm_values_rejects_unknown_firm_type() -> None:
    """Path param is a Literal -> FastAPI 422s an out-of-set firm_type."""
    app.dependency_overrides[get_current_user] = lambda: _user("admin")
    app.dependency_overrides[get_db_session] = _fake_db_session
    try:
        async with _client() as client:
            response = await client.get(
                "/api/v1/extraction-analytics/firms/bogus_kind/1/values"
            )
        assert response.status_code == 422
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)
