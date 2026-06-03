"""Tests for the "High Value Participant" segment.

The dashboard KPI tile, the Top Prospects card, and the master-list
``?segment=high_value`` deep-link all resolve to one predicate
(``high_value_participant_filter``): latest net capital in the [$5M, $100M]
band OR the OTC corporate-equity retailing business type. These tests lock:

1. ``count_high_value_participants`` counts that union (both arms present in
   the WHERE), so the KPI number reflects the business-type firms too.
2. ``list_broker_dealers(segment="high_value")`` appends the same predicate,
   AND-joined with every other filter, so the drill-down list matches the
   count and can still be narrowed by state / clearing type / etc.
3. The endpoint forwards ``segment`` verbatim, rejects unknown values with a
   422 (strict ``pattern``), and defaults to ``None`` when omitted.

Pure SQL-shape + endpoint-passthrough assertions — no database required, so
these run in the default (non-integration) suite. We compile the statements
the repository hands to ``db.execute`` and inspect their text, mirroring
``test_broker_dealers_range_filters.py``.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.api.v1.endpoints.broker_dealers import repository as endpoint_repository
from app.db.session import get_db_session
from app.main import app
from app.schemas.auth import AuthenticatedUser
from app.schemas.broker_dealer import BrokerDealerListMeta, BrokerDealerListResponse
from app.services.auth import get_current_user
from app.services.broker_dealers import (
    HIGH_VALUE_BUSINESS_TYPES,
    BrokerDealerRepository,
)

# Canonical FINRA label, lowercased, for substring assertions on compiled SQL.
BUSINESS_TYPE_SQL = HIGH_VALUE_BUSINESS_TYPES[0].lower()


# ─────────────────────────────────────────────────────────────────────────────
# Repository-layer helpers (staged session + SQL compilation)
# ─────────────────────────────────────────────────────────────────────────────


class _StagedSession:
    """AsyncSession mock that records every executed statement.

    ``list_broker_dealers`` runs count -> data -> pipeline-run; the single-arg
    count helpers run just one statement. Each call returns a MagicMock whose
    scalar accessors are pre-stubbed so the caller's ``int(...)`` / ``.all()``
    post-processing doesn't blow up.
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
        elif self._call_count == 2:
            scalars = MagicMock()
            scalars.all.return_value = []
            result.scalars.return_value = scalars
        else:
            result.scalar_one_or_none.return_value = None
        return result


def _compile_sql(statement: object) -> str:
    compiled = statement.compile(compile_kwargs={"literal_binds": True})
    return str(compiled).lower()


def _captured_where_sql(session: _StagedSession) -> str:
    """Compile the data_stmt (second execute) and return its WHERE text."""
    assert len(session.executed_statements) >= 2, "expected count + data stmts"
    sql = _compile_sql(session.executed_statements[1])
    return sql.split("where", 1)[1] if "where" in sql else ""


def _default_kwargs() -> dict[str, object]:
    """Baseline list_broker_dealers kwargs with no filters. ``list_mode='all'``
    keeps the WHERE focused on the segment predicate under test (``primary``
    would add ``is_deficient = false``)."""
    return {
        "search": None,
        "states": [],
        "statuses": [],
        "health_statuses": [],
        "lead_priorities": [],
        "clearing_partners": [],
        "clearing_types": [],
        "types_of_business": [],
        "list_mode": "all",
        "sort_by": "name",
        "sort_dir": "asc",
        "page": 1,
        "limit": 25,
    }


@pytest.fixture
def repository() -> BrokerDealerRepository:
    return BrokerDealerRepository()


def _assert_union_predicate(sql: str) -> None:
    """Both arms of the segment OR are present in ``sql``."""
    assert "latest_net_capital between 5000000 and 100000000" in sql, (
        f"net-capital band arm missing; got: {sql}"
    )
    assert "types_of_business ?|" in sql, f"business-type arm missing; got: {sql}"
    assert BUSINESS_TYPE_SQL in sql, f"canonical type label missing; got: {sql}"


# ─────────────────────────────────────────────────────────────────────────────
# count_high_value_participants
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_count_unions_band_and_business_type(
    repository: BrokerDealerRepository,
) -> None:
    """The KPI count is the union, not the bare net-capital band."""
    session = _StagedSession()

    total = await repository.count_high_value_participants(session)

    assert total == 0  # stubbed scalar_one
    count_sql = _compile_sql(session.executed_statements[0])
    assert "count(broker_dealers.id)" in count_sql
    _assert_union_predicate(count_sql)


# ─────────────────────────────────────────────────────────────────────────────
# list_broker_dealers(segment=...)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_segment_high_value_emits_union_predicate(
    repository: BrokerDealerRepository,
) -> None:
    """segment='high_value' appends the band-OR-business-type predicate."""
    session = _StagedSession()

    await repository.list_broker_dealers(
        session, **_default_kwargs(), segment="high_value"
    )

    _assert_union_predicate(_captured_where_sql(session))


@pytest.mark.asyncio
async def test_segment_count_and_data_share_predicate(
    repository: BrokerDealerRepository,
) -> None:
    """count_stmt and data_stmt apply the same segment predicate, so the
    paginated total never drifts from the visible page."""
    session = _StagedSession()

    await repository.list_broker_dealers(
        session, **_default_kwargs(), segment="high_value"
    )

    count_sql = _compile_sql(session.executed_statements[0])
    data_sql = _compile_sql(session.executed_statements[1])
    _assert_union_predicate(count_sql)
    _assert_union_predicate(data_sql)


@pytest.mark.asyncio
async def test_segment_ands_with_state_filter(
    repository: BrokerDealerRepository,
) -> None:
    """The segment narrows alongside per-field filters rather than replacing
    them — a user can scope the High Value segment to one state."""
    session = _StagedSession()

    await repository.list_broker_dealers(
        session,
        **{**_default_kwargs(), "states": ["NY"]},
        segment="high_value",
    )

    where = _captured_where_sql(session)
    assert "broker_dealers.state in ('ny')" in where
    _assert_union_predicate(where)


@pytest.mark.asyncio
async def test_no_segment_leaves_query_unchanged(
    repository: BrokerDealerRepository,
) -> None:
    """Regression: omitting segment (every existing caller) adds no
    high-value predicate."""
    session = _StagedSession()

    await repository.list_broker_dealers(session, **_default_kwargs())

    where = _captured_where_sql(session)
    assert "types_of_business ?|" not in where
    assert "latest_net_capital between" not in where


@pytest.mark.asyncio
async def test_unknown_segment_is_ignored_by_repository(
    repository: BrokerDealerRepository,
) -> None:
    """Defense in depth: an unrecognised segment value (the endpoint pattern
    should already have rejected it) adds no predicate rather than erroring."""
    session = _StagedSession()

    await repository.list_broker_dealers(
        session, **_default_kwargs(), segment="bogus"
    )

    where = _captured_where_sql(session)
    assert "types_of_business ?|" not in where


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint param handling
# ─────────────────────────────────────────────────────────────────────────────


def _user() -> AuthenticatedUser:
    return AuthenticatedUser(
        id="test-viewer",
        name="Test Viewer",
        email="viewer@example.com",
        role="viewer",
        feature_permissions=["master_list"],
        session_expires_at=datetime(2099, 1, 1),
    )


async def _fake_db_session():
    yield None


def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


@pytest.fixture
def stubbed_endpoint():
    """Patch the endpoint repository + auth/db deps so the handler runs without
    Postgres or BetterAuth. Yields the AsyncMock for kwargs assertions."""
    fake_response = BrokerDealerListResponse(
        items=[],
        meta=BrokerDealerListMeta(
            page=1, limit=25, total=0, total_pages=1, pipeline_refreshed_at=None
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


async def test_endpoint_forwards_known_segment(stubbed_endpoint) -> None:
    async with _client() as client:
        response = await client.get(
            "/api/v1/broker-dealers", params={"segment": "high_value"}
        )
    assert response.status_code == 200, response.text
    assert stubbed_endpoint.await_args.kwargs["segment"] == "high_value"


async def test_endpoint_rejects_unknown_segment(stubbed_endpoint) -> None:
    """Strict ``pattern`` 422s a typo'd/unknown segment instead of silently
    returning the unfiltered list."""
    async with _client() as client:
        response = await client.get(
            "/api/v1/broker-dealers", params={"segment": "high_valu"}
        )
    assert response.status_code == 422
    stubbed_endpoint.assert_not_called()


async def test_endpoint_defaults_segment_to_none(stubbed_endpoint) -> None:
    async with _client() as client:
        response = await client.get("/api/v1/broker-dealers")
    assert response.status_code == 200, response.text
    assert stubbed_endpoint.await_args.kwargs["segment"] is None
