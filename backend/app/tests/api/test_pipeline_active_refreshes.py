"""API-layer tests for ``GET /pipeline/active-refreshes``.

The polling endpoint backing the dashboard "refreshing your records"
banner. Returns whether any user-visible refresh pipeline is in flight
(``running`` or ``queued``) and the earliest in-flight ``started_at``
so the FE can render a single banner with one timestamp.

Coverage:

- 200 ``is_active=false`` when no qualifying ``PipelineRun`` rows exist.
- 200 ``is_active=true`` with a ``started_at`` when a row matches one of
  ``USER_FACING_REFRESH_PIPELINES`` and has ``status='running'``.
- 401 when unauthenticated.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
import pytest

from app.db.session import get_db_session
from app.main import app
from app.schemas.auth import AuthenticatedUser
from app.services.auth import get_current_user


def _viewer_user() -> AuthenticatedUser:
    return AuthenticatedUser(
        id="viewer-1",
        name="Viewer User",
        email="viewer@example.com",
        role="viewer",
        session_expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
    )


class _FakeResult:
    """Minimal SQLAlchemy ``Result`` surface — the endpoint only calls
    ``scalar_one_or_none()`` after ``await db.execute(stmt)``."""

    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


class _FakeAsyncSession:
    """Async-session shim that returns a configured value from
    ``execute(stmt)`` regardless of the statement. We do NOT verify the
    SQL here — that's a job for an integration test against real
    Postgres. The unit-level contract is: handler reads the scalar
    result and folds it into ``ActiveRefreshResponse``."""

    def __init__(self) -> None:
        self.scalar_result: datetime | None = None

    async def execute(self, _stmt: Any) -> _FakeResult:
        return _FakeResult(self.scalar_result)


_FAKE_SESSION = _FakeAsyncSession()


async def _fake_db_dep():
    yield _FAKE_SESSION


def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


@pytest.fixture
def override_db():
    app.dependency_overrides[get_db_session] = _fake_db_dep
    _FAKE_SESSION.scalar_result = None
    try:
        yield _FAKE_SESSION
    finally:
        app.dependency_overrides.pop(get_db_session, None)


async def test_no_in_flight_runs_returns_is_active_false(
    override_db: _FakeAsyncSession,
) -> None:
    """Empty query result → ``is_active=false``, ``started_at=null``."""
    override_db.scalar_result = None

    app.dependency_overrides[get_current_user] = _viewer_user
    try:
        async with _client() as client:
            response = await client.get("/api/v1/pipeline/active-refreshes")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 200
    body = response.json()
    assert body == {"is_active": False, "started_at": None}


async def test_in_flight_run_returns_is_active_true_with_started_at(
    override_db: _FakeAsyncSession,
) -> None:
    """Scalar result populated → ``is_active=true`` and the timestamp
    surfaces in ISO 8601 form on the wire."""
    started = datetime(2026, 5, 28, 5, 35, 5, tzinfo=timezone.utc)
    override_db.scalar_result = started

    app.dependency_overrides[get_current_user] = _viewer_user
    try:
        async with _client() as client:
            response = await client.get("/api/v1/pipeline/active-refreshes")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 200
    body = response.json()
    assert body["is_active"] is True
    assert body["started_at"] is not None
    # FastAPI serializes datetimes as ISO 8601; verify the date portion is
    # intact (avoids tying the assertion to the exact "+00:00" vs "Z" form
    # that pydantic v2 emits).
    assert body["started_at"].startswith("2026-05-28T05:35:05")


async def test_unauthenticated_returns_401(
    override_db: _FakeAsyncSession,
) -> None:
    """No session → 401 before the handler runs. ``override_db`` stays
    applied so the request can reach the auth dependency without trying
    to spin up a real Postgres session."""

    async with _client() as client:
        response = await client.get("/api/v1/pipeline/active-refreshes")

    assert response.status_code == 401
