"""Tests for the NULL-health backfill entry on FocusReportService.

The backfill run is a one-shot pipeline against the BDs the standard
financial pipeline never reached: rows where ``health_status IS NULL``
and a ``filings_index_url`` is on file. The risk surface is the
selector — if the WHERE clause drifts (forgets the
``filings_index_url IS NOT NULL`` predicate, or filters on
``latest_net_capital`` instead of ``health_status``), a backfill run
will burn Gemini budget on the wrong target set.

These tests pin:

  1. ``_select_null_health_targets`` issues a SELECT whose compiled SQL
     filters on BOTH ``health_status IS NULL`` and
     ``filings_index_url IS NOT NULL`` and orders by ``id`` ASC.
  2. The selector returns whatever rows the DB returns, in the DB's
     order — slicing is the caller's job, not the selector's.
  3. ``load_financial_metrics_for_null_health`` delegates to the shared
     extraction pipeline with ``narrow_delete=True`` so the backfill
     never wipes other firms' fiscal-year history.
  4. ``--limit`` from the CLI overrides
     ``settings.financial_pipeline_limit`` for the run, and
     ``settings.financial_pipeline_offset`` is honored when the CLI
     does not pass one (mirrors the default-path knobs).
  5. The backfill returns the full :class:`FinancialExtractionResult`
     so the script can print the structured ``populated / needs_review
     / errors`` summary without re-querying.

The fakes mirror the shape used by ``test_focus_reports_pipeline_run``
so the test surface stays consistent with the rest of the FocusReport
suite. No real Postgres, no httpx — the selector is exercised against
a session that captures the executed Statement, and the backfill is
exercised against a service whose ``_run_extraction_pipeline`` is
spied on.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.sql import Select

from app.core.config import settings
from app.models.broker_dealer import BrokerDealer
from app.services.focus_reports import (
    FinancialExtractionResult,
    FocusReportService,
)
from app.services.service_models import FinancialMetricRecord


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
    """AsyncSession stand-in that captures every Statement passed to
    ``execute``. Used by the selector tests to compile and inspect the
    WHERE clause. No real HTTP, no real DB; the captured statement is
    the contract under test."""

    def __init__(self, return_rows: list[Any]) -> None:
        self.captured_statements: list[Any] = []
        self.return_rows = return_rows
        self.flush_count = 0
        self.commit_count = 0
        self.added: list[Any] = []

    async def execute(self, stmt: Any) -> _CapturingResult:
        self.captured_statements.append(stmt)
        return _CapturingResult(self.return_rows)

    def add(self, item: Any) -> None:
        self.added.append(item)

    async def flush(self) -> None:
        self.flush_count += 1

    async def commit(self) -> None:
        self.commit_count += 1


def _make_broker_dealer(bd_id: int) -> BrokerDealer:
    bd = BrokerDealer()
    bd.id = bd_id
    bd.name = f"BD {bd_id}"
    bd.cik = f"{bd_id:010d}"
    bd.filings_index_url = f"https://www.sec.gov/cgi-bin/browse-edgar?CIK={bd_id}"
    bd.health_status = None
    return bd


def _make_metric_record(bd_id: int = 1) -> FinancialMetricRecord:
    return FinancialMetricRecord(
        bd_id=bd_id,
        report_date=date(2025, 12, 31),
        net_capital=1_000_000.0,
        excess_net_capital=500_000.0,
        total_assets=5_000_000.0,
        required_min_capital=250_000.0,
        source_filing_url="https://www.sec.gov/Archives/test.pdf",
    )


@pytest.fixture
def service() -> FocusReportService:
    return FocusReportService()


# ─────────────── 1. Selector pins the right WHERE clause ───────────────


class TestSelectNullHealthTargets:
    """The compiled SELECT must filter on both health_status IS NULL and
    filings_index_url IS NOT NULL. Drift on either column would re-route
    the backfill onto the wrong rows and waste Gemini budget."""

    @pytest.mark.asyncio
    async def test_where_clause_filters_health_status_is_null(
        self, service: FocusReportService
    ) -> None:
        session = _CapturingSession(return_rows=[])

        await service._select_null_health_targets(session)  # type: ignore[arg-type]

        assert len(session.captured_statements) == 1
        stmt = session.captured_statements[0]
        assert isinstance(stmt, Select)
        compiled_sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        # SQLAlchemy emits the predicate as ``broker_dealers.health_status IS NULL``.
        assert "health_status IS NULL" in compiled_sql, compiled_sql

    @pytest.mark.asyncio
    async def test_where_clause_requires_filings_index_url_not_null(
        self, service: FocusReportService
    ) -> None:
        """Without this predicate, the backfill would target firms with
        no reachable X-17A-5 — every Gemini call would short-circuit at
        the PDF download stage."""
        session = _CapturingSession(return_rows=[])

        await service._select_null_health_targets(session)  # type: ignore[arg-type]

        stmt = session.captured_statements[0]
        compiled_sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "filings_index_url IS NOT NULL" in compiled_sql, compiled_sql

    @pytest.mark.asyncio
    async def test_orders_by_id_ascending(
        self, service: FocusReportService
    ) -> None:
        """Stable ordering keeps offset/limit canary runs deterministic
        across operator invocations and aligns with the default
        pipeline's secondary ordering."""
        session = _CapturingSession(return_rows=[])

        await service._select_null_health_targets(session)  # type: ignore[arg-type]

        stmt = session.captured_statements[0]
        compiled_sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "ORDER BY broker_dealers.id ASC" in compiled_sql, compiled_sql

    @pytest.mark.asyncio
    async def test_returns_rows_in_db_order_unchanged(
        self, service: FocusReportService
    ) -> None:
        """The selector returns the DB's ordering verbatim — slicing is
        the caller's responsibility (the backfill entrypoint applies
        offset/limit; the dry-run path slices in the script). Pinning
        this contract guards against accidental in-Python re-sorting
        that would diverge from a real Postgres ORDER BY."""
        rows = [_make_broker_dealer(7), _make_broker_dealer(3), _make_broker_dealer(99)]
        session = _CapturingSession(return_rows=rows)

        result = await service._select_null_health_targets(session)  # type: ignore[arg-type]

        assert [bd.id for bd in result] == [7, 3, 99]


# ─────────────── 2. Backfill entry delegates with narrow_delete=True ───────────────


class TestLoadFinancialMetricsForNullHealth:
    """The backfill entry must (a) feed the selector universe through
    ``_apply_batch_window`` with the right offset/limit, and (b)
    delegate to ``_run_extraction_pipeline`` with narrow_delete=True so
    other firms' rows survive the run."""

    @pytest.mark.asyncio
    async def test_delegates_to_pipeline_with_narrow_delete_true(
        self,
        service: FocusReportService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A backfill is by definition a subset run; the bare ``DELETE
        FROM financial_metrics`` fallback would wipe every firm's
        fiscal-year history. Pin narrow_delete=True so a future refactor
        can't silently flip the flag."""
        rows = [_make_broker_dealer(i) for i in (1, 2, 3)]

        async def _fake_select(_db: Any) -> list[BrokerDealer]:
            return rows

        captured: dict[str, Any] = {}

        async def _fake_run_extraction_pipeline(
            _db: Any,
            target: list[BrokerDealer],
            *,
            trigger_source: str,
            narrow_delete: bool,
        ) -> tuple[FinancialExtractionResult, int]:
            captured["target_ids"] = [bd.id for bd in target]
            captured["trigger_source"] = trigger_source
            captured["narrow_delete"] = narrow_delete
            return FinancialExtractionResult(records=[], target_count=len(target)), 999

        monkeypatch.setattr(service, "_select_null_health_targets", _fake_select)
        monkeypatch.setattr(service, "_run_extraction_pipeline", _fake_run_extraction_pipeline)
        monkeypatch.setattr(settings, "financial_pipeline_offset", 0)
        monkeypatch.setattr(settings, "financial_pipeline_limit", None)

        result = await service.load_financial_metrics_for_null_health(MagicMock())

        assert captured["narrow_delete"] is True
        assert captured["target_ids"] == [1, 2, 3]
        assert captured["trigger_source"] == "manual_backfill"
        assert isinstance(result, FinancialExtractionResult)
        assert result.target_count == 3

    @pytest.mark.asyncio
    async def test_cli_limit_overrides_settings_limit(
        self,
        service: FocusReportService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``--limit N`` from the CLI takes precedence over
        FINANCIAL_PIPELINE_LIMIT for the run, so a canary run isn't
        forced to also unset the env var."""
        rows = [_make_broker_dealer(i) for i in range(10)]

        async def _fake_select(_db: Any) -> list[BrokerDealer]:
            return rows

        captured_targets: dict[str, list[int]] = {}

        async def _fake_run_extraction_pipeline(
            _db: Any,
            target: list[BrokerDealer],
            *,
            trigger_source: str,
            narrow_delete: bool,
        ) -> tuple[FinancialExtractionResult, int]:
            captured_targets["ids"] = [bd.id for bd in target]
            return FinancialExtractionResult(records=[], target_count=len(target)), 1

        monkeypatch.setattr(service, "_select_null_health_targets", _fake_select)
        monkeypatch.setattr(service, "_run_extraction_pipeline", _fake_run_extraction_pipeline)
        monkeypatch.setattr(settings, "financial_pipeline_offset", 0)
        monkeypatch.setattr(settings, "financial_pipeline_limit", 100)

        await service.load_financial_metrics_for_null_health(MagicMock(), limit=2)

        assert captured_targets["ids"] == [rows[0].id, rows[1].id]

    @pytest.mark.asyncio
    async def test_settings_offset_is_honored_when_cli_limit_unset(
        self,
        service: FocusReportService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """FINANCIAL_PIPELINE_OFFSET still gates the slice when the CLI
        does not override the limit — the chunked-backfill knob behaves
        the same as on the default path."""
        rows = [_make_broker_dealer(i) for i in range(5)]

        async def _fake_select(_db: Any) -> list[BrokerDealer]:
            return rows

        captured_targets: dict[str, list[int]] = {}

        async def _fake_run_extraction_pipeline(
            _db: Any,
            target: list[BrokerDealer],
            *,
            trigger_source: str,
            narrow_delete: bool,
        ) -> tuple[FinancialExtractionResult, int]:
            captured_targets["ids"] = [bd.id for bd in target]
            return FinancialExtractionResult(records=[], target_count=len(target)), 1

        monkeypatch.setattr(service, "_select_null_health_targets", _fake_select)
        monkeypatch.setattr(service, "_run_extraction_pipeline", _fake_run_extraction_pipeline)
        monkeypatch.setattr(settings, "financial_pipeline_offset", 2)
        monkeypatch.setattr(settings, "financial_pipeline_limit", None)

        await service.load_financial_metrics_for_null_health(MagicMock())

        # offset=2 skips ids 0,1 → expect ids 2,3,4 in order.
        assert captured_targets["ids"] == [rows[2].id, rows[3].id, rows[4].id]

    @pytest.mark.asyncio
    async def test_returns_full_extraction_result_for_summary_line(
        self,
        service: FocusReportService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The script's ``Backfill complete: target_null_health=...,
        attempted=..., populated=..., needs_review=..., errors=...``
        summary needs every counter from the result. Returning the full
        ``FinancialExtractionResult`` (vs. just an int) is the contract
        the CLI relies on — pin it so a future refactor doesn't silently
        narrow the return type."""
        rows = [_make_broker_dealer(i) for i in (10, 20)]

        async def _fake_select(_db: Any) -> list[BrokerDealer]:
            return rows

        async def _fake_run_extraction_pipeline(
            _db: Any,
            target: list[BrokerDealer],
            *,
            trigger_source: str,
            narrow_delete: bool,
        ) -> tuple[FinancialExtractionResult, int]:
            return (
                FinancialExtractionResult(
                    records=[_make_metric_record(bd_id=10)],
                    target_count=2,
                    skipped_no_url=0,
                    skipped_no_pdf=1,
                    skipped_extraction_error=0,
                    skipped_low_confidence=0,
                    needs_review_count=0,
                ),
                42,
            )

        monkeypatch.setattr(service, "_select_null_health_targets", _fake_select)
        monkeypatch.setattr(service, "_run_extraction_pipeline", _fake_run_extraction_pipeline)

        result = await service.load_financial_metrics_for_null_health(MagicMock())

        assert isinstance(result, FinancialExtractionResult)
        assert result.target_count == 2
        assert len(result.records) == 1
        assert result.skipped_no_pdf == 1
        # CLI summary derives ``errors`` from the sum of skip buckets;
        # locking the field surface guards that derivation.
        assert hasattr(result, "skipped_no_url")
        assert hasattr(result, "skipped_no_pdf")
        assert hasattr(result, "skipped_extraction_error")
        assert hasattr(result, "skipped_low_confidence")
        assert hasattr(result, "needs_review_count")


# ─────────────── 3. Empty universe is a clean no-op ───────────────


class TestEmptyUniverse:
    @pytest.mark.asyncio
    async def test_no_null_health_rows_runs_pipeline_with_zero_targets(
        self,
        service: FocusReportService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When every BD already has a health_status, the backfill must
        still invoke the pipeline (so the operator gets a clean
        pipeline_run audit row stamped 'completed') with target_count=0
        rather than short-circuiting silently."""
        ran = AsyncMock(
            return_value=(FinancialExtractionResult(records=[], target_count=0), 7)
        )

        async def _fake_select(_db: Any) -> list[BrokerDealer]:
            return []

        monkeypatch.setattr(service, "_select_null_health_targets", _fake_select)
        monkeypatch.setattr(service, "_run_extraction_pipeline", ran)

        result = await service.load_financial_metrics_for_null_health(MagicMock())

        ran.assert_awaited_once()
        kwargs = ran.await_args.kwargs
        assert kwargs["narrow_delete"] is True
        assert ran.await_args.args[1] == []  # target_broker_dealers arg
        assert result.target_count == 0
