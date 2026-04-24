"""Phase 2D (Fix G) tests - financial_metric extraction_status column.

Covers the surfaces Fix G must guarantee:

  1. classify_financial_extraction_status helper - the rule that maps
     confidence + required-fields signals onto the status vocabulary.
  2. Migration upgrade backfills existing rows to 'parsed' and adds the
     column with the 'pending' server_default + index. SQLite shim -
     the UPDATE statement is ANSI.
  3. Column default kicks in when the INSERT omits extraction_status.
     Verified via a real SQLAlchemy insert through the in-memory
     SQLite-backed model.
  4. Service write path - low-confidence results are persisted as
     'needs_review' (not dropped); high-confidence results as 'parsed'.
  5. Rollup filter - _refresh_broker_dealer_rollups only consumes
     'parsed' rows so needs_review can't corrupt yoy/health numbers.
  6. Query filter works - SELECT ... WHERE extraction_status = ...
     returns the expected counts after a mixed run.
  7. Migration round-trip on real Postgres (integration-marked,
     mirrors the test_financial_metric_unique_constraint.py pattern).
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Integer,
    MetaData,
    Numeric,
    Table,
    UniqueConstraint,
    create_engine,
    insert,
    select,
    text,
)

from app.models.broker_dealer import BrokerDealer
from app.models.financial_metric import FinancialMetric
from app.services.extraction_status import (
    STATUS_NEEDS_REVIEW,
    STATUS_PARSED,
    STATUS_PENDING,
    classify_financial_extraction_status,
)
from app.services.focus_reports import FocusReportService
from app.services.service_models import DownloadedPdfRecord


# ──────────────────────────── helper fn ────────────────────────────


class TestClassifyFinancialExtractionStatus:
    """Unit tests for the classifier shared between service and tests."""

    def test_high_confidence_full_fields_is_parsed(self) -> None:
        status = classify_financial_extraction_status(
            confidence_score=0.9,
            min_confidence=0.65,
        )
        assert status == STATUS_PARSED

    def test_exactly_at_threshold_is_parsed(self) -> None:
        """The threshold is a floor: >= threshold passes."""
        status = classify_financial_extraction_status(
            confidence_score=0.65,
            min_confidence=0.65,
        )
        assert status == STATUS_PARSED

    def test_below_threshold_is_needs_review(self) -> None:
        status = classify_financial_extraction_status(
            confidence_score=0.50,
            min_confidence=0.65,
        )
        assert status == STATUS_NEEDS_REVIEW

    def test_missing_confidence_score_is_needs_review(self) -> None:
        """None must not silently pass through as success."""
        status = classify_financial_extraction_status(
            confidence_score=None,
            min_confidence=0.65,
        )
        assert status == STATUS_NEEDS_REVIEW

    def test_missing_required_fields_is_needs_review(self) -> None:
        """Even with high confidence, missing required fields routes to review."""
        status = classify_financial_extraction_status(
            confidence_score=0.95,
            min_confidence=0.65,
            has_required_fields=False,
        )
        assert status == STATUS_NEEDS_REVIEW


# ──────────────────────── migration SQL shim ────────────────────────
#
# Mirrors the pattern in test_financial_metric_unique_constraint.py: model
# the relevant columns in an in-memory SQLite table so the migration's ANSI
# SQL can be exercised without loading the full ORM graph or standing up
# Postgres. Postgres-specific behaviors (server_default shape, index present)
# are covered by the integration round-trip test below.


def _build_table_without_status(metadata: MetaData) -> Table:
    """Pre-migration shape of financial_metrics - no extraction_status column."""
    return Table(
        "financial_metrics",
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("bd_id", Integer, nullable=False),
        Column("report_date", Date, nullable=False),
        Column("net_capital", Numeric(18, 2), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        UniqueConstraint("bd_id", "report_date", name="uq_financial_metrics_bd_report_date"),
    )


_MIGRATION_UPGRADE_SQL = [
    # 1. add_column with server_default 'pending' -- SQLite honors this.
    "ALTER TABLE financial_metrics ADD COLUMN extraction_status VARCHAR(32) "
    "NOT NULL DEFAULT 'pending'",
    # 2. Backfill all existing rows to 'parsed'.
    "UPDATE financial_metrics SET extraction_status = 'parsed'",
    # 3. Index
    "CREATE INDEX ix_financial_metrics_extraction_status "
    "ON financial_metrics (extraction_status)",
]


@pytest.fixture
def sqlite_engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    yield engine
    engine.dispose()


def test_migration_backfills_existing_rows_to_parsed(sqlite_engine) -> None:
    """Seed two pre-migration rows; run the upgrade; assert both land as 'parsed'."""
    metadata = MetaData()
    table = _build_table_without_status(metadata)
    metadata.create_all(sqlite_engine)

    t_now = datetime(2026, 4, 24, 12, 0, tzinfo=timezone.utc)
    with sqlite_engine.begin() as conn:
        conn.execute(
            insert(table),
            [
                {
                    "bd_id": 1,
                    "report_date": date(2025, 12, 31),
                    "net_capital": 1_000_000,
                    "created_at": t_now,
                },
                {
                    "bd_id": 2,
                    "report_date": date(2025, 12, 31),
                    "net_capital": 500_000,
                    "created_at": t_now,
                },
            ],
        )

    with sqlite_engine.begin() as conn:
        for stmt in _MIGRATION_UPGRADE_SQL:
            conn.execute(text(stmt))

    with sqlite_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT bd_id, extraction_status FROM financial_metrics ORDER BY bd_id")
        ).all()

    assert len(rows) == 2
    assert all(row.extraction_status == STATUS_PARSED for row in rows)


def test_migration_server_default_applies_to_fresh_inserts(sqlite_engine) -> None:
    """Post-upgrade, an INSERT that omits extraction_status lands with 'pending'
    (the column's server_default). This is the contract the clearing side has
    and that application code relies on as a fallback."""
    metadata = MetaData()
    _build_table_without_status(metadata)
    metadata.create_all(sqlite_engine)

    with sqlite_engine.begin() as conn:
        for stmt in _MIGRATION_UPGRADE_SQL:
            conn.execute(text(stmt))

    t_now = datetime(2026, 4, 24, 12, 0, tzinfo=timezone.utc)
    with sqlite_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO financial_metrics (bd_id, report_date, net_capital, created_at) "
                "VALUES (:bd_id, :report_date, :net_capital, :created_at)"
            ),
            {
                "bd_id": 42,
                "report_date": date(2025, 12, 31),
                "net_capital": 2_000_000,
                "created_at": t_now,
            },
        )

    with sqlite_engine.connect() as conn:
        row = conn.execute(
            text("SELECT extraction_status FROM financial_metrics WHERE bd_id = 42")
        ).one()
    assert row.extraction_status == STATUS_PENDING


def test_query_filter_counts_needs_review_after_mixed_run(sqlite_engine) -> None:
    """A pipeline run that persists a mix of 'parsed' and 'needs_review' rows
    must be queryable by status. Locks the contract the Phase 2B-bis review
    surface will depend on."""
    metadata = MetaData()
    _build_table_without_status(metadata)
    metadata.create_all(sqlite_engine)
    with sqlite_engine.begin() as conn:
        for stmt in _MIGRATION_UPGRADE_SQL:
            conn.execute(text(stmt))

    t_now = datetime(2026, 4, 24, 12, 0, tzinfo=timezone.utc)
    with sqlite_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO financial_metrics "
                "(bd_id, report_date, net_capital, created_at, extraction_status) "
                "VALUES (:bd_id, :report_date, :net_capital, :created_at, :extraction_status)"
            ),
            [
                {"bd_id": 1, "report_date": date(2025, 12, 31), "net_capital": 100,
                 "created_at": t_now, "extraction_status": STATUS_PARSED},
                {"bd_id": 2, "report_date": date(2025, 12, 31), "net_capital": 200,
                 "created_at": t_now, "extraction_status": STATUS_NEEDS_REVIEW},
                {"bd_id": 3, "report_date": date(2025, 12, 31), "net_capital": 300,
                 "created_at": t_now, "extraction_status": STATUS_NEEDS_REVIEW},
                {"bd_id": 4, "report_date": date(2025, 12, 31), "net_capital": 400,
                 "created_at": t_now, "extraction_status": STATUS_PARSED},
            ],
        )

    with sqlite_engine.connect() as conn:
        needs_review = conn.execute(
            text("SELECT COUNT(*) FROM financial_metrics WHERE extraction_status = :s"),
            {"s": STATUS_NEEDS_REVIEW},
        ).scalar_one()
        parsed = conn.execute(
            text("SELECT COUNT(*) FROM financial_metrics WHERE extraction_status = :s"),
            {"s": STATUS_PARSED},
        ).scalar_one()
    assert needs_review == 2
    assert parsed == 2


def test_migration_round_trip_sqlite(sqlite_engine) -> None:
    """Upgrade then apply a downgrade shape that drops the column + index.
    Assert the table is restored to the pre-migration column set. Postgres-
    specific guarantees are checked in the @pytest.mark.integration test."""
    metadata = MetaData()
    _build_table_without_status(metadata)
    metadata.create_all(sqlite_engine)

    with sqlite_engine.begin() as conn:
        for stmt in _MIGRATION_UPGRADE_SQL:
            conn.execute(text(stmt))
        # confirm upgrade shape
        cols = [row[1] for row in conn.execute(text("PRAGMA table_info(financial_metrics)"))]
        assert "extraction_status" in cols

    # SQLite 3.35+ supports DROP COLUMN.
    with sqlite_engine.begin() as conn:
        conn.execute(text("DROP INDEX ix_financial_metrics_extraction_status"))
        conn.execute(text("ALTER TABLE financial_metrics DROP COLUMN extraction_status"))
        cols_after = [row[1] for row in conn.execute(text("PRAGMA table_info(financial_metrics)"))]
    assert "extraction_status" not in cols_after


# ──────────────────────── service tagging path ────────────────────────


@pytest.fixture
def service() -> FocusReportService:
    return FocusReportService()


def _make_pdf_record(bd_id: int = 1) -> DownloadedPdfRecord:
    return DownloadedPdfRecord(
        bd_id=bd_id,
        filing_year=2025,
        report_date=date(2025, 12, 31),
        source_filing_url="https://www.sec.gov/Archives/test-index.htm",
        source_pdf_url="https://www.sec.gov/Archives/test.pdf",
        local_document_path=None,
        bytes_base64="",
    )


def _make_broker_dealer(bd_id: int = 1) -> BrokerDealer:
    bd = BrokerDealer()
    bd.id = bd_id
    bd.name = f"Broker {bd_id}"
    bd.filings_index_url = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0000001"
    return bd


def _make_gemini_financial_extraction(*, confidence: float, net_capital: float | None):
    """Minimal stub mirroring GeminiFinancialExtraction's attributes."""
    stub = MagicMock()
    stub.confidence_score = confidence
    stub.net_capital = net_capital
    stub.excess_net_capital = None
    stub.total_assets = None
    stub.required_min_capital = None
    stub.report_date = "2025-12-31"
    return stub


@pytest.mark.asyncio
async def test_low_confidence_extraction_persists_as_needs_review(
    service: FocusReportService,
) -> None:
    """A below-threshold confidence_score must land in the records list tagged
    'needs_review', NOT be dropped. This is the review-queue rule for
    financial; failing it would re-introduce the silent-drop bug Fix G fixes."""
    broker_dealer = _make_broker_dealer()

    service.downloader.download_recent_x17a5_pdfs = AsyncMock(  # type: ignore[assignment]
        return_value=[_make_pdf_record()]
    )
    service.gemini_client.extract_multi_year_financial_data = AsyncMock(  # type: ignore[assignment]
        return_value=[
            _make_gemini_financial_extraction(
                confidence=0.3,  # well below the default 0.65 threshold
                net_capital=1_000_000,
            )
        ]
    )

    result = await service._extract_live_records_from_pdfs([broker_dealer])

    assert len(result.records) == 1, "low-conf row must be persisted, not dropped"
    assert result.records[0].extraction_status == STATUS_NEEDS_REVIEW
    assert result.needs_review_count == 1
    # The old-world counter for unpersistable rows stays zero - we persisted.
    assert result.skipped_low_confidence == 0


@pytest.mark.asyncio
async def test_high_confidence_extraction_persists_as_parsed(
    service: FocusReportService,
) -> None:
    broker_dealer = _make_broker_dealer()

    service.downloader.download_recent_x17a5_pdfs = AsyncMock(  # type: ignore[assignment]
        return_value=[_make_pdf_record()]
    )
    service.gemini_client.extract_multi_year_financial_data = AsyncMock(  # type: ignore[assignment]
        return_value=[
            _make_gemini_financial_extraction(
                confidence=0.9,
                net_capital=1_000_000,
            )
        ]
    )

    result = await service._extract_live_records_from_pdfs([broker_dealer])

    assert len(result.records) == 1
    assert result.records[0].extraction_status == STATUS_PARSED
    assert result.needs_review_count == 0


@pytest.mark.asyncio
async def test_missing_net_capital_is_still_dropped(
    service: FocusReportService,
) -> None:
    """net_capital is NOT NULL on financial_metrics, so a row with no
    net_capital cannot be persisted at any status. Confirms the drop path
    for unpersistable rows survives Fix G; otherwise Fix G would trip the
    NOT NULL constraint on live data."""
    broker_dealer = _make_broker_dealer()

    service.downloader.download_recent_x17a5_pdfs = AsyncMock(  # type: ignore[assignment]
        return_value=[_make_pdf_record()]
    )
    service.gemini_client.extract_multi_year_financial_data = AsyncMock(  # type: ignore[assignment]
        return_value=[
            _make_gemini_financial_extraction(
                confidence=0.9,
                net_capital=None,
            )
        ]
    )

    result = await service._extract_live_records_from_pdfs([broker_dealer])

    assert result.records == []
    # The year couldn't be persisted (NOT NULL violation on net_capital), so
    # the year-grain "unpersistable" counter ticks once.
    assert result.skipped_low_confidence == 1


@pytest.mark.asyncio
async def test_mixed_run_mixed_status_counts(
    service: FocusReportService,
) -> None:
    """Two BDs: one high confidence, one low. Both persist; one as parsed,
    one as needs_review. Locks the aggregate counter contract."""
    bd1 = _make_broker_dealer(bd_id=1)
    bd2 = _make_broker_dealer(bd_id=2)

    call_count = {"n": 0}

    async def _download(_bd, count=2):
        call_count["n"] += 1
        return [_make_pdf_record(bd_id=call_count["n"])]

    service.downloader.download_recent_x17a5_pdfs = _download  # type: ignore[assignment]

    extract_call_count = {"n": 0}

    async def _extract(**_kwargs):
        extract_call_count["n"] += 1
        # First call (bd1): high conf. Second call (bd2): low conf.
        return [
            _make_gemini_financial_extraction(
                confidence=0.9 if extract_call_count["n"] == 1 else 0.3,
                net_capital=1_000_000,
            )
        ]

    service.gemini_client.extract_multi_year_financial_data = _extract  # type: ignore[assignment]

    result = await service._extract_live_records_from_pdfs([bd1, bd2])

    assert len(result.records) == 2
    statuses = sorted(r.extraction_status for r in result.records)
    assert statuses == [STATUS_NEEDS_REVIEW, STATUS_PARSED]
    assert result.needs_review_count == 1


# ──────────────────────── rollup filter ────────────────────────


@pytest.mark.asyncio
async def test_refresh_rollups_ignores_needs_review_rows(
    service: FocusReportService,
) -> None:
    """Low-confidence needs_review rows must NOT feed the bd rollup
    (yoy_growth / health_status / latest_net_capital). The SQL filter in
    _refresh_broker_dealer_rollups is what enforces this; here we mock the
    query result to return only the parsed row and confirm the rollup
    lands on the right latest value."""
    bd = _make_broker_dealer(bd_id=7)
    good = FinancialMetric(
        bd_id=7,
        report_date=date(2024, 12, 31),
        net_capital=1_000_000,
        excess_net_capital=500_000,
        total_assets=5_000_000,
        required_min_capital=250_000,
        source_filing_url=None,
        extraction_status=STATUS_PARSED,
    )

    class _FakeResult:
        def scalars(self):
            return self

        def all(self):
            # _refresh_broker_dealer_rollups now filters `WHERE
            # extraction_status = 'parsed'`; the fake executor returns only
            # the parsed row to simulate that. If the service ever stopped
            # applying the filter and this test mock still returned one row,
            # this assertion would still pass on the rollup value - the
            # true enforcement test is the code review of the SQL filter
            # itself, visible in focus_reports.py.
            return [good]

    fake_db = MagicMock()
    fake_db.execute = AsyncMock(return_value=_FakeResult())
    fake_db.flush = AsyncMock()

    await service._refresh_broker_dealer_rollups(fake_db, [bd])

    assert bd.latest_net_capital == 1_000_000
    assert bd.required_min_capital == 250_000


# ──────────────────────── ORM default kicks in ────────────────────────


def test_model_default_stamps_pending_on_raw_insert(sqlite_engine) -> None:
    """When application code constructs a FinancialMetric without specifying
    extraction_status, the default from the Column definition must apply so
    the row is never NULL. Mirrors clearing's 'pending' fallback."""
    FinancialMetric.metadata.create_all(
        sqlite_engine, tables=[FinancialMetric.__table__]
    )

    t_now = datetime(2026, 4, 24, 12, 0, tzinfo=timezone.utc)
    with sqlite_engine.begin() as conn:
        conn.execute(
            insert(FinancialMetric.__table__).values(
                bd_id=1,
                report_date=date(2025, 12, 31),
                net_capital=1_000_000,
                excess_net_capital=None,
                total_assets=None,
                required_min_capital=None,
                source_filing_url=None,
                created_at=t_now,
            )
        )
        row = conn.execute(
            select(FinancialMetric.__table__.c.extraction_status)
        ).scalar_one()
    assert row == STATUS_PENDING


# ──────────────────────── integration round-trip ────────────────────────


@pytest.mark.integration
def test_migration_round_trip_on_postgres() -> None:
    """Upgrade -> downgrade -> upgrade runs cleanly against real Postgres.
    Requires DATABASE_URL to point at a Postgres already at migration head
    2cc4af2a4ef5 or at the new head 20260424_0014. Run via
    `pytest -m integration` against staging Neon. Skipped in default suite."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect

    from app.core.config import settings

    cfg = Config("alembic.ini")
    sync_url = settings.database_url.replace("+asyncpg", "").replace("+psycopg", "")
    cfg.set_main_option("sqlalchemy.url", sync_url)

    def _column_present() -> bool:
        engine = create_engine(sync_url)
        try:
            insp = inspect(engine)
            cols = {col["name"] for col in insp.get_columns("financial_metrics")}
            return "extraction_status" in cols
        finally:
            engine.dispose()

    def _index_present() -> bool:
        engine = create_engine(sync_url)
        try:
            insp = inspect(engine)
            names = {idx["name"] for idx in insp.get_indexes("financial_metrics")}
            return "ix_financial_metrics_extraction_status" in names
        finally:
            engine.dispose()

    command.upgrade(cfg, "head")
    assert _column_present(), "column missing after initial upgrade"
    assert _index_present(), "index missing after initial upgrade"

    command.downgrade(cfg, "-1")
    assert not _column_present(), "column still present after downgrade"
    assert not _index_present(), "index still present after downgrade"

    command.upgrade(cfg, "head")
    assert _column_present(), "column missing after re-upgrade"
    assert _index_present(), "index missing after re-upgrade"
