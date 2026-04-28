"""Unit tests for ``BrokerDealerRepository.list_types_of_business``.

Covers the master-list types-of-business filter introduced to drop free-text
"other" values that polluted the FE dropdown with ~3,300 one-off entries.

Two layers of coverage:

1. SQL-shape assertions: compile the statement issued to ``db.execute`` and
   confirm the trim, non-empty, ``HAVING COUNT(*) >= 2``, and sort predicates
   are present. The compile pattern mirrors
   ``test_clearing_pipeline_target_ordering.py``.
2. Result-shape assertions: feed staged ``(type, count)`` rows back through
   the function and confirm the post-aggregation Python layer preserves
   order and casts counts to ``int``.

JSONB array unnesting is Postgres-specific so we don't try to run real SQL
here — Phase 2's prod smoke check is the live integration signal.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.services.broker_dealers import (
    BrokerDealerRepository,
    TYPES_OF_BUSINESS_MIN_COUNT,
)


class _StagedSession:
    """AsyncSession mock that captures the executed statement and returns a
    pre-staged row payload as a result.all() iterable."""

    def __init__(self, rows: list[object]) -> None:
        self._rows = rows
        self.captured_statement: object | None = None

    async def execute(self, statement: object) -> object:
        self.captured_statement = statement
        result = MagicMock()
        result.all.return_value = self._rows
        return result


def _row(type_value: str, count_value: int) -> object:
    """Lightweight stand-in for a SQLAlchemy Row with .type and .count."""
    row = MagicMock()
    row.type = type_value
    row.count = count_value
    return row


def _compile_sql(statement: object) -> str:
    compiled = statement.compile(compile_kwargs={"literal_binds": True})
    return str(compiled).lower()


@pytest.fixture
def repository() -> BrokerDealerRepository:
    return BrokerDealerRepository()


@pytest.mark.asyncio
async def test_query_filters_null_and_empty_types(repository: BrokerDealerRepository) -> None:
    """The compiled SQL trims values and excludes null + zero-length results."""
    session = _StagedSession(rows=[])

    await repository.list_types_of_business(session)

    sql = _compile_sql(session.captured_statement)
    assert "trim(" in sql, "expected trim() to normalize the unnested type"
    assert "is not null" in sql, "expected an IS NOT NULL guard on the trimmed type"
    assert "length(trim(" in sql, "expected a length(trim(...)) > 0 guard to drop empty strings"
    assert "> 0" in sql, "expected length comparison to exclude empty strings"


@pytest.mark.asyncio
async def test_query_excludes_one_off_types_via_having(repository: BrokerDealerRepository) -> None:
    """The compiled SQL uses ``HAVING COUNT(*) >= TYPES_OF_BUSINESS_MIN_COUNT``."""
    assert TYPES_OF_BUSINESS_MIN_COUNT == 2, "filter threshold contract"
    session = _StagedSession(rows=[])

    await repository.list_types_of_business(session)

    sql = _compile_sql(session.captured_statement)
    assert "having count(*) >= 2" in sql, (
        "expected HAVING COUNT(*) >= 2 to drop one-off 'other' values; got SQL:\n" + sql
    )


@pytest.mark.asyncio
async def test_query_sorts_by_count_desc_then_type_asc(repository: BrokerDealerRepository) -> None:
    """The compiled SQL preserves the original count desc / type asc sort."""
    session = _StagedSession(rows=[])

    await repository.list_types_of_business(session)

    sql = _compile_sql(session.captured_statement)
    assert "order by" in sql
    order_clause = sql.split("order by", 1)[1]
    assert "count(*) desc" in order_clause, "primary sort should be count desc"
    type_position = order_clause.find("trim(")
    count_position = order_clause.find("count(*) desc")
    assert count_position != -1 and type_position != -1
    assert count_position < type_position, (
        "count desc must appear before the trimmed-type asc tiebreaker"
    )


@pytest.mark.asyncio
async def test_result_shape_preserves_order_and_casts_count(
    repository: BrokerDealerRepository,
) -> None:
    """The Python layer returns dicts in the order Postgres provided and
    converts the count column to a real ``int``."""
    session = _StagedSession(
        rows=[
            _row("FINRA member", 412),
            _row("broker or dealer", 305),
            _row("investment adviser", 2),
        ]
    )

    result = await repository.list_types_of_business(session)

    assert result == [
        {"type": "FINRA member", "count": 412},
        {"type": "broker or dealer", "count": 305},
        {"type": "investment adviser", "count": 2},
    ]
    for row in result:
        assert isinstance(row["count"], int)
