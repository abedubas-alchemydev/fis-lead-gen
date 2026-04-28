"""Unit tests for the ``category`` query param on the alerts list endpoint.

Sprint 4 task #18 BE half from the 2026-04-27 client meeting. Deshorn flagged
that deficiency notices were leading the alerts page and felt noisy; he
wanted Form BD as the primary alert category and deficiencies in a separate
tab. This is the BE contract that unblocks the FE tabs.

Two layers of coverage:

1. SQL-shape assertions: compile the WHERE-bearing statements that
   ``AlertRepository.list_alerts`` issues to ``db.execute`` and confirm the
   expected ``form_type`` predicate is present (or absent, for the no-filter
   regression case). Mirrors the ``_StagedSession`` pattern from
   ``test_broker_dealers_range_filters.py``.
2. Endpoint-layer assertions: hit ``GET /api/v1/alerts`` through the FastAPI
   app with a stubbed repository and confirm:
     a) ``category=invalid`` is rejected by FastAPI's ``Literal`` guard with 422.
     b) Each accepted value forwards verbatim to the repository.
     c) Omitted ``category`` forwards as ``None`` (default = no filter).

The discriminator column is ``filing_alerts.form_type``, populated only by
``services/filing_monitor.py`` with values ``"Form BD"`` (canonical Form BD
filing) or ``"Form 17a-11"`` (deficiency notice). The category mapping is
therefore a clean two-way split — no third bucket.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.api.v1.endpoints.alerts import repository as endpoint_repository
from app.db.session import get_db_session
from app.main import app
from app.schemas.alerts import AlertListMeta, AlertListResponse
from app.schemas.auth import AuthenticatedUser
from app.services.alerts import AlertRepository
from app.services.auth import get_current_user


# ─────────────────────────────────────────────────────────────────────────────
# Repository-layer helpers
# ─────────────────────────────────────────────────────────────────────────────


class _StagedSession:
    """AsyncSession mock that captures every executed statement and returns
    pre-staged result objects in call order.

    ``list_alerts`` issues two execute() calls:
      1. count_stmt -> result.scalar_one() returns the total row count
      2. data_stmt  -> result.all() returns the page rows
    """

    def __init__(self) -> None:
        self.executed_statements: list[object] = []
        self._call_count = 0

    async def execute(self, statement: object) -> object:
        self.executed_statements.append(statement)
        self._call_count += 1
        result = MagicMock()
        if self._call_count == 1:
            result.scalar_one.return_value = 0
        else:
            result.all.return_value = []
        return result


def _compile_sql(statement: object) -> str:
    compiled = statement.compile(compile_kwargs={"literal_binds": True})
    return str(compiled).lower()


def _captured_where_sql(session: _StagedSession) -> str:
    assert len(session.executed_statements) >= 2, "expected count_stmt + data_stmt to have run"
    data_stmt = session.executed_statements[1]
    sql = _compile_sql(data_stmt)
    if "where" not in sql:
        return ""
    return sql.split("where", 1)[1]


def _default_kwargs() -> dict[str, object]:
    return {
        "form_types": [],
        "priorities": [],
        "is_read": None,
        "broker_dealer_id": None,
        "page": 1,
        "limit": 20,
    }


@pytest.fixture
def repository() -> AlertRepository:
    return AlertRepository()


# ─────────────────────────────────────────────────────────────────────────────
# Repository-layer SQL tests
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_category_form_bd_emits_form_bd_predicate(
    repository: AlertRepository,
) -> None:
    session = _StagedSession()

    await repository.list_alerts(session, **_default_kwargs(), category="form_bd")

    where = _captured_where_sql(session)
    assert "filing_alerts.form_type = 'form bd'" in where
    assert "form 17a-11" not in where


@pytest.mark.asyncio
async def test_category_deficiency_emits_form_17a11_predicate(
    repository: AlertRepository,
) -> None:
    session = _StagedSession()

    await repository.list_alerts(session, **_default_kwargs(), category="deficiency")

    where = _captured_where_sql(session)
    assert "filing_alerts.form_type = 'form 17a-11'" in where
    assert "form bd" not in where


@pytest.mark.asyncio
async def test_category_all_leaves_form_type_unfiltered(
    repository: AlertRepository,
) -> None:
    """``all`` is the explicit no-filter sentinel — it must not add any
    ``form_type`` predicate so the page returns everything."""
    session = _StagedSession()

    await repository.list_alerts(session, **_default_kwargs(), category="all")

    where = _captured_where_sql(session)
    assert "filing_alerts.form_type" not in where


@pytest.mark.asyncio
async def test_category_none_leaves_form_type_unfiltered(
    repository: AlertRepository,
) -> None:
    """Regression: when ``category`` is omitted the existing callers
    (firm detail, ``get_recent_alerts``) see no ``form_type`` predicate."""
    session = _StagedSession()

    await repository.list_alerts(session, **_default_kwargs())

    where = _captured_where_sql(session)
    assert "filing_alerts.form_type" not in where


@pytest.mark.asyncio
async def test_count_and_data_statements_share_the_category_filter(
    repository: AlertRepository,
) -> None:
    """count_stmt and data_stmt must apply identical predicates so the
    paginated total stays consistent with the page items."""
    session = _StagedSession()

    await repository.list_alerts(session, **_default_kwargs(), category="form_bd")

    count_sql = _compile_sql(session.executed_statements[0])
    data_sql = _compile_sql(session.executed_statements[1])
    assert "filing_alerts.form_type = 'form bd'" in count_sql
    assert "filing_alerts.form_type = 'form bd'" in data_sql


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint-layer tests (FastAPI Literal validation + parameter passthrough)
# ─────────────────────────────────────────────────────────────────────────────


def _user() -> AuthenticatedUser:
    return AuthenticatedUser(
        id="test-viewer",
        name="Test Viewer",
        email="viewer@example.com",
        role="viewer",
        session_expires_at=datetime(2099, 1, 1),
    )


async def _fake_db_session():
    yield None


def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


@pytest.fixture
def stubbed_endpoint():
    fake_response = AlertListResponse(
        items=[],
        meta=AlertListMeta(page=1, limit=20, total=0, total_pages=1),
    )
    mock_list = AsyncMock(return_value=fake_response)

    app.dependency_overrides[get_db_session] = _fake_db_session
    app.dependency_overrides[get_current_user] = _user
    with patch.object(endpoint_repository, "list_alerts", new=mock_list):
        try:
            yield mock_list
        finally:
            app.dependency_overrides.pop(get_db_session, None)
            app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_endpoint_rejects_invalid_category(stubbed_endpoint) -> None:
    """FastAPI's ``Literal`` guard short-circuits with 422 — protects the
    repository from arbitrary discriminator strings the FE might send."""
    async with _client() as client:
        response = await client.get(
            "/api/v1/alerts", params={"category": "withdrawal"}
        )
    assert response.status_code == 422
    stubbed_endpoint.assert_not_called()


@pytest.mark.asyncio
async def test_endpoint_forwards_form_bd_category(stubbed_endpoint) -> None:
    async with _client() as client:
        response = await client.get(
            "/api/v1/alerts", params={"category": "form_bd"}
        )
    assert response.status_code == 200, response.text
    stubbed_endpoint.assert_awaited_once()
    assert stubbed_endpoint.await_args.kwargs["category"] == "form_bd"


@pytest.mark.asyncio
async def test_endpoint_forwards_deficiency_category(stubbed_endpoint) -> None:
    async with _client() as client:
        response = await client.get(
            "/api/v1/alerts", params={"category": "deficiency"}
        )
    assert response.status_code == 200, response.text
    assert stubbed_endpoint.await_args.kwargs["category"] == "deficiency"


@pytest.mark.asyncio
async def test_endpoint_forwards_all_category(stubbed_endpoint) -> None:
    """``all`` is an accepted Literal value but resolves to no filter at the
    repository layer (covered separately above)."""
    async with _client() as client:
        response = await client.get(
            "/api/v1/alerts", params={"category": "all"}
        )
    assert response.status_code == 200, response.text
    assert stubbed_endpoint.await_args.kwargs["category"] == "all"


@pytest.mark.asyncio
async def test_endpoint_passes_none_when_category_omitted(stubbed_endpoint) -> None:
    """Backward-compat: a request that omits ``category`` reaches the
    repository with ``None``, so existing FE clients keep their behavior."""
    async with _client() as client:
        response = await client.get("/api/v1/alerts")
    assert response.status_code == 200, response.text
    assert stubbed_endpoint.await_args.kwargs["category"] is None
