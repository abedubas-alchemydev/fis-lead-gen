"""Unit tests for the watched-firm predicate used by the filing-monitor
auto re-extraction hook.

The predicate is a union: favorites OR ``lead_priority='hot'``. These
tests stub the SQLAlchemy session to exercise the union semantics
without spinning up Postgres — the integration test in
``test_filing_monitor_auto_extract.py`` covers the SQL end of the path.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from app.services.watched_firms import get_watched_bd_ids


class _StubScalars:
    def __init__(self, values: list[int]) -> None:
        self._values = values

    def all(self) -> list[int]:
        return self._values


class _StubResult:
    def __init__(self, values: list[int]) -> None:
        self._values = values

    def scalars(self) -> _StubScalars:
        return _StubScalars(self._values)


class _StubSession:
    """Returns pre-staged scalars-result lists in call order.

    The watched-firms predicate runs two queries (favorites, then
    hot-priority). Each `execute` returns the next staged result.
    """

    def __init__(self, results: list[list[int]]) -> None:
        self._results = list(results)
        self.executed: list[Any] = []

    async def execute(self, statement: Any) -> _StubResult:
        self.executed.append(statement)
        return _StubResult(self._results.pop(0))


@pytest.mark.asyncio
async def test_returns_empty_set_when_nothing_watched() -> None:
    session = _StubSession(results=[[], []])
    assert await get_watched_bd_ids(session) == set()
    assert len(session.executed) == 2


@pytest.mark.asyncio
async def test_favorites_only() -> None:
    session = _StubSession(results=[[1, 2, 3], []])
    assert await get_watched_bd_ids(session) == {1, 2, 3}


@pytest.mark.asyncio
async def test_hot_priority_only() -> None:
    session = _StubSession(results=[[], [10, 20]])
    assert await get_watched_bd_ids(session) == {10, 20}


@pytest.mark.asyncio
async def test_union_dedupes_overlap() -> None:
    """A firm both favorited and hot-priority must appear in the set once.

    Sanity check on the union semantics — the consumer (filing monitor's
    auto-extract intersection) uses ``in`` against the returned set, so
    duplicates would be invisible but it's still worth asserting we
    return a real set type and not a list with duplicates."""
    session = _StubSession(results=[[1, 2, 3], [3, 4, 5]])
    result = await get_watched_bd_ids(session)
    assert result == {1, 2, 3, 4, 5}
    assert isinstance(result, set)
