"""Bulk roster backfill for investment-advisor contacts (Job B).

Walks every 13F-filer advisor that carries an ``executive_officers`` roster
and materialises a per-officer ``advisor_contacts`` row via the discovery
chain — bypassing the ``_run_enrich_contacts`` idempotency guard that froze
most advisors at ~1 contact. Non-destructive: existing rows are gap-filled,
missing officers are created, nobody is overwritten, and re-runs don't
duplicate (officers de-dupe against existing rows by parsed first/last).

Why this exists
---------------
``scripts/gap_fill_investment_advisors.py`` runs the guarded enrich path, so
it skips any advisor that already has a contact row. On staging that's ~2,243
advisors stuck at one org-level row each, even though 2,951 carry a full
officer roster. This script is the one-time (per-cooldown) backfill that turns
those rosters into real per-officer contacts with LinkedIn / email / phone.

Provider coverage
-----------------
Each officer runs the full ``contact_discovery_chain`` (pdl, apollo_match,
hunter, snov, linkedin_search). ``linkedin_search`` prefers serper.dev and
falls back to SerpAPI — set ``SERPER_API_KEY`` for cheap bulk Google
(SerpAPI's free tier caps at 100/month and will block a full sweep). The
banner below prints which provider keys are visible so you can confirm
serper is wired in before committing to the run. Apollo phone-reveal lands
mobiles asynchronously via the webhook after each match.

Cost
----
This fans the chain out across tens of thousands of officers. Apollo charges
per match (+ per mobile reveal); serper ~$0.30/1k; SerpAPI ~$0.015/query.
Use ``--scan-only`` first to size it, ``--limit N`` for a bounded smoke.

Cooldown
--------
Shares ``investment_advisors.last_gap_fill_attempt_at`` (30 days) with the
gap-fill flows, so an advisor backfilled here is skipped by the nightly
gap-fill for 30 days and vice-versa. ``--reset-cooldown`` ignores it.

Usage:
    python scripts/backfill_advisor_roster_contacts.py --scan-only
    python scripts/backfill_advisor_roster_contacts.py --apply --limit 25
    python scripts/backfill_advisor_roster_contacts.py --apply --reset-cooldown
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import selectors
import sys
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)
if sys.platform == "win32" and sys.version_info < (3, 14):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


COOLDOWN_DAYS = 30
TRIGGER_SOURCE = "roster_backfill_script"
MAX_CONCURRENCY = int(os.environ.get("GAP_FILL_CONCURRENCY", "4"))


def _looks_like_prod(database_url: str) -> bool:
    try:
        host = (urlparse(database_url).hostname or "").lower()
    except Exception:
        return False
    return "prod" in host


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


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scan-only", action="store_true",
                        help="Read-only sizing summary; no mutations.")
    parser.add_argument("--apply", action="store_true",
                        help="Skip the interactive confirmation prompt.")
    parser.add_argument("--reset-cooldown", action="store_true",
                        help="Ignore last_gap_fill_attempt_at; consider every roster advisor.")
    parser.add_argument("--limit", type=int, default=None,
                        help="Cap to the first N eligible advisors (AUM desc). For smoke runs.")
    parser.add_argument("--allow-prod", action="store_true",
                        help="Acknowledge DATABASE_URL points at production.")
    return parser.parse_args()


def _provider_banner() -> str:
    """One line listing which discovery-chain provider keys are visible, so the
    operator can confirm serper (cheap LinkedIn search) is wired in before a
    full sweep."""
    from app.core.config import settings

    def mark(present: bool) -> str:
        return "set" if present else "MISSING"

    serper = bool(getattr(settings, "serper_api_key", None))
    serpapi = bool(getattr(settings, "serpapi_api_key", None))
    parts = [
        f"pdl={mark(bool(settings.pdl_api_key))}",
        f"apollo={mark(bool(settings.apollo_api_key))}",
        f"hunter={mark(bool(settings.hunter_api_key))}",
        f"serper={mark(serper)}",
        f"serpapi={mark(serpapi)}",
        f"chain={settings.contact_discovery_chain}",
    ]
    line = "Providers: " + "  ".join(parts)
    if not serper and not serpapi:
        line += "\n  WARNING: neither serper nor serpapi key set — linkedin_search will no-op."
    elif not serper and serpapi:
        line += ("\n  NOTE: serper key absent — linkedin_search uses SerpAPI "
                 "(free tier caps at 100/mo; set SERPER_API_KEY for a full sweep).")
    return line


async def main() -> None:
    args = _parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL is not set.")
        sys.exit(2)
    db_host = urlparse(database_url).hostname or "?"
    print(f"DATABASE_URL host: {db_host}")
    if _looks_like_prod(database_url) and not args.allow_prod:
        print("ERROR: host looks like production; refusing without --allow-prod "
              "(standing instruction is staging-only).", file=sys.stderr)
        sys.exit(2)

    from sqlalchemy import or_, select

    from app.db.session import SessionLocal
    from app.models.investment_advisor import InvestmentAdvisor
    from app.models.pipeline_run import PipelineRun
    from app.services.advisor_refresh_orchestrator import (
        BACKFILL_ROSTER_CONTACTS_PIPELINE_NAME,
        _run_backfill_roster_contacts,
    )

    print(_provider_banner())
    print()

    cutoff = datetime.now(timezone.utc) - timedelta(days=COOLDOWN_DAYS)
    # Eligible: 13F filer + has a non-empty executive_officers roster + cooldown.
    roster_present = InvestmentAdvisor.executive_officers.isnot(None)
    async with SessionLocal() as db:
        conds = [InvestmentAdvisor.files_13f.is_(True), roster_present]
        if not args.reset_cooldown:
            conds.append(
                or_(
                    InvestmentAdvisor.last_gap_fill_attempt_at.is_(None),
                    InvestmentAdvisor.last_gap_fill_attempt_at < cutoff,
                )
            )
        stmt = (
            select(InvestmentAdvisor)
            .where(*conds)
            .order_by(
                InvestmentAdvisor.regulatory_aum.desc().nullslast(),
                InvestmentAdvisor.id,
            )
        )
        eligible = list((await db.execute(stmt)).scalars().all())

    # Drop advisors whose roster JSONB is an empty list (the isnot(None) filter
    # can't see inside JSONB), and compute officer totals for sizing.
    def _roster_len(adv: InvestmentAdvisor) -> int:
        r = adv.executive_officers
        return len(r) if isinstance(r, list) else 0

    eligible = [a for a in eligible if _roster_len(a) > 0]
    if args.limit is not None and args.limit > 0:
        eligible = eligible[: args.limit]

    total = len(eligible)
    total_officers = sum(_roster_len(a) for a in eligible)
    cooldown_msg = "reset" if args.reset_cooldown else f"{COOLDOWN_DAYS}d"

    # ── Scan summary ──────────────────────────────────────────────────
    by_tier: dict[str, dict[str, int]] = {}
    for a in eligible:
        t = _aum_tier(a.regulatory_aum)
        b = by_tier.setdefault(t, {"advisors": 0, "officers": 0})
        b["advisors"] += 1
        b["officers"] += _roster_len(a)

    print("=" * 78)
    print(f"ROSTER BACKFILL SCAN  --  {total:,} eligible advisors "
          f"(cooldown = {cooldown_msg}, concurrency = {MAX_CONCURRENCY})")
    print(f"Total roster officers to process: {total_officers:,}")
    print("-" * 78)
    print(f"  {'AUM tier':<12}{'advisors':>12}{'officers':>12}")
    for t in (">=$10B", "$1B-$10B", "$100M-$1B", "<$100M", "unknown"):
        if t in by_tier:
            print(f"  {t:<12}{by_tier[t]['advisors']:>12,}{by_tier[t]['officers']:>12,}")
    print("=" * 78)

    if total == 0:
        print("No eligible advisors (all within cooldown, or none with a roster).")
        return

    if args.scan_only:
        print("\n--scan-only: exiting before any mutations.")
        return

    if not args.apply:
        if not sys.stdin.isatty():
            print("\nNon-interactive stdin and no --apply; refusing to mutate.",
                  file=sys.stderr)
            sys.exit(2)
        ans = input("\nProceed with roster backfill? (y/N): ").strip().lower()
        if ans != "y":
            print("Aborted by operator.")
            return

    print("\nProceeding. Per-advisor backfill begins now.\n")
    counters = {"processed": 0, "failed": 0}
    started = time.monotonic()
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

    async def _process(idx: int, advisor: InvestmentAdvisor) -> None:
        tag = f"[{idx:>5}/{total:>5}]"
        name_short = (advisor.name or "")[:38]
        async with semaphore:
            try:
                async with SessionLocal() as db:
                    run = PipelineRun(
                        pipeline_name=BACKFILL_ROSTER_CONTACTS_PIPELINE_NAME,
                        trigger_source=TRIGGER_SOURCE,
                        status="queued",
                        total_items=1,
                        processed_items=0,
                        success_count=0,
                        failure_count=0,
                        notes=json.dumps({"advisor_id": advisor.id, "stage": "queued"}),
                        started_at=datetime.now(timezone.utc),
                    )
                    db.add(run)
                    await db.commit()
                    await db.refresh(run)
                    run_id = run.id

                t0 = time.monotonic()
                await _run_backfill_roster_contacts(run_id, advisor.id, TRIGGER_SOURCE)
                elapsed = time.monotonic() - t0

                async with SessionLocal() as db:
                    fresh = await db.get(PipelineRun, run_id)
                    summary = ""
                    status = fresh.status if fresh else "unknown"
                    if fresh and fresh.notes:
                        try:
                            summary = json.loads(fresh.notes).get("summary", "")
                        except json.JSONDecodeError:
                            pass
                if status == "failed":
                    counters["failed"] += 1
                counters["processed"] += 1
                print(f"{tag} IA {advisor.id:<6} {name_short:<38}  {elapsed:>5.1f}s  "
                      f"{status}: {summary[:90]}")
            except Exception as exc:
                counters["failed"] += 1
                counters["processed"] += 1
                print(f"{tag} IA {advisor.id:<6} {name_short:<38}  CRASH: "
                      f"{type(exc).__name__}: {str(exc)[:100]}")

    try:
        await asyncio.gather(
            *[_process(i, a) for i, a in enumerate(eligible, start=1)],
            return_exceptions=True,
        )
    except KeyboardInterrupt:
        print("\nInterrupted. Cooldown stamps let a re-run resume where it left off.")

    wall = time.monotonic() - started
    print("\n" + "=" * 70)
    print("  ROSTER BACKFILL SUMMARY")
    print("=" * 70)
    print(f"Advisors processed: {counters['processed']:>6,}")
    print(f"Advisors failed:    {counters['failed']:>6,}")
    print(f"Wall-clock:         {wall/60:>6.1f} min")
    print("=" * 70)


if __name__ == "__main__":
    if sys.platform == "win32" and sys.version_info >= (3, 14):
        with asyncio.Runner(
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())
        ) as runner:
            runner.run(main())
    else:
        asyncio.run(main())
