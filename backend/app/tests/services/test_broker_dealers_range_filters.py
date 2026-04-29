"""Unit tests for the net-capital and registration-date range filters on
``BrokerDealerRepository.list_broker_dealers``.

Covers Sprint 3 tasks #15 + #16 BE half: four optional query params on the
master-list endpoint that filter ``broker_dealers.latest_net_capital`` and
``broker_dealers.registration_date`` by inclusive range bounds.

Two layers of coverage:

1. SQL-shape assertions: compile the WHERE-bearing statements that the
   repository issues to ``db.execute`` and confirm the expected predicates
   are present (or absent, for the no-filter regression case). This mirrors
   the ``_StagedSession`` pattern from ``test_types_of_business_filter.py``.
2. Endpoint-layer assertions: hit ``GET /api/v1/broker-dealers`` through
   the FastAPI app with a stubbed repository and confirm:
     a) FastAPI's ``ge=0`` guard rejects negative net-capital values with 422.
     b) ISO 8601 date strings parse cleanly into the repository call.
     c) The endpoint forwards the four params verbatim to the repository.

Null exclusion is a property of SQL itself (``NULL >= 5`` evaluates to
unknown, which WHERE treats as false) — verifying the predicate compiles to
``broker_dealers.latest_net_capital >= :param`` is enough; SQLAlchemy +
Postgres do the rest. We don't try to run the JSONB-flavored statement
locally; Phase 2's prod smoke check is the live integration signal.
"""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.api.v1.endpoints.broker_dealers import repository as endpoint_repository
from app.db.session import get_db_session
from app.main import app
from app.schemas.auth import AuthenticatedUser
from app.schemas.broker_dealer import BrokerDealerListMeta, BrokerDealerListResponse
from app.services.auth import get_current_user
from app.services.broker_dealers import BrokerDealerRepository


# ─────────────────────────────────────────────────────────────────────────────
# Repository-layer helpers
# ─────────────────────────────────────────────────────────────────────────────


class _StagedSession:
    """AsyncSession mock that captures every executed statement and returns
    pre-staged result objects in call order.

    ``list_broker_dealers`` issues three execute() calls:
      1. count_stmt -> result.scalar_one() returns the total row count
      2. data_stmt  -> result.scalars().all() returns the page items
      3. latest pipeline-run -> result.scalar_one_or_none() returns the run
    """

    def __init__(self) -> None:
        self.executed_statements: list[object] = []
        self._call_count = 0

    async def execute(self, statement: object) -> object:
        self.executed_statements.append(statement)
        self._call_count += 1
        result = MagicMock()
        if self._call_count == 1:
            # count_stmt -> total
            result.scalar_one.return_value = 0
        elif self._call_count == 2:
            # data_stmt -> items
            scalars = MagicMock()
            scalars.all.return_value = []
            result.scalars.return_value = scalars
        else:
            # latest pipeline-run lookup (and any further calls)
            result.scalar_one_or_none.return_value = None
        return result


def _compile_sql(statement: object) -> str:
    """Render a SQLAlchemy statement to its parameter-substituted SQL text."""
    compiled = statement.compile(compile_kwargs={"literal_binds": True})
    return str(compiled).lower()


def _captured_where_sql(session: _StagedSession) -> str:
    """Compile the data_stmt (second execute) and return its WHERE clause text."""
    assert len(session.executed_statements) >= 2, "expected count_stmt + data_stmt to have run"
    data_stmt = session.executed_statements[1]
    sql = _compile_sql(data_stmt)
    if "where" not in sql:
        return ""
    return sql.split("where", 1)[1]


def _default_kwargs() -> dict[str, object]:
    """Baseline kwargs that exercise no filters — every test starts here and
    overrides only what it cares about."""
    return {
        "search": None,
        "states": [],
        "statuses": [],
        "health_statuses": [],
        "lead_priorities": [],
        "clearing_partners": [],
        "clearing_types": [],
        "types_of_business": [],
        # ``primary`` adds ``is_deficient = false``; ``all`` keeps the WHERE
        # focused on the range filters under test.
        "list_mode": "all",
        "sort_by": "name",
        "sort_dir": "asc",
        "page": 1,
        "limit": 25,
    }


@pytest.fixture
def repository() -> BrokerDealerRepository:
    return BrokerDealerRepository()


# ─────────────────────────────────────────────────────────────────────────────
# Repository-layer SQL tests
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_min_net_capital_alone_emits_gte_predicate(
    repository: BrokerDealerRepository,
) -> None:
    """min_net_capital alone produces ``latest_net_capital >= :param`` and no
    other range predicate."""
    session = _StagedSession()

    await repository.list_broker_dealers(
        session, **_default_kwargs(), min_net_capital=10_000_000.0
    )

    where = _captured_where_sql(session)
    assert "broker_dealers.latest_net_capital >= 10000000.0" in where
    assert "broker_dealers.latest_net_capital <= " not in where
    assert "broker_dealers.registration_date" not in where


@pytest.mark.asyncio
async def test_max_net_capital_alone_emits_lte_predicate(
    repository: BrokerDealerRepository,
) -> None:
    """Symmetric to the min case: max alone produces only the <= predicate."""
    session = _StagedSession()

    await repository.list_broker_dealers(
        session, **_default_kwargs(), max_net_capital=5_000_000.0
    )

    where = _captured_where_sql(session)
    assert "broker_dealers.latest_net_capital <= 5000000.0" in where
    assert "broker_dealers.latest_net_capital >= " not in where
    assert "broker_dealers.registration_date" not in where


@pytest.mark.asyncio
async def test_both_net_capital_bounds_emit_range(
    repository: BrokerDealerRepository,
) -> None:
    """Min + max together produce both predicates AND-joined."""
    session = _StagedSession()

    await repository.list_broker_dealers(
        session,
        **_default_kwargs(),
        min_net_capital=1_000_000.0,
        max_net_capital=10_000_000.0,
    )

    where = _captured_where_sql(session)
    assert "broker_dealers.latest_net_capital >= 1000000.0" in where
    assert "broker_dealers.latest_net_capital <= 10000000.0" in where


@pytest.mark.asyncio
async def test_registered_after_alone_emits_gte_predicate(
    repository: BrokerDealerRepository,
) -> None:
    """registered_after alone produces ``registration_date >= :param``."""
    session = _StagedSession()

    await repository.list_broker_dealers(
        session, **_default_kwargs(), registered_after=date(2020, 1, 1)
    )

    where = _captured_where_sql(session)
    assert "broker_dealers.registration_date >= '2020-01-01'" in where
    assert "broker_dealers.registration_date <= " not in where
    assert "broker_dealers.latest_net_capital" not in where


@pytest.mark.asyncio
async def test_registered_before_alone_emits_lte_predicate(
    repository: BrokerDealerRepository,
) -> None:
    """Symmetric to registered_after."""
    session = _StagedSession()

    await repository.list_broker_dealers(
        session, **_default_kwargs(), registered_before=date(2024, 12, 31)
    )

    where = _captured_where_sql(session)
    assert "broker_dealers.registration_date <= '2024-12-31'" in where
    assert "broker_dealers.registration_date >= " not in where
    assert "broker_dealers.latest_net_capital" not in where


@pytest.mark.asyncio
async def test_all_four_filters_combine(
    repository: BrokerDealerRepository,
) -> None:
    """All four range filters together produce four AND-joined predicates."""
    session = _StagedSession()

    await repository.list_broker_dealers(
        session,
        **_default_kwargs(),
        min_net_capital=1_000_000.0,
        max_net_capital=50_000_000.0,
        registered_after=date(2015, 6, 1),
        registered_before=date(2024, 12, 31),
    )

    where = _captured_where_sql(session)
    assert "broker_dealers.latest_net_capital >= 1000000.0" in where
    assert "broker_dealers.latest_net_capital <= 50000000.0" in where
    assert "broker_dealers.registration_date >= '2015-06-01'" in where
    assert "broker_dealers.registration_date <= '2024-12-31'" in where


@pytest.mark.asyncio
async def test_no_range_filters_leaves_existing_query_unchanged(
    repository: BrokerDealerRepository,
) -> None:
    """Regression: when none of the four params are supplied the existing
    callers (export, etc.) see exactly the same WHERE clause they had before
    this feature shipped."""
    session = _StagedSession()

    await repository.list_broker_dealers(session, **_default_kwargs())

    where = _captured_where_sql(session)
    assert "broker_dealers.latest_net_capital" not in where
    assert "broker_dealers.registration_date" not in where


@pytest.mark.asyncio
async def test_count_and_data_statements_share_the_same_filters(
    repository: BrokerDealerRepository,
) -> None:
    """count_stmt and data_stmt must apply identical predicates so the
    paginated total stays consistent with the page items."""
    session = _StagedSession()

    await repository.list_broker_dealers(
        session,
        **_default_kwargs(),
        min_net_capital=2_500_000.0,
        registered_after=date(2018, 3, 15),
    )

    count_sql = _compile_sql(session.executed_statements[0])
    data_sql = _compile_sql(session.executed_statements[1])
    for clause in (
        "broker_dealers.latest_net_capital >= 2500000.0",
        "broker_dealers.registration_date >= '2018-03-15'",
    ):
        assert clause in count_sql, f"count_stmt missing predicate: {clause}"
        assert clause in data_sql, f"data_stmt missing predicate: {clause}"


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint-layer tests (FastAPI Query validation + parameter passthrough)
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
    """Override auth + db dependencies and patch the endpoint's repository
    so the handler runs without touching Postgres or BetterAuth.

    Yields the AsyncMock so tests can assert on the kwargs the endpoint
    forwards to ``repository.list_broker_dealers``.
    """
    fake_response = BrokerDealerListResponse(
        items=[],
        meta=BrokerDealerListMeta(
            page=1,
            limit=25,
            total=0,
            total_pages=1,
            pipeline_refreshed_at=None,
        ),
    )
    mock_list = AsyncMock(return_value=fake_response)

    app.dependency_overrides[get_db_session] = _fake_db_session
    app.dependency_overrides[get_current_user] = _user
    with patch.object(endpoint_repository, "list_broker_dealers", new=mock_list):
        try:
            yield mock_list
        finally:
            app.dependency_overrides.pop(get_db_session, None)
            app.dependency_overrides.pop(get_current_user, None)


async def test_endpoint_rejects_negative_min_net_capital(stubbed_endpoint) -> None:
    """FastAPI's ``ge=0`` guard short-circuits with a 422 before the handler
    runs — protects the repository from nonsense bounds."""
    async with _client() as client:
        response = await client.get(
            "/api/v1/broker-dealers", params={"min_net_capital": -1}
        )
    assert response.status_code == 422
    stubbed_endpoint.assert_not_called()


async def test_endpoint_rejects_negative_max_net_capital(stubbed_endpoint) -> None:
    """Symmetric to the min case."""
    async with _client() as client:
        response = await client.get(
            "/api/v1/broker-dealers", params={"max_net_capital": -0.01}
        )
    assert response.status_code == 422
    stubbed_endpoint.assert_not_called()


async def test_endpoint_parses_iso_dates_and_forwards_all_four_params(
    stubbed_endpoint,
) -> None:
    """Happy path: ISO 8601 date strings parse to ``date`` objects and the
    endpoint forwards the parsed values into ``repository.list_broker_dealers``
    by keyword."""
    async with _client() as client:
        response = await client.get(
            "/api/v1/broker-dealers",
            params={
                "min_net_capital": "10000000",
                "max_net_capital": "50000000",
                "registered_after": "2020-01-01",
                "registered_before": "2024-12-31",
            },
        )
    assert response.status_code == 200, response.text
    stubbed_endpoint.assert_awaited_once()
    kwargs = stubbed_endpoint.await_args.kwargs
    assert kwargs["min_net_capital"] == 10_000_000.0
    assert kwargs["max_net_capital"] == 50_000_000.0
    assert kwargs["registered_after"] == date(2020, 1, 1)
    assert kwargs["registered_before"] == date(2024, 12, 31)


async def test_endpoint_passes_none_for_omitted_range_params(stubbed_endpoint) -> None:
    """Backward-compat: a request that omits all four new params still
    reaches the repository with ``None`` for each, so the no-filter regression
    case is preserved end-to-end."""
    async with _client() as client:
        response = await client.get("/api/v1/broker-dealers")
    assert response.status_code == 200, response.text
    kwargs = stubbed_endpoint.await_args.kwargs
    assert kwargs["min_net_capital"] is None
    assert kwargs["max_net_capital"] is None
    assert kwargs["registered_after"] is None
    assert kwargs["registered_before"] is None


async def test_endpoint_rejects_inverted_net_capital_bounds(stubbed_endpoint) -> None:
    """min > max is a client error, not a silent zero-row response.

    A user pasting an inverted band (lower > upper) gets a 422 with a clear
    message; the repository is never called.
    """
    async with _client() as client:
        response = await client.get(
            "/api/v1/broker-dealers",
            params={"min_net_capital": "10000000", "max_net_capital": "5000000"},
        )
    assert response.status_code == 422
    body = response.json()
    assert "min_net_capital" in body["detail"]
    stubbed_endpoint.assert_not_called()


async def test_endpoint_accepts_equal_net_capital_bounds(stubbed_endpoint) -> None:
    """Bounds are inclusive on both ends: min == max is a single-point band,
    not an inversion. Documents the boundary so a future tightening of the
    422 guard doesn't accidentally reject equality."""
    async with _client() as client:
        response = await client.get(
            "/api/v1/broker-dealers",
            params={"min_net_capital": "5000000", "max_net_capital": "5000000"},
        )
    assert response.status_code == 200, response.text
    kwargs = stubbed_endpoint.await_args.kwargs
    assert kwargs["min_net_capital"] == 5_000_000.0
    assert kwargs["max_net_capital"] == 5_000_000.0


async def test_endpoint_rejects_inverted_registration_dates(stubbed_endpoint) -> None:
    """registered_after > registered_before -> 422, repository never called."""
    async with _client() as client:
        response = await client.get(
            "/api/v1/broker-dealers",
            params={
                "registered_after": "2024-12-31",
                "registered_before": "2020-01-01",
            },
        )
    assert response.status_code == 422
    body = response.json()
    assert "registered_after" in body["detail"]
    stubbed_endpoint.assert_not_called()


async def test_endpoint_accepts_equal_registration_dates(stubbed_endpoint) -> None:
    """Same-day band is valid (inclusive on both ends)."""
    async with _client() as client:
        response = await client.get(
            "/api/v1/broker-dealers",
            params={
                "registered_after": "2020-06-15",
                "registered_before": "2020-06-15",
            },
        )
    assert response.status_code == 200, response.text
    kwargs = stubbed_endpoint.await_args.kwargs
    assert kwargs["registered_after"] == date(2020, 6, 15)
    assert kwargs["registered_before"] == date(2020, 6, 15)


@pytest.mark.asyncio
async def test_range_filters_combine_with_state_filter(
    repository: BrokerDealerRepository,
) -> None:
    """Regression: range filters AND-join with the existing ``state`` filter
    instead of replacing it. A request that scopes to NY firms with a
    net-capital floor must keep both predicates in the same WHERE."""
    session = _StagedSession()

    await repository.list_broker_dealers(
        session,
        **{**_default_kwargs(), "states": ["NY"]},
        min_net_capital=2_500_000.0,
    )

    where = _captured_where_sql(session)
    assert "broker_dealers.state in ('ny')" in where
    assert "broker_dealers.latest_net_capital >= 2500000.0" in where
