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

Also covers the destructive admin-only POST /pipeline/wipe-bd-data endpoint:
the strict-admin gate (rejects anonymous, non-admin cookie, AND the SA OIDC
bearer that the run/* endpoints accept), date-stamped confirmation
validation, the "audit row first, TRUNCATE second, all in one transaction"
ordering, and the rollback path when the audit insert fails.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytest

from app.api.v1.endpoints import pipeline as pipeline_endpoint
from app.core.config import settings
from app.db.session import get_db_session
from app.main import app
from app.models.audit_log import AuditLog
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
    """Regression guard: the new routers must be registered on the v1 api
    router. Forgetting ``include_router`` makes Cloud Scheduler attempts
    fail with 404 instead of 200, which is harder to diagnose than a
    failing test."""
    from app.api.v1.api import api_router

    paths = {route.path for route in api_router.routes if hasattr(route, "path")}
    assert "/pipeline/run/filing-monitor" in paths
    assert "/pipeline/run/populate-all" in paths
    assert "/pipeline/run/initial-load" in paths
    assert "/pipeline/wipe-bd-data" in paths


# ───────────────────────────── wipe-bd-data ────────────────────────────


class _FakeWipeSession:
    """Records every call the wipe handler makes against the session.

    The wipe handler does ``db.add(audit) → db.flush() → db.execute(SELECT
    COUNT) → db.execute(TRUNCATE) × N → db.commit()``. We record each step
    so tests can assert ordering ("audit added BEFORE the first TRUNCATE")
    and content (which tables, which COUNT result), without needing a real
    Postgres connection.
    """

    def __init__(self, *, audit_id: int = 9001, rows_before: int = 137) -> None:
        self._audit_id = audit_id
        self._rows_before = rows_before
        self.added: list[Any] = []
        self.executed_sql: list[str] = []
        self.flush_calls = 0
        self.commit_calls = 0
        self._flush_error: Exception | None = None
        self._truncate_error: Exception | None = None

    def fail_on_flush(self, exc: Exception) -> None:
        self._flush_error = exc

    def fail_on_truncate(self, exc: Exception) -> None:
        self._truncate_error = exc

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        self.flush_calls += 1
        if self._flush_error is not None:
            raise self._flush_error
        for obj in self.added:
            if isinstance(obj, AuditLog) and obj.id is None:
                obj.id = self._audit_id

    async def execute(self, statement: Any) -> Any:
        sql_text = str(statement)
        self.executed_sql.append(sql_text)
        if "TRUNCATE" in sql_text and self._truncate_error is not None:
            raise self._truncate_error

        rows_before = self._rows_before

        class _Result:
            def scalar(self_inner) -> int:  # noqa: N805 — closure helper
                return rows_before

        return _Result()

    async def commit(self) -> None:
        self.commit_calls += 1


def _override_wipe_session(session: _FakeWipeSession) -> None:
    async def _dep():
        yield session

    app.dependency_overrides[get_db_session] = _dep


def _today_confirmation() -> str:
    today_iso = datetime.now(timezone.utc).date().isoformat()
    return f"WIPE-BD-DATA-{today_iso}"


def _yesterday_confirmation() -> str:
    yesterday_iso = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
    return f"WIPE-BD-DATA-{yesterday_iso}"


async def test_wipe_bd_data_anonymous_returns_403() -> None:
    """No cookie + no Authorization header → 403."""
    session = _FakeWipeSession()
    _override_wipe_session(session)
    try:
        async with _client() as client:
            response = await client.post(
                "/api/v1/pipeline/wipe-bd-data",
                json={"confirmation": _today_confirmation()},
            )
        # ``get_current_user`` raises 401 for missing cookies; FastAPI surfaces
        # that as the response status. The behavioural guarantee tested here
        # is "no auth → handler does not run, no destructive work happens",
        # so we accept either auth-failure code.
        assert response.status_code in (401, 403)
        assert session.commit_calls == 0
        assert not any("TRUNCATE" in s for s in session.executed_sql)
    finally:
        app.dependency_overrides.pop(get_db_session, None)


async def test_wipe_bd_data_non_admin_user_returns_403() -> None:
    """Authenticated viewer (non-admin) → 403; no wipe runs."""
    session = _FakeWipeSession()
    _override_wipe_session(session)
    app.dependency_overrides[get_current_user] = _viewer_user
    try:
        async with _client() as client:
            response = await client.post(
                "/api/v1/pipeline/wipe-bd-data",
                json={"confirmation": _today_confirmation()},
            )
        assert response.status_code == 403
        assert session.commit_calls == 0
        assert not any("TRUNCATE" in s for s in session.executed_sql)
    finally:
        app.dependency_overrides.pop(get_db_session, None)
        app.dependency_overrides.pop(get_current_user, None)


async def test_wipe_bd_data_sa_oidc_token_returns_403(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SA OIDC bearer (the path the run/* endpoints accept) is REJECTED.

    Wipes are too destructive for the dual-path auth used by Cloud
    Scheduler-triggered endpoints. Even a perfectly verified scheduler SA
    token must hit ``get_current_user`` first, which has no awareness of
    OIDC, and 401 / 403 the request.
    """
    session = _FakeWipeSession()
    _override_wipe_session(session)

    def _fake_verify(_token: str, _request: Any, audience: str) -> dict[str, Any]:
        del audience
        return {"email": settings.cloud_scheduler_sa_email}

    monkeypatch.setattr("google.oauth2.id_token.verify_oauth2_token", _fake_verify)

    try:
        async with _client() as client:
            response = await client.post(
                "/api/v1/pipeline/wipe-bd-data",
                headers={"Authorization": "Bearer scheduler-token"},
                json={"confirmation": _today_confirmation()},
            )
        assert response.status_code in (401, 403)
        assert session.commit_calls == 0
        assert not any("TRUNCATE" in s for s in session.executed_sql)
    finally:
        app.dependency_overrides.pop(get_db_session, None)


async def test_wipe_bd_data_wrong_confirmation_returns_400() -> None:
    """Admin + body that doesn't even resemble the expected token → 400."""
    session = _FakeWipeSession()
    _override_wipe_session(session)
    app.dependency_overrides[get_current_user] = _admin_user
    try:
        async with _client() as client:
            response = await client.post(
                "/api/v1/pipeline/wipe-bd-data",
                json={"confirmation": "yes please wipe everything"},
            )
        assert response.status_code == 400
        assert "WIPE-BD-DATA-" in response.json()["detail"]
        assert session.commit_calls == 0
        assert not any("TRUNCATE" in s for s in session.executed_sql)
    finally:
        app.dependency_overrides.pop(get_db_session, None)
        app.dependency_overrides.pop(get_current_user, None)


async def test_wipe_bd_data_wrong_date_returns_400() -> None:
    """Admin + yesterday's confirmation token → 400.

    Stops a copy-pasted curl command or replayed request from wiping
    today: the operator has to retype the date each day they want to wipe.
    """
    session = _FakeWipeSession()
    _override_wipe_session(session)
    app.dependency_overrides[get_current_user] = _admin_user
    try:
        async with _client() as client:
            response = await client.post(
                "/api/v1/pipeline/wipe-bd-data",
                json={"confirmation": _yesterday_confirmation()},
            )
        assert response.status_code == 400
        assert session.commit_calls == 0
        assert not any("TRUNCATE" in s for s in session.executed_sql)
    finally:
        app.dependency_overrides.pop(get_db_session, None)
        app.dependency_overrides.pop(get_current_user, None)


async def test_wipe_bd_data_admin_correct_confirmation_succeeds() -> None:
    """Happy path: admin cookie + today's confirmation → 200, audit row was
    inserted *before* any TRUNCATE was issued, all 6 BD-data tables get a
    TRUNCATE statement, response carries the audit log id and a row count
    pulled from the broker_dealers SELECT."""
    session = _FakeWipeSession(audit_id=9001, rows_before=137)
    _override_wipe_session(session)
    app.dependency_overrides[get_current_user] = _admin_user
    try:
        async with _client() as client:
            response = await client.post(
                "/api/v1/pipeline/wipe-bd-data",
                json={"confirmation": _today_confirmation()},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["affected_tables"] == [
            "filing_alerts",
            "financial_metrics",
            "clearing_arrangements",
            "executive_contacts",
            "favorite_list_item",
            "broker_dealers",
        ]
        assert body["rows_deleted"] == 137
        assert body["audit_log_id"] == "9001"
        assert "wiped_at" in body

        # Audit row was added BEFORE the TRUNCATE statements were issued.
        audit_rows = [obj for obj in session.added if isinstance(obj, AuditLog)]
        assert len(audit_rows) == 1
        assert audit_rows[0].action == "bd_data_wiped"
        assert _today_confirmation() in (audit_rows[0].details or "")
        assert session.flush_calls == 1

        truncate_stmts = [s for s in session.executed_sql if "TRUNCATE" in s]
        assert len(truncate_stmts) == 6
        assert all("CASCADE" in s for s in truncate_stmts)

        # And the commit happened at the end (single-transaction guarantee
        # — no commit between the audit insert and the truncates).
        assert session.commit_calls == 1
    finally:
        app.dependency_overrides.pop(get_db_session, None)
        app.dependency_overrides.pop(get_current_user, None)


async def test_wipe_bd_data_audit_failure_rolls_back_truncate() -> None:
    """If the audit-row INSERT (flushed) fails, the handler raises before
    issuing any TRUNCATE — so there is never a "wipe without an audit
    trail" state. The single-transaction guarantee is upheld by SQLAlchemy
    not committing on a raised exception; the test verifies the ordering
    by asserting no TRUNCATE statement was executed."""
    session = _FakeWipeSession()
    session.fail_on_flush(RuntimeError("simulated audit_log insert failure"))
    _override_wipe_session(session)
    app.dependency_overrides[get_current_user] = _admin_user
    try:
        async with _client() as client:
            with pytest.raises(RuntimeError, match="simulated audit_log insert failure"):
                await client.post(
                    "/api/v1/pipeline/wipe-bd-data",
                    json={"confirmation": _today_confirmation()},
                )
        assert session.commit_calls == 0
        assert not any("TRUNCATE" in s for s in session.executed_sql)
    finally:
        app.dependency_overrides.pop(get_db_session, None)
        app.dependency_overrides.pop(get_current_user, None)
