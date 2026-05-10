"""API-layer tests for ``POST /broker-dealers/{id}/refresh-all``.

The orchestrator endpoint that fans out to a subset of the four
existing per-firm pipelines (refresh-financials, resolve-website,
health-check, enrich) based on which target fields are still NULL on
the BD record.

Coverage:

  - 200 ``skipped`` when every gate is closed (already-complete firm).
    Asserts no PipelineRun row written, no provider calls scheduled.
  - 202 happy path with a fully-empty firm — all four gates open;
    parent row queued, background task scheduled with the right
    pipelines tuple.
  - 202 selective happy path — only ``website`` is null; only the
    resolve-website pipeline gets scheduled.
  - 401 unauthenticated.
  - 404 firm not found.
  - 503 when a required provider key is missing.
  - 409 when an in-flight refresh-all already exists for this firm.
  - 429 when the per-(user, BD) cooldown is hit.
  - Background-task wrapper delegates to the orchestrator and swallows
    exceptions.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
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


# ─────────────────────────── auth fixture ────────────────────────────


def _viewer_user() -> AuthenticatedUser:
    return AuthenticatedUser(
        id="viewer-1",
        name="Viewer User",
        email="viewer@example.com",
        role="viewer",
        session_expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
    )


# ─────────────────────────── fake DB session ────────────────────────────


class _FakeAsyncSession:
    """Records side-effects + serves a chain of canned ``execute`` results
    so the endpoint's three SELECT queries (in-flight + cooldown +
    has_executive_contacts) return what each test expects.

    The endpoint runs SELECT queries in this order (per current handler):
      1. ``has_executive_contacts`` — bool gate input
      2. In-flight (409) check — single PipelineRun or None
      3. Cooldown (429) check — single PipelineRun or None
    Tests append the canned scalar value for each query in that order via
    ``queue_scalar(...)``. ``add(obj)`` and ``commit()`` capture the
    parent row creation.
    """

    def __init__(self) -> None:
        self.added: list[Any] = []
        self.committed = False
        self._scalar_queue: list[Any] = []
        self._next_id = 9500

    def queue_scalar(self, value: Any) -> None:
        self._scalar_queue.append(value)

    async def execute(self, _stmt: Any) -> Any:
        next_value = self._scalar_queue.pop(0) if self._scalar_queue else None

        class _Result:
            def scalar_one_or_none(self_inner) -> Any:
                return next_value

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


def _bd(*, firm_id: int = 11, **fields: Any) -> BrokerDealer:
    """Construct a BrokerDealer with sensible "incomplete" defaults so the
    fully-empty firm is the default fixture; tests pass in field overrides
    to flip individual gates closed."""
    defaults: dict[str, Any] = {
        "id": firm_id,
        "name": "Acme Securities LLC",
        "crd_number": "1234",
        "website": None,
        "website_source": None,
        "latest_net_capital": None,
        "yoy_growth": None,
        "health_status": None,
        "current_clearing_type": None,
        "current_clearing_partner": None,
    }
    defaults.update(fields)
    return BrokerDealer(**defaults)


@pytest.fixture
def override_db():
    app.dependency_overrides[get_db_session] = _fake_db_dep
    _FAKE_SESSION.added.clear()
    _FAKE_SESSION.committed = False
    _FAKE_SESSION._scalar_queue.clear()
    _FAKE_SESSION._next_id = 9500
    try:
        yield _FAKE_SESSION
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.fixture
def stub_background(monkeypatch: pytest.MonkeyPatch):
    """Replace the orchestrator's background task with a recorder so
    tests don't accidentally fire real Apollo / Hunter / Gemini calls."""

    captured: list[dict[str, Any]] = []

    async def _recorder(parent_run_id, bd_id, trigger_source, pipelines_to_run, pipelines_to_skip):
        captured.append(
            {
                "parent_run_id": parent_run_id,
                "bd_id": bd_id,
                "trigger_source": trigger_source,
                "pipelines_to_run": pipelines_to_run,
                "pipelines_to_skip": pipelines_to_skip,
            }
        )

    monkeypatch.setattr(bd_endpoint, "_run_refresh_all_background", _recorder)
    return captured


def _enable_all_provider_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bd_endpoint.settings, "llm_provider", "gemini")
    monkeypatch.setattr(bd_endpoint.settings, "gemini_api_key", "test-gemini")
    monkeypatch.setattr(bd_endpoint.settings, "apollo_api_key", "test-apollo")
    monkeypatch.setattr(bd_endpoint.settings, "hunter_api_key", "test-hunter")
    monkeypatch.setattr(bd_endpoint.settings, "serpapi_api_key", "test-serpapi")


# ─────────────────────────── happy paths ────────────────────────────


async def test_skipped_already_complete_returns_200_no_writes(
    monkeypatch: pytest.MonkeyPatch,
    override_db: _FakeAsyncSession,
    stub_background: list[dict[str, Any]],
) -> None:
    """Every field populated → return 200 with status='skipped' and do
    not create a PipelineRun row, do not schedule a background task."""

    bd = _bd(
        website="https://acme.example",
        latest_net_capital=1_000_000.0,
        yoy_growth=5.0,
        health_status="healthy",
        current_clearing_type="self_clearing",
        current_clearing_partner="Acme Self Clearing",
    )

    async def _fake_get(_db: Any, _firm_id: int) -> BrokerDealer | None:
        return bd

    # has_executive_contacts → True (any contact suffices)
    override_db.queue_scalar(123)

    monkeypatch.setattr(bd_endpoint.repository, "get_broker_dealer", _fake_get)
    _enable_all_provider_keys(monkeypatch)

    app.dependency_overrides[get_current_user] = _viewer_user
    try:
        async with _client() as client:
            response = await client.post(f"/api/v1/broker-dealers/{bd.id}/refresh-all")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] is None
    assert body["status"] == "skipped"
    assert body["broker_dealer_id"] == bd.id
    assert body["reason"] == "Already complete."

    # No PipelineRun row, no background task.
    assert override_db.added == []
    assert stub_background == []


async def test_all_gates_open_returns_202_with_all_four_pipelines(
    monkeypatch: pytest.MonkeyPatch,
    override_db: _FakeAsyncSession,
    stub_background: list[dict[str, Any]],
) -> None:
    bd = _bd()  # all defaults are NULL

    async def _fake_get(_db: Any, _firm_id: int) -> BrokerDealer | None:
        return bd

    # has_executive_contacts → False, in-flight → None, cooldown → None
    override_db.queue_scalar(None)
    override_db.queue_scalar(None)
    override_db.queue_scalar(None)

    monkeypatch.setattr(bd_endpoint.repository, "get_broker_dealer", _fake_get)
    _enable_all_provider_keys(monkeypatch)

    app.dependency_overrides[get_current_user] = _viewer_user
    try:
        async with _client() as client:
            response = await client.post(f"/api/v1/broker-dealers/{bd.id}/refresh-all")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    assert body["broker_dealer_id"] == bd.id
    assert body["run_id"] >= 9500

    # One PipelineRun queued; notes carry bd_id + ran/skipped split.
    assert len(override_db.added) == 1
    parent: PipelineRun = override_db.added[0]
    assert parent.pipeline_name == "broker_dealer_refresh_all"
    assert parent.trigger_source == "manual_single:viewer@example.com"
    assert parent.status == "queued"
    assert f'"bd_id": {bd.id}' in parent.notes

    # Background task scheduled with all 4 sub-pipelines.
    assert len(stub_background) == 1
    scheduled = stub_background[0]
    assert scheduled["bd_id"] == bd.id
    assert set(scheduled["pipelines_to_run"]) == {
        "financial_pdf_pipeline_single",
        "broker_dealer_resolve_website",
        "broker_dealer_health_check",
        "broker_dealer_enrich_contacts",
    }
    assert scheduled["pipelines_to_skip"] == ()


async def test_only_website_missing_runs_only_resolve_website(
    monkeypatch: pytest.MonkeyPatch,
    override_db: _FakeAsyncSession,
    stub_background: list[dict[str, Any]],
) -> None:
    bd = _bd(
        latest_net_capital=2_000_000.0,
        yoy_growth=3.5,
        health_status="ok",
        current_clearing_type="introducing",
        current_clearing_partner="Pershing",
    )

    async def _fake_get(_db: Any, _firm_id: int) -> BrokerDealer | None:
        return bd

    # has_executive_contacts → True (so enrich is skipped)
    override_db.queue_scalar(456)
    override_db.queue_scalar(None)  # in-flight
    override_db.queue_scalar(None)  # cooldown

    monkeypatch.setattr(bd_endpoint.repository, "get_broker_dealer", _fake_get)
    _enable_all_provider_keys(monkeypatch)

    app.dependency_overrides[get_current_user] = _viewer_user
    try:
        async with _client() as client:
            response = await client.post(f"/api/v1/broker-dealers/{bd.id}/refresh-all")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 202
    scheduled = stub_background[0]
    assert scheduled["pipelines_to_run"] == ("broker_dealer_resolve_website",)
    assert set(scheduled["pipelines_to_skip"]) == {
        "financial_pdf_pipeline_single",
        "broker_dealer_health_check",
        "broker_dealer_enrich_contacts",
    }


# ─────────────────────────── 4xx / 5xx paths ────────────────────────────


async def test_unauthenticated_returns_401(
    monkeypatch: pytest.MonkeyPatch,
    override_db: _FakeAsyncSession,
) -> None:
    _enable_all_provider_keys(monkeypatch)
    async with _client() as client:
        response = await client.post("/api/v1/broker-dealers/11/refresh-all")
    assert response.status_code == 401


async def test_missing_firm_returns_404(
    monkeypatch: pytest.MonkeyPatch,
    override_db: _FakeAsyncSession,
    stub_background: list[dict[str, Any]],
) -> None:
    async def _fake_get(_db: Any, _firm_id: int) -> BrokerDealer | None:
        return None

    monkeypatch.setattr(bd_endpoint.repository, "get_broker_dealer", _fake_get)
    _enable_all_provider_keys(monkeypatch)

    app.dependency_overrides[get_current_user] = _viewer_user
    try:
        async with _client() as client:
            response = await client.post("/api/v1/broker-dealers/9999/refresh-all")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 404
    assert override_db.added == []
    assert stub_background == []


async def test_provider_key_missing_returns_503(
    monkeypatch: pytest.MonkeyPatch,
    override_db: _FakeAsyncSession,
    stub_background: list[dict[str, Any]],
) -> None:
    """Firm needs financials but Gemini key is unset → 503 before any
    PipelineRun row is created. The orchestrator never gets queued
    because it would have no chance to complete."""

    bd = _bd()  # all NULL → all gates open

    async def _fake_get(_db: Any, _firm_id: int) -> BrokerDealer | None:
        return bd

    # has_executive_contacts → False
    override_db.queue_scalar(None)

    monkeypatch.setattr(bd_endpoint.repository, "get_broker_dealer", _fake_get)
    monkeypatch.setattr(bd_endpoint.settings, "llm_provider", "gemini")
    monkeypatch.setattr(bd_endpoint.settings, "gemini_api_key", None)
    monkeypatch.setattr(bd_endpoint.settings, "apollo_api_key", "test-apollo")
    monkeypatch.setattr(bd_endpoint.settings, "hunter_api_key", "test-hunter")
    monkeypatch.setattr(bd_endpoint.settings, "serpapi_api_key", "test-serpapi")

    app.dependency_overrides[get_current_user] = _viewer_user
    try:
        async with _client() as client:
            response = await client.post(f"/api/v1/broker-dealers/{bd.id}/refresh-all")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 503
    assert "Gemini" in response.json()["detail"]
    assert override_db.added == []
    assert stub_background == []


async def test_in_flight_run_returns_409_with_existing_run_id(
    monkeypatch: pytest.MonkeyPatch,
    override_db: _FakeAsyncSession,
    stub_background: list[dict[str, Any]],
) -> None:
    bd = _bd()
    in_flight = PipelineRun(
        id=8888,
        pipeline_name="broker_dealer_refresh_all",
        trigger_source="manual_single:viewer@example.com",
        status="running",
        total_items=2,
        processed_items=1,
        success_count=1,
        failure_count=0,
        notes='{"bd_id": 11, "stage": "running"}',
    )

    async def _fake_get(_db: Any, _firm_id: int) -> BrokerDealer | None:
        return bd

    # has_executive_contacts → False, in-flight → present
    override_db.queue_scalar(None)
    override_db.queue_scalar(in_flight)

    monkeypatch.setattr(bd_endpoint.repository, "get_broker_dealer", _fake_get)
    _enable_all_provider_keys(monkeypatch)

    app.dependency_overrides[get_current_user] = _viewer_user
    try:
        async with _client() as client:
            response = await client.post(f"/api/v1/broker-dealers/{bd.id}/refresh-all")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["run_id"] == 8888
    assert detail["status"] == "running"
    assert detail["broker_dealer_id"] == bd.id
    assert override_db.added == []
    assert stub_background == []


async def test_cooldown_active_returns_429_with_retry_after(
    monkeypatch: pytest.MonkeyPatch,
    override_db: _FakeAsyncSession,
    stub_background: list[dict[str, Any]],
) -> None:
    bd = _bd()
    started_5_seconds_ago = datetime.now(timezone.utc) - timedelta(seconds=5)
    recent_completed = PipelineRun(
        id=8000,
        pipeline_name="broker_dealer_refresh_all",
        trigger_source="manual_single:viewer@example.com",
        status="completed",
        total_items=1,
        processed_items=1,
        success_count=1,
        failure_count=0,
        notes='{"bd_id": 11, "summary": "Refreshed: website."}',
        started_at=started_5_seconds_ago,
        completed_at=datetime.now(timezone.utc),
    )

    async def _fake_get(_db: Any, _firm_id: int) -> BrokerDealer | None:
        return bd

    # has_executive_contacts → False, in-flight → None (the recent run
    # already completed so it's not in the queued/running window),
    # cooldown → present (started 5s ago, well within 30s window)
    override_db.queue_scalar(None)
    override_db.queue_scalar(None)
    override_db.queue_scalar(recent_completed)

    monkeypatch.setattr(bd_endpoint.repository, "get_broker_dealer", _fake_get)
    _enable_all_provider_keys(monkeypatch)

    app.dependency_overrides[get_current_user] = _viewer_user
    try:
        async with _client() as client:
            response = await client.post(f"/api/v1/broker-dealers/{bd.id}/refresh-all")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 429
    assert "Retry-After" in response.headers
    retry_after = int(response.headers["Retry-After"])
    # Started ~5s ago, 30s window → ~25s remaining; tolerate ±2s for
    # test-runner clock jitter.
    assert 22 <= retry_after <= 30
    assert override_db.added == []
    assert stub_background == []


# ─────────────────────────── background task wrapper ────────────────────


async def test_background_task_delegates_to_orchestrator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def _fake_run(parent_run_id, bd_id, *, trigger_source, pipelines_to_run, pipelines_to_skip):
        captured["parent_run_id"] = parent_run_id
        captured["bd_id"] = bd_id
        captured["trigger_source"] = trigger_source
        captured["pipelines_to_run"] = pipelines_to_run
        captured["pipelines_to_skip"] = pipelines_to_skip

    monkeypatch.setattr(bd_endpoint, "run_refresh_all", _fake_run)

    await bd_endpoint._run_refresh_all_background(
        7777,
        11,
        "manual_single:viewer@example.com",
        ("broker_dealer_resolve_website",),
        ("financial_pdf_pipeline_single",),
    )

    assert captured["parent_run_id"] == 7777
    assert captured["bd_id"] == 11
    assert captured["pipelines_to_run"] == ("broker_dealer_resolve_website",)
    assert captured["pipelines_to_skip"] == ("financial_pdf_pipeline_single",)


async def test_background_task_swallows_exceptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _raises(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("simulated orchestrator failure")

    monkeypatch.setattr(bd_endpoint, "run_refresh_all", _raises)

    # Must NOT propagate.
    await bd_endpoint._run_refresh_all_background(
        7777,
        11,
        "manual_single:viewer@example.com",
        ("broker_dealer_resolve_website",),
        (),
    )
