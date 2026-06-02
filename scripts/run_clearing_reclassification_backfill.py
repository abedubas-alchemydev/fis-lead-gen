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
produce the spot-check CSV. Runs against the staging DB, so execute it where
5432 works (Cloud Run / staging), not the DPI'd workstation.

Live progress + crash recovery:
  * Each firm is stamped ``current_clearing_type = 'Identifying'`` and committed
    BEFORE it is processed, so the master list shows live which firms are being
    corrected right now (up to ``--concurrency`` at a time). The real label
    overwrites it when the firm finishes.
  * Every completed ``bd_id`` is appended to the checkpoint file the moment its
    real label is written. ``--resume`` skips everything already in the
    checkpoint and appends to the CSV, so a crash resumes exactly where it
    stopped. A firm left on 'Identifying' (killed mid-flight, or a per-firm
    error) is NOT checkpointed, so it is re-done on the next run / --resume.

Best + efficient + fast, without sacrificing quality:
  * DEFAULT = ``--model pro --concurrency 8`` -- every firm read by the best
    model (no quality trade-off), parallelized so it runs as fast as the Pro
    quota allows. The quota (~1,000/day on Tier 1) is the only wall; raise the
    tier or chunk with ``--offset`` if 3 days is too slow.
  * ``--concurrency 1`` -- strict one-firm-at-a-time (single 'Identifying').
  * Quota-wall fallback (small, known quality residual on confident-but-wrong
    high-cap reads): ``--model flash --concurrency 8`` for the bulk, then
    ``--model pro --only-needs-review`` to re-read just the uncertain rows.

CSV columns: bd_id, name, crd, old_type, new_type, old_classification,
new_classification, partner, required_min_capital, memberships, finra_partner,
confidence, status, notes, model.

Usage:
    python -m scripts.run_clearing_reclassification_backfill --limit 30   # Pro spot-check
    python -m scripts.run_clearing_reclassification_backfill              # full Pro, 8-wide
    python -m scripts.run_clearing_reclassification_backfill --resume     # after a crash
    python -m scripts.run_clearing_reclassification_backfill --model flash --concurrency 8
    python -m scripts.run_clearing_reclassification_backfill --model pro --only-needs-review
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

from app.core.config import settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.broker_dealer import BrokerDealer  # noqa: E402
from app.models.pipeline_run import PipelineRun  # noqa: E402
from app.services.clearing_validator import load_clearing_signals  # noqa: E402
from app.services.finra_reconciler import FinraClearingReconciler  # noqa: E402
from app.services.pipeline import ClearingPipelineService  # noqa: E402

logger = logging.getLogger(__name__)

# Transient marker written to ``current_clearing_type`` while a firm is being
# corrected so the master list shows live which firms are in flight. Rendered as
# an amber "Identifying" pill on the FE (see pill-helpers / clearing-type-badge).
IN_PROGRESS_MARKER = "Identifying"

MODEL_IDS = {"flash": "gemini-2.5-flash", "pro": "gemini-2.5-pro"}

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
    "model",
]


def _load_checkpoint(path: Path) -> set[int]:
    if not path.exists():
        return set()
    done: set[int] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.isdigit():
            done.add(int(line))
    return done


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


async def _snapshot_and_mark_identifying(bd_id: int) -> tuple[str | None, str | None] | None:
    """Read the firm's pre-run clearing label, then stamp 'Identifying' and
    commit immediately so the master list shows it in flight. Returns the
    (old_type, old_classification) snapshot, or None if the BD vanished."""
    async with SessionLocal() as db:
        bd = await db.get(BrokerDealer, bd_id)
        if bd is None:
            return None
        old_type = bd.current_clearing_type
        old_classification = bd.clearing_classification
        bd.current_clearing_type = IN_PROGRESS_MARKER
        await db.commit()
    return old_type, old_classification


async def _reclassify_one(
    service: ClearingPipelineService,
    reconciler: FinraClearingReconciler,
    run_id: int,
    competitors: list,
    bd_id: int,
    *,
    old_type: str | None,
    old_classification: str | None,
) -> dict | None:
    async with SessionLocal() as db:
        bd = await db.get(BrokerDealer, bd_id)
        if bd is None:
            return None
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
        "model": settings.gemini_pdf_model,
    }


async def main(
    limit: int | None,
    offset: int,
    out_path: Path,
    checkpoint_path: Path,
    model: str,
    concurrency: int,
    only_needs_review: bool,
    resume: bool,
) -> None:
    started_at = time.monotonic()
    settings.gemini_pdf_model = MODEL_IDS[model]
    concurrency = max(1, concurrency)

    service = ClearingPipelineService()
    reconciler = FinraClearingReconciler()

    async with SessionLocal() as db:
        await service.competitors.seed_defaults(db)
        competitors = await service.competitors.list_active(db)
        query = select(BrokerDealer.id).where(BrokerDealer.filings_index_url.isnot(None))
        if only_needs_review:
            query = query.where(BrokerDealer.clearing_classification == "needs_review")
        bd_ids = (
            await db.execute(query.order_by(BrokerDealer.id.asc()))
        ).scalars().all()

    bd_ids = bd_ids[offset:]
    if limit is not None:
        bd_ids = bd_ids[:limit]

    completed = _load_checkpoint(checkpoint_path) if resume else set()
    pending = [b for b in bd_ids if b not in completed]
    total = len(pending)

    run_id = await _create_backfill_run()
    print(f"Re-classifying {total} firms (offset={offset}, run_id={run_id})")
    print(f"  model: {settings.gemini_pdf_model} | concurrency: {concurrency}")
    print(f"  only_needs_review: {only_needs_review} | resume: {resume} "
          f"({len(completed)} already checkpointed)")
    print(f"  change CSV  -> {out_path}")
    print(f"  checkpoint  -> {checkpoint_path}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    csv_mode = "a" if (resume and out_path.exists()) else "w"
    ckpt_mode = "a" if resume else "w"

    sem = asyncio.Semaphore(concurrency)

    async def _process(bd_id: int) -> tuple[int, dict | None, bool]:
        """Mark 'Identifying' -> correct -> return (bd_id, row, do_checkpoint).
        A per-firm failure returns do_checkpoint=False so the firm (left on
        'Identifying') is retried on the next run / --resume, never silently
        skipped."""
        async with sem:
            try:
                snapshot = await _snapshot_and_mark_identifying(bd_id)
                if snapshot is None:
                    return bd_id, None, True  # BD vanished: nothing to do, skip it
                old_type, old_classification = snapshot
                row = await _reclassify_one(
                    service, reconciler, run_id, competitors, bd_id,
                    old_type=old_type, old_classification=old_classification,
                )
                return bd_id, row, True
            except Exception:
                logger.exception("reclassify failed for bd %s", bd_id)
                return bd_id, None, False

    processed = 0
    changed = 0
    transitions: dict[str, int] = {}

    with out_path.open(csv_mode, encoding="utf-8", newline="") as fh, \
            checkpoint_path.open(ckpt_mode, encoding="utf-8") as ckpt:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        if csv_mode == "w":
            writer.writeheader()
            fh.flush()

        # Up to ``concurrency`` firms in flight; each marks 'Identifying',
        # corrects, and is written + checkpointed the instant it finishes
        # (streamed via as_completed, so no batch barrier).
        tasks = [asyncio.create_task(_process(b)) for b in pending]
        for fut in asyncio.as_completed(tasks):
            bd_id, row, do_checkpoint = await fut
            if row is not None:
                writer.writerow(row)
                fh.flush()
                if row["old_type"] != row["new_type"]:
                    changed += 1
                    key = f"{row['old_type']} -> {row['new_type']}"
                    transitions[key] = transitions.get(key, 0) + 1
            if do_checkpoint:
                ckpt.write(f"{bd_id}\n")
                ckpt.flush()
            processed += 1
            elapsed = time.monotonic() - started_at
            rate = processed / elapsed if elapsed > 0 else 0.0
            last = row["new_type"] if row else "skipped/failed"
            print(f"  {processed}/{total} done: bd {bd_id} -> {last} ({rate:.2f}/s)")

    async with SessionLocal() as db:
        run = await db.get(PipelineRun, run_id)
        if run is not None:
            run.status = "completed"
            run.processed_items = processed
            await db.commit()

    elapsed = time.monotonic() - started_at
    print(f"Done in {elapsed:.1f}s. Processed {processed}/{total}; {changed} changed.")
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
        "--model",
        choices=["flash", "pro"],
        default="pro",
        help="Extraction model (default: pro -- best accuracy).",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=8,
        help="Firms processed in parallel (default: 8; 1 = strict one-at-a-time).",
    )
    parser.add_argument(
        "--only-needs-review",
        action="store_true",
        help="Restrict the universe to firms currently classified needs_review "
        "(the Pro re-verify pass; pair with --model pro).",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip firms already in the checkpoint file and append to the CSV.",
    )
    parser.add_argument(
        "--out",
        type=str,
        default=str(ROOT / "reports" / "clearing_reclass_backfill.csv"),
        help="Change-CSV output path.",
    )
    parser.add_argument(
        "--checkpoint",
        type=str,
        default=str(ROOT / "reports" / "clearing_reclass_checkpoint.txt"),
        help="Checkpoint file of completed bd_ids (for --resume).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    coro = main(
        args.limit, args.offset, Path(args.out), Path(args.checkpoint), args.model,
        args.concurrency, args.only_needs_review, args.resume,
    )
    if sys.platform == "win32" and sys.version_info >= (3, 14):
        with asyncio.Runner(
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())
        ) as runner:
            runner.run(coro)
    else:
        asyncio.run(coro)
