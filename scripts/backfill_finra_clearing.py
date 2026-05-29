"""Backfill: reconcile clearing partners against FINRA Form BD.

One-off driver for ``app.services.finra_reconciler``. The 2026-05-28 prompt
fix only changes *future* FOCUS extractions; existing ``clearing_arrangements``
rows stay wrong until reconciled. This walks BDs that have a CRD + a clearing
row and runs the reconciler on each, throttled so we don't hammer FINRA.

Idempotent — re-running re-confirms MATCH rows and re-applies nothing.

Usage (run from repo root):
    # List candidates, no fetches, no writes.
    python -m scripts.backfill_finra_clearing

    # Reconcile the first 50 candidates (most-recent filing first).
    python -m scripts.backfill_finra_clearing --apply --limit 50

    # Single firm (e.g. verify ORTEX → Alpaca).
    python -m scripts.backfill_finra_clearing --apply --bd-id 23503

Report is written to reports/finra-clearing-backfill-<date>.md.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if sys.platform == "win32" and sys.version_info < (3, 14):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import aliased  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models.broker_dealer import BrokerDealer  # noqa: E402
from app.models.clearing_arrangement import ClearingArrangement  # noqa: E402
from app.services.competitors import CompetitorProviderService  # noqa: E402
from app.services.finra_reconciler import (  # noqa: E402
    STATUS_RECONCILED,
    FinraClearingReconciler,
)

THROTTLE_SECONDS = 1.0
REPORT_DIR = ROOT / "reports"


async def _select_candidates(
    db, *, bd_id: int | None, limit: int, skip_verified: bool = False
) -> list[int]:
    if bd_id is not None:
        return [bd_id]
    # BDs with a CRD (needed for BrokerCheck) and at least one clearing row,
    # most-recent filing first — same population as the audit probe.
    stmt = (
        select(BrokerDealer.id)
        .join(ClearingArrangement, ClearingArrangement.bd_id == BrokerDealer.id)
        .where(BrokerDealer.crd_number.is_not(None))
    )
    if skip_verified:
        # Advance past already-reconciled firms: a verified clearing row means
        # FINRA has been applied. Without this, a recurring/batched driver would
        # re-fetch the same top-of-list PDFs every run (reconcile is idempotent,
        # so it's a no-op MATCH, but still burns a FINRA fetch + ~5s each).
        # Alias the subquery's clearing_arrangements so it keeps its own FROM —
        # the outer query already joins ClearingArrangement, so without an alias
        # SQLAlchemy auto-correlates both tables away and the EXISTS has no FROM.
        ca_verified = aliased(ClearingArrangement)
        verified = (
            select(ca_verified.id)
            .where(
                ca_verified.bd_id == BrokerDealer.id,
                ca_verified.is_verified.is_(True),
            )
            .correlate(BrokerDealer)
        )
        stmt = stmt.where(~verified.exists())
    stmt = stmt.order_by(
        ClearingArrangement.report_date.desc().nullslast(), BrokerDealer.id.asc()
    ).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    # de-dupe preserving order (a BD can have multiple clearing rows)
    seen: set[int] = set()
    ordered: list[int] = []
    for rid in rows:
        if rid not in seen:
            seen.add(rid)
            ordered.append(rid)
    return ordered


async def main() -> int:
    parser = argparse.ArgumentParser(description="FINRA clearing reconciliation backfill.")
    parser.add_argument("--apply", action="store_true", help="Actually reconcile (else just list candidates).")
    parser.add_argument("--limit", type=int, default=50, help="Max candidates (default 50).")
    parser.add_argument("--bd-id", type=int, default=None, help="Reconcile a single BD by id.")
    parser.add_argument("--no-verify", action="store_true", help="Leave reconciled rows is_verified=False.")
    parser.add_argument(
        "--skip-verified",
        action="store_true",
        help="Skip BDs that already have a verified clearing row (FINRA already "
        "applied). Lets a recurring/batched driver advance instead of re-doing "
        "the same firms each run.",
    )
    args = parser.parse_args()

    async with SessionLocal() as db:
        candidates = await _select_candidates(
            db, bd_id=args.bd_id, limit=args.limit, skip_verified=args.skip_verified
        )

    print(f"Candidates: {len(candidates)}")
    if not args.apply:
        print("Dry run — pass --apply to reconcile. First 10 ids:", candidates[:10])
        return 0

    reconciler = FinraClearingReconciler()
    async with SessionLocal() as seed_db:
        await reconciler.competitors.seed_defaults(seed_db)
        competitors = await reconciler.competitors.list_active(seed_db)

    counts: Counter[str] = Counter()
    reconciled_rows: list[str] = []

    for i, bd_id in enumerate(candidates, 1):
        async with SessionLocal() as db:
            try:
                result = await reconciler.reconcile_for_broker_dealer(
                    db, bd_id, competitors=competitors, mark_verified=not args.no_verify
                )
            except Exception as exc:  # noqa: BLE001 — log + continue the batch
                counts["exception"] += 1
                print(f"  [{i}/{len(candidates)}] bd {bd_id}: EXCEPTION {type(exc).__name__}: {exc}")
                continue

        counts[result.status] += 1
        line = (
            f"  [{i}/{len(candidates)}] bd {bd_id} [{result.status}] "
            f"{result.detail}"
        )
        print(line)
        if result.status == STATUS_RECONCILED:
            reconciled_rows.append(
                f"| {bd_id} | {result.previous_partner or 'Self-Clearing'} | "
                f"{result.new_partner} | {result.introducing_rows_written} |"
            )

        await asyncio.sleep(THROTTLE_SECONDS)

    print("\nSummary:", dict(counts))

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report = REPORT_DIR / "finra-clearing-backfill.md"
    lines = ["# FINRA clearing reconciliation backfill\n", "## Status counts\n"]
    for status, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        lines.append(f"- {status}: {n}")
    lines.append("\n## Reconciled firms\n")
    lines.append("| BD ID | Previous partner | New partner (FINRA) | Introducing rows |")
    lines.append("|---|---|---|---|")
    lines.extend(reconciled_rows)
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Report: {report}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
