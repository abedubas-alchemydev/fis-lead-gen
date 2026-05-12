from __future__ import annotations

import argparse
import asyncio
import selectors
import sys
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
sys.stderr.reconfigure(line_buffering=True)  # type: ignore[attr-defined]

ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

if sys.platform == "win32" and sys.version_info < (3, 14):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.core.config import settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.services.focus_reports import FocusReportService  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the X-17A-5 -> Gemini financial extraction pipeline. "
            "Defaults to the standard target selector (firms with a "
            "filings index but no rollup data, then refresh tail). "
            "Use --only-null-health to backfill firms whose "
            "health_status is still NULL."
        )
    )
    parser.add_argument(
        "--only-null-health",
        action="store_true",
        help=(
            "Backfill mode: target only broker-dealers whose "
            "health_status is NULL but have a filings_index_url on "
            "file. The default selector is bypassed entirely."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Print the target broker-dealer count (and a sample of "
            "IDs) without invoking Gemini or writing to the database. "
            "Requires --only-null-health."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Cap the target set to the first N broker-dealers (after "
            "the FINANCIAL_PIPELINE_OFFSET setting is applied). Useful "
            "for canary runs. Overrides FINANCIAL_PIPELINE_LIMIT for "
            "this invocation. Currently honored only with "
            "--only-null-health."
        ),
    )
    return parser.parse_args()


async def _run_dry_run(*, limit: int | None) -> None:
    """Print ``target_null_health=X, would_attempt=Y`` plus a sample of
    BD ids without invoking Gemini or touching write paths. The selector
    SELECT runs against the read DB, but no INSERT / UPDATE / DELETE is
    issued and no PipelineRun row is created — safe to run before
    committing budget to a real backfill."""
    service = FocusReportService()
    async with SessionLocal() as db:
        universe = await service._select_null_health_targets(db)

    target_null_health = len(universe)

    sliced = list(universe)
    if settings.financial_pipeline_offset > 0:
        sliced = sliced[settings.financial_pipeline_offset :]
    effective_limit = limit if limit is not None else settings.financial_pipeline_limit
    if effective_limit:
        sliced = sliced[:effective_limit]

    would_attempt = len(sliced)
    sample_ids = [bd.id for bd in sliced[:25]]

    print(
        f"Backfill dry-run: target_null_health={target_null_health}, "
        f"would_attempt={would_attempt}, sample_bd_ids={sample_ids}"
    )


async def _run_null_health_backfill(*, limit: int | None) -> None:
    """Execute the NULL-health backfill end-to-end and print the
    structured ``target_null_health=X, attempted=Y, populated=Z,
    needs_review=W, errors=V`` summary the brief calls for.

    ``populated`` is the count of FinancialMetric rows persisted (parsed
    + needs_review). ``errors`` aggregates every skip bucket because
    each one represents an attempted firm/year that did not land as a
    usable metric — operators care about the failure count, not the
    bucket breakdown, in the one-line summary (the bucket detail is
    preserved verbatim in pipeline_runs.notes for follow-up)."""
    service = FocusReportService()
    async with SessionLocal() as db:
        extraction = await service.load_financial_metrics_for_null_health(db, limit=limit)

    attempted = extraction.target_count
    populated = len(extraction.records)
    needs_review = extraction.needs_review_count
    errors = (
        extraction.skipped_no_url
        + extraction.skipped_no_pdf
        + extraction.skipped_extraction_error
        + extraction.skipped_low_confidence
    )

    # ``target_null_health`` is the operator-facing label for the size
    # of the backfill target set in this run. After offset/limit have
    # been applied this equals ``attempted``; printing both keeps the
    # summary line aligned with the dry-run format and surfaces the
    # match for canary verification.
    print(
        f"Backfill complete: target_null_health={attempted}, "
        f"attempted={attempted}, populated={populated}, "
        f"needs_review={needs_review}, errors={errors}"
    )


async def main() -> None:
    import logging

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    args = _parse_args()

    if args.dry_run and not args.only_null_health:
        raise SystemExit("--dry-run requires --only-null-health.")

    if args.limit is not None and args.limit <= 0:
        raise SystemExit("--limit must be a positive integer.")

    if args.only_null_health:
        if args.dry_run:
            await _run_dry_run(limit=args.limit)
        else:
            await _run_null_health_backfill(limit=args.limit)
        return

    # Default path (unchanged behavior): run the standard selector.
    service = FocusReportService()
    async with SessionLocal() as db:
        count = await service.load_financial_metrics(db)
    print(f"Loaded {count} financial metric rows.")


if __name__ == "__main__":
    if sys.platform == "win32" and sys.version_info >= (3, 14):
        with asyncio.Runner(loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())) as runner:
            runner.run(main())
    else:
        asyncio.run(main())
