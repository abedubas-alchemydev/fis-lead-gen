"""One-off backfill for ``broker_dealers.three_year_cagr``.

The rollup writers in ``services/focus_reports.py`` and
``services/focus_ceo_extraction.py`` populate this column on every fresh
financial extraction, but existing rows don't get touched until their
next refresh. This script seeds the column for the current population:

  - Walks every BD that has at least 3 parsed ``financial_metrics`` rows.
  - Computes CAGR via ``calculate_three_year_cagr`` so the math matches
    what the rollup writers use (single source of truth).
  - UPDATEs ``broker_dealers.three_year_cagr`` only when the computed
    value differs from what's already stored — idempotent and chatty
    enough to re-run safely.

Usage:
    python -m scripts.backfill_three_year_cagr
    python -m scripts.backfill_three_year_cagr --dry-run

The script is read-mostly: each BD batch reads its metrics and writes
at most one column on the BD row. Designed to run in well under a
minute against the staging master list (~2,800 firms).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import selectors
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if sys.platform == "win32" and sys.version_info < (3, 14):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute and report deltas without writing.",
    )
    args = parser.parse_args()

    if not os.environ.get("DATABASE_URL"):
        print("ERROR: DATABASE_URL is not set.", file=sys.stderr)
        sys.exit(2)

    from sqlalchemy import select

    from app.db.session import SessionLocal
    from app.models.broker_dealer import BrokerDealer
    from app.models.financial_metric import FinancialMetric
    from app.services.extraction_status import STATUS_PARSED
    from app.services.scoring import calculate_three_year_cagr

    metrics_by_bd: dict[int, list[FinancialMetric]] = defaultdict(list)
    async with SessionLocal() as db:
        rows = (
            await db.execute(
                select(FinancialMetric)
                .where(FinancialMetric.extraction_status == STATUS_PARSED)
                .order_by(FinancialMetric.report_date.desc())
            )
        ).scalars().all()
    for metric in rows:
        metrics_by_bd[metric.bd_id].append(metric)

    eligible = {bd_id: ms for bd_id, ms in metrics_by_bd.items() if len(ms) >= 3}
    total_metrics = sum(len(ms) for ms in metrics_by_bd.values())
    print(
        f"Loaded {total_metrics:,} parsed financial_metrics rows across "
        f"{len(metrics_by_bd):,} BDs."
    )
    print(f"  {len(eligible):,} BDs have >=3 rows (CAGR-eligible).")

    if not eligible:
        print("Nothing to backfill.")
        return

    updated = 0
    unchanged = 0
    cleared = 0  # eligible BD whose CAGR resolves to None (e.g. zero base)
    async with SessionLocal() as db:
        bds = (
            await db.execute(
                select(BrokerDealer).where(BrokerDealer.id.in_(eligible.keys()))
            )
        ).scalars().all()
        bd_by_id = {bd.id: bd for bd in bds}

        for bd_id, metrics in eligible.items():
            bd = bd_by_id.get(bd_id)
            if bd is None:
                continue
            cagr = calculate_three_year_cagr(metrics)
            current = (
                float(bd.three_year_cagr) if bd.three_year_cagr is not None else None
            )
            target = float(cagr) if cagr is not None else None

            # Round both to two decimals before comparing so a stored
            # Decimal('20.00') matches a computed 20.0 (no spurious write).
            same = (current is None and target is None) or (
                current is not None
                and target is not None
                and round(current, 2) == round(target, 2)
            )
            if same:
                unchanged += 1
                continue

            if target is None:
                cleared += 1
            else:
                updated += 1

            if not args.dry_run:
                bd.three_year_cagr = Decimal(str(target)) if target is not None else None

        if not args.dry_run:
            await db.commit()

    label = "Would update" if args.dry_run else "Updated"
    print(f"{label}: {updated:,} BDs with new CAGR values.")
    print(f"           {cleared:,} BDs cleared to NULL (oldest <= 0).")
    print(f"           {unchanged:,} BDs already correct.")
    if args.dry_run:
        print("(dry-run — no writes committed)")


if __name__ == "__main__":
    if sys.platform == "win32" and sys.version_info >= (3, 14):
        with asyncio.Runner(
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())
        ) as runner:
            runner.run(main())
    else:
        asyncio.run(main())
