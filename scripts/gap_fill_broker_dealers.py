"""Bulk row-by-row gap-fill for broker_dealers.

Walks every BD in priority order (hot → warm → cold → no-priority,
high score first inside each bucket), inspects which fields are still
NULL, and fires the corresponding sub-pipelines via the existing
``run_refresh_all`` orchestrator. Skips firms whose
``last_gap_fill_attempt_at`` is within the last ``COOLDOWN_DAYS`` so
sources that genuinely have no value aren't re-queried every pass.

All six sub-pipelines participate — financials, health-check
(FINRA Form BD + search metadata), clearing extraction, contact
enrichment, filings refresh, and website resolution. ``decide_pipelines``
gates each on its target field being NULL, so a sub-pipeline only
fires when there's actually something for it to fill.

Designed to run unattended for hours. Interruptible with Ctrl+C; the
cooldown stamp is set per-BD so a rerun resumes automatically.

Usage:
    DATABASE_URL=<staging-url> python scripts/gap_fill_broker_dealers.py
"""
from __future__ import annotations

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
GAP_FILL_PIPELINE_NAME = "broker_dealer_gap_fill"
TRIGGER_SOURCE = "gap_fill_script"


async def main() -> None:
    if not os.environ.get("DATABASE_URL"):
        print("ERROR: DATABASE_URL is not set.")
        sys.exit(2)

    from sqlalchemy import case, or_, select

    from app.db.session import SessionLocal
    from app.models.broker_dealer import BrokerDealer
    from app.models.executive_contact import ExecutiveContact
    from app.models.pipeline_run import PipelineRun
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

    async with SessionLocal() as db:
        eligible_stmt = (
            select(BrokerDealer)
            .where(
                or_(
                    BrokerDealer.last_gap_fill_attempt_at.is_(None),
                    BrokerDealer.last_gap_fill_attempt_at < cutoff,
                )
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
        print("No eligible broker-dealers (all stamped within cooldown).")
        return

    print(f"Gap-fill: {total_eligible:,} eligible BDs (cooldown = {COOLDOWN_DAYS}d, all sub-pipelines included).")
    print()

    processed = 0
    skipped_noop = 0
    sub_success: dict[str, int] = defaultdict(int)
    sub_failure: dict[str, int] = defaultdict(int)
    failures: list[tuple[int, str, str]] = []  # (bd_id, name, summary)
    started_wall = time.monotonic()

    try:
        for idx, bd in enumerate(eligible, start=1):
            has_contacts = bd.id in contact_ids
            decision = decide_pipelines(bd, has_contacts, scope="all")
            to_run = decision.to_run
            to_skip = decision.to_skip

            tag = f"[{idx:>5}/{total_eligible:>5}]"
            name_short = (bd.name or "")[:38]

            if not to_run:
                # Nothing to do — still stamp so we don't re-evaluate every pass.
                async with SessionLocal() as db:
                    fresh = await db.get(BrokerDealer, bd.id)
                    if fresh is not None:
                        fresh.last_gap_fill_attempt_at = datetime.now(timezone.utc)
                        await db.commit()
                skipped_noop += 1
                print(f"{tag} BD {bd.id:<6} {name_short:<38}  noop (all targets already present)")
                continue

            # Create parent PipelineRun (status=queued).
            async with SessionLocal() as db:
                parent = PipelineRun(
                    pipeline_name=GAP_FILL_PIPELINE_NAME,
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
                # Orchestrator already marks the parent failed on internal errors;
                # this is a defensive belt for anything that escapes.
                print(
                    f"{tag} BD {bd.id:<6} {name_short:<38}  ORCHESTRATOR CRASH: "
                    f"{type(exc).__name__}: {str(exc)[:120]}"
                )
                failures.append((bd.id, bd.name, f"{type(exc).__name__}: {exc}"))
                # Still stamp — we tried.
                async with SessionLocal() as db:
                    fresh = await db.get(BrokerDealer, bd.id)
                    if fresh is not None:
                        fresh.last_gap_fill_attempt_at = datetime.now(timezone.utc)
                        await db.commit()
                processed += 1
                continue

            elapsed = time.monotonic() - t0

            # Read parent's notes to surface per-child status.
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

            # Stamp the cooldown.
            async with SessionLocal() as db:
                fresh = await db.get(BrokerDealer, bd.id)
                if fresh is not None:
                    fresh.last_gap_fill_attempt_at = datetime.now(timezone.utc)
                    await db.commit()

            processed += 1
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

    except KeyboardInterrupt:
        print()
        print("Interrupted by user. Resuming on next run via the cooldown stamp.")

    # Re-score every BD after the gap-fill pass. Newly-filled financials,
    # clearing partners, and competitor flags all change scores; running
    # the scorer at the end keeps lead_priority consistent with whatever
    # data the pass managed to land. Cheap (no API calls) and idempotent,
    # so it's safe to run even on a partial / interrupted bulk pass.
    print()
    print("Re-scoring every BD against current data...")
    score_t0 = time.monotonic()
    try:
        async with SessionLocal() as db:
            score_summary = await score_broker_dealers(
                db, only_null_priority=False, limit=None, dry_run=False
            )
            await db.commit()
        print(
            f"Scored {score_summary.scored:,} BDs "
            f"(skipped_no_data={score_summary.skipped_no_data:,}, "
            f"elapsed={time.monotonic() - score_t0:.1f}s)."
        )
    except Exception as exc:
        print(f"Scoring failed: {type(exc).__name__}: {exc}")

    # Final summary
    wall = time.monotonic() - started_wall
    print()
    print("=" * 70)
    print("  GAP-FILL SUMMARY")
    print("=" * 70)
    print(f"BDs processed (orchestrator fired):   {processed:>6,}")
    print(f"BDs skipped (no-op, stamped only):    {skipped_noop:>6,}")
    print(f"Wall-clock:                           {wall/60:>6.1f} min")
    print()
    print("Sub-pipeline outcomes:")
    for sub, label in sub_label.items():
        s = sub_success[sub]
        f = sub_failure[sub]
        if s or f:
            print(f"  {label:<14}  {s:>5,} ok / {f:>5,} fail")
    if failures:
        print()
        print(f"Per-firm failures (first 20 of {len(failures):,}):")
        for bd_id, name, summary in failures[:20]:
            display = (name or "")[:40]
            print(f"  BD {bd_id:<6} {display:<40} - {summary[:100]}")
    print("=" * 70)


if __name__ == "__main__":
    if sys.platform == "win32" and sys.version_info >= (3, 14):
        with asyncio.Runner(
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())
        ) as runner:
            runner.run(main())
    else:
        asyncio.run(main())
