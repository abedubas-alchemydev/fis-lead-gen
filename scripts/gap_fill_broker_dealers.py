"""Bulk row-by-row gap-fill for broker_dealers.

Walks every BD in priority order (hot -> warm -> cold -> no-priority,
high score first inside each bucket), inspects which fields are still
NULL or carry sentinel values like ``'unknown'`` / ``'needs_review'``,
and fires the corresponding sub-pipelines via the existing
``run_refresh_all`` orchestrator. Skips firms whose
``last_gap_fill_attempt_at`` is within the last ``COOLDOWN_DAYS``
unless ``--reset-cooldown`` is passed.

All seven sub-pipelines participate -- financials, health-check
(FINRA Form BD + search metadata), clearing extraction, contact
enrichment, filings refresh, website resolution, and FOCUS-contact
extraction (the X-17A-5 "PERSON TO CONTACT" block -> a focus_report
ExecutiveContact; the only automated source of BD filing-contact
emails). ``decide_pipelines`` gates each one; this script defaults to
aggressive mode so the gates also fire on sentinel values and
detail-page-only fields, not just strict ``IS NULL``.

Designed to run unattended for hours. Interruptible with Ctrl+C; the
cooldown stamp is set per-BD so a rerun resumes automatically.

Flow:
  1. Phase 1 (read-only scan): walk every eligible BD, accumulate
     per-column gap counts grouped by priority bucket, print a
     summary table.
  2. Confirmation: if neither ``--scan-only`` nor ``--apply`` is
     passed, prompt the operator to proceed before any mutations.
  3. Phase 2 (fill): existing concurrent ``run_refresh_all`` fan-out
     over the same eligible set. DB commits land row-by-row; the
     per-BD cooldown stamp makes the next invocation auto-resume
     from where the last one stopped.

Usage:
    # Default: scan, summarize, prompt, then fill.
    python scripts/gap_fill_broker_dealers.py

    # Scan-only -- useful for size/cost preview before committing.
    python scripts/gap_fill_broker_dealers.py --scan-only

    # Non-interactive (cron) -- scan + fill without prompt.
    python scripts/gap_fill_broker_dealers.py --apply

    # First run after a code fix -- bypass cooldown so previously-
    # attempted BDs get a re-try with the new behavior.
    python scripts/gap_fill_broker_dealers.py --reset-cooldown

    # Cost-bounded smoke test on the first 25 eligible BDs.
    python scripts/gap_fill_broker_dealers.py --apply --limit 25
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
GAP_FILL_PIPELINE_NAME = "broker_dealer_gap_fill"
TRIGGER_SOURCE = "gap_fill_script"
# Number of BDs processed concurrently. Each BD's sub-pipelines already run
# in parallel via asyncio.gather inside run_refresh_all; this controls how
# many *BDs* the bulk loop has in flight at once. Defaults to 2 (≈ 2× the
# sequential pace). Override with GAP_FILL_CONCURRENCY=N for higher throughput
# if Gemini's per-key rate limit allows; drop to 1 to revert to sequential.
MAX_CONCURRENCY = int(os.environ.get("GAP_FILL_CONCURRENCY", "2"))


_PRIORITY_BUCKETS = ("hot", "warm", "cold", "none")


async def _flush_dns_cache() -> None:
    """Best-effort: flush the OS DNS resolver cache.

    Windows DNS Client caches negative responses ("host not found")
    for 15 minutes by default (NegativeCacheTime). A single Neon
    pooler endpoint blip then turns into a 15-minute apparent outage
    for the script: the first lookup fails, the negative result gets
    cached, and every subsequent BD's connect attempt hits the cache
    and "fails" instantly without even touching the network.

    Flushing the cache breaks the cascade. ``ipconfig /flushdns``
    works without admin rights. Subprocess failure is suppressed --
    if we can't flush, the next attempt will likely re-fail and the
    BD just stays unstamped for the next run.

    No-op on non-Windows -- Linux glibc doesn't aggressively cache
    negative DNS results the same way.
    """
    if sys.platform != "win32":
        return
    try:
        proc = await asyncio.create_subprocess_exec(
            "ipconfig", "/flushdns",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
    except Exception:
        pass


def _is_dns_or_transient_db_error(exc: BaseException) -> bool:
    """True if the exception looks like a DNS-cache cascade or a
    transient DB connection drop -- both of which are worth retrying
    after a DNS flush, not giving up on.

    Substring match on the chained exception message is the most
    robust signal across psycopg / sqlalchemy / asyncpg wrappers.
    """
    msg = str(exc)
    return (
        "getaddrinfo failed" in msg
        or "Name or service not known" in msg
        or "Temporary failure in name resolution" in msg
        or "server closed the connection" in msg
        or "Connection reset" in msg
        or "Connection refused" in msg
    )


def _print_scan_summary(
    *,
    total_eligible: int,
    gap_counts: dict[str, dict[str, int]],
    bds_with_any_gap_by_bucket: dict[str, int],
    pipeline_fires_by_bucket: dict[str, dict[str, int]],
    sub_label_for_table: dict[str, str],
    aggressive: bool,
    cooldown_msg: str,
) -> None:
    """Render the phase 1 summary table to stdout.

    Header line + per-column rows sorted by total gap count desc, then
    a "TOTAL BDs with at least one gap" row, then per-pipeline fire
    estimates by priority bucket. ASCII-only characters so the script
    is portable across Windows cmd / PowerShell / Linux terminals
    (cp1252 cannot encode the box-drawing characters).
    """
    aggressive_msg = "on" if aggressive else "off (strict)"
    header_line = (
        f"GAP-FILL SCAN  --  {total_eligible:,} eligible BDs "
        f"(cooldown = {cooldown_msg}, aggressive = {aggressive_msg})"
    )
    bar = "=" * max(len(header_line), 80)
    print(bar)
    print(header_line)
    print(bar)

    # Per-column row header.
    col_header = (
        f"  {'column':<40}"
        f"{'hot':>8}{'warm':>8}{'cold':>8}{'none':>8}"
    )
    print(col_header)
    print("  " + "-" * (40 + 8 * 4))

    # Sort columns by total gap count desc so the biggest offenders surface first.
    def _col_total(col_key: str) -> int:
        buckets = gap_counts[col_key]
        return sum(buckets.values())

    for col_key in sorted(gap_counts.keys(), key=lambda k: -_col_total(k)):
        buckets = gap_counts[col_key]
        row = f"  {col_key:<40}"
        for bucket in _PRIORITY_BUCKETS:
            row += f"{buckets.get(bucket, 0):>8,}"
        print(row)

    print("  " + "-" * (40 + 8 * 4))

    # "TOTAL BDs with at least one gap" -- the deduped count per bucket.
    total_row = f"  {'TOTAL BDs with at least one gap':<40}"
    grand_total = 0
    for bucket in _PRIORITY_BUCKETS:
        n = bds_with_any_gap_by_bucket.get(bucket, 0)
        grand_total += n
        total_row += f"{n:>8,}"
    print(total_row)
    print(f"  Grand total BDs with at least one gap: {grand_total:,} "
          f"/ {total_eligible:,} eligible "
          f"({100 * grand_total / total_eligible:.1f}%)")
    print()

    # Sub-pipeline fire estimates (how many *pipeline runs* would fire,
    # not how many *columns* are gapped).
    print("  Estimated sub-pipeline fires (per priority bucket):")
    for sub, label in sub_label_for_table.items():
        buckets = pipeline_fires_by_bucket.get(sub, {})
        parts = [f"{b}={buckets.get(b, 0):,}" for b in _PRIORITY_BUCKETS]
        total = sum(buckets.values())
        if total == 0:
            continue
        print(f"    {label:<14} {'  '.join(parts):<48}  total={total:,}")
    print(bar)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scan-only",
        action="store_true",
        help="Run phase 1 (read-only summary) only; do not mutate any rows.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Skip the interactive confirmation prompt. Intended for "
             "non-interactive runs (cron, CI). Phase 1 still runs first.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Use strict IS-NULL predicates only (no sentinel / "
             "detail-page widening). Matches the per-firm refresh-all "
             "endpoint's behavior. Default is aggressive=True.",
    )
    parser.add_argument(
        "--reset-cooldown",
        action="store_true",
        help="Ignore last_gap_fill_attempt_at and consider every BD "
             "eligible. Use this on the first run after a code change "
             "that should produce a different extraction outcome.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap to the first N eligible BDs. Useful for cost-bounded "
             "smoke tests before committing to a full pass.",
    )
    parser.add_argument(
        "--newest-first",
        action="store_true",
        help="Order the eligible walk by never-gap-filled + most-recently-"
             "created first, instead of the default highest-value order "
             "(priority bucket -> score -> id). Freshly-ingested BDs score "
             "'cold', so under the default order they sit behind the entire "
             "hot/warm/cold backlog and a bounded --limit never reaches them. "
             "The nightly new-BD gap-fill job passes this so overnight arrivals "
             "are enriched the same morning; leave it off for value-first passes.",
    )
    return parser.parse_args()


async def main() -> None:
    args = _parse_args()
    aggressive = not args.strict

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
        SUB_FOCUS_CONTACT,
        SUB_HEALTH_CHECK,
        SUB_REFRESH_CLEARING,
        SUB_REFRESH_FILINGS,
        SUB_REFRESH_FINANCIALS,
        SUB_RESOLVE_WEBSITE,
        decide_pipelines,
        gap_report_for,
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
        SUB_FOCUS_CONTACT: "focus-contact",
    }

    cutoff = datetime.now(timezone.utc) - timedelta(days=COOLDOWN_DAYS)

    priority_rank = case(
        (BrokerDealer.lead_priority == "hot", 0),
        (BrokerDealer.lead_priority == "warm", 1),
        (BrokerDealer.lead_priority == "cold", 2),
        else_=3,
    )

    # Ordering of the eligible walk. Default = highest-value first (priority
    # bucket -> lead_score -> id) so a bounded --limit spends the budget on the
    # best leads. --newest-first flips it to never-gap-filled + most-recently-
    # created first: freshly-ingested BDs score 'cold', so under the default
    # order they sit behind the entire hot/warm/cold backlog and a bounded
    # --limit never reaches them (the nightly BD gap-fill job relies on this so
    # overnight arrivals are enriched the same morning). NULLS FIRST on the
    # cooldown stamp keeps never-attempted rows ahead of the cooldown-expired
    # tail; created_at DESC then orders the newest ingests first.
    if args.newest_first:
        order_cols = (
            BrokerDealer.last_gap_fill_attempt_at.asc().nullsfirst(),
            BrokerDealer.created_at.desc(),
            BrokerDealer.id.desc(),
        )
    else:
        order_cols = (
            priority_rank,
            BrokerDealer.lead_score.desc().nullslast(),
            BrokerDealer.id,
        )

    # BDs whose last extraction attempt hit a retryable transient failure
    # (today: network_error from DNS / timeout / connection failures) are
    # eligible regardless of cooldown -- a network blip 12 days ago should
    # not gate a re-attempt for the next 18. See #399.
    retryable_bd_ids_subq = (
        select(ClearingArrangement.bd_id)
        .where(ClearingArrangement.extraction_status.in_(RETRYABLE_TRANSIENT_STATUSES))
        .distinct()
    )

    async with SessionLocal() as db:
        if args.reset_cooldown:
            # Bypass the cooldown filter entirely -- every BD is eligible.
            eligible_stmt = select(BrokerDealer).order_by(*order_cols)
        else:
            eligible_stmt = (
                select(BrokerDealer)
                .where(
                    or_(
                        BrokerDealer.last_gap_fill_attempt_at.is_(None),
                        BrokerDealer.last_gap_fill_attempt_at < cutoff,
                        BrokerDealer.id.in_(retryable_bd_ids_subq),
                    )
                )
                .order_by(*order_cols)
            )
        eligible = list((await db.execute(eligible_stmt)).scalars().all())
        contact_ids: set[int] = set(
            (await db.execute(select(ExecutiveContact.bd_id).distinct())).scalars().all()
        )
        # BDs that already carry a FOCUS-report filing contact. Gates the
        # focus-contact sub-pipeline: a BD outside this set still needs one
        # extracted (the only automated source of BD filing-contact emails).
        focus_contact_ids: set[int] = set(
            (
                await db.execute(
                    select(ExecutiveContact.bd_id)
                    .where(ExecutiveContact.source == "focus_report")
                    .distinct()
                )
            )
            .scalars()
            .all()
        )

    if args.limit is not None and args.limit > 0:
        eligible = eligible[: args.limit]

    total_eligible = len(eligible)
    if total_eligible == 0:
        print("No eligible broker-dealers (all stamped within cooldown).")
        return

    cooldown_msg = "reset" if args.reset_cooldown else f"{COOLDOWN_DAYS}d"
    aggressive_msg = "on" if aggressive else "off (strict)"
    print(
        f"Gap-fill scan: {total_eligible:,} eligible BDs "
        f"(cooldown = {cooldown_msg}, aggressive = {aggressive_msg}, "
        f"concurrency = {MAX_CONCURRENCY})."
    )
    print()

    # ── Phase 1 -- read-only gap scan ─────────────────────────────────
    # column key → bucket key → count. Bucket keys are "hot" / "warm" /
    # "cold" / "none". The "column" key embeds both the sub-pipeline
    # name and the BD column so the table reads sub_pipeline.column.
    gap_counts: dict[str, dict[str, int]] = defaultdict(
        lambda: defaultdict(int)
    )
    bds_with_any_gap_by_bucket: dict[str, int] = defaultdict(int)
    pipeline_fires_by_bucket: dict[str, dict[str, int]] = defaultdict(
        lambda: defaultdict(int)
    )

    sub_label_for_table = {
        SUB_REFRESH_FINANCIALS: "financials",
        SUB_RESOLVE_WEBSITE: "website",
        SUB_HEALTH_CHECK: "health",
        SUB_REFRESH_CLEARING: "clearing",
        SUB_ENRICH: "enrich",
        SUB_REFRESH_FILINGS: "filings",
        SUB_FOCUS_CONTACT: "focus",
    }

    def _bucket(bd: BrokerDealer) -> str:
        return bd.lead_priority if bd.lead_priority in ("hot", "warm", "cold") else "none"

    for bd in eligible:
        has_contacts = bd.id in contact_ids
        report = gap_report_for(
            bd,
            has_contacts,
            aggressive=aggressive,
            has_focus_contact=bd.id in focus_contact_ids,
        )
        bucket = _bucket(bd)
        had_any = False
        for sub, cols in report.items():
            if not cols:
                continue
            had_any = True
            pipeline_fires_by_bucket[sub][bucket] += 1
            label = sub_label_for_table.get(sub, sub)
            for col in cols:
                gap_counts[f"{label}.{col}"][bucket] += 1
        if had_any:
            bds_with_any_gap_by_bucket[bucket] += 1

    _print_scan_summary(
        total_eligible=total_eligible,
        gap_counts=gap_counts,
        bds_with_any_gap_by_bucket=bds_with_any_gap_by_bucket,
        pipeline_fires_by_bucket=pipeline_fires_by_bucket,
        sub_label_for_table=sub_label_for_table,
        aggressive=aggressive,
        cooldown_msg=cooldown_msg,
    )

    if args.scan_only:
        print()
        print("--scan-only: exiting before any mutations.")
        return

    if not args.apply:
        if not sys.stdin.isatty():
            print()
            print(
                "Non-interactive stdin detected and --apply not passed. "
                "Refusing to mutate without explicit confirmation. "
                "Re-run with --apply or --scan-only.",
                file=sys.stderr,
            )
            sys.exit(2)
        print()
        answer = input("Proceed with fill? (y/N): ").strip().lower()
        if answer != "y":
            print("Aborted by operator.")
            return

    print()
    print("Proceeding with fill. Row-by-row updates begin now.")
    print()

    counters: dict[str, int] = {"processed": 0, "skipped_noop": 0}
    sub_success: dict[str, int] = defaultdict(int)
    sub_failure: dict[str, int] = defaultdict(int)
    failures: list[tuple[int, str, str]] = []  # (bd_id, name, summary)
    started_wall = time.monotonic()
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

    async def _safe_stamp_cooldown(bd_id: int) -> None:
        """Stamp ``last_gap_fill_attempt_at`` on the BD, suppressing any
        DB error. A transient Neon DNS / pooler blip should not crash
        the worker -- the BD will be re-evaluated on the next pass."""
        try:
            async with SessionLocal() as db:
                fresh = await db.get(BrokerDealer, bd_id)
                if fresh is not None:
                    fresh.last_gap_fill_attempt_at = datetime.now(timezone.utc)
                    await db.commit()
        except Exception:
            pass

    async def _process_bd(idx: int, bd: BrokerDealer) -> None:
        """Process a single BD end-to-end. Acquires the semaphore so only
        ``MAX_CONCURRENCY`` BDs are in flight at once. Mutations to the
        shared counters / dicts / lists are safe under single-threaded
        asyncio (no preemption between ``+=`` and ``append``).

        Entire body is wrapped in try/except so any escape -- DB
        transient, network DNS blip, anything -- is logged as a worker
        crash and does NOT propagate to ``asyncio.gather``. Combined
        with ``return_exceptions=True`` on gather, this means one bad
        BD never tears down the rest of the batch.

        On DNS-cascade or transient-DB-drop errors, the worker
        proactively flushes the Windows DNS resolver cache and retries
        the BD once before giving up. Without this, a single Neon
        pooler blip would 15-minute-DNS-cache its way into dozens of
        consecutive BD failures (the cascade we observed twice on this
        machine)."""
        tag = f"[{idx:>5}/{total_eligible:>5}]"
        name_short = (bd.name or "")[:38]
        async with semaphore:
          for attempt in range(2):
            try:
                has_contacts = bd.id in contact_ids
                decision = decide_pipelines(
                    bd,
                    has_contacts,
                    scope="all",
                    aggressive=aggressive,
                    has_focus_contact=bd.id in focus_contact_ids,
                )
                to_run = decision.to_run
                to_skip = decision.to_skip

                if not to_run:
                    # Nothing to do -- still stamp so we don't re-evaluate every pass.
                    await _safe_stamp_cooldown(bd.id)
                    counters["skipped_noop"] += 1
                    print(f"{tag} BD {bd.id:<6} {name_short:<38}  noop (all targets already present)")
                    return

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
                    # Still stamp -- we tried. Suppress any DB-side error here so
                    # one bad blip on stamp doesn't escalate to a batch teardown.
                    await _safe_stamp_cooldown(bd.id)
                    counters["processed"] += 1
                    return

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
                await _safe_stamp_cooldown(bd.id)

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
                # Success -- exit the retry loop AND the function. Without
                # this return the for loop would iterate and re-run the BD.
                return

            except Exception as exc:
                # Last-resort catch-all. Any escape from the orchestrator-
                # crash branch's stamp call, the parent-PipelineRun creation,
                # or the read-children-notes block lands here. The BD stays
                # unstamped so the next invocation re-attempts it.
                if attempt == 0 and _is_dns_or_transient_db_error(exc):
                    # DNS or transient DB drop on the first attempt -- this
                    # is precisely the cascade Windows DNS Client negative
                    # caching turns into a 15-minute outage. Flush the OS
                    # cache, brief pause to let things settle, then retry
                    # this BD once before giving up.
                    print(
                        f"{tag} BD {bd.id:<6} {name_short:<38}  "
                        f"DNS/CONN BLIP -- flushing DNS cache and retrying once: "
                        f"{type(exc).__name__}: {str(exc)[:80]}"
                    )
                    await _flush_dns_cache()
                    await asyncio.sleep(2)
                    continue
                # Either a non-retryable error or the retry already failed.
                # Log and give up; do NOT propagate to gather.
                print(
                    f"{tag} BD {bd.id:<6} {name_short:<38}  WORKER CRASH: "
                    f"{type(exc).__name__}: {str(exc)[:120]}"
                )
                failures.append(
                    (bd.id, bd.name, f"worker crash: {type(exc).__name__}: {exc}")
                )
                counters["processed"] += 1
                return

    try:
        tasks = [
            _process_bd(idx, bd)
            for idx, bd in enumerate(eligible, start=1)
        ]
        # return_exceptions=True so one rogue worker that somehow escapes
        # _process_bd's own try/except doesn't tear down the batch. The
        # broad inner except already absorbs everything we expect; this is
        # belt-and-braces for the unexpected.
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for idx, result in enumerate(results, start=1):
            if isinstance(result, BaseException):
                # Unexpected escape -- log so it's at least visible in the
                # run output. The corresponding BD stays unstamped.
                bd = eligible[idx - 1]
                print(
                    f"[{idx:>5}/{total_eligible:>5}] BD {bd.id:<6} {(bd.name or '')[:38]:<38}  "
                    f"GATHER ESCAPE: {type(result).__name__}: {str(result)[:120]}"
                )
                failures.append(
                    (bd.id, bd.name, f"gather escape: {type(result).__name__}: {result}")
                )
    except KeyboardInterrupt:
        print()
        print("Interrupted by user. Resuming on next run via the cooldown stamp.")

    processed = counters["processed"]
    skipped_noop = counters["skipped_noop"]

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
