"""Bulk row-by-row gap-fill for investment_advisors.

Walks every 13F-filer advisor in AUM-descending order, inspects which
fields are still NULL via the orchestrator's ``decide_pipelines``
gate, and fires the corresponding sub-pipelines via the existing
``run_advisor_refresh`` orchestrator. Skips advisors whose
``last_gap_fill_attempt_at`` is within the last ``COOLDOWN_DAYS``
unless ``--reset-cooldown`` is passed.

Five sub-pipelines participate -- owners/officers (Gemini ADV
extraction), website resolution, EDGAR filings refresh, advisor
contact discovery, and IAPD per-firm summary. ``decide_pipelines``
gates each one against the advisor's current row state.

Scope is ``files_13f = TRUE`` (the master-list endpoint's default at
``backend/app/api/v1/endpoints/investment_advisors.py``), so the pass
covers exactly the advisor population the client sees on /advisor-list.

Designed to run unattended for hours. Interruptible with Ctrl+C; the
cooldown stamp is set per-advisor so a rerun resumes automatically.

IA analog of ``scripts/gap_fill_broker_dealers.py``. Two key
behavioural differences vs. the BD script:

- No re-scoring at the end -- IAs don't carry ``lead_score`` /
  ``lead_priority``. Ordering instead uses ``regulatory_aum`` DESC.
- No ``aggressive`` flag -- the IA orchestrator's ``decide_pipelines``
  takes a single positional argument and already enforces its own
  per-firm cooldown (``ADV_REEXTRACT_COOLDOWN``).

Usage:
    # Default: scan, summarize, prompt, then fill.
    python scripts/gap_fill_investment_advisors.py

    # Scan-only -- size/cost preview before committing.
    python scripts/gap_fill_investment_advisors.py --scan-only

    # Non-interactive (cron) -- scan + fill without prompt.
    python scripts/gap_fill_investment_advisors.py --apply

    # Cost-bounded smoke on the first 25 eligible advisors.
    python scripts/gap_fill_investment_advisors.py --apply --limit 25

    # Bypass cooldown after a code fix.
    python scripts/gap_fill_investment_advisors.py --reset-cooldown
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
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if sys.platform == "win32" and sys.version_info < (3, 14):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


COOLDOWN_DAYS = 30
GAP_FILL_PIPELINE_NAME = "investment_advisor_gap_fill"
TRIGGER_SOURCE = "gap_fill_script"
# Number of advisors processed concurrently. Each advisor's sub-pipelines
# already run in parallel via asyncio.gather inside run_advisor_refresh; this
# controls how many *advisors* the bulk loop has in flight at once. Defaults
# to 2 (≈ 2× sequential). Override with GAP_FILL_CONCURRENCY=N. Drop to 1 for
# strict sequential.
MAX_CONCURRENCY = int(os.environ.get("GAP_FILL_CONCURRENCY", "2"))


_TIER_LABELS = (">=$10B", "$1B-$10B", "$100M-$1B", "<$100M", "unknown")


def _aum_tier(value: Decimal | None) -> str:
    if value is None:
        return "unknown"
    aum = Decimal(value)
    if aum >= Decimal("10000000000"):
        return ">=$10B"
    if aum >= Decimal("1000000000"):
        return "$1B-$10B"
    if aum >= Decimal("100000000"):
        return "$100M-$1B"
    return "<$100M"


async def _flush_dns_cache() -> None:
    """Best-effort: flush the OS DNS resolver cache.

    Windows DNS Client caches negative responses ("host not found") for
    15 minutes by default. A single Neon pooler endpoint blip then turns
    into a 15-minute apparent outage as every subsequent advisor's
    connect attempt hits the cache and "fails" instantly.

    ``ipconfig /flushdns`` works without admin rights. No-op on
    non-Windows.
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
    msg = str(exc)
    return (
        "getaddrinfo failed" in msg
        or "Name or service not known" in msg
        or "Temporary failure in name resolution" in msg
        or "server closed the connection" in msg
        or "Connection reset" in msg
        or "Connection refused" in msg
    )


def _looks_like_prod(database_url: str) -> bool:
    """Heuristic: does the URL host string suggest production?

    User's standing instruction is staging-only. We refuse --apply
    without --allow-prod when the host looks like prod. Conservative:
    only triggers on the literal substring 'prod' in the host so
    ``ep-noisy-lab`` (the suspected prod Neon endpoint) is caught
    nominally if it's ever renamed, and a staging host that happens to
    embed ``prod`` is opt-in via --allow-prod.
    """
    try:
        host = (urlparse(database_url).hostname or "").lower()
    except Exception:
        return False
    return "prod" in host


def _print_scan_summary(
    *,
    total_eligible: int,
    null_counts_by_tier: dict[str, dict[str, int]],
    advisors_with_gap_by_tier: dict[str, int],
    pipeline_fires_by_tier: dict[str, dict[str, int]],
    sub_label_for_table: dict[str, str],
    cooldown_msg: str,
) -> None:
    header_line = (
        f"ADVISOR GAP-FILL SCAN  --  {total_eligible:,} eligible (files_13f=TRUE) "
        f"(cooldown = {cooldown_msg})"
    )
    bar = "=" * max(len(header_line), 80)
    print(bar)
    print(header_line)
    print(bar)

    col_header = f"  {'column':<28}"
    for tier in _TIER_LABELS:
        col_header += f"{tier:>12}"
    print(col_header)
    print("  " + "-" * (28 + 12 * len(_TIER_LABELS)))

    def _col_total(col_key: str) -> int:
        return sum(null_counts_by_tier[col_key].values())

    for col_key in sorted(null_counts_by_tier.keys(), key=lambda k: -_col_total(k)):
        per_tier = null_counts_by_tier[col_key]
        row = f"  {col_key:<28}"
        for tier in _TIER_LABELS:
            row += f"{per_tier.get(tier, 0):>12,}"
        print(row)

    print("  " + "-" * (28 + 12 * len(_TIER_LABELS)))
    total_row = f"  {'advisors with any gap':<28}"
    grand_total = 0
    for tier in _TIER_LABELS:
        n = advisors_with_gap_by_tier.get(tier, 0)
        grand_total += n
        total_row += f"{n:>12,}"
    print(total_row)
    print(
        f"  Grand total advisors with at least one gap: {grand_total:,} "
        f"/ {total_eligible:,} eligible "
        f"({100 * grand_total / max(total_eligible, 1):.1f}%)"
    )
    print()

    print("  Estimated sub-pipeline fires (per AUM tier):")
    for sub, label in sub_label_for_table.items():
        per_tier = pipeline_fires_by_tier.get(sub, {})
        parts = [f"{t}={per_tier.get(t, 0):,}" for t in _TIER_LABELS]
        total = sum(per_tier.values())
        if total == 0:
            continue
        print(f"    {label:<22} {'  '.join(parts):<70}  total={total:,}")
    print(bar)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scan-only",
        action="store_true",
        help="Run phase 1 (read-only summary) only; no mutations.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Skip the interactive confirmation prompt. Intended for "
             "non-interactive runs. Phase 1 still runs first.",
    )
    parser.add_argument(
        "--reset-cooldown",
        action="store_true",
        help="Ignore last_gap_fill_attempt_at and consider every "
             "13F-filer advisor eligible.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap to the first N eligible advisors (by AUM desc). "
             "Use for cost-bounded smoke runs.",
    )
    parser.add_argument(
        "--allow-prod",
        action="store_true",
        help="Acknowledge that DATABASE_URL points at production. "
             "Required when the host string contains 'prod'.",
    )
    return parser.parse_args()


async def main() -> None:
    args = _parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL is not set.")
        sys.exit(2)
    db_host = (urlparse(database_url).hostname or "?")
    print(f"DATABASE_URL host: {db_host}")
    if _looks_like_prod(database_url) and not args.allow_prod:
        print(
            "ERROR: DATABASE_URL host looks like production. Refusing to run "
            "without --allow-prod. Standing operator instruction is "
            "staging-only.",
            file=sys.stderr,
        )
        sys.exit(2)

    from sqlalchemy import or_, select

    from app.db.session import SessionLocal
    from app.models.advisor_contact import AdvisorContact
    from app.models.investment_advisor import InvestmentAdvisor
    from app.models.pipeline_run import PipelineRun
    from app.services.advisor_refresh_orchestrator import (
        SUB_ENRICH_CONTACTS,
        SUB_REFRESH_FILINGS,
        SUB_REFRESH_IAPD_SUMMARY,
        SUB_REFRESH_OWNERS_OFFICERS,
        SUB_RESOLVE_ADVISOR_WEBSITE,
        decide_pipelines,
        required_provider_keys,
        run_advisor_refresh,
    )

    sub_label = {
        SUB_REFRESH_OWNERS_OFFICERS: "owners",
        SUB_RESOLVE_ADVISOR_WEBSITE: "website",
        SUB_REFRESH_FILINGS: "filings",
        SUB_ENRICH_CONTACTS: "contacts",
        SUB_REFRESH_IAPD_SUMMARY: "iapd",
    }

    cutoff = datetime.now(timezone.utc) - timedelta(days=COOLDOWN_DAYS)

    async with SessionLocal() as db:
        if args.reset_cooldown:
            eligible_stmt = (
                select(InvestmentAdvisor)
                .where(InvestmentAdvisor.files_13f.is_(True))
                .order_by(
                    InvestmentAdvisor.regulatory_aum.desc().nullslast(),
                    InvestmentAdvisor.id,
                )
            )
        else:
            eligible_stmt = (
                select(InvestmentAdvisor)
                .where(
                    InvestmentAdvisor.files_13f.is_(True),
                    or_(
                        InvestmentAdvisor.last_gap_fill_attempt_at.is_(None),
                        InvestmentAdvisor.last_gap_fill_attempt_at < cutoff,
                    ),
                )
                .order_by(
                    InvestmentAdvisor.regulatory_aum.desc().nullslast(),
                    InvestmentAdvisor.id,
                )
            )
        eligible = list((await db.execute(eligible_stmt)).scalars().all())
        contact_ids: set[int] = set(
            (
                await db.execute(select(AdvisorContact.advisor_id).distinct())
            ).scalars().all()
        )

    if args.limit is not None and args.limit > 0:
        eligible = eligible[: args.limit]

    total_eligible = len(eligible)
    if total_eligible == 0:
        print(
            "No eligible advisors (all stamped within cooldown, or none with "
            "files_13f=TRUE)."
        )
        return

    cooldown_msg = "reset" if args.reset_cooldown else f"{COOLDOWN_DAYS}d"
    print(
        f"Gap-fill scan: {total_eligible:,} eligible advisors "
        f"(cooldown = {cooldown_msg}, concurrency = {MAX_CONCURRENCY})."
    )
    print()

    # ── Phase 1 -- read-only gap scan ─────────────────────────────────
    sub_label_for_table = {
        SUB_REFRESH_OWNERS_OFFICERS: "owners (Gemini)",
        SUB_RESOLVE_ADVISOR_WEBSITE: "resolve-website",
        SUB_REFRESH_FILINGS: "refresh-filings",
        SUB_ENRICH_CONTACTS: "enrich-contacts",
        SUB_REFRESH_IAPD_SUMMARY: "refresh-iapd",
    }

    # Columns the orchestrator targets, tracked per-tier so the scan
    # surfaces which AUM band carries the biggest gap.
    SCAN_COLUMNS = (
        "executive_officers",
        "direct_owners",
        "indirect_owners",
        "firm_operations_text",
        "client_types",
        "website",
        "website_source",
        "registration_date",
        "formation_date",
    )

    null_counts_by_tier: dict[str, dict[str, int]] = {
        col: defaultdict(int) for col in SCAN_COLUMNS
    }
    advisors_with_gap_by_tier: dict[str, int] = defaultdict(int)
    pipeline_fires_by_tier: dict[str, dict[str, int]] = {
        sub: defaultdict(int) for sub in sub_label_for_table
    }

    def _is_empty(value: object) -> bool:
        if value is None:
            return True
        if isinstance(value, (list, dict, str)) and len(value) == 0:
            return True
        return False

    for advisor in eligible:
        tier = _aum_tier(advisor.regulatory_aum)
        had_any = False

        for col in SCAN_COLUMNS:
            if _is_empty(getattr(advisor, col)):
                null_counts_by_tier[col][tier] += 1
                had_any = True

        if advisor.id not in contact_ids:
            had_any = True

        decision = decide_pipelines(advisor)
        for sub in decision.to_run:
            if sub in pipeline_fires_by_tier:
                pipeline_fires_by_tier[sub][tier] += 1
                had_any = True

        if had_any:
            advisors_with_gap_by_tier[tier] += 1

    _print_scan_summary(
        total_eligible=total_eligible,
        null_counts_by_tier=null_counts_by_tier,
        advisors_with_gap_by_tier=advisors_with_gap_by_tier,
        pipeline_fires_by_tier=pipeline_fires_by_tier,
        sub_label_for_table=sub_label_for_table,
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

    counters: dict[str, int] = {"processed": 0, "skipped_noop": 0, "skipped_keys": 0}
    sub_success: dict[str, int] = defaultdict(int)
    sub_failure: dict[str, int] = defaultdict(int)
    failures: list[tuple[int, str, str]] = []
    started_wall = time.monotonic()
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

    async def _safe_stamp_cooldown(advisor_id: int) -> None:
        try:
            async with SessionLocal() as db:
                fresh = await db.get(InvestmentAdvisor, advisor_id)
                if fresh is not None:
                    fresh.last_gap_fill_attempt_at = datetime.now(timezone.utc)
                    await db.commit()
        except Exception:
            pass

    async def _process_advisor(idx: int, advisor: InvestmentAdvisor) -> None:
        tag = f"[{idx:>5}/{total_eligible:>5}]"
        name_short = (advisor.name or "")[:38]
        async with semaphore:
          for attempt in range(2):
            try:
                decision = decide_pipelines(advisor)
                to_run = decision.to_run
                to_skip = decision.to_skip

                if not to_run:
                    await _safe_stamp_cooldown(advisor.id)
                    counters["skipped_noop"] += 1
                    print(
                        f"{tag} IA {advisor.id:<6} {name_short:<38}  "
                        f"noop (all targets already present)"
                    )
                    return

                missing_keys = required_provider_keys(to_run)
                if missing_keys:
                    # Pre-flight: required provider keys are absent in this
                    # process env. Don't stamp -- when keys are configured the
                    # next pass should reconsider this advisor.
                    counters["skipped_keys"] += 1
                    print(
                        f"{tag} IA {advisor.id:<6} {name_short:<38}  "
                        f"SKIPPED -- missing provider keys: "
                        f"{','.join(missing_keys)}"
                    )
                    return

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
                                "advisor_id": advisor.id,
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
                    await run_advisor_refresh(
                        parent_id,
                        advisor.id,
                        trigger_source=TRIGGER_SOURCE,
                        pipelines_to_run=to_run,
                        pipelines_to_skip=to_skip,
                    )
                except Exception as exc:
                    print(
                        f"{tag} IA {advisor.id:<6} {name_short:<38}  "
                        f"ORCHESTRATOR CRASH: {type(exc).__name__}: "
                        f"{str(exc)[:120]}"
                    )
                    failures.append(
                        (advisor.id, advisor.name, f"{type(exc).__name__}: {exc}")
                    )
                    await _safe_stamp_cooldown(advisor.id)
                    counters["processed"] += 1
                    return

                elapsed = time.monotonic() - t0

                async with SessionLocal() as db:
                    refreshed_parent = await db.get(PipelineRun, parent_id)
                    payload = (
                        json.loads(refreshed_parent.notes or "{}")
                        if refreshed_parent
                        else {}
                    )
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

                await _safe_stamp_cooldown(advisor.id)
                counters["processed"] += 1
                ran_str = ",".join(ok_subs) if ok_subs else "-"
                fail_str = (
                    " failed:[" + ",".join(fail_subs) + "]" if fail_subs else ""
                )
                print(
                    f"{tag} IA {advisor.id:<6} {name_short:<38}  "
                    f"{elapsed:>5.1f}s  ran:[{ran_str}]{fail_str}"
                )

                if fail_subs:
                    failures.append(
                        (advisor.id, advisor.name, f"failed: {','.join(fail_subs)}")
                    )
                return

            except Exception as exc:
                if attempt == 0 and _is_dns_or_transient_db_error(exc):
                    print(
                        f"{tag} IA {advisor.id:<6} {name_short:<38}  "
                        f"DNS/CONN BLIP -- flushing DNS cache and retrying once: "
                        f"{type(exc).__name__}: {str(exc)[:80]}"
                    )
                    await _flush_dns_cache()
                    await asyncio.sleep(2)
                    continue
                print(
                    f"{tag} IA {advisor.id:<6} {name_short:<38}  WORKER CRASH: "
                    f"{type(exc).__name__}: {str(exc)[:120]}"
                )
                failures.append(
                    (
                        advisor.id,
                        advisor.name,
                        f"worker crash: {type(exc).__name__}: {exc}",
                    )
                )
                counters["processed"] += 1
                return

    try:
        tasks = [
            _process_advisor(idx, advisor)
            for idx, advisor in enumerate(eligible, start=1)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for idx, result in enumerate(results, start=1):
            if isinstance(result, BaseException):
                advisor = eligible[idx - 1]
                print(
                    f"[{idx:>5}/{total_eligible:>5}] IA {advisor.id:<6} "
                    f"{(advisor.name or '')[:38]:<38}  "
                    f"GATHER ESCAPE: {type(result).__name__}: "
                    f"{str(result)[:120]}"
                )
                failures.append(
                    (
                        advisor.id,
                        advisor.name,
                        f"gather escape: {type(result).__name__}: {result}",
                    )
                )
    except KeyboardInterrupt:
        print()
        print("Interrupted by user. Resuming on next run via the cooldown stamp.")

    processed = counters["processed"]
    skipped_noop = counters["skipped_noop"]
    skipped_keys = counters["skipped_keys"]

    wall = time.monotonic() - started_wall
    print()
    print("=" * 70)
    print("  ADVISOR GAP-FILL SUMMARY")
    print("=" * 70)
    print(f"Advisors processed (orchestrator fired):   {processed:>6,}")
    print(f"Advisors skipped (no-op, stamped only):    {skipped_noop:>6,}")
    print(f"Advisors skipped (provider keys missing):  {skipped_keys:>6,}")
    print(f"Wall-clock:                                {wall/60:>6.1f} min")
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
        for advisor_id, name, summary in failures[:20]:
            display = (name or "")[:40]
            print(f"  IA {advisor_id:<6} {display:<40} - {summary[:100]}")
    print("=" * 70)


if __name__ == "__main__":
    if sys.platform == "win32" and sys.version_info >= (3, 14):
        with asyncio.Runner(
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())
        ) as runner:
            runner.run(main())
    else:
        asyncio.run(main())
