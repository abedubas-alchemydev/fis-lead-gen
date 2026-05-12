"""Standalone entrypoint for the post-clearing-loop rollup work.

The clearing pipeline normally finishes by copying the latest
``clearing_arrangements`` row per BD into the ``broker_dealers.current_*``
rollup columns and re-scoring every firm. When the pipeline is killed
mid-run (e.g. Gemini quota exhaustion), those steps never execute and
the freshly extracted partners stay invisible in the /master-list UI.

This script re-runs ONLY the non-LLM rollup helpers so the data already
sitting in ``clearing_arrangements`` shows up in the UI. It is idempotent
and safe to re-run after any killed pipeline.

Skipped on purpose: ``classification.apply_classification_to_all`` is
LLM-backed and out of scope here.

Usage:
    python -m scripts.refresh_clearing_rollups               # write
    python -m scripts.refresh_clearing_rollups --dry-run     # report only

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

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models.broker_dealer import BrokerDealer  # noqa: E402
from app.services.broker_dealers import BrokerDealerRepository  # noqa: E402

logger = logging.getLogger(__name__)


async def _count_populated(db: AsyncSession) -> int:
    return (await db.execute(
        select(func.count())
        .select_from(BrokerDealer)
        .where(BrokerDealer.current_clearing_partner.is_not(None))
    )).scalar_one()


async def main(*, dry_run: bool) -> None:
    started_at = time.monotonic()
    repository = BrokerDealerRepository()

    async with SessionLocal() as db:
        populated_before = await _count_populated(db)
        total_bd = (await db.execute(
            select(func.count()).select_from(BrokerDealer)
        )).scalar_one()

        if dry_run:
            elapsed = time.monotonic() - started_at
            print(
                f"DRY RUN — would refresh rollups + lead scores over "
                f"{total_bd} broker_dealers, "
                f"clearing_partner_populated_before={populated_before}, "
                f"classification_updated=skipped(no-gemini), "
                f"elapsed={elapsed:.2f}s"
            )
            return

        await repository.refresh_clearing_rollups(db)
        await repository.refresh_lead_scores(db)
        await db.commit()

        populated_after = await _count_populated(db)

    elapsed = time.monotonic() - started_at
    print(
        f"clearing_partner_populated_before={populated_before}, "
        f"after={populated_after}, "
        f"classification_updated=skipped(no-gemini), "
        f"scores_updated={total_bd} "
        f"elapsed={elapsed:.2f}s"
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the post-clearing-loop rollup work standalone: copy the "
            "latest clearing_arrangements row per broker_dealer into the "
            "current_* rollup columns and re-score every firm. No LLM."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print before-counts and exit without writing.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    coro = main(dry_run=args.dry_run)
    if sys.platform == "win32" and sys.version_info >= (3, 14):
        with asyncio.Runner(
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())
        ) as runner:
            runner.run(coro)
    else:
        asyncio.run(coro)
