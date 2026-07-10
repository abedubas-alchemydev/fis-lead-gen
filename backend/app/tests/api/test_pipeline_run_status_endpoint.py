"""API-layer tests for ``GET /pipeline/run/{run_id}``.

The polling endpoint backing the per-firm ``refresh-financials`` flow.
Returns the current state of a PipelineRun row by id; the FE polls it
to learn when the queued background task transitions through
``running`` → ``completed`` (or ``failed``) so it can refetch the
firm-detail page and render the now-populated financial fields.

Coverage:

  - 200 happy path: existing run row → response body carries the
    explicit ``PipelineRunStatusResponse`` shape (handler maps fields
    by hand because the model field is ``id`` but the response field
    is ``run_id``; this test pins that mapping).
  - 401 when unauthenticated.
  - 404 when the run id doesn't exist.
  - Owner-scope for ``favorite_list_enrich`` runs (whose ``notes`` carry
    a whole favorite list's firm names + membership): 404 to an authed
    non-owner non-admin, 200 to the owner, 200 to an admin with a
    different email; every OTHER pipeline type stays readable by any
    authenticated user (regression guard).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
import pytest

from app.db.session import get_db_session
from app.main import app
from app.models.pipeline_run import PipelineRun
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


def _admin_user() -> AuthenticatedUser:
    """Admin whose email deliberately differs from any run's owner — pins
    that the admin bypass is role-based, not email-based."""
    return AuthenticatedUser(
        id="admin-1",
        name="Admin User",
        email="admin@example.com",
        role="admin",
        session_expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
    )


# A favorite_list_enrich parent run's ``notes`` carry the whole list's firm
# names + membership — exactly the payload the owner-scope check protects.
_ENRICH_NOTES = (
    '{"list_name": "Q3 Targets", '
    '"firms": ["Acme Securities LLC", "Globex Brokerage Inc"]}'
)


def _favorite_list_enrich_run(owner_email: str) -> PipelineRun:
    """Bulk-enrich parent run owned by ``owner_email`` (stamped into
    ``trigger_source`` as ``favorite_list_enrich:<email>``)."""
    return PipelineRun(
        id=7001,
        pipeline_name="favorite_list_enrich",
        trigger_source=f"favorite_list_enrich:{owner_email}",
        status="running",
        total_items=2,
        processed_items=1,
        success_count=1,
        failure_count=0,
        notes=_ENRICH_NOTES,
        started_at=datetime(2026, 6, 1, 9, 0, 0, tzinfo=timezone.utc),
        completed_at=None,
    )


class _FakeAsyncSession:
    """Minimal async-session surface — only needs ``get`` for the
    handler's ``await db.get(PipelineRun, run_id)`` call."""

    def __init__(self) -> None:
        self.run: PipelineRun | None = None

    async def get(self, _model: Any, run_id: int) -> PipelineRun | None:
        if self.run is None:
            return None
        return self.run if self.run.id == run_id else None


_FAKE_SESSION = _FakeAsyncSession()


async def _fake_db_dep():
    yield _FAKE_SESSION


def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


@pytest.fixture
def override_db():
    app.dependency_overrides[get_db_session] = _fake_db_dep
    _FAKE_SESSION.run = None
    try:
        yield _FAKE_SESSION
    finally:
        app.dependency_overrides.pop(get_db_session, None)


async def test_get_run_status_returns_200_with_payload(
    override_db: _FakeAsyncSession,
) -> None:
    started = datetime(2026, 5, 2, 12, 0, 0, tzinfo=timezone.utc)
    completed = datetime(2026, 5, 2, 12, 1, 30, tzinfo=timezone.utc)
    override_db.run = PipelineRun(
        id=4321,
        pipeline_name="financial_pdf_pipeline_single",
        trigger_source="manual_single:viewer@example.com",
        status="completed",
        total_items=1,
        processed_items=1,
        success_count=2,
        failure_count=0,
        notes='{"summary": "Processed 1 filings via gemini."}',
        started_at=started,
        completed_at=completed,
    )

    app.dependency_overrides[get_current_user] = _viewer_user
    try:
        async with _client() as client:
            response = await client.get("/api/v1/pipeline/run/4321")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == 4321
    assert body["pipeline_name"] == "financial_pdf_pipeline_single"
    assert body["status"] == "completed"
    assert body["total_items"] == 1
    assert body["processed_items"] == 1
    assert body["success_count"] == 2
    assert body["failure_count"] == 0
    assert "summary" in body["notes"]
    assert body["completed_at"] is not None


async def test_get_run_status_returns_404_for_unknown_id(
    override_db: _FakeAsyncSession,
) -> None:
    """``db.get`` returning None → 404."""

    app.dependency_overrides[get_current_user] = _viewer_user
    try:
        async with _client() as client:
            response = await client.get("/api/v1/pipeline/run/9999")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 404


async def test_get_run_status_returns_401_when_unauthenticated(
    override_db: _FakeAsyncSession,
) -> None:
    """No auth → 401 before the handler body runs. ``override_db`` is
    still applied so the request can reach the auth dependency without
    the real Postgres session being instantiated."""

    async with _client() as client:
        response = await client.get("/api/v1/pipeline/run/4321")

    assert response.status_code == 401


async def test_favorite_list_enrich_run_hidden_from_non_owner(
    override_db: _FakeAsyncSession,
) -> None:
    """favorite_list_enrich run + authed NON-owner NON-admin → 404.

    The viewer (viewer@example.com) is not the owner stamped into
    ``trigger_source``, so the list's firm names + membership in ``notes``
    must not leak. 404 (not 403) so the run's existence isn't confirmed.
    """
    override_db.run = _favorite_list_enrich_run("owner@example.com")

    app.dependency_overrides[get_current_user] = _viewer_user
    try:
        async with _client() as client:
            response = await client.get("/api/v1/pipeline/run/7001")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 404
    # The protected list contents must not appear anywhere in the response.
    assert "Acme Securities" not in response.text


async def test_favorite_list_enrich_run_visible_to_owner(
    override_db: _FakeAsyncSession,
) -> None:
    """favorite_list_enrich run + the OWNER (caller email matches the
    ``favorite_list_enrich:<email>`` trigger) → 200 with notes present."""
    override_db.run = _favorite_list_enrich_run("viewer@example.com")

    app.dependency_overrides[get_current_user] = _viewer_user
    try:
        async with _client() as client:
            response = await client.get("/api/v1/pipeline/run/7001")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == 7001
    assert body["pipeline_name"] == "favorite_list_enrich"
    assert body["notes"] == _ENRICH_NOTES


async def test_favorite_list_enrich_run_visible_to_admin(
    override_db: _FakeAsyncSession,
) -> None:
    """favorite_list_enrich run + an ADMIN with a DIFFERENT email → 200.

    admin@example.com is not the owner (owner@example.com) yet still reads
    the run — the bypass is role-based, not email-based."""
    override_db.run = _favorite_list_enrich_run("owner@example.com")

    app.dependency_overrides[get_current_user] = _admin_user
    try:
        async with _client() as client:
            response = await client.get("/api/v1/pipeline/run/7001")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == 7001
    assert body["notes"] == _ENRICH_NOTES


async def test_non_enrich_run_readable_by_any_authenticated_user(
    override_db: _FakeAsyncSession,
) -> None:
    """Regression guard: a NON-enrich run stays readable by any authed
    user even when the trigger email doesn't match the caller. Proves the
    owner-scope check is confined to favorite_list_enrich and leaves every
    other pipeline type's polling untouched."""
    override_db.run = PipelineRun(
        id=7002,
        pipeline_name="broker_dealer_refresh_all",
        trigger_source="manual_single:someone@example.com",
        status="running",
        total_items=5,
        processed_items=2,
        success_count=2,
        failure_count=0,
        notes='{"summary": "refreshing"}',
        started_at=datetime(2026, 6, 1, 9, 0, 0, tzinfo=timezone.utc),
        completed_at=None,
    )

    app.dependency_overrides[get_current_user] = _viewer_user
    try:
        async with _client() as client:
            response = await client.get("/api/v1/pipeline/run/7002")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == 7002
    assert body["pipeline_name"] == "broker_dealer_refresh_all"
