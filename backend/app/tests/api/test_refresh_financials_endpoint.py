"""API-layer tests for ``POST /broker-dealers/{id}/refresh-financials``.

Exercises the per-firm on-demand financial-pipeline trigger. The handler
returns 202 Accepted, persists a ``status="queued"`` PipelineRun row, and
schedules a background task that drives the X-17A-5 → Gemini extraction
through ``FocusReportService.load_financial_metrics_for_broker_dealer``.

Coverage:

  - 202 happy path (authenticated user, BD exists, provider key set,
    no in-flight run) → row queued, background task scheduled, response
    body carries the queued run id.
  - 404 when the firm doesn't exist.
  - 503 when the active LLM provider's API key is unset.
  - 409 when a queued/running run already exists for the same BD;
    response includes the existing run id so the FE can keep polling.
  - 401 when the request is unauthenticated.
  - Background task wrapper delegates to FocusReportService and swallows
    exceptions (the service marks the run failed; the wrapper just logs).
  - Already-populated firms still trigger the run (no short-circuit on
    ``latest_net_capital is not None``) — required by the user-facing
    "always run, overwrite latest" decision.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
import pytest

from app.api.v1.endpoints import broker_dealers as bd_endpoint
from app.db.session import get_db_session
from app.main import app
from app.models.broker_dealer import BrokerDealer
from app.models.pipeline_run import PipelineRun
from app.schemas.auth import AuthenticatedUser
from app.services.auth import get_current_user


# ─────────────────────────── auth fixtures ────────────────────────────


def _viewer_user() -> AuthenticatedUser:
    """Authenticated non-admin user — the endpoint accepts any signed-in
    role, so this is the canonical fixture for happy-path tests."""
    return AuthenticatedUser(
        id="viewer-1",
        name="Viewer User",
        email="viewer@example.com",
        role="viewer",
        session_expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
    )


# ─────────────────────────── fake DB session ────────────────────────────


class _FakeAsyncSession:
    """Records side-effects for the endpoint and serves a configurable
    in-flight scalar result for the 409 concurrency-guard query.

    - ``execute(stmt)`` returns a result whose ``scalar_one_or_none()``
      yields whatever the test assigned to ``self.in_flight_run``.
    - ``add(obj)`` captures objects passed in (the queued PipelineRun).
    - ``commit()`` and ``refresh(obj)`` simulate the row id assignment
      so the response shape can be asserted predictably.
    """

    def __init__(self) -> None:
        self.added: list[Any] = []
        self.executed: list[Any] = []
        self.committed = False
        self.in_flight_run: PipelineRun | None = None
        self._next_id = 9100

    async def execute(self, stmt: Any) -> Any:
        self.executed.append(stmt)
        captured = self.in_flight_run

        class _Result:
            def scalar_one_or_none(self_inner) -> Any:
                return captured

        return _Result()

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.committed = True

    async def refresh(self, obj: Any) -> None:
        if isinstance(obj, PipelineRun) and obj.id is None:
            obj.id = self._next_id
            self._next_id += 1


_FAKE_SESSION = _FakeAsyncSession()


async def _fake_db_dep():
    yield _FAKE_SESSION


def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


def _broker_dealer(*, firm_id: int = 11, latest_net_capital: float | None = None) -> BrokerDealer:
    return BrokerDealer(
        id=firm_id,
        name="Acme Securities LLC",
        crd_number="1234",
        latest_net_capital=latest_net_capital,
    )


# ─────────────────────────── shared fixtures ──────────────────────────────


@pytest.fixture
def override_db():
    app.dependency_overrides[get_db_session] = _fake_db_dep
    _FAKE_SESSION.added.clear()
    _FAKE_SESSION.executed.clear()
    _FAKE_SESSION.committed = False
    _FAKE_SESSION.in_flight_run = None
    _FAKE_SESSION._next_id = 9100
    try:
        yield _FAKE_SESSION
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.fixture
def stub_background(monkeypatch: pytest.MonkeyPatch):
    """Replace the background-task entrypoint with a recorder so the
    endpoint can be exercised without spinning up Gemini or Postgres.

    Returns a list mutated by the recorder; tests assert on its
    contents to verify the task was scheduled with the right args.
    """

    captured: list[tuple[int, int, str]] = []

    async def _recorder(run_id: int, bd_id: int, trigger_source: str) -> None:
        captured.append((run_id, bd_id, trigger_source))

    monkeypatch.setattr(bd_endpoint, "_run_refresh_financials_background", _recorder)
    return captured


# ─────────────────────────── happy path ────────────────────────────


async def test_authenticated_user_kicks_off_run_returns_202(
    monkeypatch: pytest.MonkeyPatch,
    override_db: _FakeAsyncSession,
    stub_background: list[tuple[int, int, str]],
) -> None:
    bd = _broker_dealer()

    async def _fake_get(_db: Any, firm_id: int) -> BrokerDealer | None:
        return bd if firm_id == bd.id else None

    monkeypatch.setattr(bd_endpoint.repository, "get_broker_dealer", _fake_get)
    monkeypatch.setattr(bd_endpoint.settings, "llm_provider", "gemini")
    monkeypatch.setattr(bd_endpoint.settings, "gemini_api_key", "test-gemini")

    app.dependency_overrides[get_current_user] = _viewer_user
    try:
        async with _client() as client:
            response = await client.post(
                f"/api/v1/broker-dealers/{bd.id}/refresh-financials",
            )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 202
    body = response.json()
    assert body["broker_dealer_id"] == bd.id
    assert body["status"] == "queued"
    assert body["run_id"] >= 9100

    # Queued PipelineRun was persisted.
    assert override_db.committed is True
    assert len(override_db.added) == 1
    queued: PipelineRun = override_db.added[0]
    assert queued.pipeline_name == "financial_pdf_pipeline_single"
    assert queued.trigger_source == "manual_single:viewer@example.com"
    assert queued.status == "queued"
    assert f'"bd_id": {bd.id}' in queued.notes

    # Background task scheduled with the expected args.
    assert len(stub_background) == 1
    scheduled_run_id, scheduled_bd_id, scheduled_trigger = stub_background[0]
    assert scheduled_run_id == body["run_id"]
    assert scheduled_bd_id == bd.id
    assert scheduled_trigger == "manual_single:viewer@example.com"


async def test_already_populated_firm_still_runs_no_short_circuit(
    monkeypatch: pytest.MonkeyPatch,
    override_db: _FakeAsyncSession,
    stub_background: list[tuple[int, int, str]],
) -> None:
    """User decision: re-runs on already-populated firms ARE allowed —
    the endpoint must not short-circuit when ``latest_net_capital`` is
    set, because the rollup re-derives from the freshest FinancialMetric
    rows after a new annual filing lands."""

    bd = _broker_dealer(latest_net_capital=12_345_678.0)

    async def _fake_get(_db: Any, _firm_id: int) -> BrokerDealer | None:
        return bd

    monkeypatch.setattr(bd_endpoint.repository, "get_broker_dealer", _fake_get)
    monkeypatch.setattr(bd_endpoint.settings, "llm_provider", "gemini")
    monkeypatch.setattr(bd_endpoint.settings, "gemini_api_key", "test-gemini")

    app.dependency_overrides[get_current_user] = _viewer_user
    try:
        async with _client() as client:
            response = await client.post(
                f"/api/v1/broker-dealers/{bd.id}/refresh-financials",
            )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    assert len(stub_background) == 1
    assert override_db.committed is True


# ─────────────────────────── 4xx paths ────────────────────────────


async def test_unauthenticated_returns_401(
    monkeypatch: pytest.MonkeyPatch,
    override_db: _FakeAsyncSession,
) -> None:
    """No auth cookie → 401 from ``get_current_user`` before the handler
    body runs."""

    monkeypatch.setattr(bd_endpoint.settings, "gemini_api_key", "test-gemini")

    async with _client() as client:
        response = await client.post(
            "/api/v1/broker-dealers/11/refresh-financials",
        )

    assert response.status_code == 401


async def test_missing_firm_returns_404(
    monkeypatch: pytest.MonkeyPatch,
    override_db: _FakeAsyncSession,
    stub_background: list[tuple[int, int, str]],
) -> None:
    async def _fake_get(_db: Any, _firm_id: int) -> BrokerDealer | None:
        return None

    monkeypatch.setattr(bd_endpoint.repository, "get_broker_dealer", _fake_get)
    monkeypatch.setattr(bd_endpoint.settings, "gemini_api_key", "test-gemini")

    app.dependency_overrides[get_current_user] = _viewer_user
    try:
        async with _client() as client:
            response = await client.post(
                "/api/v1/broker-dealers/9999/refresh-financials",
            )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 404
    assert override_db.added == []
    assert stub_background == []


async def test_gemini_key_missing_returns_503(
    monkeypatch: pytest.MonkeyPatch,
    override_db: _FakeAsyncSession,
    stub_background: list[tuple[int, int, str]],
) -> None:
    bd = _broker_dealer()

    async def _fake_get(_db: Any, _firm_id: int) -> BrokerDealer | None:
        return bd

    monkeypatch.setattr(bd_endpoint.repository, "get_broker_dealer", _fake_get)
    monkeypatch.setattr(bd_endpoint.settings, "llm_provider", "gemini")
    monkeypatch.setattr(bd_endpoint.settings, "gemini_api_key", None)

    app.dependency_overrides[get_current_user] = _viewer_user
    try:
        async with _client() as client:
            response = await client.post(
                f"/api/v1/broker-dealers/{bd.id}/refresh-financials",
            )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 503
    assert "Gemini" in response.json()["detail"]
    assert override_db.added == []
    assert stub_background == []


async def test_openai_key_missing_returns_503_when_provider_is_openai(
    monkeypatch: pytest.MonkeyPatch,
    override_db: _FakeAsyncSession,
    stub_background: list[tuple[int, int, str]],
) -> None:
    bd = _broker_dealer()

    async def _fake_get(_db: Any, _firm_id: int) -> BrokerDealer | None:
        return bd

    monkeypatch.setattr(bd_endpoint.repository, "get_broker_dealer", _fake_get)
    monkeypatch.setattr(bd_endpoint.settings, "llm_provider", "openai")
    monkeypatch.setattr(bd_endpoint.settings, "openai_api_key", None)

    app.dependency_overrides[get_current_user] = _viewer_user
    try:
        async with _client() as client:
            response = await client.post(
                f"/api/v1/broker-dealers/{bd.id}/refresh-financials",
            )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 503
    assert "OpenAI" in response.json()["detail"]


async def test_concurrent_run_returns_409_with_existing_run_id(
    monkeypatch: pytest.MonkeyPatch,
    override_db: _FakeAsyncSession,
    stub_background: list[tuple[int, int, str]],
) -> None:
    """An existing queued/running PipelineRun for the same BD → 409 with
    the existing ``run_id`` so the FE can pick up polling instead of
    silently scheduling a duplicate."""

    bd = _broker_dealer()
    in_flight = PipelineRun(
        id=8888,
        pipeline_name="financial_pdf_pipeline_single",
        trigger_source="manual_single:viewer@example.com",
        status="running",
        total_items=1,
        processed_items=0,
        success_count=0,
        failure_count=0,
        notes='{"bd_id": 11, "stage": "running"}',
    )
    override_db.in_flight_run = in_flight

    async def _fake_get(_db: Any, _firm_id: int) -> BrokerDealer | None:
        return bd

    monkeypatch.setattr(bd_endpoint.repository, "get_broker_dealer", _fake_get)
    monkeypatch.setattr(bd_endpoint.settings, "llm_provider", "gemini")
    monkeypatch.setattr(bd_endpoint.settings, "gemini_api_key", "test-gemini")

    app.dependency_overrides[get_current_user] = _viewer_user
    try:
        async with _client() as client:
            response = await client.post(
                f"/api/v1/broker-dealers/{bd.id}/refresh-financials",
            )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["run_id"] == 8888
    assert detail["status"] == "running"
    assert detail["broker_dealer_id"] == bd.id

    # No new queued run, no scheduled task.
    assert override_db.added == []
    assert stub_background == []


# ─────────────────────────── background task ────────────────────────────


async def test_background_task_delegates_to_focus_report_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The wrapper just calls ``FocusReportService.load_financial_metrics_for_broker_dealer``
    with the run_id, bd_id, and trigger_source. Service-layer tests cover
    the actual extraction + persistence; this only verifies the wrapper
    contract."""

    captured_args: dict[str, Any] = {}

    async def _fake_service_call(
        self_obj: Any,
        bd_id: int,
        *,
        trigger_source: str,
        pipeline_run_id: int,
    ) -> int:
        captured_args["bd_id"] = bd_id
        captured_args["trigger_source"] = trigger_source
        captured_args["pipeline_run_id"] = pipeline_run_id
        return 2

    monkeypatch.setattr(
        "app.services.focus_reports.FocusReportService.load_financial_metrics_for_broker_dealer",
        _fake_service_call,
    )

    await bd_endpoint._run_refresh_financials_background(
        run_id=4321,
        bd_id=11,
        trigger_source="manual_single:viewer@example.com",
    )

    assert captured_args == {
        "bd_id": 11,
        "trigger_source": "manual_single:viewer@example.com",
        "pipeline_run_id": 4321,
    }


async def test_background_task_swallows_exceptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The wrapper logs and returns on extraction failure rather than
    propagating — the service has already marked the run row 'failed'
    via ``_mark_pipeline_run_failed``, so the BackgroundTask doesn't
    need to surface the exception to the (already-returned) caller."""

    async def _raises(
        self_obj: Any,
        bd_id: int,
        *,
        trigger_source: str,
        pipeline_run_id: int,
    ) -> int:
        raise RuntimeError("simulated extraction failure")

    monkeypatch.setattr(
        "app.services.focus_reports.FocusReportService.load_financial_metrics_for_broker_dealer",
        _raises,
    )

    # Should NOT raise.
    await bd_endpoint._run_refresh_financials_background(
        run_id=4321,
        bd_id=11,
        trigger_source="manual_single:viewer@example.com",
    )
