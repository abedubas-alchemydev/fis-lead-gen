"""Tests for ``GET /api/v1/stats`` after the Pending-Approval swap.

The dashboard's "Deficiency Notices" KPI was replaced by "Pending Approval
BDs" (client request): ``DashboardStatsResponse.deficiency_alerts`` →
``pending_approval_bds``, computed by
``BrokerDealerRepository.count_pending_approval`` (filed + checked + no
approval date, 90-day filed-proxy window). The alerts endpoints/pages are
deliberately untouched — these tests pin the response shape so the old
field can't creep back and the new one can't silently vanish.
"""

from __future__ import annotations

from datetime import datetime

import httpx

import app.api.v1.endpoints.stats as stats_endpoints
from app.db.session import get_db_session
from app.main import app
from app.schemas.auth import AuthenticatedUser
from app.schemas.stats import DashboardStatsResponse
from app.services.auth import get_current_user


def _override_user() -> AuthenticatedUser:
    return AuthenticatedUser(
        id="test-user",
        name="Test User",
        email="test-user@example.com",
        role="viewer",
        feature_permissions=["dashboard"],
        session_expires_at=datetime(2099, 1, 1),
    )


class _StubDb:
    """Only the endpoint's inline new-BDs count touches the session directly;
    the repository methods are monkeypatched."""

    async def execute(self, statement: object) -> object:
        from unittest.mock import MagicMock

        result = MagicMock()
        result.scalar_one.return_value = 12
        return result


async def test_dashboard_stats_shape_has_pending_not_deficiency(monkeypatch) -> None:
    async def _count_all(_db) -> int:
        return 3500

    async def _count_pending(_db) -> int:
        return 7

    async def _count_high_value(_db) -> int:
        return 240

    monkeypatch.setattr(stats_endpoints.repository, "count_all", _count_all)
    monkeypatch.setattr(
        stats_endpoints.repository, "count_pending_approval", _count_pending
    )
    monkeypatch.setattr(
        stats_endpoints.repository, "count_high_value_participants", _count_high_value
    )
    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[get_db_session] = lambda: _StubDb()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            response = await client.get("/api/v1/stats")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {
        "total_active_bds": 3500,
        "new_bds_90_days": 12,
        "pending_approval_bds": 7,
        "high_value_participants": 240,
    }
    # The retired KPI must be gone from the payload entirely.
    assert "deficiency_alerts" not in body


def test_schema_field_swap_is_total() -> None:
    fields = set(DashboardStatsResponse.model_fields)
    assert "pending_approval_bds" in fields
    assert "deficiency_alerts" not in fields


async def test_stats_requires_auth() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        response = await client.get("/api/v1/stats")
    assert response.status_code == 401
