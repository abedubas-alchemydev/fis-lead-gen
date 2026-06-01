"""Gemini-maximal clearing re-classification backfill.

Re-runs the FOCUS X-17A-5 clearing extraction for every broker-dealer with
the deterministic regulatory signals (15c3-1 capital tier, DTC/NSCC/OCC
membership, FINRA Form BD partner) injected into the Gemini prompt, then
applies the ``clearing_validator`` guardrail, the verified-aware rollup, the
``clearing_classification`` unify mirror, and the FINRA Form BD reconciler —
exactly the path ``ClearingPipelineService.extract_clearing_for_broker_dealer``
runs live, reused here so the backfill and the live pipeline can't diverge.

This is the fix for the audit finding that ~1,028 sub-$250k firms were
mislabeled ``self_clearing``. After this run they move to ``non_carrying``
(no customer accounts) or ``fully_disclosed`` (introduces to a partner), and
the membership-confirmed megabanks stay ``self_clearing``.

DEFERRED: do not run against staging until the masterlist data fill completes
and Deshorn has spot-checked a sample (>=27/30). Use ``--limit 30`` first to
produce the spot-check CSV. Run with ``GEMINI_PDF_MODEL=gemini-2.5-pro`` for
best accuracy; the Pro quota (~1,000/day) means chunk a full run with
``--offset``.

A change CSV is written for review:
    bd_id, name, crd, old_type, new_type, old_classification,
    new_classification, partner, required_min_capital, memberships,
    finra_partner, confidence, status, notes

Usage:
    python -m scripts.run_clearing_reclassification_backfill --limit 30
    python -m scripts.run_clearing_reclassification_backfill --offset 0 --limit 1000
    python -m scripts.run_clearing_reclassification_backfill            # full run
"""

from __future__ import annotations

import argparse
import asyncio
import csv
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

from app.db.session import SessionLocal  # noqa: E402
from app.models.broker_dealer import BrokerDealer  # noqa: E402
from app.models.pipeline_run import PipelineRun  # noqa: E402
from app.services.clearing_validator import load_clearing_signals  # noqa: E402
from app.services.finra_reconciler import FinraClearingReconciler  # noqa: E402
from app.services.pipeline import ClearingPipelineService  # noqa: E402

logger = logging.getLogger(__name__)

BATCH_SIZE = 8  # smaller than the text-only classifier batch: each firm does a
# PDF fetch + Gemini PDF call, so 8 concurrent keeps provider load sane.
SLEEP_BETWEEN_BATCHES_SECONDS = 2.0

CSV_FIELDS = [
    "bd_id",
    "name",
    "crd",
    "old_type",
    "new_type",
    "old_classification",
    "new_classification",
    "partner",
    "required_min_capital",
    "memberships",
    "finra_partner",
    "confidence",
    "status",
    "notes",
]


async def _create_backfill_run() -> int:
    async with SessionLocal() as db:
        run = PipelineRun(
            pipeline_name="clearing_reclass_backfill",
            status="running",
            trigger_source="manual_backfill",
        )
        db.add(run)
        await db.commit()
        await db.refresh(run)
        return run.id


async def _reclassify_one(
    service: ClearingPipelineService,
    reconciler: FinraClearingReconciler,
    run_id: int,
    competitors: list,
    bd_id: int,
) -> dict | None:
    # Snapshot before-state + assemble the deterministic signals.
    async with SessionLocal() as db:
        bd = await db.get(BrokerDealer, bd_id)
        if bd is None:
            return None
        old_type = bd.current_clearing_type
        old_classification = bd.clearing_classification
        name = bd.name
        crd = bd.crd_number
        min_cap = (
            float(bd.required_min_capital)
            if bd.required_min_capital is not None
            else None
        )
        signals = await load_clearing_signals(db, bd)

    # Signal-aware Gemini extraction + deterministic validator (the live path).
    result = await service._extract_one_bd(bd, run_id, competitors, signals=signals)

    async with SessionLocal() as db:
        await service.repository.upsert_clearing_arrangements(db, [result])
        await db.commit()
        await service._refresh_clearing_rollup_for_bd(db, bd_id)
        bd_row = await db.get(BrokerDealer, bd_id)
        if bd_row is not None:
            ctype = bd_row.current_clearing_type
            bd_row.clearing_classification = (
                ctype if ctype and ctype != "unknown" else "needs_review"
            )
        await db.commit()

    # FINRA Form BD reconciliation (authoritative current partner). Best-effort.
    try:
        async with SessionLocal() as db:
            await reconciler.reconcile_for_broker_dealer(db, bd_id)
    except Exception:
        logger.exception("reconcile failed for bd %s", bd_id)

    async with SessionLocal() as db:
        bd_after = await db.get(BrokerDealer, bd_id)
        new_type = bd_after.current_clearing_type if bd_after else None
        new_classification = bd_after.clearing_classification if bd_after else None
        partner = bd_after.current_clearing_partner if bd_after else None

    return {
        "bd_id": bd_id,
        "name": name,
        "crd": crd,
        "old_type": old_type,
        "new_type": new_type,
        "old_classification": old_classification,
        "new_classification": new_classification,
        "partner": partner,
        "required_min_capital": min_cap,
        "memberships": "|".join(sorted(signals.memberships)),
        "finra_partner": signals.finra_introducing_partner or "",
        "confidence": result.get("extraction_confidence"),
        "status": result.get("extraction_status"),
        "notes": (result.get("extraction_notes") or "")[:300],
    }


async def main(limit: int | None, offset: int, out_path: Path) -> None:
    started_at = time.monotonic()
    service = ClearingPipelineService()
    reconciler = FinraClearingReconciler()

    async with SessionLocal() as db:
        await service.competitors.seed_defaults(db)
        competitors = await service.competitors.list_active(db)
        bd_ids = (
            await db.execute(
                select(BrokerDealer.id)
                .where(BrokerDealer.filings_index_url.isnot(None))
                .order_by(BrokerDealer.id.asc())
            )
        ).scalars().all()

    bd_ids = bd_ids[offset:]
    if limit is not None:
        bd_ids = bd_ids[:limit]
    total = len(bd_ids)

    run_id = await _create_backfill_run()
    print(f"Re-classifying {total} firms (offset={offset}, run_id={run_id})")
    print(f"  change CSV -> {out_path}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    processed = 0
    changed = 0
    transitions: dict[str, int] = {}

    with out_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()

        for batch_start in range(0, total, BATCH_SIZE):
            batch = bd_ids[batch_start : batch_start + BATCH_SIZE]
            rows = await asyncio.gather(
                *(
                    _reclassify_one(service, reconciler, run_id, competitors, bd_id)
                    for bd_id in batch
                ),
                return_exceptions=True,
            )
            for bd_id, row in zip(batch, rows):
                if isinstance(row, Exception):
                    logger.error("reclassify failed for bd %s: %r", bd_id, row)
                    continue
                if row is None:
                    continue
                writer.writerow(row)
                processed += 1
                if row["old_type"] != row["new_type"]:
                    changed += 1
                    key = f"{row['old_type']} -> {row['new_type']}"
                    transitions[key] = transitions.get(key, 0) + 1
            fh.flush()

            elapsed = time.monotonic() - started_at
            rate = processed / elapsed if elapsed > 0 else 0.0
            print(f"  ... {processed}/{total} (changed={changed}, {rate:.2f} firms/s)")
            if batch_start + BATCH_SIZE < total:
                await asyncio.sleep(SLEEP_BETWEEN_BATCHES_SECONDS)

    async with SessionLocal() as db:
        run = await db.get(PipelineRun, run_id)
        if run is not None:
            run.status = "completed"
            run.processed_items = processed
            await db.commit()

    elapsed = time.monotonic() - started_at
    print(f"Done. Processed {processed}/{total} in {elapsed:.1f}s; {changed} changed.")
    print("  top transitions:")
    for key, count in sorted(transitions.items(), key=lambda kv: -kv[1])[:15]:
        print(f"    {key}: {count}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Gemini-maximal clearing re-classification backfill."
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="Only process the first N firms."
    )
    parser.add_argument(
        "--offset", type=int, default=0, help="Skip the first N firms (chunking)."
    )
    parser.add_argument(
        "--out",
        type=str,
        default=str(ROOT / "reports" / "clearing_reclass_backfill.csv"),
        help="Change-CSV output path.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    out = Path(args.out)
    if sys.platform == "win32" and sys.version_info >= (3, 14):
        with asyncio.Runner(
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())
        ) as runner:
            runner.run(main(args.limit, args.offset, out))
    else:
        asyncio.run(main(args.limit, args.offset, out))
