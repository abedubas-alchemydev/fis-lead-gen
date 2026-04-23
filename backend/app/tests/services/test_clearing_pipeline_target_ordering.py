"""Fix E-clearing tests - default-mode target ordering.

Mirrors the financial pipeline's Fix E. Default-mode candidate selection must
place firms with a filings_index_url but no clearing_arrangement row first, so
small CLEARING_PIPELINE_LIMIT batches reach firms the pipeline has never
attempted instead of re-running on the same saturated top-100 set.

These are unit tests. They mock AsyncSession.execute to serve staged per-
bucket results by call order (first call -> bucket 1, second call -> bucket 2)
and inspect the compiled SQL to confirm each query's WHERE predicate matches
the documented shape. An integration-marked smoke test (out of scope here) is
run against staging Neon in the PR's smoke-test section.
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

from app.core.config import settings
from app.services.pipeline import ClearingPipelineService


@dataclass
class _FakeBD:
    """Lightweight stand-in for BrokerDealer rows in mocked result sets."""
    id: int
    filings_index_url: str | None
    name: str = ""


class _StagedSession:
    """AsyncSession mock that returns staged result sets by call order.

    _select_default_targets issues exactly two queries: bds_without_clearing
    first, then bds_with_clearing. Each call is captured so tests can inspect
    the compiled SQL and confirm the intended WHERE predicate is present.
    """

    def __init__(self, responses: list[list[_FakeBD]]) -> None:
        self._responses = list(responses)
        self.captured_statements: list[object] = []

    async def execute(self, statement: object) -> object:
        self.captured_statements.append(statement)
        payload = self._responses.pop(0) if self._responses else []
        result = MagicMock()
        result.scalars.return_value.all.return_value = payload
        return result


def _compile_sql(statement: object) -> str:
    """Compile a SQLAlchemy Core select to a best-effort SQL string for
    structural assertions. literal_binds=True inlines parameters; the result
    is lowercased for case-insensitive predicate matching."""
    try:
        compiled = statement.compile(compile_kwargs={"literal_binds": True})
    except Exception:
        compiled = statement.compile()
    return str(compiled).lower()


@pytest.fixture
def service() -> ClearingPipelineService:
    return ClearingPipelineService()


@pytest.fixture(autouse=True)
def _reset_pipeline_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test starts with offset=0 and no limit so slice behaviour only
    reflects what the test itself sets."""
    monkeypatch.setattr(settings, "clearing_pipeline_offset", 0)
    monkeypatch.setattr(settings, "clearing_pipeline_limit", None)


# ─────────────────── Ordering ───────────────────


class TestOrdering:
    """Default mode puts never-attempted firms first, refresh tail after."""

    @pytest.mark.asyncio
    async def test_never_attempted_firms_are_returned_first(
        self, service: ClearingPipelineService
    ) -> None:
        bds_without_clearing = [
            _FakeBD(id=10, filings_index_url="https://example.com/a"),
            _FakeBD(id=20, filings_index_url="https://example.com/b"),
            _FakeBD(id=30, filings_index_url="https://example.com/c"),
        ]
        bds_with_clearing = [
            _FakeBD(id=1, filings_index_url="https://example.com/old-1"),
            _FakeBD(id=2, filings_index_url="https://example.com/old-2"),
            _FakeBD(id=3, filings_index_url="https://example.com/old-3"),
            _FakeBD(id=4, filings_index_url="https://example.com/old-4"),
            _FakeBD(id=5, filings_index_url="https://example.com/old-5"),
        ]
        db = _StagedSession([bds_without_clearing, bds_with_clearing])

        targets = await service._select_default_targets(db)  # type: ignore[arg-type]

        assert [bd.id for bd in targets] == [10, 20, 30, 1, 2, 3, 4, 5], (
            "never-attempted bucket must precede refresh-tail bucket"
        )

    @pytest.mark.asyncio
    async def test_within_bucket_order_is_ascending_bd_id(
        self, service: ClearingPipelineService
    ) -> None:
        bds_without_clearing = [
            _FakeBD(id=5, filings_index_url="u"),
            _FakeBD(id=9, filings_index_url="u"),
            _FakeBD(id=40, filings_index_url="u"),
        ]
        bds_with_clearing: list[_FakeBD] = []
        db = _StagedSession([bds_without_clearing, bds_with_clearing])

        targets = await service._select_default_targets(db)  # type: ignore[arg-type]

        assert [bd.id for bd in targets] == [5, 9, 40]

    @pytest.mark.asyncio
    async def test_empty_buckets_produce_empty_target_list(
        self, service: ClearingPipelineService
    ) -> None:
        db = _StagedSession([[], []])

        targets = await service._select_default_targets(db)  # type: ignore[arg-type]

        assert targets == []


# ─────────────────── Batch-window ───────────────────


class TestBatchWindow:
    """CLEARING_PIPELINE_OFFSET and CLEARING_PIPELINE_LIMIT slice the
    concatenated list, not each bucket. A limit=3 against a 3-firm never-
    attempted set must return exactly that set (none of the refresh tail)."""

    @pytest.mark.asyncio
    async def test_limit_returns_only_never_attempted_slice(
        self,
        service: ClearingPipelineService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "clearing_pipeline_limit", 3)
        monkeypatch.setattr(settings, "clearing_pipeline_offset", 0)

        bds_without_clearing = [
            _FakeBD(id=10, filings_index_url="u"),
            _FakeBD(id=20, filings_index_url="u"),
            _FakeBD(id=30, filings_index_url="u"),
        ]
        bds_with_clearing = [
            _FakeBD(id=1, filings_index_url="u"),
            _FakeBD(id=2, filings_index_url="u"),
            _FakeBD(id=3, filings_index_url="u"),
            _FakeBD(id=4, filings_index_url="u"),
            _FakeBD(id=5, filings_index_url="u"),
        ]
        db = _StagedSession([bds_without_clearing, bds_with_clearing])

        targets = await service._select_default_targets(db)  # type: ignore[arg-type]

        assert [bd.id for bd in targets] == [10, 20, 30]

    @pytest.mark.asyncio
    async def test_offset_3_moves_window_into_refresh_tail(
        self,
        service: ClearingPipelineService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "clearing_pipeline_limit", 3)
        monkeypatch.setattr(settings, "clearing_pipeline_offset", 3)

        bds_without_clearing = [
            _FakeBD(id=10, filings_index_url="u"),
            _FakeBD(id=20, filings_index_url="u"),
            _FakeBD(id=30, filings_index_url="u"),
        ]
        bds_with_clearing = [
            _FakeBD(id=1, filings_index_url="u"),
            _FakeBD(id=2, filings_index_url="u"),
            _FakeBD(id=3, filings_index_url="u"),
            _FakeBD(id=4, filings_index_url="u"),
            _FakeBD(id=5, filings_index_url="u"),
        ]
        db = _StagedSession([bds_without_clearing, bds_with_clearing])

        targets = await service._select_default_targets(db)  # type: ignore[arg-type]

        assert [bd.id for bd in targets] == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_no_limit_returns_full_concatenation(
        self, service: ClearingPipelineService
    ) -> None:
        bds_without_clearing = [_FakeBD(id=10, filings_index_url="u")]
        bds_with_clearing = [
            _FakeBD(id=1, filings_index_url="u"),
            _FakeBD(id=2, filings_index_url="u"),
        ]
        db = _StagedSession([bds_without_clearing, bds_with_clearing])

        targets = await service._select_default_targets(db)  # type: ignore[arg-type]

        assert [bd.id for bd in targets] == [10, 1, 2]


# ─────────────────── Predicate shape ───────────────────


class TestQueryShape:
    """Captures the compiled SQL of each issued select and asserts the
    predicate shape. This is the structural guardrail against a future
    refactor that might silently drop the URL filter or flip the
    EXISTS / NOT EXISTS pair."""

    @pytest.mark.asyncio
    async def test_first_query_filters_urls_and_negates_clearing_exists(
        self, service: ClearingPipelineService
    ) -> None:
        db = _StagedSession([[], []])

        await service._select_default_targets(db)  # type: ignore[arg-type]

        assert len(db.captured_statements) == 2
        first_sql = _compile_sql(db.captured_statements[0])
        assert "filings_index_url is not null" in first_sql, (
            "bucket 1 must require filings_index_url to be set"
        )
        assert "not (exists" in first_sql or "not exists" in first_sql, (
            "bucket 1 must exclude firms with any clearing_arrangement row"
        )
        assert "clearing_arrangements" in first_sql

    @pytest.mark.asyncio
    async def test_second_query_is_refresh_tail_of_firms_with_clearing_rows(
        self, service: ClearingPipelineService
    ) -> None:
        db = _StagedSession([[], []])

        await service._select_default_targets(db)  # type: ignore[arg-type]

        second_sql = _compile_sql(db.captured_statements[1])
        assert "exists" in second_sql
        assert "clearing_arrangements" in second_sql
        assert "not (exists" not in second_sql and "not exists" not in second_sql, (
            "bucket 2 is the refresh tail - must NOT negate the EXISTS subquery"
        )


# ─────────────────── Stability ───────────────────


class TestStability:
    """Two sequential calls against the same DB state return targets in the
    same order. Ordering semantics cannot depend on non-deterministic row
    order from the result set."""

    @pytest.mark.asyncio
    async def test_two_calls_return_identical_ordering(
        self, service: ClearingPipelineService
    ) -> None:
        bds_without_clearing = [
            _FakeBD(id=5, filings_index_url="u"),
            _FakeBD(id=12, filings_index_url="u"),
            _FakeBD(id=17, filings_index_url="u"),
        ]
        bds_with_clearing = [
            _FakeBD(id=1, filings_index_url="u"),
            _FakeBD(id=2, filings_index_url="u"),
        ]

        db_first = _StagedSession([list(bds_without_clearing), list(bds_with_clearing)])
        db_second = _StagedSession([list(bds_without_clearing), list(bds_with_clearing)])

        first = await service._select_default_targets(db_first)  # type: ignore[arg-type]
        second = await service._select_default_targets(db_second)  # type: ignore[arg-type]

        assert [bd.id for bd in first] == [bd.id for bd in second]

    @pytest.mark.asyncio
    async def test_both_queries_carry_explicit_order_by_clause(
        self, service: ClearingPipelineService
    ) -> None:
        """The stable call-to-call ordering above depends on each query
        having an explicit ORDER BY. A refactor that drops it would leave
        result order at the database's discretion."""
        db = _StagedSession([[], []])

        await service._select_default_targets(db)  # type: ignore[arg-type]

        for statement in db.captured_statements:
            sql = _compile_sql(statement)
            assert "order by" in sql, f"missing ORDER BY in query: {sql}"
            assert "broker_dealers.id" in sql


# ─────────────────── only_failed unchanged ───────────────────


class TestOnlyFailedPathUntouched:
    """The only_failed=True branch is orthogonal to Fix E. Confirm the new
    helper is not invoked on that code path - otherwise the retry semantics
    would silently change shape."""

    def test_select_default_targets_is_only_called_from_default_branch(self) -> None:
        """Source-level check. Grep substitute: the method body for run()
        must not reference _select_default_targets inside the only_failed
        branch. If a future refactor moves the call, this test fails loudly
        so the retry-path implications get revisited."""
        import inspect

        source = inspect.getsource(ClearingPipelineService.run)
        default_branch_idx = source.find("else:")
        call_idx = source.find("_select_default_targets")
        assert default_branch_idx > 0
        assert call_idx > default_branch_idx, (
            "_select_default_targets must only be called from the default "
            "(non only_failed) branch"
        )
