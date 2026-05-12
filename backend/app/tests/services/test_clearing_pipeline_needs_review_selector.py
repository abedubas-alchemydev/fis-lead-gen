"""Tests for ``ClearingPipelineService._select_needs_review_targets``.

The targeted re-run flag (``--only-needs-review``) re-runs the clearing
pipeline against firms whose latest ``clearing_arrangements`` row landed
as ``extraction_status='needs_review'``. The risk surface is the
selector — if the WHERE clause drifts (forgets the
``extraction_status='needs_review'`` predicate, drops the
``MAX(extracted_at)`` scope so an OLD needs_review row re-targets a
firm whose newer extraction is already parsed, or loses the explicit
ORDER BY), a re-run will burn Gemini budget on the wrong universe.

These tests pin the structural contract of the compiled SELECT and the
batch-window slicing behaviour. The fakes mirror
``test_focus_reports_null_health_backfill.py`` (capturing session +
result stand-in) so the test surface stays consistent with the rest of
the pipeline-selector suite. No real Postgres, no httpx — the captured
statement is the contract under test.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy.sql import Select

from app.core.config import settings
from app.models.broker_dealer import BrokerDealer
from app.services.pipeline import ClearingPipelineService


# ───────────────────────── shared fakes ─────────────────────────


class _CapturingResult:
    """Minimal Result stand-in: ``scalars().all()`` returns the staged rows."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> "_CapturingResult":
        return self

    def all(self) -> list[Any]:
        return list(self._rows)


class _CapturingSession:
    """AsyncSession stand-in capturing every Statement passed to
    ``execute``. The captured statement is then compiled to inspect the
    WHERE clause shape."""

    def __init__(self, return_rows: list[Any]) -> None:
        self.captured_statements: list[Any] = []
        self.return_rows = return_rows

    async def execute(self, stmt: Any) -> _CapturingResult:
        self.captured_statements.append(stmt)
        return _CapturingResult(self.return_rows)


def _make_broker_dealer(bd_id: int) -> BrokerDealer:
    bd = BrokerDealer()
    bd.id = bd_id
    bd.name = f"BD {bd_id}"
    bd.cik = f"{bd_id:010d}"
    return bd


def _compile_sql(statement: Any) -> str:
    """Compile a Select to a best-effort SQL string. literal_binds=True
    inlines parameters; the result is lowercased for case-insensitive
    predicate matching against the assertions below."""
    return str(statement.compile(compile_kwargs={"literal_binds": True})).lower()


@pytest.fixture
def service() -> ClearingPipelineService:
    return ClearingPipelineService()


@pytest.fixture(autouse=True)
def _reset_pipeline_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test starts with offset=0 and no limit so slice behaviour
    only reflects what the test itself sets."""
    monkeypatch.setattr(settings, "clearing_pipeline_offset", 0)
    monkeypatch.setattr(settings, "clearing_pipeline_limit", None)


# ─────────────── 1. Selector pins the right WHERE clause ───────────────


class TestSelectNeedsReviewTargets:
    """The compiled SELECT must filter on extraction_status='needs_review'
    AND scope to the latest row per bd_id (MAX(extracted_at) subquery).
    Either drift would re-target firms whose newer extractions already
    moved out of needs_review."""

    @pytest.mark.asyncio
    async def test_emits_a_single_select(
        self, service: ClearingPipelineService
    ) -> None:
        session = _CapturingSession(return_rows=[])

        await service._select_needs_review_targets(session)  # type: ignore[arg-type]

        assert len(session.captured_statements) == 1
        assert isinstance(session.captured_statements[0], Select)

    @pytest.mark.asyncio
    async def test_where_clause_filters_extraction_status_needs_review(
        self, service: ClearingPipelineService
    ) -> None:
        """Without this predicate the selector would target every firm
        with any clearing row — defeating the point of the targeted
        re-run."""
        session = _CapturingSession(return_rows=[])

        await service._select_needs_review_targets(session)  # type: ignore[arg-type]

        sql = _compile_sql(session.captured_statements[0])
        assert "extraction_status = 'needs_review'" in sql, sql

    @pytest.mark.asyncio
    async def test_where_clause_correlates_clearing_arrangements_to_broker_dealers(
        self, service: ClearingPipelineService
    ) -> None:
        """The EXISTS subquery must correlate
        ``clearing_arrangements.bd_id = broker_dealers.id`` so each
        broker_dealers row is gated on its own clearing rows, not on the
        global presence of any needs_review row."""
        session = _CapturingSession(return_rows=[])

        await service._select_needs_review_targets(session)  # type: ignore[arg-type]

        sql = _compile_sql(session.captured_statements[0])
        assert "exists" in sql
        assert "clearing_arrangements" in sql
        assert "clearing_arrangements.bd_id = broker_dealers.id" in sql, sql

    @pytest.mark.asyncio
    async def test_where_clause_scopes_to_latest_extraction_per_firm(
        self, service: ClearingPipelineService
    ) -> None:
        """The MAX(extracted_at) subquery is the load-bearing piece. Without
        it, a firm whose OLD row was needs_review and whose NEWER row is
        parsed would still be re-targeted, wasting Gemini budget and
        potentially overwriting the parsed row with a regression."""
        session = _CapturingSession(return_rows=[])

        await service._select_needs_review_targets(session)  # type: ignore[arg-type]

        sql = _compile_sql(session.captured_statements[0])
        assert "max(clearing_arrangements.extracted_at)" in sql, sql
        # The MAX subquery is compared against the EXISTS row's
        # extracted_at — confirm the equality predicate is wired so the
        # EXISTS only fires for the latest row.
        assert "clearing_arrangements.extracted_at = " in sql, sql

    @pytest.mark.asyncio
    async def test_orders_by_broker_dealer_id_ascending(
        self, service: ClearingPipelineService
    ) -> None:
        """Stable ordering keeps offset/limit chunked re-runs deterministic
        across operator invocations and aligns with the other selector
        helpers."""
        session = _CapturingSession(return_rows=[])

        await service._select_needs_review_targets(session)  # type: ignore[arg-type]

        sql = _compile_sql(session.captured_statements[0])
        assert "order by broker_dealers.id asc" in sql, sql

    @pytest.mark.asyncio
    async def test_returns_rows_in_db_order_unchanged_when_no_window(
        self, service: ClearingPipelineService
    ) -> None:
        """The selector returns the DB's ordering verbatim when no
        offset/limit is set — slicing is the caller's responsibility
        modulated by ``clearing_pipeline_offset/limit``."""
        rows = [_make_broker_dealer(7), _make_broker_dealer(3), _make_broker_dealer(99)]
        session = _CapturingSession(return_rows=rows)

        result = await service._select_needs_review_targets(session)  # type: ignore[arg-type]

        assert [bd.id for bd in result] == [7, 3, 99]


# ─────────────── 2. Batch-window honors offset/limit ───────────────


class TestBatchWindow:
    """``CLEARING_PIPELINE_OFFSET`` and ``CLEARING_PIPELINE_LIMIT`` slice
    the result list. Mirrors the other selectors so an operator can
    chunk a large needs_review re-run without re-engineering knobs."""

    @pytest.mark.asyncio
    async def test_limit_truncates_result(
        self,
        service: ClearingPipelineService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "clearing_pipeline_limit", 2)
        rows = [_make_broker_dealer(i) for i in (1, 2, 3, 4, 5)]
        session = _CapturingSession(return_rows=rows)

        result = await service._select_needs_review_targets(session)  # type: ignore[arg-type]

        assert [bd.id for bd in result] == [1, 2]

    @pytest.mark.asyncio
    async def test_offset_skips_rows_from_head(
        self,
        service: ClearingPipelineService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "clearing_pipeline_offset", 2)
        rows = [_make_broker_dealer(i) for i in (1, 2, 3, 4, 5)]
        session = _CapturingSession(return_rows=rows)

        result = await service._select_needs_review_targets(session)  # type: ignore[arg-type]

        assert [bd.id for bd in result] == [3, 4, 5]

    @pytest.mark.asyncio
    async def test_offset_and_limit_compose(
        self,
        service: ClearingPipelineService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "clearing_pipeline_offset", 1)
        monkeypatch.setattr(settings, "clearing_pipeline_limit", 2)
        rows = [_make_broker_dealer(i) for i in (1, 2, 3, 4, 5)]
        session = _CapturingSession(return_rows=rows)

        result = await service._select_needs_review_targets(session)  # type: ignore[arg-type]

        assert [bd.id for bd in result] == [2, 3]

    @pytest.mark.asyncio
    async def test_no_window_returns_full_result(
        self, service: ClearingPipelineService
    ) -> None:
        rows = [_make_broker_dealer(i) for i in (10, 20, 30)]
        session = _CapturingSession(return_rows=rows)

        result = await service._select_needs_review_targets(session)  # type: ignore[arg-type]

        assert [bd.id for bd in result] == [10, 20, 30]


# ─────────────── 3. run() enforces the mutex on the new flag ───────────────


class TestRunMutex:
    """``run()`` must reject combinations of ``only_needs_review`` with
    either ``only_failed`` or ``only_null_partner``. Two selectors at
    once would leave the universe undefined."""

    @pytest.mark.asyncio
    async def test_combining_only_needs_review_with_only_failed_raises(
        self, service: ClearingPipelineService
    ) -> None:
        with pytest.raises(ValueError, match="mutually exclusive"):
            await service.run(MagicMock(), only_failed=True, only_needs_review=True)

    @pytest.mark.asyncio
    async def test_combining_only_needs_review_with_only_null_partner_raises(
        self, service: ClearingPipelineService
    ) -> None:
        with pytest.raises(ValueError, match="mutually exclusive"):
            await service.run(
                MagicMock(), only_null_partner=True, only_needs_review=True
            )

    @pytest.mark.asyncio
    async def test_combining_all_three_flags_raises(
        self, service: ClearingPipelineService
    ) -> None:
        with pytest.raises(ValueError, match="mutually exclusive"):
            await service.run(
                MagicMock(),
                only_failed=True,
                only_null_partner=True,
                only_needs_review=True,
            )
