"""Tests for Fix D — FocusReportService pipeline_run writes.

Covers start-of-run insert shape, success finalization, failure finalization,
and the known mid-run-interruption (KeyboardInterrupt) behavior. Mirrors the
clearing pipeline's pipeline_run pattern on the financial side so every
financial extraction run leaves an audit trail in pipeline_runs.
"""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import settings
from app.models.pipeline_run import PipelineRun
from app.services.focus_reports import (
    FINANCIAL_PIPELINE_NAME,
    FinancialExtractionResult,
    FocusReportService,
)
from app.services.service_models import FinancialMetricRecord


@pytest.fixture
def service() -> FocusReportService:
    return FocusReportService()


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


class _FakeDbSession:
    """Mimics the handful of AsyncSession calls used by load_financial_metrics.

    Tracks inserted PipelineRun instances, assigns a synthetic primary key on
    flush (mirroring what psycopg would do on INSERT … RETURNING), and exposes
    call-order hooks so tests can assert the commit-before-extraction contract.
    """

    def __init__(self) -> None:
        self.added: list[object] = []
        self.flush_calls = 0
        self.commit_calls = 0
        self._next_id = 100

    async def execute(self, _stmt: object) -> object:
        fake = MagicMock()
        fake.scalars.return_value.all.return_value = []
        return fake

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        self.flush_calls += 1
        for obj in self.added:
            if isinstance(obj, PipelineRun) and obj.id is None:
                obj.id = self._next_id
                self._next_id += 1

    async def commit(self) -> None:
        self.commit_calls += 1


class _FakeWriteSession:
    """Stand-in for the SessionLocal() write session used for financial_metric
    writes and pipeline_run finalization. Captures the pipeline_run object so
    get() returns the same instance across the session lifecycle."""

    def __init__(self, pipeline_run: PipelineRun) -> None:
        self._pipeline_run = pipeline_run
        self.execute = AsyncMock(return_value=MagicMock(scalars=lambda: MagicMock(all=lambda: [])))
        self.flush = AsyncMock()
        self.commit = AsyncMock()
        self.get = AsyncMock(return_value=pipeline_run)
        self.add_all = MagicMock()

    async def __aenter__(self) -> "_FakeWriteSession":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None


def _patch_session_local(monkeypatch: pytest.MonkeyPatch, db: _FakeDbSession) -> None:
    """Route SessionLocal() to a fresh FakeWriteSession that resolves
    PipelineRun.get() to whichever pipeline_run row the main session inserted."""

    def _factory() -> _FakeWriteSession:
        run = next((obj for obj in db.added if isinstance(obj, PipelineRun)), PipelineRun())
        return _FakeWriteSession(run)

    monkeypatch.setattr("app.services.focus_reports.SessionLocal", _factory)


# ─────────────────── Start-of-run insert shape ───────────────────


class TestStartOfRunInsert:
    """The pipeline_run row must land before the extraction loop starts and
    be committed in its own transaction so a mid-loop crash leaves a
    discoverable 'running' row behind."""

    @pytest.mark.asyncio
    async def test_inserts_pipeline_run_with_name_and_id_assigned(
        self, service: FocusReportService, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        db = _FakeDbSession()

        async def _fake_load_live_records(_bds: list[object]) -> FinancialExtractionResult:
            return FinancialExtractionResult(records=[], target_count=0)

        monkeypatch.setattr(service, "_load_live_records", _fake_load_live_records)
        _patch_session_local(monkeypatch, db)

        await service.load_financial_metrics(db)  # type: ignore[arg-type]

        runs = [obj for obj in db.added if isinstance(obj, PipelineRun)]
        assert len(runs) == 1
        run = runs[0]
        assert run.pipeline_name == FINANCIAL_PIPELINE_NAME == "financial_pdf_pipeline"
        assert run.trigger_source == "manual"
        assert run.id is not None, "flush should assign an id before the loop"

    @pytest.mark.asyncio
    async def test_commits_run_before_load_live_records(
        self, service: FocusReportService, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If _load_live_records ran before the commit, a crash inside it
        would roll back the pipeline_run INSERT and leave no audit trail."""
        db = _FakeDbSession()
        load_call_commit_count: dict[str, int] = {}

        async def _fake_load_live_records(_bds: list[object]) -> FinancialExtractionResult:
            load_call_commit_count["commits"] = db.commit_calls
            return FinancialExtractionResult(records=[], target_count=0)

        monkeypatch.setattr(service, "_load_live_records", _fake_load_live_records)
        _patch_session_local(monkeypatch, db)

        await service.load_financial_metrics(db)  # type: ignore[arg-type]

        assert load_call_commit_count["commits"] >= 1, (
            "pipeline_run must be committed before extraction starts"
        )

    @pytest.mark.asyncio
    async def test_start_notes_json_contains_offset_limit_target_count(
        self,
        service: FocusReportService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        db = _FakeDbSession()
        monkeypatch.setattr(settings, "financial_pipeline_offset", 0)
        monkeypatch.setattr(settings, "financial_pipeline_limit", 10)

        captured_start_notes: dict[str, object] = {}

        async def _fake_load_live_records(_bds: list[object]) -> FinancialExtractionResult:
            run = next(obj for obj in db.added if isinstance(obj, PipelineRun))
            captured_start_notes.update(json.loads(run.notes))
            return FinancialExtractionResult(records=[], target_count=0)

        monkeypatch.setattr(service, "_load_live_records", _fake_load_live_records)
        _patch_session_local(monkeypatch, db)

        await service.load_financial_metrics(db)  # type: ignore[arg-type]

        assert captured_start_notes["stage"] == "started"
        assert captured_start_notes["offset"] == 0
        assert captured_start_notes["limit"] == 10
        assert "target_count" in captured_start_notes
        assert "provider" in captured_start_notes


# ─────────────────── End-of-run success finalization ───────────────────


class TestSuccessFinalize:
    """_finalize_pipeline_run writes final counters and status; Fix B
    counters round-trip through notes as parseable JSON so downstream
    queries can UNION across pipeline types."""

    @pytest.mark.asyncio
    async def test_completed_when_no_skips(self, service: FocusReportService) -> None:
        run = PipelineRun(
            pipeline_name=FINANCIAL_PIPELINE_NAME,
            trigger_source="manual",
            status="running",
            total_items=5,
            processed_items=0,
            success_count=0,
            failure_count=0,
            notes="{}",
        )
        run.id = 1
        write_db = _FakeWriteSession(run)
        extraction = FinancialExtractionResult(
            records=[_make_metric_record(bd_id=i) for i in range(5)],
            target_count=5,
        )

        await service._finalize_pipeline_run(write_db, 1, extraction)  # type: ignore[arg-type]

        assert run.status == "completed"
        assert run.success_count == 5
        assert run.failure_count == 0
        assert run.processed_items == 5
        assert run.total_items == 5
        assert run.completed_at is not None

    @pytest.mark.asyncio
    async def test_completed_with_errors_when_skips_present(
        self, service: FocusReportService
    ) -> None:
        run = PipelineRun(
            pipeline_name=FINANCIAL_PIPELINE_NAME,
            trigger_source="manual",
            status="running",
            total_items=10,
            processed_items=0,
            success_count=0,
            failure_count=0,
            notes="{}",
        )
        run.id = 2
        write_db = _FakeWriteSession(run)
        extraction = FinancialExtractionResult(
            records=[_make_metric_record(bd_id=i) for i in range(3)],
            target_count=10,
            skipped_no_url=2,
            skipped_no_pdf=1,
            skipped_extraction_error=3,
            skipped_low_confidence=1,
        )

        await service._finalize_pipeline_run(write_db, 2, extraction)  # type: ignore[arg-type]

        assert run.status == "completed_with_errors"
        assert run.success_count == 3
        assert run.failure_count == 7
        assert run.total_items == 10

    @pytest.mark.asyncio
    async def test_notes_json_includes_fix_b_counters_with_exact_keys(
        self, service: FocusReportService
    ) -> None:
        """Downstream UNION-across-pipeline-types queries depend on the exact
        key names. This test locks the shape so a refactor can't rename keys
        without a deliberate follow-up."""
        run = PipelineRun(
            pipeline_name=FINANCIAL_PIPELINE_NAME,
            trigger_source="manual",
            status="running",
            total_items=4,
            processed_items=0,
            success_count=0,
            failure_count=0,
            notes="{}",
        )
        run.id = 3
        write_db = _FakeWriteSession(run)
        extraction = FinancialExtractionResult(
            records=[_make_metric_record(bd_id=1)],
            target_count=4,
            skipped_no_url=1,
            skipped_no_pdf=1,
            skipped_extraction_error=0,
            skipped_low_confidence=1,
        )

        await service._finalize_pipeline_run(write_db, 3, extraction)  # type: ignore[arg-type]

        details = json.loads(run.notes)
        assert details["records"] == 1
        assert details["skipped_no_url"] == 1
        assert details["skipped_no_pdf"] == 1
        assert details["skipped_extraction_error"] == 0
        assert details["skipped_low_confidence"] == 1
        assert details["target_count"] == 4
        assert "summary" in details and "Processed 4" in details["summary"]

    @pytest.mark.asyncio
    async def test_counters_sum_to_target_count(
        self, service: FocusReportService
    ) -> None:
        """Watchdog contract: records + all skip buckets must equal target_count
        for a coherent audit row. Any finalize path that drops firms silently
        would break this assertion and expose the drop."""
        run = PipelineRun(
            pipeline_name=FINANCIAL_PIPELINE_NAME,
            trigger_source="manual",
            status="running",
            total_items=10,
            processed_items=0,
            success_count=0,
            failure_count=0,
            notes="{}",
        )
        run.id = 4
        write_db = _FakeWriteSession(run)
        extraction = FinancialExtractionResult(
            records=[_make_metric_record(bd_id=i) for i in range(4)],
            target_count=10,
            skipped_no_url=2,
            skipped_no_pdf=2,
            skipped_extraction_error=1,
            skipped_low_confidence=1,
        )

        await service._finalize_pipeline_run(write_db, 4, extraction)  # type: ignore[arg-type]

        assert run.success_count + run.failure_count == extraction.target_count


# ─────────────────── End-of-run failure finalization ───────────────────


class TestFailureFinalize:
    """_mark_pipeline_run_failed uses a fresh session so the failure write is
    not bound to the extraction path's rolled-back transaction state."""

    @pytest.mark.asyncio
    async def test_failed_status_and_error_in_notes(
        self,
        service: FocusReportService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        run = PipelineRun(
            pipeline_name=FINANCIAL_PIPELINE_NAME,
            trigger_source="manual",
            status="running",
            total_items=10,
            processed_items=0,
            success_count=0,
            failure_count=0,
            notes="{}",
        )
        run.id = 10

        fake_session = _FakeWriteSession(run)
        monkeypatch.setattr(
            "app.services.focus_reports.SessionLocal",
            lambda: fake_session,
        )

        exc = RuntimeError("simulated Gemini failure")

        await service._mark_pipeline_run_failed(10, exc, target_count=10)

        assert run.status == "failed"
        assert run.completed_at is not None
        details = json.loads(run.notes)
        assert "RuntimeError" in details["error"]
        assert "simulated Gemini failure" in details["error"]
        assert details["target_count"] == 10
        fake_session.commit.assert_awaited()

    @pytest.mark.asyncio
    async def test_missing_run_id_does_not_raise(
        self,
        service: FocusReportService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If somehow the initial INSERT never landed (theoretical edge case),
        _mark_pipeline_run_failed must not crash the caller. The caller is
        about to re-raise the real exception; this helper must not mask it."""
        fake_session = _FakeWriteSession(PipelineRun())
        fake_session.get = AsyncMock(return_value=None)
        monkeypatch.setattr(
            "app.services.focus_reports.SessionLocal",
            lambda: fake_session,
        )

        await service._mark_pipeline_run_failed(999, ValueError("gone"), target_count=0)
        # Contract is "don't raise" — no assertion needed past the clean return.


class TestLoadFinancialMetricsFailurePath:
    """Integration-flavor test: an exception raised inside the extraction path
    triggers _mark_pipeline_run_failed and re-raises so the script exits non-
    zero."""

    @pytest.mark.asyncio
    async def test_extraction_exception_marks_failed_and_reraises(
        self,
        service: FocusReportService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        db = _FakeDbSession()

        async def _boom(_bds: list[object]) -> FinancialExtractionResult:
            raise RuntimeError("synthetic extraction failure")

        monkeypatch.setattr(service, "_load_live_records", _boom)

        captured_failed: dict[str, object] = {}

        async def _fake_mark_failed(
            run_id: int, exc: BaseException, target_count: int
        ) -> None:
            captured_failed["run_id"] = run_id
            captured_failed["exc_type"] = type(exc).__name__
            captured_failed["target_count"] = target_count

        monkeypatch.setattr(service, "_mark_pipeline_run_failed", _fake_mark_failed)

        with pytest.raises(RuntimeError, match="synthetic extraction failure"):
            await service.load_financial_metrics(db)  # type: ignore[arg-type]

        assert captured_failed["exc_type"] == "RuntimeError"
        assert "run_id" in captured_failed


# ─────────────────── Mid-run interruption (known behavior) ───────────────────


class TestKeyboardInterruptBehavior:
    """Known, deliberate behavior for this PR: a KeyboardInterrupt raised
    mid-loop propagates past the `except Exception` clause (KeyboardInterrupt
    is a BaseException subclass, not an Exception subclass), so
    _mark_pipeline_run_failed is NOT invoked and the pipeline_run row is left
    in status='running'.

    A future reconciliation job can sweep orphaned 'running' rows past a
    heartbeat threshold — out of scope for this PR per the Fix D task brief.
    """

    @pytest.mark.asyncio
    async def test_keyboard_interrupt_propagates_without_calling_mark_failed(
        self,
        service: FocusReportService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        db = _FakeDbSession()

        async def _interrupt(_bds: list[object]) -> FinancialExtractionResult:
            raise KeyboardInterrupt

        monkeypatch.setattr(service, "_load_live_records", _interrupt)

        mark_failed_called = {"count": 0}

        async def _fake_mark_failed(
            _run_id: int, _exc: BaseException, _target_count: int
        ) -> None:
            mark_failed_called["count"] += 1

        monkeypatch.setattr(service, "_mark_pipeline_run_failed", _fake_mark_failed)

        with pytest.raises(KeyboardInterrupt):
            await service.load_financial_metrics(db)  # type: ignore[arg-type]

        assert mark_failed_called["count"] == 0, (
            "KeyboardInterrupt must NOT trigger the failure-finalize path — "
            "the pipeline_run row is left in 'running' state on purpose."
        )

        # The INSERT still committed before the interrupt, so the audit row
        # remains discoverable.
        runs = [obj for obj in db.added if isinstance(obj, PipelineRun)]
        assert len(runs) == 1
        assert db.commit_calls >= 1


# ─────────────────── Empty-target-list smoke ───────────────────


class TestEmptyTargetSet:
    """When there are zero broker-dealers to process, the pipeline_run row
    still lands as an honest 'ran with zero work' audit entry."""

    @pytest.mark.asyncio
    async def test_empty_target_still_writes_pipeline_run(
        self,
        service: FocusReportService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        db = _FakeDbSession()

        async def _fake_load_live_records(_bds: list[object]) -> FinancialExtractionResult:
            return FinancialExtractionResult(records=[], target_count=0)

        monkeypatch.setattr(service, "_load_live_records", _fake_load_live_records)
        _patch_session_local(monkeypatch, db)

        await service.load_financial_metrics(db)  # type: ignore[arg-type]

        runs = [obj for obj in db.added if isinstance(obj, PipelineRun)]
        assert len(runs) == 1
        assert runs[0].pipeline_name == FINANCIAL_PIPELINE_NAME
