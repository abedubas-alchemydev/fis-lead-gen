"""Backfill clearing_classification across all broker_dealers.

Invokes the LLM-based classifier in
``backend/app/services/clearing_classifier.py`` against every firm in
``broker_dealers`` and writes the canonical four-value enum back to
``broker_dealers.clearing_classification``.

Background (2026-04-29): the regex top-level decision was rewritten to
a Gemini-backed prompt under PR #129. The migration in that PR mapped
all legacy values to ``needs_review``; existing firms have not been
re-classified yet because the pipeline hasn't touched them since the
migration. This script is the one-shot backfill that flips the column
to real labels.

Idempotent: re-running converges to the same state. Within each
batch the 10 firms are dispatched concurrently via ``asyncio.gather``
so the wall clock follows Gemini round-trip latency once, not ten
times; a 2-second sleep between batches caps the in-flight load at
the batch size and gives the Gemini API sustained headroom (the
Gemini client itself retries on 429 with exponential backoff; the
batch sleep is belt-and-suspenders for long runs). Progress is
logged after every batch.

Review-queue semantics preserved: results below
``settings.clearing_classification_min_confidence`` (default 0.7), or
the ``unknown`` sentinel, are persisted as ``"needs_review"`` instead
of being promoted to the canonical column.

Usage:
    python -m scripts.run_classifier_backfill              # full run
    python -m scripts.run_classifier_backfill --limit 10   # 10-firm dry-run
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

from sqlalchemy import select  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.broker_dealer import BrokerDealer  # noqa: E402
from app.models.clearing_arrangement import ClearingArrangement  # noqa: E402
from app.services.clearing_classifier import (  # noqa: E402
    CANONICAL_VALUES,
    classify,
)

logger = logging.getLogger(__name__)

BATCH_SIZE = 10
SLEEP_BETWEEN_BATCHES_SECONDS = 2.0


async def _load_latest_focus_text_by_bd(db) -> dict[int, str | None]:
    """One round-trip fetch of the most-recent FOCUS clearing statement per BD.

    Mirrors the join performed by
    ``services.classification.apply_classification_to_all`` so the
    backfill ranks the same source text the live pipeline uses.
    """
    rows = (
        await db.execute(
            select(
                ClearingArrangement.bd_id,
                ClearingArrangement.clearing_statement_text,
            ).order_by(
                ClearingArrangement.filing_year.desc().nullslast(),
                ClearingArrangement.id.desc(),
            )
        )
    ).all()
    latest: dict[int, str | None] = {}
    for bd_id, statement_text in rows:
        # First row per bd_id wins because rows are ordered most-recent-first.
        latest.setdefault(bd_id, statement_text)
    return latest


async def main(limit: int | None = None) -> None:
    threshold = float(settings.clearing_classification_min_confidence)
    started_at = time.monotonic()

    async with SessionLocal() as db:
        bds = (
            await db.execute(select(BrokerDealer).order_by(BrokerDealer.id.asc()))
        ).scalars().all()

        if limit is not None:
            bds = bds[:limit]

        latest_focus_text_by_bd = await _load_latest_focus_text_by_bd(db)

        total = len(bds)
        print(f"Backfilling classifier for {total} firms (threshold={threshold})")
        if limit is not None:
            print(f"  --limit {limit} active (dry-run)")

        processed = 0
        promoted = 0
        needs_review_count = 0
        unchanged = 0
        counts_by_value: dict[str, int] = {}

        for batch_start in range(0, total, BATCH_SIZE):
            batch = bds[batch_start : batch_start + BATCH_SIZE]
            results = await asyncio.gather(
                *(
                    classify(
                        firm_operations_text=bd.firm_operations_text,
                        focus_report_text=latest_focus_text_by_bd.get(bd.id),
                    )
                    for bd in batch
                )
            )

            for bd, result in zip(batch, results):
                if (
                    result.value in CANONICAL_VALUES
                    and result.value != "unknown"
                    and result.confidence >= threshold
                ):
                    new_classification = result.value
                    promoted += 1
                else:
                    new_classification = "needs_review"
                    needs_review_count += 1
                    if result.value not in CANONICAL_VALUES:
                        logger.warning(
                            "Classifier returned non-canonical value '%s' for bd_id=%s; coercing to needs_review",
                            result.value,
                            bd.id,
                        )

                if bd.clearing_classification != new_classification:
                    bd.clearing_classification = new_classification
                else:
                    unchanged += 1

                counts_by_value[new_classification] = (
                    counts_by_value.get(new_classification, 0) + 1
                )
                processed += 1

            await db.commit()
            elapsed = time.monotonic() - started_at
            rate = processed / elapsed if elapsed > 0 else 0.0
            print(
                f"  ... {processed}/{total} "
                f"(promoted={promoted}, needs_review={needs_review_count}, "
                f"unchanged={unchanged}, {rate:.2f} firms/s)"
            )
            if batch_start + BATCH_SIZE < total:
                await asyncio.sleep(SLEEP_BETWEEN_BATCHES_SECONDS)

    elapsed = time.monotonic() - started_at
    print(f"Done. Processed {processed}/{total} in {elapsed:.1f}s")
    print(
        f"  promoted={promoted}  needs_review={needs_review_count}  unchanged={unchanged}"
    )
    print("  per-value distribution:")
    for value, count in sorted(counts_by_value.items(), key=lambda kv: -kv[1]):
        print(f"    {value}: {count}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill clearing_classification using the LLM classifier."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only process the first N firms (dry-run helper).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    if sys.platform == "win32" and sys.version_info >= (3, 14):
        with asyncio.Runner(
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())
        ) as runner:
            runner.run(main(args.limit))
    else:
        asyncio.run(main(args.limit))
