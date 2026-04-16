"""Initial data load script.

Populates the broker-dealer database from live FINRA and SEC EDGAR sources.

Usage:
    python -m scripts.initial_load

Environment:
    DATA_SOURCE_MODE must be "live" (default).
    All live API endpoints (FINRA BrokerCheck, SEC EDGAR) must be reachable.

The script:
    1. Harvests active BDs from FINRA BrokerCheck.
    2. Resolves corresponding SEC/EDGAR records via file-number matching.
    3. Merges the two datasets with full QA reporting.
    4. Upserts the merged set into the broker_dealers table.
    5. Optionally runs FOCUS import, clearing pipeline, and filing monitor.
    6. Recalculates lead scores.
    7. Prints a complete ingestion QA report.
"""

from __future__ import annotations

import asyncio
import selectors
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

if sys.platform == "win32" and sys.version_info < (3, 14):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.db.session import SessionLocal  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.services.broker_dealers import BrokerDealerRepository  # noqa: E402
from app.services.data_merge import BrokerDealerMergeService  # noqa: E402
from app.services.edgar import EdgarService  # noqa: E402
from app.services.filing_monitor import FilingMonitorService  # noqa: E402
from app.services.finra import FinraService  # noqa: E402
from app.services.focus_reports import FocusReportService  # noqa: E402
from app.services.classification import apply_classification_to_all  # noqa: E402
from app.services.pipeline import ClearingPipelineService  # noqa: E402


def _print_section(title: str) -> None:
    """Print a visible section header."""
    print(f"\n{'─' * 55}")
    print(f"  {title}")
    print(f"{'─' * 55}")


async def main() -> None:
    edgar_service = EdgarService()
    finra_service = FinraService()
    merge_service = BrokerDealerMergeService()
    repository = BrokerDealerRepository()
    focus_report_service = FocusReportService()
    clearing_pipeline_service = ClearingPipelineService()
    filing_monitor_service = FilingMonitorService()

    # ── Step 1: Harvest FINRA BrokerCheck ──
    _print_section("Step 1: Harvesting active FINRA broker-dealers")
    finra_records = await finra_service.fetch_broker_dealers(limit=settings.initial_load_limit)
    print(f"  Fetched {len(finra_records):,} FINRA records.")

    # ── Step 1b: Enrich with FINRA detail (Stream A: business types, owners, officers) ──
    _print_section("Step 1b: Enriching with FINRA detail reports (Stream A)")
    finra_records = await finra_service.enrich_with_detail(finra_records)
    enriched_count = sum(1 for r in finra_records if r.types_of_business or r.direct_owners)
    print(f"  Enriched {enriched_count:,} / {len(finra_records):,} records with detail data.")

    # ── Step 2: Resolve SEC/EDGAR records ──
    _print_section("Step 2: Resolving SEC/EDGAR records for FINRA firms")
    sec_file_numbers = [r.sec_file_number for r in finra_records if r.sec_file_number]
    print(f"  Submitting {len(sec_file_numbers):,} SEC file numbers for EDGAR resolution...")
    edgar_records = await edgar_service.fetch_records_for_sec_numbers(sec_file_numbers)
    print(f"  Resolved {len(edgar_records):,} EDGAR records.")

    # ── Step 3: Merge with QA reporting ──
    _print_section("Step 3: Merging FINRA + EDGAR datasets")
    merged_records, qa_report = merge_service.merge(edgar_records, finra_records)

    # Print QA report
    print()
    for line in qa_report.summary_lines():
        print(line)

    # Print bad-source rows if any
    if qa_report.bad_source_rows:
        print()
        print("  Bad-source row log:")
        for line in qa_report.bad_source_summary(max_rows=50):
            print(line)

    # ── Validate: safety threshold ──
    if qa_report.output_count < settings.minimum_initial_load_records:
        raise RuntimeError(
            "Initial load aborted: the verified broker-dealer set is smaller than the safety threshold. "
            f"Expected at least {settings.minimum_initial_load_records:,}, got {qa_report.output_count:,}. "
            "Check FINRA/EDGAR API connectivity and review the bad-source row log above."
        )

    # ── Validate: source classification ──
    edgar_only_count = sum(1 for r in merged_records if r.matched_source == "edgar")
    if edgar_only_count > 0:
        raise RuntimeError(
            f"MERGE INVARIANT VIOLATION: {edgar_only_count} rows classified as 'edgar' (only 'both' and 'finra_only' are allowed). "
            "This indicates a bug in the merge pipeline."
        )

    # ── Validate: no duplicates ──
    seen_sec_numbers: set[str | None] = set()
    duplicate_count = 0
    for record in merged_records:
        if record.sec_file_number and record.sec_file_number in seen_sec_numbers:
            duplicate_count += 1
        elif record.sec_file_number:
            seen_sec_numbers.add(record.sec_file_number)
    if duplicate_count > 0:
        raise RuntimeError(
            f"MERGE INVARIANT VIOLATION: {duplicate_count} duplicate SEC file numbers in output. "
            "This indicates a bug in the dedup logic."
        )

    print(f"\n  ✓ All {qa_report.output_count:,} rows pass validation (no edgar-only, no duplicates).")

    # ── Step 4: Upsert to database ──
    _print_section("Step 4: Upserting to database")
    async with SessionLocal() as db:
        count = await repository.replace_dataset(db, merged_records)
        print(f"  Upserted {count:,} broker-dealer records.")

        # ── Step 4b: Apply classification logic gates (Revision 1.2) ──
        _print_section("Step 4b: Applying classification logic gates")
        classified_count = await apply_classification_to_all(db)
        await db.commit()
        print(f"  Classified {classified_count:,} broker-dealers (self-clearing / introducing / niche-restricted).")

        # ── Step 5: Optional enrichment passes ──
        if settings.run_focus_import_on_initial_load:
            _print_section("Step 5a: Loading FOCUS financial metrics")
            financial_count = await focus_report_service.load_financial_metrics(db)
            print(f"  Loaded {financial_count:,} financial metric rows.")
        else:
            financial_count = 0
            print("\n  Skipped FOCUS financial metrics import (disabled in config).")

        clearing_run = None
        if settings.run_clearing_pipeline_on_initial_load:
            _print_section("Step 5b: Running clearing pipeline")
            clearing_run = await clearing_pipeline_service.run(db, trigger_source="initial_load")
            print(
                f"  Pipeline run {clearing_run.id}: "
                f"{clearing_run.success_count} successes, {clearing_run.failure_count} failures."
            )
        else:
            print("\n  Skipped clearing pipeline run (disabled in config).")

        filing_monitor_run = None
        if settings.run_filing_monitor_on_initial_load:
            _print_section("Step 5c: Running filing monitor")
            filing_monitor_run = await filing_monitor_service.run(db, trigger_source="initial_load")
            print(
                f"  Monitor run {filing_monitor_run.id}: "
                f"{filing_monitor_run.success_count} alerts, {filing_monitor_run.failure_count} failures."
            )
        else:
            print("\n  Skipped filing monitor run (disabled in config).")

        # ── Step 6: Recalculate lead scores ──
        _print_section("Step 6: Recalculating lead scores")
        await repository.refresh_lead_scores(db)
        await db.commit()
        print("  Lead scores refreshed and committed.")

    # ── Final summary ──
    _print_section("INITIAL LOAD COMPLETE")
    print(f"  Total broker-dealers in DB:    {count:,}")
    print(f"  Matched (both sources):        {qa_report.matched_both_count:,}")
    print(f"  FINRA-only (justified):        {qa_report.finra_only_count:,}")
    print(f"  Financial metrics loaded:      {financial_count:,}")
    if clearing_run:
        print(f"  Clearing extractions:          {clearing_run.success_count} ok / {clearing_run.failure_count} failed")
    if filing_monitor_run:
        print(f"  Filing alerts:                 {filing_monitor_run.success_count} created")
    print()


if __name__ == "__main__":
    if sys.platform == "win32" and sys.version_info >= (3, 14):
        with asyncio.Runner(loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())) as runner:
            runner.run(main())
    else:
        asyncio.run(main())
