"""API-layer tests for the user_activity branch of /users/{id}/activities.

Integration-marked: writes real rows into ``user_activity`` and reads
them back through the UNION endpoint. Auth is mocked via
``app.dependency_overrides`` (same pattern as
``test_users_admin_saved_firms.py``).

The branches sourced from ``audit_log`` / ``user_visit`` /
``favorite_list_item`` / ``outreach_sends`` are exercised by other test
modules; this one focuses on the nav/search/input rows we added with
migration ``20260520_0053``.
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
from app.models.user_activity import UserActivity
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


async def _seed_activity(
    user_id: str, action: str, path: str | None, details: dict | None = None
) -> None:
    async with SessionLocal() as session:
        session.add(
            UserActivity(
                user_id=user_id,
                session_id=None,
                action=action,
                path=path,
                details=details,
            )
        )
        await session.commit()


async def _cleanup(user_ids: list[str]) -> None:
    async with SessionLocal() as session:
        if user_ids:
            await session.execute(
                delete(UserActivity).where(UserActivity.user_id.in_(user_ids))
            )
            await session.execute(delete(AuthUser).where(AuthUser.id.in_(user_ids)))
        await session.commit()


def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


async def test_activities_includes_user_activity_rows() -> None:
    admin_id = await _seed_user(role="admin", suffix="-admin")
    target_id = await _seed_user(suffix="-target")

    await _seed_activity(target_id, "nav_view", "/master-list", {"filter_keys": ["q"]})
    await _seed_activity(
        target_id,
        "nav_click",
        "/master-list",
        {"nav_label": "Master List", "to_path": "/master-list/:id"},
    )
    await _seed_activity(target_id, "link_open", "/master-list/:id", {"host": "sec.gov"})
    await _seed_activity(
        target_id,
        "search_query",
        "/master-list",
        {"query_present": True, "query_length": 7, "filter_keys": ["q"]},
    )
    await _seed_activity(
        target_id,
        "input_used",
        "/outreach/new",
        {
            "field_name": "company_name",
            "field_length": 12,
            "field_populated": True,
            "form_id": "outreach_form",
        },
    )

    app.dependency_overrides[get_current_user] = lambda: _override_user(admin_id, role="admin")
    try:
        async with _client() as client:
            response = await client.get(f"/api/v1/users/{target_id}/activities")
        assert response.status_code == 200
        body = response.json()
        actions = {row["event_type"] for row in body["items"]}
        assert {
            "nav_view",
            "nav_click",
            "link_open",
            "search_query",
            "input_used",
        }.issubset(actions)

        # target_name carries the path (no firm binding for these rows).
        nav_view_row = next(
            row for row in body["items"] if row["event_type"] == "nav_view"
        )
        assert nav_view_row["target_name"] == "/master-list"
        assert nav_view_row["target_type"] is None
        assert nav_view_row["details"]["filter_keys"] == ["q"]

        search_row = next(
            row for row in body["items"] if row["event_type"] == "search_query"
        )
        assert search_row["details"]["query_length"] == 7
        # Raw query text must never come back.
        assert "query" not in search_row["details"]
        assert "q" not in search_row["details"]

        input_row = next(
            row for row in body["items"] if row["event_type"] == "input_used"
        )
        assert input_row["details"]["field_name"] == "company_name"
        # field value must never come back.
        assert "value" not in input_row["details"]
    finally:
        app.dependency_overrides.clear()
        await _cleanup([admin_id, target_id])


async def test_activities_type_nav_only_returns_nav_family() -> None:
    admin_id = await _seed_user(role="admin", suffix="-admin")
    target_id = await _seed_user(suffix="-target")

    await _seed_activity(target_id, "nav_view", "/dashboard", None)
    await _seed_activity(target_id, "nav_click", "/dashboard", {"nav_label": "Dashboard"})
    await _seed_activity(target_id, "link_open", "/dashboard", {"host": "example.com"})
    await _seed_activity(target_id, "search_query", "/master-list", {"query_length": 3})
    await _seed_activity(
        target_id, "input_used", "/outreach/new", {"field_name": "subject"}
    )

    app.dependency_overrides[get_current_user] = lambda: _override_user(admin_id, role="admin")
    try:
        async with _client() as client:
            response = await client.get(
                f"/api/v1/users/{target_id}/activities", params={"type": "nav"}
            )
        assert response.status_code == 200
        body = response.json()
        actions = {row["event_type"] for row in body["items"]}
        assert actions == {"nav_view", "nav_click", "link_open"}
        assert body["total"] == 3
    finally:
        app.dependency_overrides.clear()
        await _cleanup([admin_id, target_id])


async def test_activities_type_search_filters_to_search_only() -> None:
    admin_id = await _seed_user(role="admin", suffix="-admin")
    target_id = await _seed_user(suffix="-target")

    await _seed_activity(target_id, "nav_view", "/dashboard", None)
    await _seed_activity(target_id, "search_query", "/master-list", {"query_length": 5})
    await _seed_activity(target_id, "search_query", "/advisor-list", {"query_length": 9})
    await _seed_activity(target_id, "input_used", "/outreach/new", None)

    app.dependency_overrides[get_current_user] = lambda: _override_user(admin_id, role="admin")
    try:
        async with _client() as client:
            response = await client.get(
                f"/api/v1/users/{target_id}/activities", params={"type": "search"}
            )
        assert response.status_code == 200
        body = response.json()
        actions = {row["event_type"] for row in body["items"]}
        assert actions == {"search_query"}
        assert body["total"] == 2
    finally:
        app.dependency_overrides.clear()
        await _cleanup([admin_id, target_id])


async def test_activities_403_for_viewer_caller() -> None:
    caller_id = await _seed_user(role="viewer", suffix="-caller")
    target_id = await _seed_user(suffix="-target")

    app.dependency_overrides[get_current_user] = lambda: _override_user(caller_id, role="viewer")
    try:
        async with _client() as client:
            response = await client.get(f"/api/v1/users/{target_id}/activities")
        assert response.status_code == 403
    finally:
        app.dependency_overrides.clear()
        await _cleanup([caller_id, target_id])
