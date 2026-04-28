"""Sprint 2 task #14 — re-extract Net Capital must persist to the BD profile.

Covers the contract added by ``FocusCeoExtractionService._persist_net_capital``
and ``_refresh_bd_rollup``:

  1. Happy path. A high-confidence extraction with a parseable report_date is
     persisted to ``financial_metrics`` tagged ``parsed`` and the BD rollup
     fields (``latest_net_capital`` etc.) are refreshed from the latest
     parsed metric for the firm.
  2. Low-confidence rows persist tagged ``needs_review`` and do NOT promote
     to the BD rollup. This preserves the review-queue semantics financial
     extraction has carried since Phase 2D / Fix G.
  3. Idempotency. Calling the helper twice for the same ``(bd_id, report_date)``
     pair updates the existing row in place rather than inserting a duplicate
     (would otherwise trip ``uq_financial_metrics_bd_report_date``).
  4. Missing data. None ``net_capital`` skips persistence entirely; a missing
     ``report_date`` with no BD fallback also skips, since both columns are
     NOT NULL on ``financial_metrics``.
  5. Fallback report_date. When extraction omits ``report_date`` but the BD
     carries ``last_audit_report_date`` / ``last_filing_date``, the helper
     uses that fallback so on-demand re-extracts still persist.
  6. Provider-error guard. When the extraction path returns the existing
     ``error`` status (Gemini config / extraction failure), the persistence
     helper is not called and the DB is untouched.
  7. End-to-end wiring. ``extract()`` reaches the persistence helper with
     the values the prompt returned — locks the call site so a future
     refactor can't silently re-introduce the original bug.

The tests use a hand-rolled ``AsyncSession`` stand-in so they don't need a
live Postgres or aiosqlite engine — the upsert helper makes deterministic
SELECTs the fake session can stage by call order.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.models.broker_dealer import BrokerDealer
from app.models.financial_metric import FinancialMetric
from app.services.extraction_status import STATUS_NEEDS_REVIEW, STATUS_PARSED
from app.services.focus_ceo_extraction import FocusCeoExtractionService
from app.services.gemini_responses import GeminiFocusCeoExtraction
from app.services.service_models import DownloadedPdfRecord


# ───────────────────────── shared fakes ─────────────────────────


class _FakeResult:
    """Minimal Result stand-in supporting both ``scalar_one_or_none`` (for the
    upsert lookup) and ``scalars().all()`` / iterating (for the rollup query)."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalar_one_or_none(self) -> Any | None:
        if not self._rows:
            return None
        return self._rows[0]

    def scalars(self) -> "_FakeResult":
        return self

    def all(self) -> list[Any]:
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)


class _FakeSession:
    """Tiny AsyncSession stand-in. ``execute()`` returns staged results in
    order; ``add()`` records the entity for later assertion; ``flush()`` is a
    no-op so the rollup step can read the BD attributes the helper set inline."""

    def __init__(self, staged_results: list[list[Any]] | None = None) -> None:
        self._staged = list(staged_results or [])
        self.added: list[Any] = []
        self.execute_count = 0
        self.flush_count = 0

    async def execute(self, _stmt: Any) -> _FakeResult:
        if not self._staged:
            rows: list[Any] = []
        else:
            rows = self._staged.pop(0)
        self.execute_count += 1
        return _FakeResult(rows)

    def add(self, item: Any) -> None:
        self.added.append(item)

    async def flush(self) -> None:
        self.flush_count += 1


def _make_broker_dealer(
    bd_id: int = 42,
    *,
    last_audit_report_date: date | None = None,
    last_filing_date: date | None = None,
    latest_net_capital: float | None = None,
) -> BrokerDealer:
    bd = BrokerDealer()
    bd.id = bd_id
    bd.name = f"Broker {bd_id}"
    bd.cik = "0000000042"
    bd.filings_index_url = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=42"
    bd.last_audit_report_date = last_audit_report_date
    bd.last_filing_date = last_filing_date
    bd.latest_net_capital = latest_net_capital
    bd.latest_excess_net_capital = None
    bd.latest_total_assets = None
    bd.required_min_capital = None
    bd.yoy_growth = None
    bd.health_status = None
    return bd


def _make_metric(
    *,
    bd_id: int = 42,
    report_date: date = date(2025, 12, 31),
    net_capital: float = 1_000_000.0,
    excess_net_capital: float | None = 500_000.0,
    required_min_capital: float | None = 250_000.0,
    extraction_status: str = STATUS_PARSED,
) -> FinancialMetric:
    metric = FinancialMetric()
    metric.bd_id = bd_id
    metric.report_date = report_date
    metric.net_capital = net_capital
    metric.excess_net_capital = excess_net_capital
    metric.total_assets = None
    metric.required_min_capital = required_min_capital
    metric.source_filing_url = None
    metric.extraction_status = extraction_status
    return metric


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> FocusCeoExtractionService:
    """Construct the service with a known min-confidence and a syntactically
    valid Gemini key so ``GeminiResponsesClient.__init__`` does not raise."""
    monkeypatch.setattr(settings, "gemini_api_key", "AIzaSy" + "a" * 33)
    monkeypatch.setattr(settings, "financial_extraction_min_confidence", 0.65)
    return FocusCeoExtractionService()


# ─────────────── 1. Happy path: persists + refreshes rollup ───────────────


@pytest.mark.asyncio
async def test_high_confidence_persists_parsed_and_refreshes_bd_rollup(
    service: FocusCeoExtractionService,
) -> None:
    """High-confidence extraction with a parseable report_date lands as a
    new ``parsed`` row in ``financial_metrics`` and refreshes the BD's
    denormalized rollup fields. Without this fix, the firm-detail page
    reverts to the stale pre-re-extraction value on reload."""
    broker_dealer = _make_broker_dealer(latest_net_capital=500_000.0)

    inserted_metric = _make_metric(
        net_capital=2_500_000.0,
        excess_net_capital=None,
        required_min_capital=None,
    )
    # First execute() = upsert lookup (no existing row); second = rollup SELECT.
    session = _FakeSession(staged_results=[[], [inserted_metric]])

    await service._persist_net_capital(
        session,  # type: ignore[arg-type]
        broker_dealer=broker_dealer,
        net_capital=2_500_000.0,
        excess_net_capital=None,
        total_assets=None,
        required_min_capital=None,
        extracted_report_date=date(2025, 12, 31),
        confidence_score=0.92,
        source_filing_url="https://www.sec.gov/Archives/test.pdf",
    )

    assert len(session.added) == 1, "exactly one financial_metrics row should be inserted"
    persisted = session.added[0]
    assert isinstance(persisted, FinancialMetric)
    assert persisted.bd_id == broker_dealer.id
    assert persisted.report_date == date(2025, 12, 31)
    assert persisted.net_capital == 2_500_000.0
    assert persisted.extraction_status == STATUS_PARSED
    assert persisted.source_filing_url == "https://www.sec.gov/Archives/test.pdf"

    # Rollup ran — the BD cache moved off the stale 500k value.
    assert broker_dealer.latest_net_capital == 2_500_000.0


# ─────────────── 2. Low-confidence persists tagged, no rollup ───────────────


@pytest.mark.asyncio
async def test_low_confidence_persists_needs_review_and_skips_rollup(
    service: FocusCeoExtractionService,
) -> None:
    """Below-threshold confidence lands tagged ``needs_review`` and does NOT
    promote to ``broker_dealers.latest_net_capital``. This is the existing
    review-queue rule from Phase 2D / Fix G — corrupting the master-list
    rollup with low-confidence values would re-introduce the bug."""
    broker_dealer = _make_broker_dealer(latest_net_capital=500_000.0)

    # Only one execute() expected — the upsert lookup. The rollup query must
    # NOT fire because needs_review rows don't qualify.
    session = _FakeSession(staged_results=[[]])

    await service._persist_net_capital(
        session,  # type: ignore[arg-type]
        broker_dealer=broker_dealer,
        net_capital=2_500_000.0,
        excess_net_capital=None,
        total_assets=None,
        required_min_capital=None,
        extracted_report_date=date(2025, 12, 31),
        confidence_score=0.30,  # well below the 0.65 threshold
        source_filing_url=None,
    )

    assert len(session.added) == 1
    persisted = session.added[0]
    assert persisted.extraction_status == STATUS_NEEDS_REVIEW
    # Rollup did NOT run — stale cache value preserved.
    assert broker_dealer.latest_net_capital == 500_000.0
    # Confirm we only consulted the DB once (the upsert lookup).
    assert session.execute_count == 1


# ─────────────── 3. Idempotency ───────────────


@pytest.mark.asyncio
async def test_idempotent_update_replaces_existing_metric_row(
    service: FocusCeoExtractionService,
) -> None:
    """A second re-extract for the same (bd_id, report_date) updates the
    existing FinancialMetric row in place. Must not insert a duplicate
    (would trip uq_financial_metrics_bd_report_date) and must rotate the
    BD rollup to the new value."""
    broker_dealer = _make_broker_dealer()

    existing = _make_metric(net_capital=1_000_000.0, extraction_status=STATUS_PARSED)

    # Upsert lookup returns the existing row; rollup returns it post-mutation.
    session = _FakeSession(staged_results=[[existing], [existing]])

    await service._persist_net_capital(
        session,  # type: ignore[arg-type]
        broker_dealer=broker_dealer,
        net_capital=3_000_000.0,
        excess_net_capital=None,
        total_assets=None,
        required_min_capital=None,
        extracted_report_date=date(2025, 12, 31),
        confidence_score=0.90,
        source_filing_url="https://www.sec.gov/Archives/refresh.pdf",
    )

    assert session.added == [], "must not insert when an existing row matches"
    assert existing.net_capital == 3_000_000.0
    assert existing.extraction_status == STATUS_PARSED
    assert existing.source_filing_url == "https://www.sec.gov/Archives/refresh.pdf"
    assert broker_dealer.latest_net_capital == 3_000_000.0


# ─────────────── 4. Missing data: net_capital + no report_date ───────────────


@pytest.mark.asyncio
async def test_none_net_capital_is_a_noop(
    service: FocusCeoExtractionService,
) -> None:
    """No net_capital -> no DB write. ``financial_metrics.net_capital`` is
    NOT NULL so an attempt to persist would crash at flush; the helper must
    short-circuit before any DB call."""
    broker_dealer = _make_broker_dealer(last_audit_report_date=date(2025, 12, 31))
    session = _FakeSession(staged_results=[])

    await service._persist_net_capital(
        session,  # type: ignore[arg-type]
        broker_dealer=broker_dealer,
        net_capital=None,
        excess_net_capital=None,
        total_assets=None,
        required_min_capital=None,
        extracted_report_date=None,
        confidence_score=0.95,
        source_filing_url=None,
    )

    assert session.execute_count == 0
    assert session.added == []


@pytest.mark.asyncio
async def test_missing_report_date_without_fallback_is_a_noop(
    service: FocusCeoExtractionService,
) -> None:
    """``financial_metrics.report_date`` is NOT NULL. If the extraction did
    not surface a date AND the BD has no audit/filing date to fall back on,
    skip the persistence rather than fabricate a date."""
    broker_dealer = _make_broker_dealer(
        last_audit_report_date=None,
        last_filing_date=None,
    )
    session = _FakeSession(staged_results=[])

    await service._persist_net_capital(
        session,  # type: ignore[arg-type]
        broker_dealer=broker_dealer,
        net_capital=2_500_000.0,
        excess_net_capital=None,
        total_assets=None,
        required_min_capital=None,
        extracted_report_date=None,
        confidence_score=0.95,
        source_filing_url=None,
    )

    assert session.execute_count == 0
    assert session.added == []


# ─────────────── 5. Fallback report_date ───────────────


@pytest.mark.asyncio
async def test_fallback_report_date_uses_bd_last_audit_date(
    service: FocusCeoExtractionService,
) -> None:
    """pdfplumber rarely surfaces a parseable report_date. When extraction
    omits it, the helper uses ``last_audit_report_date`` so the on-demand
    re-extract still persists."""
    fallback = date(2024, 12, 31)
    broker_dealer = _make_broker_dealer(last_audit_report_date=fallback)
    inserted = _make_metric(report_date=fallback, net_capital=1_750_000.0)
    session = _FakeSession(staged_results=[[], [inserted]])

    await service._persist_net_capital(
        session,  # type: ignore[arg-type]
        broker_dealer=broker_dealer,
        net_capital=1_750_000.0,
        excess_net_capital=None,
        total_assets=None,
        required_min_capital=None,
        extracted_report_date=None,
        confidence_score=0.85,
        source_filing_url=None,
    )

    assert len(session.added) == 1
    assert session.added[0].report_date == fallback
    assert broker_dealer.latest_net_capital == 1_750_000.0


# ─────────────── 6. Provider-error path: helper never invoked ───────────────


@pytest.mark.asyncio
async def test_provider_error_path_does_not_persist(
    service: FocusCeoExtractionService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Gemini extraction error returns early via the existing ``error``
    status without reaching the persistence helper. The DB stays untouched
    even though net_capital is unknown."""
    from app.services.gemini_responses import GeminiExtractionError

    broker_dealer = _make_broker_dealer()
    pdf_record = DownloadedPdfRecord(
        bd_id=broker_dealer.id,
        filing_year=2025,
        report_date=date(2025, 12, 31),
        source_filing_url="https://www.sec.gov/test-index.htm",
        source_pdf_url="https://www.sec.gov/test.pdf",
        local_document_path=None,  # forces the Gemini path
        bytes_base64="ZmFrZQ==",
    )

    service.downloader.download_latest_x17a5_pdf = AsyncMock(return_value=pdf_record)  # type: ignore[assignment]
    service.gemini.extract_focus_ceo_data = AsyncMock(  # type: ignore[assignment]
        side_effect=GeminiExtractionError("boom"),
    )
    persist_spy = AsyncMock()
    monkeypatch.setattr(service, "_persist_net_capital", persist_spy)

    session = _FakeSession()
    result = await service.extract(session, broker_dealer)  # type: ignore[arg-type]

    assert result.extraction_status == "error"
    assert persist_spy.await_count == 0
    assert session.added == []


# ─────────────── 7. End-to-end: extract() invokes the helper ───────────────


@pytest.mark.asyncio
async def test_extract_calls_persist_helper_on_gemini_success(
    service: FocusCeoExtractionService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Locks the wiring: a successful Gemini extraction reaches the
    persistence helper with the values the prompt returned. Without this
    test, a future refactor that drops the helper call would silently
    re-introduce the original bug."""
    broker_dealer = _make_broker_dealer()
    pdf_record = DownloadedPdfRecord(
        bd_id=broker_dealer.id,
        filing_year=2025,
        report_date=date(2025, 12, 31),
        source_filing_url="https://www.sec.gov/test-index.htm",
        source_pdf_url="https://www.sec.gov/test.pdf",
        local_document_path=None,  # force the Gemini path (skip pdfplumber)
        bytes_base64="ZmFrZQ==",
    )

    service.downloader.download_latest_x17a5_pdf = AsyncMock(return_value=pdf_record)  # type: ignore[assignment]
    service.gemini.extract_focus_ceo_data = AsyncMock(  # type: ignore[assignment]
        return_value=GeminiFocusCeoExtraction(
            ceo_name="Jane Doe",
            ceo_title="Chief Executive Officer",
            ceo_phone=None,
            ceo_email=None,
            net_capital=4_200_000.0,
            report_date="2025-12-31",
            confidence_score=0.91,
            rationale="cover page + computation of net capital schedule",
            evidence_excerpt=None,
        )
    )
    # Avoid hitting the unrelated executive_contacts upsert — it does its own
    # delete()+add() pair we'd otherwise need to stage.
    monkeypatch.setattr(service, "_upsert_focus_contact", AsyncMock())
    persist_spy = AsyncMock()
    monkeypatch.setattr(service, "_persist_net_capital", persist_spy)

    session = _FakeSession()
    result = await service.extract(session, broker_dealer)  # type: ignore[arg-type]

    assert result.net_capital == 4_200_000.0
    assert result.report_date == date(2025, 12, 31)
    persist_spy.assert_awaited_once()
    kwargs = persist_spy.await_args.kwargs
    assert kwargs["net_capital"] == 4_200_000.0
    assert kwargs["extracted_report_date"] == date(2025, 12, 31)
    assert kwargs["confidence_score"] == 0.91
    assert kwargs["source_filing_url"] == "https://www.sec.gov/test.pdf"
