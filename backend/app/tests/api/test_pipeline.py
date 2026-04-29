"""API-layer tests for the Tier 2 pipeline trigger endpoints.

Three endpoints — POST /pipeline/run/{filing-monitor,populate-all,initial-load}
— each guarded by ``_ensure_admin_or_scheduler_sa``. Coverage:

  - admin cookie path → 200, request handler reaches the underlying service
  - SA OIDC path → 200, ``Authorization: Bearer <id_token>`` is verified via
    a monkeypatched ``id_token.verify_oauth2_token``
  - anonymous → 403 from the auth dependency (handler never runs)
  - non-admin cookie → 403 from the auth dependency

The pipeline service runs are stubbed: ``filing_monitor_service.run`` returns
a synthetic PipelineRun and the long-running background tasks are replaced
with no-op coroutines so the tests don't actually hit FINRA / SEC / Postgres.
The DB session dependency is also overridden so a real Postgres isn't
required for this default (non-integration) test run.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
import pytest

from app.api.v1.endpoints import pipeline as pipeline_endpoint
from app.core.config import settings
from app.db.session import get_db_session
from app.main import app
from app.models.pipeline_run import PipelineRun
from app.schemas.auth import AuthenticatedUser
from app.services.auth import get_current_user, get_current_user_optional


def _admin_user() -> AuthenticatedUser:
    return AuthenticatedUser(
        id="admin-1",
        name="Admin User",
        email="admin@example.com",
        role="admin",
        session_expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
    )


def _viewer_user() -> AuthenticatedUser:
    return AuthenticatedUser(
        id="viewer-1",
        name="Viewer User",
        email="viewer@example.com",
        role="viewer",
        session_expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
    )


class _FakeAsyncSession:
    """Just enough surface for the queued-run handlers to call
    ``db.add(run)``, ``await db.commit()``, ``await db.refresh(run)``.

    Stamps a deterministic ``id`` on refresh so the response shape is
    asserted predictably.
    """

    def __init__(self) -> None:
        self._next_id = 4242
        self.added: list[Any] = []

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        pass

    async def refresh(self, obj: Any) -> None:
        if isinstance(obj, PipelineRun) and obj.id is None:
            obj.id = self._next_id
            self._next_id += 1


async def _fake_db_dep():
    yield _FakeAsyncSession()


def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


@pytest.fixture
def stub_pipeline_runs(monkeypatch: pytest.MonkeyPatch):
    """Replace every long-running pipeline call with a fast stub so the
    handlers can be exercised without hitting Postgres, FINRA, or SEC.

    - ``filing_monitor_service.run`` returns a synthetic completed PipelineRun
      with predictable counts. The filing-monitor endpoint awaits this
      directly so the response body comes from the synthetic run.
    - ``_run_populate_all_background`` and ``_run_initial_load_background``
      become no-ops. The endpoints only schedule them via BackgroundTasks
      and don't await the result, so the response body comes from the
      ``status="queued"`` row created by ``_create_queued_run``.
    """

    async def _fake_filing_run(_db: Any, *, trigger_source: str) -> PipelineRun:
        return PipelineRun(
            id=7001,
            pipeline_name="daily_filing_monitor",
            trigger_source=trigger_source,
            status="completed",
            total_items=42,
            processed_items=42,
            success_count=40,
            failure_count=2,
            notes="stubbed for tests",
        )

    async def _noop_bg(_run_id: int, _trigger_source: str) -> None:
        return None

    monkeypatch.setattr(pipeline_endpoint.filing_monitor_service, "run", _fake_filing_run)
    monkeypatch.setattr(pipeline_endpoint, "_run_populate_all_background", _noop_bg)
    monkeypatch.setattr(pipeline_endpoint, "_run_initial_load_background", _noop_bg)

    app.dependency_overrides[get_db_session] = _fake_db_dep
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_db_session, None)


# ─────────────────────────── filing-monitor ────────────────────────────


async def test_filing_monitor_admin_cookie_returns_200(stub_pipeline_runs) -> None:
    """Admin role on the cookie path → handler runs, returns the synthetic
    PipelineRun shape from the stubbed service."""
    app.dependency_overrides[get_current_user] = _admin_user
    app.dependency_overrides[get_current_user_optional] = _admin_user
    try:
        async with _client() as client:
            response = await client.post("/api/v1/pipeline/run/filing-monitor")
        assert response.status_code == 200
        body = response.json()
        assert body["run_id"] == 7001
        assert body["status"] == "completed"
        assert body["total_items"] == 42
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_current_user_optional, None)


async def test_filing_monitor_sa_oidc_returns_200(
    monkeypatch: pytest.MonkeyPatch,
    stub_pipeline_runs,
) -> None:
    """SA OIDC path → ``verify_oauth2_token`` is called with the configured
    audience and an SA email; handler runs."""

    def _fake_verify(_token: str, _request: Any, audience: str) -> dict[str, Any]:
        assert audience == settings.backend_audience
        return {"email": settings.cloud_scheduler_sa_email}

    monkeypatch.setattr("google.oauth2.id_token.verify_oauth2_token", _fake_verify)

    async with _client() as client:
        response = await client.post(
            "/api/v1/pipeline/run/filing-monitor",
            headers={"Authorization": "Bearer scheduler-token"},
        )
    assert response.status_code == 200
    assert response.json()["run_id"] == 7001


async def test_filing_monitor_anonymous_returns_403(stub_pipeline_runs) -> None:
    """No cookie + no Authorization header → 403, handler never runs."""
    async with _client() as client:
        response = await client.post("/api/v1/pipeline/run/filing-monitor")
    assert response.status_code == 403


async def test_filing_monitor_viewer_cookie_returns_403(stub_pipeline_runs) -> None:
    """Authenticated but non-admin → 403."""
    app.dependency_overrides[get_current_user_optional] = _viewer_user
    try:
        async with _client() as client:
            response = await client.post("/api/v1/pipeline/run/filing-monitor")
        assert response.status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user_optional, None)


async def test_filing_monitor_oidc_wrong_email_returns_403(
    monkeypatch: pytest.MonkeyPatch,
    stub_pipeline_runs,
) -> None:
    """Verified Google token but ``email`` claim is not the configured SA → 403."""

    def _fake_verify(_token: str, _request: Any, audience: str) -> dict[str, Any]:
        del audience
        return {"email": "imposter@example.com"}

    monkeypatch.setattr("google.oauth2.id_token.verify_oauth2_token", _fake_verify)

    async with _client() as client:
        response = await client.post(
            "/api/v1/pipeline/run/filing-monitor",
            headers={"Authorization": "Bearer wrong-email-token"},
        )
    assert response.status_code == 403


# ─────────────────────────── populate-all ──────────────────────────────


async def test_populate_all_admin_cookie_returns_queued(stub_pipeline_runs) -> None:
    """Admin → 200, response is the queued PipelineRun stub. The actual
    background work is replaced with a no-op so the test doesn't run real
    pipelines."""
    app.dependency_overrides[get_current_user] = _admin_user
    app.dependency_overrides[get_current_user_optional] = _admin_user
    try:
        async with _client() as client:
            response = await client.post("/api/v1/pipeline/run/populate-all")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "queued"
        assert body["run_id"] >= 4242
        assert body["total_items"] == 0
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_current_user_optional, None)


async def test_populate_all_sa_oidc_returns_queued(
    monkeypatch: pytest.MonkeyPatch,
    stub_pipeline_runs,
) -> None:
    def _fake_verify(_token: str, _request: Any, audience: str) -> dict[str, Any]:
        del audience
        return {"email": settings.cloud_scheduler_sa_email}

    monkeypatch.setattr("google.oauth2.id_token.verify_oauth2_token", _fake_verify)

    async with _client() as client:
        response = await client.post(
            "/api/v1/pipeline/run/populate-all",
            headers={"Authorization": "Bearer scheduler-token"},
        )
    assert response.status_code == 200
    assert response.json()["status"] == "queued"


async def test_populate_all_anonymous_returns_403(stub_pipeline_runs) -> None:
    async with _client() as client:
        response = await client.post("/api/v1/pipeline/run/populate-all")
    assert response.status_code == 403


# ─────────────────────────── initial-load ──────────────────────────────


async def test_initial_load_admin_cookie_returns_queued(stub_pipeline_runs) -> None:
    app.dependency_overrides[get_current_user] = _admin_user
    app.dependency_overrides[get_current_user_optional] = _admin_user
    try:
        async with _client() as client:
            response = await client.post("/api/v1/pipeline/run/initial-load")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "queued"
        assert body["run_id"] >= 4242
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_current_user_optional, None)


async def test_initial_load_sa_oidc_returns_queued(
    monkeypatch: pytest.MonkeyPatch,
    stub_pipeline_runs,
) -> None:
    def _fake_verify(_token: str, _request: Any, audience: str) -> dict[str, Any]:
        del audience
        return {"email": settings.cloud_scheduler_sa_email}

    monkeypatch.setattr("google.oauth2.id_token.verify_oauth2_token", _fake_verify)

    async with _client() as client:
        response = await client.post(
            "/api/v1/pipeline/run/initial-load",
            headers={"Authorization": "Bearer scheduler-token"},
        )
    assert response.status_code == 200
    assert response.json()["status"] == "queued"


async def test_initial_load_anonymous_returns_403(stub_pipeline_runs) -> None:
    async with _client() as client:
        response = await client.post("/api/v1/pipeline/run/initial-load")
    assert response.status_code == 403


# ───────────────────────── route registration ─────────────────────────


def test_scheduled_router_is_wired_into_v1_api() -> None:
    """Regression guard: the new router must be registered on the v1 api
    router. Forgetting ``include_router`` makes Cloud Scheduler attempts
    fail with 404 instead of 200, which is harder to diagnose than a
    failing test."""
    from app.api.v1.api import api_router

    paths = {route.path for route in api_router.routes if hasattr(route, "path")}
    assert "/pipeline/run/filing-monitor" in paths
    assert "/pipeline/run/populate-all" in paths
    assert "/pipeline/run/initial-load" in paths
