"""Gap-fill ``cold``-priority broker_dealers with the cheap sub-pipelines
only — skip the expensive Gemini-PDF stack (financials, clearing).

The full gap-fill (``scripts/gap_fill_broker_dealers.py``) fires every
sub-pipeline through ``decide_pipelines``. Running that across the cold
bucket (~2,500 BDs on staging) is wasteful: most cold firms are
private, have no public X-17A-5 history, and the May 7-8 backfill
already proved that clearing + financials extraction fails on the bulk
of them. Re-running the same Gemini calls a few weeks later wastes
$60-150 in PDF parses to produce essentially the same set of failures.

This script is the cost-aware alternative documented in the original
plan: process only the cheap sub-pipelines whose target fields can
plausibly land on a private firm:

  - resolve-website (Apollo company search + Hunter fallback)
  - health-check (FINRA Form BD — free)
  - enrich (Apollo + Hunter, company-only)
  - filings (SEC EDGAR submissions JSON — free)

Skips:
  - financials (Gemini PDF parse — ~$0.50/BD)
  - clearing (Gemini PDF parse — ~$0.50/BD)

For each eligible BD it calls ``decide_pipelines`` to honor the
existing NULL gates (so we don't re-run a sub-pipeline whose target
field is already populated), intersects with the cheap allow-list, and
hands the result to ``run_refresh_all``. Stamps
``last_gap_fill_attempt_at`` so the next pass skips firms already
attempted within ``COOLDOWN_DAYS``.

Usage:
    DATABASE_URL=<staging-url> python -m scripts.gap_fill_cold_cheap
    python -m scripts.gap_fill_cold_cheap --dry-run
    python -m scripts.gap_fill_cold_cheap --priority cold --priority warm
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import selectors
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if sys.platform == "win32" and sys.version_info < (3, 14):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


COOLDOWN_DAYS = 30
PIPELINE_NAME = "broker_dealer_gap_fill_cheap"
TRIGGER_SOURCE = "gap_fill_cold_cheap"
MAX_CONCURRENCY = int(os.environ.get("GAP_FILL_CONCURRENCY", "2"))


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--priority",
        action="append",
        choices=("hot", "warm", "cold", "none"),
        help="Priority bucket(s) to process. Repeatable. Defaults to 'cold'.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would run, then exit without dispatching.",
    )
    args = parser.parse_args()
    priorities = tuple(args.priority) if args.priority else ("cold",)

    if not os.environ.get("DATABASE_URL"):
        print("ERROR: DATABASE_URL is not set.")
        sys.exit(2)

    from sqlalchemy import case, or_, select

    from app.db.session import SessionLocal
    from app.models.broker_dealer import BrokerDealer
    from app.models.clearing_arrangement import ClearingArrangement
    from app.models.executive_contact import ExecutiveContact
    from app.models.pipeline_run import PipelineRun
    from app.services.extraction_status import RETRYABLE_TRANSIENT_STATUSES
    from app.services.refresh_all_orchestrator import (
        SUB_ENRICH,
        SUB_HEALTH_CHECK,
        SUB_REFRESH_CLEARING,
        SUB_REFRESH_FILINGS,
        SUB_REFRESH_FINANCIALS,
        SUB_RESOLVE_WEBSITE,
        decide_pipelines,
        run_refresh_all,
    )
    from app.services.scoring import score_broker_dealers

    CHEAP_PIPELINES: frozenset[str] = frozenset(
        {SUB_RESOLVE_WEBSITE, SUB_HEALTH_CHECK, SUB_ENRICH, SUB_REFRESH_FILINGS}
    )
    EXPENSIVE_PIPELINES: frozenset[str] = frozenset(
        {SUB_REFRESH_FINANCIALS, SUB_REFRESH_CLEARING}
    )
    sub_label = {
        SUB_REFRESH_FINANCIALS: "financials",
        SUB_HEALTH_CHECK: "health-check",
        SUB_REFRESH_CLEARING: "clearing",
        SUB_ENRICH: "enrich",
        SUB_REFRESH_FILINGS: "filings",
        SUB_RESOLVE_WEBSITE: "website",
    }

    cutoff = datetime.now(timezone.utc) - timedelta(days=COOLDOWN_DAYS)
    priority_rank = case(
        (BrokerDealer.lead_priority == "hot", 0),
        (BrokerDealer.lead_priority == "warm", 1),
        (BrokerDealer.lead_priority == "cold", 2),
        else_=3,
    )

    # Priority filter — when 'none' is requested, match either NULL or
    # priority values that aren't hot/warm/cold (the case() else_ branch).
    bucket_terms: list = []
    for p in priorities:
        if p == "none":
            bucket_terms.append(BrokerDealer.lead_priority.is_(None))
        else:
            bucket_terms.append(BrokerDealer.lead_priority == p)

    retryable_bd_ids_subq = (
        select(ClearingArrangement.bd_id)
        .where(ClearingArrangement.extraction_status.in_(RETRYABLE_TRANSIENT_STATUSES))
        .distinct()
    )

    async with SessionLocal() as db:
        eligible_stmt = (
            select(BrokerDealer)
            .where(
                or_(*bucket_terms),
                or_(
                    BrokerDealer.last_gap_fill_attempt_at.is_(None),
                    BrokerDealer.last_gap_fill_attempt_at < cutoff,
                    BrokerDealer.id.in_(retryable_bd_ids_subq),
                ),
            )
            .order_by(
                priority_rank,
                BrokerDealer.lead_score.desc().nullslast(),
                BrokerDealer.id,
            )
        )
        eligible = list((await db.execute(eligible_stmt)).scalars().all())
        contact_ids: set[int] = set(
            (await db.execute(select(ExecutiveContact.bd_id).distinct())).scalars().all()
        )

    total_eligible = len(eligible)
    if total_eligible == 0:
        print(
            f"No eligible broker-dealers in priorities {priorities} "
            f"(all stamped within {COOLDOWN_DAYS}d cooldown)."
        )
        return

    # Pre-compute what the cheap-only filter would actually queue. This
    # also drives the dry-run summary so the operator sees the real work
    # surface before authorizing.
    plan_counts: dict[str, int] = defaultdict(int)
    skip_only_count = 0
    plan_by_bd: dict[int, tuple[tuple[str, ...], tuple[str, ...]]] = {}
    for bd in eligible:
        has_contacts = bd.id in contact_ids
        decision = decide_pipelines(bd, has_contacts, scope="all")
        cheap_to_run = tuple(p for p in decision.to_run if p in CHEAP_PIPELINES)
        # Anything decide_pipelines wanted to run that we're filtering out
        # for cost reasons explicitly moves to to_skip.
        forced_skip = tuple(p for p in decision.to_run if p in EXPENSIVE_PIPELINES)
        merged_skip = tuple(set(decision.to_skip) | set(forced_skip))
        plan_by_bd[bd.id] = (cheap_to_run, merged_skip)
        if cheap_to_run:
            for p in cheap_to_run:
                plan_counts[p] += 1
        else:
            skip_only_count += 1

    print(
        f"Cheap gap-fill: {total_eligible:,} eligible BDs in priorities "
        f"{priorities} (cooldown = {COOLDOWN_DAYS}d, concurrency = "
        f"{MAX_CONCURRENCY})."
    )
    print()
    print(f"  BDs with at least one cheap pipeline to run : "
          f"{total_eligible - skip_only_count:,}")
    print(f"  BDs whose targets are all already filled    : {skip_only_count:,}")
    print()
    print("  Per-pipeline fire count:")
    for p in (SUB_RESOLVE_WEBSITE, SUB_HEALTH_CHECK, SUB_ENRICH, SUB_REFRESH_FILINGS):
        print(f"    {sub_label[p]:<14}: {plan_counts.get(p, 0):>5}")
    print()
    print(f"  Forced-skip (out of scope for cheap pass)   : "
          f"{sub_label[SUB_REFRESH_FINANCIALS]}, {sub_label[SUB_REFRESH_CLEARING]}")

    if args.dry_run:
        print()
        print("(dry-run — pass without --dry-run to dispatch)")
        return

    counters: dict[str, int] = {"processed": 0, "skipped_noop": 0}
    sub_success: dict[str, int] = defaultdict(int)
    sub_failure: dict[str, int] = defaultdict(int)
    failures: list[tuple[int, str, str]] = []
    started_wall = time.monotonic()
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

    async def _process_bd(idx: int, bd: BrokerDealer) -> None:
        async with semaphore:
            to_run, to_skip = plan_by_bd[bd.id]
            tag = f"[{idx:>5}/{total_eligible:>5}]"
            name_short = (bd.name or "")[:38]

            if not to_run:
                async with SessionLocal() as db:
                    fresh = await db.get(BrokerDealer, bd.id)
                    if fresh is not None:
                        fresh.last_gap_fill_attempt_at = datetime.now(timezone.utc)
                        await db.commit()
                counters["skipped_noop"] += 1
                print(f"{tag} BD {bd.id:<6} {name_short:<38}  noop (cheap targets present)")
                return

            async with SessionLocal() as db:
                parent = PipelineRun(
                    pipeline_name=PIPELINE_NAME,
                    trigger_source=TRIGGER_SOURCE,
                    status="queued",
                    total_items=len(to_run),
                    processed_items=0,
                    success_count=0,
                    failure_count=0,
                    notes=json.dumps(
                        {
                            "bd_id": bd.id,
                            "stage": "queued",
                            "ran": list(to_run),
                            "skipped": list(to_skip),
                            "mode": "cheap_only",
                        }
                    ),
                )
                db.add(parent)
                await db.commit()
                await db.refresh(parent)
                parent_id = parent.id

            t0 = time.monotonic()
            try:
                await run_refresh_all(
                    parent_id,
                    bd.id,
                    trigger_source=TRIGGER_SOURCE,
                    pipelines_to_run=to_run,
                    pipelines_to_skip=to_skip,
                )
            except Exception as exc:
                print(
                    f"{tag} BD {bd.id:<6} {name_short:<38}  ORCHESTRATOR CRASH: "
                    f"{type(exc).__name__}: {str(exc)[:120]}"
                )
                failures.append((bd.id, bd.name, f"{type(exc).__name__}: {exc}"))
                async with SessionLocal() as db:
                    fresh = await db.get(BrokerDealer, bd.id)
                    if fresh is not None:
                        fresh.last_gap_fill_attempt_at = datetime.now(timezone.utc)
                        await db.commit()
                counters["processed"] += 1
                return

            elapsed = time.monotonic() - t0

            async with SessionLocal() as db:
                refreshed_parent = await db.get(PipelineRun, parent_id)
                payload = json.loads(refreshed_parent.notes or "{}") if refreshed_parent else {}
                children = payload.get("children", {})

            ok_subs: list[str] = []
            fail_subs: list[str] = []
            for sub, info in children.items():
                label = sub_label.get(sub, sub)
                status = info.get("status", "unknown")
                if status in ("completed", "completed_with_errors"):
                    ok_subs.append(label)
                    sub_success[sub] += 1
                else:
                    fail_subs.append(label)
                    sub_failure[sub] += 1

            async with SessionLocal() as db:
                fresh = await db.get(BrokerDealer, bd.id)
                if fresh is not None:
                    fresh.last_gap_fill_attempt_at = datetime.now(timezone.utc)
                    await db.commit()

            counters["processed"] += 1
            ran_str = ",".join(ok_subs) if ok_subs else "-"
            fail_str = (" failed:[" + ",".join(fail_subs) + "]") if fail_subs else ""
            print(
                f"{tag} BD {bd.id:<6} {name_short:<38}  "
                f"{elapsed:>5.1f}s  ran:[{ran_str}]{fail_str}"
            )
            if fail_subs:
                failures.append(
                    (bd.id, bd.name, f"failed: {','.join(fail_subs)}")
                )

    try:
        tasks = [
            _process_bd(idx, bd)
            for idx, bd in enumerate(eligible, start=1)
        ]
        await asyncio.gather(*tasks, return_exceptions=False)
    except KeyboardInterrupt:
        print()
        print("Interrupted. Cooldown stamps persist; rerun resumes naturally.")

    processed = counters["processed"]
    skipped_noop = counters["skipped_noop"]

    print()
    print("Re-scoring every BD against current data...")
    async with SessionLocal() as db:
        await score_broker_dealers(db)
        await db.commit()

    elapsed_total = time.monotonic() - started_wall
    print()
    print("=" * 60)
    print(f"CHEAP GAP-FILL SUMMARY ({elapsed_total/60:.1f}m wall)")
    print("=" * 60)
    print(f"  processed     : {processed:,}")
    print(f"  skipped_noop  : {skipped_noop:,}")
    print()
    print("  Sub-pipeline ok / fail:")
    for sub in (SUB_RESOLVE_WEBSITE, SUB_HEALTH_CHECK, SUB_ENRICH, SUB_REFRESH_FILINGS):
        ok = sub_success.get(sub, 0)
        fail = sub_failure.get(sub, 0)
        print(f"    {sub_label[sub]:<14}: ok={ok:>5}  fail={fail:>5}")
    if failures:
        print()
        print(f"  First 20 of {len(failures)} failures:")
        for bd_id, name, summary in failures[:20]:
            print(f"    BD {bd_id} {(name or '')[:42]:<42} {summary[:80]}")


if __name__ == "__main__":
    if sys.platform == "win32" and sys.version_info >= (3, 14):
        with asyncio.Runner(
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())
        ) as runner:
            runner.run(main())
    else:
        asyncio.run(main())
