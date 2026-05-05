"""Standalone entrypoint for the ACG ICP lead-scoring service.

Scoring is non-LLM (pure computation over financials, clearing-partner
strings, competitor flags, FINRA flags, etc.) using the weights stored
in the ``scoring_setting`` table. The pipeline normally runs scoring as
a follow-on to refresh; this script is the on-demand path for filling
in ``broker_dealers.lead_priority`` rows the pipeline never scored, or
re-scoring everything after a financial backfill lands.

Usage:
    python -m scripts.run_scoring                 # score lead_priority IS NULL
    python -m scripts.run_scoring --dry-run       # report target set, no writes
    python -m scripts.run_scoring --limit 50      # cap target set (canary)
    python -m scripts.run_scoring --all           # re-score every BD

Cost: ZERO API cost (no LLM, no external HTTP).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import selectors
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
sys.stderr.reconfigure(line_buffering=True)  # type: ignore[attr-defined]

ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

if sys.platform == "win32" and sys.version_info < (3, 14):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.db.session import SessionLocal  # noqa: E402
from app.services.scoring import score_broker_dealers  # noqa: E402

logger = logging.getLogger(__name__)


def _format_sample(sample_ids: tuple[int, ...]) -> str:
    if not sample_ids:
        return "[]"
    return "[" + ", ".join(str(i) for i in sample_ids) + "]"


async def main(*, all_rows: bool, limit: int | None, dry_run: bool) -> None:
    started_at = time.monotonic()
    only_null = not all_rows
    mode = "ALL" if all_rows else "NULL-priority"

    print(
        f"Running scoring (mode={mode}, limit={limit}, dry_run={dry_run})"
    )

    async with SessionLocal() as db:
        summary = await score_broker_dealers(
            db,
            only_null_priority=only_null,
            limit=limit,
            dry_run=dry_run,
        )
        if not dry_run:
            await db.commit()

    elapsed = time.monotonic() - started_at

    target_label = "target_all" if all_rows else "target_null_priority"

    if dry_run:
        print(
            f"{target_label}={summary.target_count}, "
            f"would_score={summary.target_count} "
            f"sample_ids={_format_sample(summary.sample_ids)} "
            f"elapsed={elapsed:.2f}s"
        )
    else:
        print(
            f"{target_label}={summary.target_count}, "
            f"scored={summary.scored}, "
            f"skipped_no_data={summary.skipped_no_data} "
            f"sample_ids={_format_sample(summary.sample_ids)} "
            f"elapsed={elapsed:.2f}s"
        )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the ACG ICP lead-scoring service against broker_dealers. "
            "Defaults to rows where lead_priority IS NULL."
        )
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Re-score every broker dealer regardless of current priority.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap the target set to the first N rows (useful for canary runs).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print target_count and sample IDs without writing to the DB.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    coro = main(all_rows=args.all, limit=args.limit, dry_run=args.dry_run)
    if sys.platform == "win32" and sys.version_info >= (3, 14):
        with asyncio.Runner(
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())
        ) as runner:
            runner.run(coro)
    else:
        asyncio.run(coro)
