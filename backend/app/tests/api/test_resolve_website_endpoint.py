"""API-layer tests for ``POST /broker-dealers/{id}/resolve-website``.

Exercises the lazy on-demand resolver endpoint. The handler is admin-
only, idempotent (returns cached value without re-running the chain when
``website`` is already set), and race-safe (UPDATE ... WHERE website IS
NULL).

The ``resolve_website`` chain function and the repository's
``get_broker_dealer`` are monkeypatched so the handler can be exercised
without Apollo + Hunter credentials, real network, or a live Postgres.
The DB session dependency is overridden with a tiny fake session that
records executed UPDATE statements so the race-safe UPSERT can be
asserted without a database round-trip.
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
from app.schemas.auth import AuthenticatedUser
from app.services.auth import get_current_user


# ─────────────────────────── auth fixtures ────────────────────────────


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


# ─────────────────────────── fake DB session ────────────────────────────


class _FakeAsyncSession:
    """Minimal async-session surface for the endpoint.

    Captures any ``execute(stmt)`` call so race-safe UPSERT semantics can
    be inspected by the test. ``refresh`` is a no-op — the handler also
    falls back to ``website or website`` so the response is fully
    populated even without a real refresh.
    """

    def __init__(self) -> None:
        self.executed: list[Any] = []
        self.committed = False

    async def execute(self, stmt: Any) -> None:
        self.executed.append(stmt)

    async def commit(self) -> None:
        self.committed = True

    async def refresh(self, _obj: Any) -> None:
        return None


_FAKE_SESSION = _FakeAsyncSession()


async def _fake_db_dep():
    yield _FAKE_SESSION


def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


def _broker_dealer(
    *,
    firm_id: int = 11,
    website: str | None = None,
    website_source: str | None = None,
) -> BrokerDealer:
    bd = BrokerDealer(
        id=firm_id,
        name="Acme Securities LLC",
        crd_number="1234",
        website=website,
        website_source=website_source,
    )
    return bd


# ─────────────────────────── shared override helper ──────────────────────


@pytest.fixture
def override_db():
    app.dependency_overrides[get_db_session] = _fake_db_dep
    # Reset the captured state per test.
    _FAKE_SESSION.executed.clear()
    _FAKE_SESSION.committed = False
    try:
        yield _FAKE_SESSION
    finally:
        app.dependency_overrides.pop(get_db_session, None)


# ─────────────────────────── happy / cached paths ────────────────────────


async def test_admin_runs_chain_persists_returns(
    monkeypatch: pytest.MonkeyPatch,
    override_db: _FakeAsyncSession,
) -> None:
    """Admin + firm with NULL website → chain runs, UPDATE issued, response
    carries the resolved website + source."""

    bd = _broker_dealer(website=None, website_source=None)

    async def _fake_get(_db: Any, firm_id: int) -> BrokerDealer | None:
        return bd if firm_id == bd.id else None

    async def _fake_resolve(
        _name: str,
        _crd: str | None,
        _apollo: Any,
        _hunter: Any,
        _serpapi: Any = None,
    ) -> tuple[str | None, str | None, str | None]:
        return ("https://acme-securities.example.test", "apollo", None)

    monkeypatch.setattr(bd_endpoint.repository, "get_broker_dealer", _fake_get)
    monkeypatch.setattr(bd_endpoint, "resolve_website", _fake_resolve)
    # The handler instantiates real Apollo/Hunter clients before passing
    # them to the chain — provide non-empty keys so construction succeeds.
    monkeypatch.setattr(bd_endpoint.settings, "apollo_api_key", "test-apollo")
    monkeypatch.setattr(bd_endpoint.settings, "hunter_api_key", "test-hunter")

    app.dependency_overrides[get_current_user] = _admin_user
    try:
        async with _client() as client:
            response = await client.post(
                f"/api/v1/broker-dealers/{bd.id}/resolve-website",
            )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 200
    body = response.json()
    assert body["website"] == "https://acme-securities.example.test"
    assert body["website_source"] == "apollo"
    assert body["reason"] is None
    # Race-safe persistence happened.
    assert override_db.committed is True
    assert len(override_db.executed) == 1


async def test_admin_already_resolved_returns_cached_skips_chain(
    monkeypatch: pytest.MonkeyPatch,
    override_db: _FakeAsyncSession,
) -> None:
    """Admin + firm that already has a website → return cached value, do
    NOT call the chain."""

    bd = _broker_dealer(
        website="https://cached.example.test",
        website_source="finra",
    )
    chain_called = {"count": 0}

    async def _fake_get(_db: Any, firm_id: int) -> BrokerDealer | None:
        return bd if firm_id == bd.id else None

    async def _fake_resolve(*_args: Any, **_kwargs: Any) -> Any:
        chain_called["count"] += 1
        return (None, None, "should-not-be-called")

    monkeypatch.setattr(bd_endpoint.repository, "get_broker_dealer", _fake_get)
    monkeypatch.setattr(bd_endpoint, "resolve_website", _fake_resolve)
    monkeypatch.setattr(bd_endpoint.settings, "apollo_api_key", "test-apollo")
    monkeypatch.setattr(bd_endpoint.settings, "hunter_api_key", "test-hunter")

    app.dependency_overrides[get_current_user] = _admin_user
    try:
        async with _client() as client:
            response = await client.post(
                f"/api/v1/broker-dealers/{bd.id}/resolve-website",
            )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 200
    body = response.json()
    assert body["website"] == "https://cached.example.test"
    assert body["website_source"] == "finra"
    assert body["reason"] is None
    assert chain_called["count"] == 0
    # No DB write on idempotent cache-hit.
    assert override_db.committed is False
    assert override_db.executed == []


# ─────────────────────────── auth + 404 paths ────────────────────────────


async def test_non_admin_returns_403(
    monkeypatch: pytest.MonkeyPatch,
    override_db: _FakeAsyncSession,
) -> None:
    monkeypatch.setattr(bd_endpoint.settings, "apollo_api_key", "test-apollo")
    app.dependency_overrides[get_current_user] = _viewer_user
    try:
        async with _client() as client:
            response = await client.post(
                "/api/v1/broker-dealers/99/resolve-website",
            )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 403


async def test_missing_firm_returns_404(
    monkeypatch: pytest.MonkeyPatch,
    override_db: _FakeAsyncSession,
) -> None:
    async def _fake_get(_db: Any, _firm_id: int) -> BrokerDealer | None:
        return None

    monkeypatch.setattr(bd_endpoint.repository, "get_broker_dealer", _fake_get)
    monkeypatch.setattr(bd_endpoint.settings, "apollo_api_key", "test-apollo")

    app.dependency_overrides[get_current_user] = _admin_user
    try:
        async with _client() as client:
            response = await client.post(
                "/api/v1/broker-dealers/9999/resolve-website",
            )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 404


# ─────────────────────────── miss + provider-error paths ─────────────────


async def test_chain_clean_miss_returns_reason_no_persistence(
    monkeypatch: pytest.MonkeyPatch,
    override_db: _FakeAsyncSession,
) -> None:
    bd = _broker_dealer()

    async def _fake_get(_db: Any, _firm_id: int) -> BrokerDealer | None:
        return bd

    async def _fake_resolve(*_args: Any, **_kwargs: Any) -> Any:
        return (None, None, "no_valid_candidate")

    monkeypatch.setattr(bd_endpoint.repository, "get_broker_dealer", _fake_get)
    monkeypatch.setattr(bd_endpoint, "resolve_website", _fake_resolve)
    monkeypatch.setattr(bd_endpoint.settings, "apollo_api_key", "test-apollo")
    monkeypatch.setattr(bd_endpoint.settings, "hunter_api_key", "test-hunter")

    app.dependency_overrides[get_current_user] = _admin_user
    try:
        async with _client() as client:
            response = await client.post(
                f"/api/v1/broker-dealers/{bd.id}/resolve-website",
            )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 200
    body = response.json()
    assert body["website"] is None
    assert body["website_source"] is None
    assert body["reason"] == "no_valid_candidate"
    # No UPDATE issued on a miss; the column stays NULL by inaction.
    assert override_db.committed is False
    assert override_db.executed == []


async def test_chain_provider_error_leaves_column_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    override_db: _FakeAsyncSession,
) -> None:
    """Total provider failure → website column stays as-is and the
    response reason starts with ``all_providers_errored``."""

    bd = _broker_dealer()

    async def _fake_get(_db: Any, _firm_id: int) -> BrokerDealer | None:
        return bd

    async def _fake_resolve(*_args: Any, **_kwargs: Any) -> Any:
        return (None, None, "all_providers_errored: apollo: 503; hunter: 500")

    monkeypatch.setattr(bd_endpoint.repository, "get_broker_dealer", _fake_get)
    monkeypatch.setattr(bd_endpoint, "resolve_website", _fake_resolve)
    monkeypatch.setattr(bd_endpoint.settings, "apollo_api_key", "test-apollo")
    monkeypatch.setattr(bd_endpoint.settings, "hunter_api_key", "test-hunter")

    app.dependency_overrides[get_current_user] = _admin_user
    try:
        async with _client() as client:
            response = await client.post(
                f"/api/v1/broker-dealers/{bd.id}/resolve-website",
            )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 200
    body = response.json()
    assert body["website"] is None
    assert body["website_source"] is None
    assert body["reason"].startswith("all_providers_errored")
    # No UPDATE — preserve the column on transient outage.
    assert override_db.committed is False
    assert override_db.executed == []


async def test_apollo_key_missing_returns_503(
    monkeypatch: pytest.MonkeyPatch,
    override_db: _FakeAsyncSession,
) -> None:
    bd = _broker_dealer()

    async def _fake_get(_db: Any, _firm_id: int) -> BrokerDealer | None:
        return bd

    monkeypatch.setattr(bd_endpoint.repository, "get_broker_dealer", _fake_get)
    monkeypatch.setattr(bd_endpoint.settings, "apollo_api_key", None)

    app.dependency_overrides[get_current_user] = _admin_user
    try:
        async with _client() as client:
            response = await client.post(
                f"/api/v1/broker-dealers/{bd.id}/resolve-website",
            )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 503
