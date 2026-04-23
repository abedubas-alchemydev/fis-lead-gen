"""Phase 2C-code tests - multi-year financial extractor call-site swap.

Covers the five behaviours Phase 2C-code must guarantee:

  1. Call-site swap. The multi-year extractor returns an iterable of
     per-year GeminiFinancialExtraction; each item becomes its own
     FinancialMetricRecord keyed on (bd_id, report_date).
  2. Year-level failure isolation. A confidence-failure on year N-1
     does not sink the siblings at years N and N-2; it increments
     skipped_low_confidence at year-grain.
  3. Counter sum invariant. records + skipped_no_url + skipped_no_pdf
     + skipped_extraction_error + skipped_low_confidence equals the
     total unit-of-work attempted across the run. records is row-grain
     (rows inserted); skipped_no_url / skipped_no_pdf are firm-grain
     (no PDF to try has no per-year analogue); skipped_low_confidence
     and skipped_extraction_error are year-grain where the extractor's
     shape allows.
  4. Re-run idempotency through the narrowed DELETE. The narrowed
     DELETE in focus_reports.py keys on (bd_id, report_date) tuples;
     running the DELETE-then-INSERT path twice with the same record
     set does not grow row count, and updated values replace prior
     values at the same (bd_id, report_date).
  5. Needs-review preservation. Low-confidence per-year rows are NOT
     persisted (today's review-queue plumbing is "skip and log and
     bump the counter"). Phase 2D / Fix G will formalize this with an
     extraction_status column; until then the contract is counter
     increment + no row emitted.

Tests 1, 2, 3, 5 mock the gemini_client + downloader on
FocusReportService and invoke _extract_live_records_from_pdfs
directly. Test 4 uses an in-memory SQLite engine and a shim of the
financial_metrics schema; the SQL under test is ANSI (tuple IN, UNIQUE
constraint, DELETE) and SQLite 3.25+ supports it.
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
    delete,
    insert,
    select,
    tuple_,
)

from app.core.config import settings
from app.services.focus_reports import FocusReportService
from app.services.gemini_responses import (
    GeminiExtractionError,
    GeminiFinancialExtraction,
)
from app.services.service_models import DownloadedPdfRecord, FinancialMetricRecord


# ───────────────────────── shared fixtures ─────────────────────────


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> FocusReportService:
    """FocusReportService with a known min-confidence so tests can pick
    confidence values above/below the threshold deterministically.

    Also monkeypatches the gemini_api_key to a syntactically valid shape
    so GeminiResponsesClient.__init__ does not fail on key validation.
    """
    _valid_key = "AIzaSy" + "a" * 33  # 39 chars, matches ^AIzaSy[A-Za-z0-9_\-]{33}$
    monkeypatch.setattr(settings, "gemini_api_key", _valid_key)
    monkeypatch.setattr(settings, "financial_extraction_min_confidence", 0.7)
    return FocusReportService()


def _make_broker_dealer(bd_id: int = 1, cik: str = "0000000001", name: str = "TEST BD") -> MagicMock:
    """Shim broker_dealer with only the attributes the extractor reads."""
    bd = MagicMock()
    bd.id = bd_id
    bd.cik = cik
    bd.name = name
    bd.filings_index_url = f"https://data.sec.gov/submissions/CIK{cik.zfill(10)}.json"
    return bd


def _make_pdf_record(bd_id: int, filing_year: int) -> DownloadedPdfRecord:
    return DownloadedPdfRecord(
        bd_id=bd_id,
        filing_year=filing_year,
        report_date=date(filing_year, 12, 31),
        source_filing_url=f"https://www.sec.gov/filing/{bd_id}-{filing_year}.html",
        source_pdf_url=f"https://www.sec.gov/filing/{bd_id}-{filing_year}.pdf",
        local_document_path=f"/tmp/{bd_id}-{filing_year}.pdf",
        bytes_base64="ZmFrZQ==",
    )


def _make_extraction(
    *,
    report_date: str,
    net_capital: float | None = 1_000_000.0,
    confidence: float = 0.95,
    excess_net_capital: float | None = 500_000.0,
    total_assets: float | None = 5_000_000.0,
    required_min_capital: float | None = 250_000.0,
) -> GeminiFinancialExtraction:
    return GeminiFinancialExtraction(
        report_date=report_date,
        net_capital=net_capital,
        excess_net_capital=excess_net_capital,
        total_assets=total_assets,
        required_min_capital=required_min_capital,
        confidence_score=confidence,
        rationale="synthetic",
        evidence_excerpt="synthetic excerpt",
    )


# ─────────────────── 1. Call-site swap ───────────────────


class TestCallSiteSwap:
    """The multi-year extractor returns an iterable of per-year records;
    each item must land as a distinct FinancialMetricRecord. When a
    single PDF yields three fiscal years, three rows are appended."""

    @pytest.mark.asyncio
    async def test_three_year_array_lands_three_distinct_records(
        self, service: FocusReportService
    ) -> None:
        bd = _make_broker_dealer(bd_id=42)
        pdf = _make_pdf_record(bd_id=42, filing_year=2026)

        service.downloader.download_recent_x17a5_pdfs = AsyncMock(return_value=[pdf])
        service.gemini_client.extract_multi_year_financial_data = AsyncMock(
            return_value=[
                _make_extraction(report_date="2025-12-31", net_capital=100.0),
                _make_extraction(report_date="2024-12-31", net_capital=90.0),
                _make_extraction(report_date="2023-12-31", net_capital=80.0),
            ]
        )

        result = await service._extract_live_records_from_pdfs([bd])

        assert len(result.records) == 3
        report_dates = sorted(r.report_date for r in result.records)
        assert report_dates == [date(2023, 12, 31), date(2024, 12, 31), date(2025, 12, 31)]
        # One firm attempted, zero skips — counter sanity.
        assert result.skipped_no_url == 0
        assert result.skipped_no_pdf == 0
        assert result.skipped_extraction_error == 0
        assert result.skipped_low_confidence == 0

    @pytest.mark.asyncio
    async def test_multi_year_call_site_invoked_not_single_year(
        self, service: FocusReportService
    ) -> None:
        """Regression guard: a refactor that accidentally flipped back to
        extract_financial_data would fail this test because the single-
        year mock would raise, while the multi-year mock must be awaited
        and produce the single-row result below."""
        bd = _make_broker_dealer(bd_id=99)
        pdf = _make_pdf_record(bd_id=99, filing_year=2026)

        service.downloader.download_recent_x17a5_pdfs = AsyncMock(return_value=[pdf])

        multi_year_mock = AsyncMock(
            return_value=[_make_extraction(report_date="2025-12-31")]
        )
        service.gemini_client.extract_multi_year_financial_data = multi_year_mock
        # If the orchestrator regressed to the single-year call, this
        # mock would be consulted instead. It deliberately raises so the
        # test fails loudly rather than silently passing.
        service.gemini_client.extract_financial_data = AsyncMock(
            side_effect=AssertionError("single-year extractor must not be called"),
        )

        await service._extract_live_records_from_pdfs([bd])

        multi_year_mock.assert_awaited()


# ─────────────────── 2. Year-level failure isolation ───────────────────


class TestYearLevelFailureIsolation:
    """A per-year failure (low confidence, null net_capital, unparseable
    date) skips that year without affecting its siblings. Skip counters
    increment at year-grain."""

    @pytest.mark.asyncio
    async def test_low_confidence_year_persists_tagged_siblings_preserved(
        self, service: FocusReportService
    ) -> None:
        """Under Fix G, a below-threshold year is PERSISTED tagged as
        'needs_review' rather than dropped. Siblings at parsed confidence
        are unaffected. Counter moves from skipped_low_confidence (year
        couldn't persist) to needs_review_count (year persisted tagged)."""
        bd = _make_broker_dealer(bd_id=7)
        pdf = _make_pdf_record(bd_id=7, filing_year=2026)

        service.downloader.download_recent_x17a5_pdfs = AsyncMock(return_value=[pdf])
        service.gemini_client.extract_multi_year_financial_data = AsyncMock(
            return_value=[
                _make_extraction(report_date="2025-12-31", confidence=0.95),
                _make_extraction(report_date="2024-12-31", confidence=0.4),  # below 0.7
                _make_extraction(report_date="2023-12-31", confidence=0.9),
            ]
        )

        result = await service._extract_live_records_from_pdfs([bd])

        assert len(result.records) == 3
        surviving = sorted(r.report_date for r in result.records)
        assert surviving == [date(2023, 12, 31), date(2024, 12, 31), date(2025, 12, 31)]
        # The low-confidence sibling is the only needs_review row.
        needs_review_dates = sorted(
            r.report_date for r in result.records if r.extraction_status == "needs_review"
        )
        assert needs_review_dates == [date(2024, 12, 31)]
        assert result.needs_review_count == 1
        # Unpersistable-year counter stays zero — the row persisted, tagged.
        assert result.skipped_low_confidence == 0
        assert result.skipped_extraction_error == 0

    @pytest.mark.asyncio
    async def test_null_net_capital_counts_as_low_confidence(
        self, service: FocusReportService
    ) -> None:
        """The confidence gate is `net_capital is None OR confidence <
        threshold`. A null net_capital with high confidence still fails."""
        bd = _make_broker_dealer(bd_id=8)
        pdf = _make_pdf_record(bd_id=8, filing_year=2026)

        service.downloader.download_recent_x17a5_pdfs = AsyncMock(return_value=[pdf])
        service.gemini_client.extract_multi_year_financial_data = AsyncMock(
            return_value=[
                _make_extraction(report_date="2025-12-31", net_capital=999.0),
                _make_extraction(report_date="2024-12-31", net_capital=None, confidence=0.99),
            ]
        )

        result = await service._extract_live_records_from_pdfs([bd])

        assert len(result.records) == 1
        assert result.records[0].report_date == date(2025, 12, 31)
        assert result.skipped_low_confidence == 1

    @pytest.mark.asyncio
    async def test_extractor_raises_increments_per_pdf_error_counter(
        self, service: FocusReportService
    ) -> None:
        bd = _make_broker_dealer(bd_id=12)
        pdf_a = _make_pdf_record(bd_id=12, filing_year=2026)
        pdf_b = _make_pdf_record(bd_id=12, filing_year=2024)

        service.downloader.download_recent_x17a5_pdfs = AsyncMock(return_value=[pdf_a, pdf_b])
        service.gemini_client.extract_multi_year_financial_data = AsyncMock(
            side_effect=[
                GeminiExtractionError("synthetic extraction failure"),
                [_make_extraction(report_date="2023-12-31")],
            ]
        )

        result = await service._extract_live_records_from_pdfs([bd])

        assert len(result.records) == 1
        assert result.records[0].report_date == date(2023, 12, 31)
        # One PDF attempt raised — counter bumped once at per-PDF grain.
        assert result.skipped_extraction_error == 1

    @pytest.mark.asyncio
    async def test_extractor_returns_empty_list_bumps_extraction_error(
        self, service: FocusReportService
    ) -> None:
        bd = _make_broker_dealer(bd_id=13)
        pdf = _make_pdf_record(bd_id=13, filing_year=2026)

        service.downloader.download_recent_x17a5_pdfs = AsyncMock(return_value=[pdf])
        service.gemini_client.extract_multi_year_financial_data = AsyncMock(return_value=[])

        result = await service._extract_live_records_from_pdfs([bd])

        assert len(result.records) == 0
        assert result.skipped_extraction_error == 1


# ─────────────────── 3. Counter sum invariant ───────────────────


class TestCounterSumInvariant:
    """The six counters span the unit-of-work attempted across a run.
    records list (parsed + needs_review) is row-grain;
    skipped_no_url / skipped_no_pdf are firm-grain;
    skipped_low_confidence and skipped_extraction_error are year-grain;
    needs_review_count is the year-grain subset of records.
    Invariant: len(records) + skipped_no_url + skipped_no_pdf +
    skipped_extraction_error + skipped_low_confidence == total units of work.
    Equivalently: (parsed rows) + needs_review_count + skipped_* == total."""

    @pytest.mark.asyncio
    async def test_mixed_outcomes_sum_to_total_unit_of_work(
        self, service: FocusReportService
    ) -> None:
        """Synthetic multi-firm run with one of each outcome class:

        - Firm A: 2 good years -> records +2 (both parsed).
        - Firm B: 1 good year + 1 low-confidence -> records +2
          (1 parsed + 1 needs_review; low-conf now persists tagged).
        - Firm C: extractor raises on the PDF -> extraction_error +1.
        - Firm D: no filings_index_url -> no_url +1.

        Total unit-of-work attempted: 4 (records) + 1 (no_url) + 1
        (extraction_error) + 0 (low_confidence) == 6.
        Equivalent breakdown: 3 parsed + 1 needs_review + 1 no_url +
        1 extraction_error + 0 low_confidence == 6.
        """
        firm_a = _make_broker_dealer(bd_id=100, cik="0000000100", name="FIRM A")
        firm_b = _make_broker_dealer(bd_id=200, cik="0000000200", name="FIRM B")
        firm_c = _make_broker_dealer(bd_id=300, cik="0000000300", name="FIRM C")
        firm_d = _make_broker_dealer(bd_id=400, cik="0000000400", name="FIRM D")
        firm_d.filings_index_url = None  # falls into the no_url bucket

        pdf_a = _make_pdf_record(bd_id=100, filing_year=2026)
        pdf_b = _make_pdf_record(bd_id=200, filing_year=2026)
        pdf_c = _make_pdf_record(bd_id=300, filing_year=2026)

        def _download_side_effect(bd, count: int = 2):  # type: ignore[no-untyped-def]
            if bd.id == 100:
                return [pdf_a]
            if bd.id == 200:
                return [pdf_b]
            if bd.id == 300:
                return [pdf_c]
            return []

        service.downloader.download_recent_x17a5_pdfs = AsyncMock(side_effect=_download_side_effect)

        # Dispatch by order of invocation: the extractor is called once
        # per PDF, in the order firms are enumerated (A, B, C).
        service.gemini_client.extract_multi_year_financial_data = AsyncMock(
            side_effect=[
                # Firm A: two good years
                [
                    _make_extraction(report_date="2025-12-31"),
                    _make_extraction(report_date="2024-12-31"),
                ],
                # Firm B: one good year + one low-confidence
                [
                    _make_extraction(report_date="2025-12-31"),
                    _make_extraction(report_date="2024-12-31", confidence=0.2),
                ],
                # Firm C: extractor raises
                GeminiExtractionError("synthetic failure"),
            ]
        )

        result = await service._extract_live_records_from_pdfs([firm_a, firm_b, firm_c, firm_d])

        records_count = len(result.records)
        skip_sum = (
            result.skipped_no_url
            + result.skipped_no_pdf
            + result.skipped_extraction_error
            + result.skipped_low_confidence
        )
        total_unit_of_work = records_count + skip_sum

        # Firm B's low-conf year now persists tagged as needs_review
        # instead of bumping skipped_low_confidence. records count goes
        # from 3 to 4; low_confidence from 1 to 0; needs_review from 0
        # to 1. Total unit-of-work attempted stays 6.
        assert records_count == 4
        parsed_count = sum(
            1 for r in result.records if r.extraction_status == "parsed"
        )
        assert parsed_count == 3
        assert result.needs_review_count == 1
        assert result.skipped_no_url == 1
        assert result.skipped_no_pdf == 0
        assert result.skipped_extraction_error == 1
        assert result.skipped_low_confidence == 0
        # Invariant: six counters span the run; nothing disappears.
        assert total_unit_of_work == 6


# ─────────────────── 4. Re-run idempotency through narrowed DELETE ───────────────────


def _build_table(metadata: MetaData, *, with_constraint: bool) -> Table:
    """Shim of financial_metrics matching the 2C-schema migration."""
    args: list = [
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("bd_id", Integer, nullable=False),
        Column("report_date", Date, nullable=False),
        Column("net_capital", Numeric(18, 2), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
    ]
    if with_constraint:
        args.append(
            UniqueConstraint(
                "bd_id", "report_date", name="uq_financial_metrics_bd_report_date"
            )
        )
    return Table("financial_metrics", metadata, *args)


@pytest.fixture
def sqlite_engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    yield engine
    engine.dispose()


class TestNarrowedDeleteIdempotency:
    """End-to-end: running the narrowed-DELETE-then-INSERT persist path
    twice against the same multi-year record set preserves row count and
    overwrites values at the same (bd_id, report_date)."""

    def test_second_run_same_records_preserves_row_count(self, sqlite_engine) -> None:
        metadata = MetaData()
        table = _build_table(metadata, with_constraint=True)
        metadata.create_all(sqlite_engine)

        bd_id = 55
        records = [
            FinancialMetricRecord(
                bd_id=bd_id, report_date=date(2025, 12, 31),
                net_capital=100.0, excess_net_capital=10.0, total_assets=500.0,
                required_min_capital=50.0, source_filing_url=None,
            ),
            FinancialMetricRecord(
                bd_id=bd_id, report_date=date(2024, 12, 31),
                net_capital=90.0, excess_net_capital=9.0, total_assets=450.0,
                required_min_capital=45.0, source_filing_url=None,
            ),
            FinancialMetricRecord(
                bd_id=bd_id, report_date=date(2023, 12, 31),
                net_capital=80.0, excess_net_capital=8.0, total_assets=400.0,
                required_min_capital=40.0, source_filing_url=None,
            ),
        ]

        def _persist(records_to_write: list[FinancialMetricRecord]) -> None:
            """Emulate focus_reports.load_financial_metrics' narrowed
            DELETE-then-INSERT block."""
            target_pairs = sorted({(r.bd_id, r.report_date) for r in records_to_write})
            now = datetime(2026, 4, 23, 12, 0, tzinfo=timezone.utc)
            with sqlite_engine.begin() as conn:
                if target_pairs:
                    conn.execute(
                        delete(table).where(
                            tuple_(table.c.bd_id, table.c.report_date).in_(target_pairs)
                        )
                    )
                conn.execute(
                    insert(table),
                    [
                        {
                            "bd_id": r.bd_id,
                            "report_date": r.report_date,
                            "net_capital": r.net_capital,
                            "created_at": now,
                        }
                        for r in records_to_write
                    ],
                )

        # Run 1: seed three rows.
        _persist(records)
        with sqlite_engine.connect() as conn:
            first_count = conn.execute(select(table)).all()
        assert len(first_count) == 3

        # Run 2: same records, updated net_capital values. Row count
        # must not grow; values must replace.
        updated = [
            FinancialMetricRecord(
                bd_id=bd_id, report_date=date(2025, 12, 31),
                net_capital=105.0, excess_net_capital=10.0, total_assets=500.0,
                required_min_capital=50.0, source_filing_url=None,
            ),
            FinancialMetricRecord(
                bd_id=bd_id, report_date=date(2024, 12, 31),
                net_capital=95.0, excess_net_capital=9.0, total_assets=450.0,
                required_min_capital=45.0, source_filing_url=None,
            ),
            FinancialMetricRecord(
                bd_id=bd_id, report_date=date(2023, 12, 31),
                net_capital=85.0, excess_net_capital=8.0, total_assets=400.0,
                required_min_capital=40.0, source_filing_url=None,
            ),
        ]
        _persist(updated)

        with sqlite_engine.connect() as conn:
            rows = conn.execute(
                select(table).order_by(table.c.report_date.desc())
            ).all()

        assert len(rows) == 3
        by_date = {row.report_date: row.net_capital for row in rows}
        assert by_date[date(2025, 12, 31)] == 105
        assert by_date[date(2024, 12, 31)] == 95
        assert by_date[date(2023, 12, 31)] == 85

    def test_narrowed_delete_preserves_prior_year_when_current_run_drops_it(
        self, sqlite_engine
    ) -> None:
        """Hypothetical trap from the audit: a run that extracts only
        year N (e.g. year N-1 aged out of EDGAR's recent window) would
        previously wipe the prior-year row alongside the current-year
        row because the DELETE was keyed on bd_id alone. The narrowed
        DELETE preserves the prior-year row."""
        metadata = MetaData()
        table = _build_table(metadata, with_constraint=True)
        metadata.create_all(sqlite_engine)

        bd_id = 66
        now = datetime(2026, 4, 23, 12, 0, tzinfo=timezone.utc)

        # Seed two historical rows from a prior multi-year run.
        with sqlite_engine.begin() as conn:
            conn.execute(
                insert(table),
                [
                    {"bd_id": bd_id, "report_date": date(2025, 12, 31),
                     "net_capital": 100, "created_at": now},
                    {"bd_id": bd_id, "report_date": date(2024, 12, 31),
                     "net_capital": 90, "created_at": now},
                ],
            )

        # Current run extracts only year N (2025); year N-1 (2024) must
        # survive because it's outside the narrowed DELETE scope.
        current_run_records = [
            FinancialMetricRecord(
                bd_id=bd_id, report_date=date(2025, 12, 31),
                net_capital=105.0, excess_net_capital=None, total_assets=None,
                required_min_capital=None, source_filing_url=None,
            ),
        ]
        target_pairs = sorted({(r.bd_id, r.report_date) for r in current_run_records})
        with sqlite_engine.begin() as conn:
            conn.execute(
                delete(table).where(
                    tuple_(table.c.bd_id, table.c.report_date).in_(target_pairs)
                )
            )
            conn.execute(
                insert(table),
                [
                    {"bd_id": r.bd_id, "report_date": r.report_date,
                     "net_capital": r.net_capital, "created_at": now}
                    for r in current_run_records
                ],
            )

        with sqlite_engine.connect() as conn:
            rows = conn.execute(select(table).order_by(table.c.report_date)).all()

        assert len(rows) == 2
        by_date = {row.report_date: row.net_capital for row in rows}
        assert by_date[date(2024, 12, 31)] == 90  # preserved
        assert by_date[date(2025, 12, 31)] == 105  # overwritten


# ─────────────────── 5. Needs-review preservation ───────────────────


class TestNeedsReviewPreservation:
    """Fix G contract: low-confidence per-year rows are persisted tagged
    'needs_review' (not dropped), so the review queue has something to
    surface. The signal stays observable via needs_review_count in
    pipeline_run.notes and the extraction_status column on the row itself.

    These tests lock in that contract at the per-year grain."""

    @pytest.mark.asyncio
    async def test_low_confidence_year_is_persisted_as_needs_review(
        self, service: FocusReportService
    ) -> None:
        bd = _make_broker_dealer(bd_id=77)
        pdf = _make_pdf_record(bd_id=77, filing_year=2026)

        service.downloader.download_recent_x17a5_pdfs = AsyncMock(return_value=[pdf])
        service.gemini_client.extract_multi_year_financial_data = AsyncMock(
            return_value=[
                _make_extraction(report_date="2025-12-31", confidence=0.95),
                _make_extraction(report_date="2024-12-31", confidence=0.3),  # below 0.7
            ]
        )

        result = await service._extract_live_records_from_pdfs([bd])

        by_date = {r.report_date: r for r in result.records}
        # Contract: both years persist; the low-conf row carries the tag.
        assert set(by_date.keys()) == {date(2025, 12, 31), date(2024, 12, 31)}
        assert by_date[date(2025, 12, 31)].extraction_status == "parsed"
        assert by_date[date(2024, 12, 31)].extraction_status == "needs_review"
        assert result.needs_review_count == 1
        # Counter for unpersistable rows stays zero — we persisted, tagged.
        assert result.skipped_low_confidence == 0

    @pytest.mark.asyncio
    async def test_low_confidence_does_not_touch_extraction_error_bucket(
        self, service: FocusReportService
    ) -> None:
        """Orthogonality: a confidence failure is distinct from an
        extraction failure. Routing low-confidence rows through
        skipped_extraction_error would obscure how much is "bad data"
        vs "bad extraction" in downstream audit queries. Under Fix G, the
        signal lives in needs_review_count, not a skip counter."""
        bd = _make_broker_dealer(bd_id=78)
        pdf = _make_pdf_record(bd_id=78, filing_year=2026)

        service.downloader.download_recent_x17a5_pdfs = AsyncMock(return_value=[pdf])
        service.gemini_client.extract_multi_year_financial_data = AsyncMock(
            return_value=[
                _make_extraction(report_date="2025-12-31", confidence=0.3),
            ]
        )

        result = await service._extract_live_records_from_pdfs([bd])

        assert result.needs_review_count == 1
        assert result.skipped_low_confidence == 0
        assert result.skipped_extraction_error == 0
