"""Unit tests for the ``search`` parameter on
``BrokerDealerRepository.list_broker_dealers``.

The endpoint surfaces a single free-text search box at ``/master-list`` that
must match across multiple firm-identity columns:

* ``broker_dealers.name`` (legal name)
* ``broker_dealers.cik``
* ``broker_dealers.crd_number``
* ``broker_dealers.sec_file_number``
* ``broker_dealers.dba_names`` (JSONB list of FINRA "Doing Business As"
  trade names — added to search so a query like "303Capital Markets"
  finds firms whose legal name reads differently, e.g.
  "303 ALTERNATIVES, LLC")

These tests assert the compiled WHERE clause contains an ILIKE branch for
each column, and that the JSONB ``dba_names`` branch is wrapped in the
``jsonb_typeof = 'array'`` guard the repository uses to keep
``jsonb_array_elements_text`` from crashing on JSONB scalar/object rows.

Mirrors the ``_StagedSession`` SQL-shape pattern from
``test_broker_dealers_range_filters.py`` — no live DB required.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.services.broker_dealers import BrokerDealerRepository


class _StagedSession:
    """AsyncSession mock that captures every executed statement and returns
    pre-staged result objects in call order. See
    ``test_broker_dealers_range_filters._StagedSession`` for the contract.
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
    assert len(session.executed_statements) >= 2
    data_stmt = session.executed_statements[1]
    sql = _compile_sql(data_stmt)
    if "where" not in sql:
        return ""
    return sql.split("where", 1)[1]


def _default_kwargs() -> dict[str, object]:
    """Baseline kwargs that exercise no filters. ``list_mode='all'`` keeps
    the WHERE focused on the search predicate under test (``primary`` would
    add ``is_deficient = false`` and clutter the assertions)."""
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


# SQLAlchemy compiles ``Column.ilike(x)`` as ``lower(col) like lower(x)``
# under the bare-Postgres dialect, so the substring assertions below match
# that shape rather than the literal ``ILIKE`` keyword.

NAME_ILIKE_CAPITAL = "lower(broker_dealers.name) like lower('%capital%')"
CIK_ILIKE_CAPITAL = "lower(broker_dealers.cik) like lower('%capital%')"
CRD_ILIKE_CAPITAL = (
    "lower(cast(broker_dealers.crd_number as varchar)) like lower('%capital%')"
)
SEC_ILIKE_CAPITAL = (
    "lower(cast(broker_dealers.sec_file_number as varchar)) like lower('%capital%')"
)
DBA_EXISTS_CAPITAL_FRAGMENTS = (
    "exists (select 1",
    "from jsonb_array_elements_text(broker_dealers.dba_names)",
    "jsonb_typeof(broker_dealers.dba_names) = 'array'",
    "lower(anon_1.value) like lower('%capital%')",
)


@pytest.mark.asyncio
async def test_search_emits_ilike_for_name_cik_crd_sec_branches(
    repository: BrokerDealerRepository,
) -> None:
    """Regression guard for the four pre-existing search columns. A single
    search string fans out into four ILIKE branches OR-joined together."""
    session = _StagedSession()

    await repository.list_broker_dealers(
        session, **{**_default_kwargs(), "search": "Capital"}
    )

    where = _captured_where_sql(session)
    assert NAME_ILIKE_CAPITAL in where
    assert CIK_ILIKE_CAPITAL in where
    assert CRD_ILIKE_CAPITAL in where
    assert SEC_ILIKE_CAPITAL in where


@pytest.mark.asyncio
async def test_search_includes_dba_names_exists_branch(
    repository: BrokerDealerRepository,
) -> None:
    """The new dba_names branch wraps an ILIKE in an EXISTS over
    ``jsonb_array_elements_text``, with a ``jsonb_typeof = 'array'`` guard
    so JSONB scalar/object rows can't crash the set-returning function."""
    session = _StagedSession()

    await repository.list_broker_dealers(
        session, **{**_default_kwargs(), "search": "Capital"}
    )

    where = _captured_where_sql(session)
    for fragment in DBA_EXISTS_CAPITAL_FRAGMENTS:
        assert fragment in where, f"dba EXISTS missing fragment: {fragment}"


@pytest.mark.asyncio
async def test_search_branches_are_or_joined(
    repository: BrokerDealerRepository,
) -> None:
    """All five search branches must sit inside a single OR group — a row
    that matches on dba_names alone (legal name doesn't contain the
    substring) still has to come through. Verified by checking that the
    name predicate and the EXISTS branch live inside one set of parens with
    only ``or`` connectors between them, with no ``and`` separator splitting
    them into independent AND-joined predicates."""
    session = _StagedSession()

    await repository.list_broker_dealers(
        session, **{**_default_kwargs(), "search": "Pershing"}
    )

    where = _captured_where_sql(session)
    # The five ILIKE branches and the EXISTS share four ``or`` connectors,
    # all sitting before the ORDER BY (which the WHERE-only slice elides).
    assert where.count(" or ") >= 4
    name_pred = "lower(broker_dealers.name) like lower('%pershing%')"
    exists_pred = "exists (select 1"
    name_idx = where.find(name_pred)
    exists_idx = where.find(exists_pred)
    assert name_idx != -1 and exists_idx != -1
    between = where[min(name_idx, exists_idx) : max(name_idx, exists_idx)]
    assert " and " not in between, (
        "name ILIKE and dba EXISTS must be OR-joined inside a single group, "
        f"but found AND between them: {between!r}"
    )


@pytest.mark.asyncio
async def test_search_omitted_emits_no_search_predicate(
    repository: BrokerDealerRepository,
) -> None:
    """Regression: ``search=None`` (or empty) leaves the WHERE clause
    untouched — the JSONB EXISTS branch must not appear unconditionally."""
    session = _StagedSession()

    await repository.list_broker_dealers(session, **_default_kwargs())

    where = _captured_where_sql(session)
    assert "like lower(" not in where
    assert "jsonb_array_elements_text(broker_dealers.dba_names)" not in where


@pytest.mark.asyncio
async def test_search_combines_with_state_filter(
    repository: BrokerDealerRepository,
) -> None:
    """Search AND-joins with other filters: a NY firm whose DBA matches
    ``Capital`` must satisfy both predicates simultaneously."""
    session = _StagedSession()

    await repository.list_broker_dealers(
        session,
        **{**_default_kwargs(), "search": "Capital", "states": ["NY"]},
    )

    where = _captured_where_sql(session)
    assert "broker_dealers.state in ('ny')" in where
    assert NAME_ILIKE_CAPITAL in where
    assert "jsonb_array_elements_text(broker_dealers.dba_names)" in where


@pytest.mark.asyncio
async def test_search_count_and_data_share_same_predicate(
    repository: BrokerDealerRepository,
) -> None:
    """count_stmt and data_stmt must apply identical search predicates so
    the paginated total agrees with the page items."""
    session = _StagedSession()

    await repository.list_broker_dealers(
        session, **{**_default_kwargs(), "search": "Capital"}
    )

    count_sql = _compile_sql(session.executed_statements[0])
    data_sql = _compile_sql(session.executed_statements[1])
    for fragment in (
        NAME_ILIKE_CAPITAL,
        "jsonb_array_elements_text(broker_dealers.dba_names)",
        "jsonb_typeof(broker_dealers.dba_names) = 'array'",
    ):
        assert fragment in count_sql, f"count_stmt missing: {fragment}"
        assert fragment in data_sql, f"data_stmt missing: {fragment}"
