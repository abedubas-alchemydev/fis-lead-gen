"""Race-safety tests for the bulk financial writer's ON CONFLICT upsert.

Background: PR #744 made the FOCUS net-capital path
(``focus_ceo_extraction._persist_net_capital``) race-safe against
``uq_financial_metrics_bd_report_date`` via a savepoint-retry. This module
covers the symmetric race in the OTHER writer to ``financial_metrics`` — the
bulk financial pipeline in ``focus_reports.py``. That writer used to do a narrow
DELETE of the BD's ``(bd_id, report_date)`` rows, then ``add_all([...])`` +
``flush()`` against the same unique constraint. If a concurrent writer (the
net-capital path, or a parallel orchestrator child) inserted the same
``(bd_id, report_date)`` between the DELETE and the flush, the bulk ``flush()``
tripped the constraint and the whole run crashed.

The fix swaps the DELETE-then-INSERT for a single Postgres
``INSERT ... ON CONFLICT (bd_id, report_date) DO UPDATE`` (``_upsert_financial_metrics``),
which is atomic per row and so cannot collide. The bulk pipeline "owns the
multi-year rollup", so its values win on conflict — but a column the new
extraction couldn't read (NULL) must NOT clobber a previously-good value.

Coverage:

  1. Statement shape (unit, no DB). The compiled SQL targets the named
     constraint, takes ``EXCLUDED`` directly for the always-present
     ``net_capital`` / ``extraction_status``, and ``COALESCE(EXCLUDED.col,
     financial_metrics.col)`` for the nullable extras (no NULL-clobber).
  2. Helper contract (unit, no DB, fake session). ``_upsert_financial_metrics``
     issues exactly one ``execute`` of an ``OnConflictDoUpdate`` insert, and
     is a no-op on an empty record set.
  3. Bulk-path no longer pre-DELETEs to dodge the constraint (unit, no DB,
     fake session). ``_run_extraction_pipeline`` on the narrow (subset) path
     issues NO ``DELETE`` at all and instead drives the conflict-safe upsert —
     so a concurrent insert can't crash the run. The full-table refresh path
     still issues the bare ``DELETE FROM financial_metrics`` for stale-firm
     eviction.
  4. Concurrent-insert race recovers (integration, real Postgres). Seed a
     conflicting ``(bd_id, report_date)`` row, then run ``_upsert_financial_metrics``
     for the same pair: no ``IntegrityError`` is raised, the bulk values win for
     the always-present columns, and a NULL extra in the new row preserves the
     prior non-NULL reading. Marked ``integration`` — the Postgres ``ON CONFLICT``
     construct can't compile against SQLite, so this runs against the CI pg15
     service / staging Neon, not the default unit run.
"""

from __future__ import annotations

import os
from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql.dml import OnConflictDoUpdate

from app.core.config import settings
from app.services.extraction_status import STATUS_NEEDS_REVIEW, STATUS_PARSED
from app.services.focus_reports import FinancialExtractionResult, FocusReportService
from app.services.service_models import FinancialMetricRecord


# ───────────────────────── shared fixtures / fakes ─────────────────────────


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> FocusReportService:
    """FocusReportService with a syntactically valid Gemini key so
    ``GeminiResponsesClient.__init__`` does not fail on key validation."""
    monkeypatch.setattr(settings, "gemini_api_key", "AIzaSy" + "a" * 33)
    return FocusReportService()


def _record(
    *,
    bd_id: int = 42,
    report_date: date = date(2025, 12, 31),
    net_capital: float = 1_000_000.0,
    excess_net_capital: float | None = 500_000.0,
    total_assets: float | None = 5_000_000.0,
    required_min_capital: float | None = 250_000.0,
    source_filing_url: str | None = "https://www.sec.gov/Archives/test.pdf",
    extraction_status: str = STATUS_PARSED,
) -> FinancialMetricRecord:
    return FinancialMetricRecord(
        bd_id=bd_id,
        report_date=report_date,
        net_capital=net_capital,
        excess_net_capital=excess_net_capital,
        total_assets=total_assets,
        required_min_capital=required_min_capital,
        source_filing_url=source_filing_url,
        extraction_status=extraction_status,
    )


class _RecordingWriteSession:
    """Captures every statement passed to ``execute`` so a test can inspect
    whether a DELETE or an ON CONFLICT insert was issued. ``add_all`` / ``flush``
    / ``commit`` / ``get`` are no-ops or benign stubs; the rollup SELECT returns
    an empty scalar list so ``_refresh_broker_dealer_rollups`` is a no-op."""

    def __init__(self) -> None:
        self.executed: list[Any] = []
        self.add_all_calls: list[Any] = []
        self.flush_calls = 0

    async def execute(self, stmt: Any) -> Any:
        self.executed.append(stmt)
        result = MagicMock()
        result.scalars.return_value.all.return_value = []
        return result

    def add_all(self, items: Any) -> None:
        self.add_all_calls.append(items)

    def add(self, _item: Any) -> None:  # pragma: no cover - not used here
        pass

    async def flush(self) -> None:
        self.flush_calls += 1

    async def commit(self) -> None:
        pass

    async def get(self, _model: Any, _pk: Any) -> Any:
        return None

    async def __aenter__(self) -> "_RecordingWriteSession":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None


def _is_delete(stmt: Any) -> bool:
    return type(stmt).__name__ == "Delete"


def _is_conflict_insert(stmt: Any) -> bool:
    return isinstance(stmt, OnConflictDoUpdate) or isinstance(
        getattr(stmt, "_post_values_clause", None), OnConflictDoUpdate
    )


# ─────────────────── 1. Statement shape ───────────────────


class TestUpsertStatementShape:
    """The compiled upsert SQL must (a) target the unique constraint by name,
    (b) take EXCLUDED directly for the always-present columns, and (c) COALESCE
    the nullable extras so a NULL in the incoming row preserves the existing
    value rather than clobbering it."""

    def _compile(self, records: list[FinancialMetricRecord]) -> str:
        captured: dict[str, Any] = {}

        class _CaptureSession:
            async def execute(self, stmt: Any) -> None:
                captured["stmt"] = stmt

        service = FocusReportService.__new__(FocusReportService)
        import asyncio

        asyncio.run(
            FocusReportService._upsert_financial_metrics(
                service, _CaptureSession(), records  # type: ignore[arg-type]
            )
        )
        return str(captured["stmt"].compile(dialect=postgresql.dialect()))

    def test_targets_named_constraint_and_does_update(self) -> None:
        sql = self._compile([_record()])
        assert "ON CONFLICT ON CONSTRAINT uq_financial_metrics_bd_report_date" in sql
        assert "DO UPDATE SET" in sql

    def test_always_present_columns_take_excluded_directly(self) -> None:
        sql = self._compile([_record()])
        # net_capital and extraction_status are always populated by the
        # extractor (NULL net_capital rows are dropped upstream), so the bulk
        # value wins unconditionally.
        assert "net_capital = excluded.net_capital" in sql
        assert "extraction_status = excluded.extraction_status" in sql

    def test_nullable_extras_coalesce_to_preserve_prior_value(self) -> None:
        sql = self._compile([_record()])
        for col in (
            "excess_net_capital",
            "total_assets",
            "required_min_capital",
            "source_filing_url",
        ):
            assert (
                f"{col} = coalesce(excluded.{col}, financial_metrics.{col})" in sql
            ), f"{col} must COALESCE to avoid NULL-clobbering a prior reading"


# ─────────────────── 2. Helper contract ───────────────────


class TestUpsertHelperContract:
    @pytest.mark.asyncio
    async def test_issues_single_conflict_insert(
        self, service: FocusReportService
    ) -> None:
        session = _RecordingWriteSession()
        await service._upsert_financial_metrics(session, [_record(), _record(report_date=date(2024, 12, 31))])  # type: ignore[arg-type]

        assert len(session.executed) == 1
        assert _is_conflict_insert(session.executed[0])
        # No legacy add_all path is used anymore.
        assert session.add_all_calls == []

    @pytest.mark.asyncio
    async def test_empty_records_is_a_noop(
        self, service: FocusReportService
    ) -> None:
        session = _RecordingWriteSession()
        await service._upsert_financial_metrics(session, [])  # type: ignore[arg-type]
        assert session.executed == []


# ─────────────── 3. Bulk path no longer pre-DELETEs to dodge the constraint ───────────────


class TestBulkPathRaceSafety:
    """``_run_extraction_pipeline`` must drive the conflict-safe upsert instead
    of the old narrow DELETE-then-INSERT, so a concurrent insert of the same
    ``(bd_id, report_date)`` can't crash the run. The full-table refresh path
    keeps its bare DELETE for stale-firm eviction."""

    async def _drive(
        self,
        service: FocusReportService,
        monkeypatch: pytest.MonkeyPatch,
        *,
        narrow_delete: bool,
    ) -> _RecordingWriteSession:
        records = [
            _record(bd_id=1, report_date=date(2025, 12, 31)),
            _record(bd_id=1, report_date=date(2024, 12, 31)),
        ]
        extraction = FinancialExtractionResult(records=records, target_count=1)

        async def _fake_load_live_records(_bds: list[object]) -> FinancialExtractionResult:
            return extraction

        monkeypatch.setattr(service, "_load_live_records", _fake_load_live_records)
        monkeypatch.setattr(service, "_refresh_broker_dealer_rollups", AsyncMock())
        monkeypatch.setattr(service, "_finalize_pipeline_run", AsyncMock())

        write_session = _RecordingWriteSession()
        monkeypatch.setattr(
            "app.services.focus_reports.SessionLocal", lambda: write_session
        )

        # The "main" DB session only needs to support the pipeline_run insert +
        # flush + commit; reuse the recording session's benign stubs.
        main_session = _RecordingWriteSession()

        async def _flush_assigns_id() -> None:
            for obj in main_session.add_all_calls:  # pragma: no cover - unused
                pass

        # Patch the pipeline_run id assignment: flush() must give the run an id.
        run_holder: dict[str, Any] = {}

        def _add(obj: Any) -> None:
            run_holder["run"] = obj

        async def _flush() -> None:
            run = run_holder.get("run")
            if run is not None and getattr(run, "id", None) is None:
                run.id = 123

        main_session.add = _add  # type: ignore[assignment]
        main_session.flush = _flush  # type: ignore[assignment]

        await service._run_extraction_pipeline(
            main_session,  # type: ignore[arg-type]
            [MagicMock()],
            trigger_source="test",
            narrow_delete=narrow_delete,
        )
        return write_session

    @pytest.mark.asyncio
    async def test_narrow_path_issues_no_delete_only_upsert(
        self, service: FocusReportService, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        write_session = await self._drive(service, monkeypatch, narrow_delete=True)

        deletes = [s for s in write_session.executed if _is_delete(s)]
        conflict_inserts = [s for s in write_session.executed if _is_conflict_insert(s)]

        assert deletes == [], (
            "narrow/subset path must NOT issue a DELETE — the upsert handles "
            "the (bd_id, report_date) collision atomically, removing the "
            "DELETE-then-flush window that crashed concurrent runs"
        )
        assert len(conflict_inserts) == 1

    @pytest.mark.asyncio
    async def test_full_refresh_keeps_bare_delete_plus_upsert(
        self, service: FocusReportService, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        write_session = await self._drive(service, monkeypatch, narrow_delete=False)

        deletes = [s for s in write_session.executed if _is_delete(s)]
        conflict_inserts = [s for s in write_session.executed if _is_conflict_insert(s)]

        # Exactly one bare full-table DELETE (stale-firm eviction) + the upsert.
        assert len(deletes) == 1
        assert deletes[0].whereclause is None, (
            "full-table refresh DELETE must be unfiltered (evicts firms not in "
            "this run's record set)"
        )
        assert len(conflict_inserts) == 1


# ─────────────── 4. Concurrent-insert race recovery (integration) ───────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_insert_does_not_crash_and_bulk_values_win() -> None:
    """End-to-end on real Postgres: a row for ``(bd_id, report_date)`` already
    exists (simulating the winner of the race — e.g. the FOCUS net-capital path
    committed first). Running ``_upsert_financial_metrics`` for the same pair
    must NOT raise ``IntegrityError``; instead the bulk pipeline's values win for
    the always-present columns, and a NULL extra in the new row preserves the
    prior non-NULL reading (no NULL-clobber).

    Requires DATABASE_URL pointing at Postgres (CI pg15 service / staging Neon).
    The Postgres ``ON CONFLICT`` construct can't compile against SQLite, so this
    is integration-only and skipped in the default unit run.
    """
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models.broker_dealer import BrokerDealer
    from app.models.financial_metric import FinancialMetric

    raw_url = os.environ.get("DATABASE_URL") or settings.database_url
    if "sqlite" in raw_url or "postgres" not in raw_url:
        pytest.skip("Requires a Postgres DATABASE_URL for the ON CONFLICT path.")

    # Normalize to an async psycopg URL.
    async_url = raw_url
    if "+asyncpg" in async_url:
        async_url = async_url.replace("+asyncpg", "+psycopg")
    if async_url.startswith("postgresql://"):
        async_url = "postgresql+psycopg://" + async_url[len("postgresql://") :]
    if async_url.startswith("postgresql+psycopg2://"):
        async_url = "postgresql+psycopg://" + async_url[len("postgresql+psycopg2://") :]

    engine = create_async_engine(async_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    service = FocusReportService.__new__(FocusReportService)

    bd_id = 999_000_777  # high, unlikely to collide with seeded fixture data
    report_date = date(2025, 12, 31)

    try:
        # Seed: a broker_dealer (FK target) + the pre-existing conflicting metric
        # with a non-NULL total_assets the new (NULL) row must preserve.
        async with maker() as db:
            await db.execute(
                FinancialMetric.__table__.delete().where(
                    FinancialMetric.bd_id == bd_id
                )
            )
            await db.execute(
                BrokerDealer.__table__.delete().where(BrokerDealer.id == bd_id)
            )
            db.add(BrokerDealer(id=bd_id, name="RACE TEST BD"))
            await db.flush()
            db.add(
                FinancialMetric(
                    bd_id=bd_id,
                    report_date=report_date,
                    net_capital=1_000_000.0,
                    excess_net_capital=10.0,
                    total_assets=7_777.0,  # prior reading to be preserved
                    required_min_capital=50.0,
                    source_filing_url="https://old.example/x.pdf",
                    extraction_status=STATUS_PARSED,
                )
            )
            await db.commit()

        # The bulk run re-extracts the same pair: new net_capital + status, but
        # total_assets came back NULL this time (Gemini couldn't read it).
        new_record = _record(
            bd_id=bd_id,
            report_date=report_date,
            net_capital=2_500_000.0,
            excess_net_capital=None,
            total_assets=None,
            required_min_capital=None,
            source_filing_url=None,
            extraction_status=STATUS_NEEDS_REVIEW,
        )

        async with maker() as db:
            # No pre-DELETE — exactly the production write path. Must not raise.
            await service._upsert_financial_metrics(db, [new_record])
            await db.commit()

        async with maker() as db:
            rows = (
                await db.execute(
                    select(FinancialMetric).where(FinancialMetric.bd_id == bd_id)
                )
            ).scalars().all()

        assert len(rows) == 1, "upsert must not create a duplicate row"
        row = rows[0]
        # Always-present columns: bulk wins.
        assert float(row.net_capital) == 2_500_000.0
        assert row.extraction_status == STATUS_NEEDS_REVIEW
        # Nullable extras: NULL in the new row preserves the prior reading.
        assert float(row.total_assets) == 7_777.0
        assert float(row.excess_net_capital) == 10.0
        assert float(row.required_min_capital) == 50.0
        assert row.source_filing_url == "https://old.example/x.pdf"
    finally:
        # Cleanup so re-runs stay idempotent.
        async with maker() as db:
            await db.execute(
                FinancialMetric.__table__.delete().where(
                    FinancialMetric.bd_id == bd_id
                )
            )
            await db.execute(
                BrokerDealer.__table__.delete().where(BrokerDealer.id == bd_id)
            )
            await db.commit()
        await engine.dispose()
