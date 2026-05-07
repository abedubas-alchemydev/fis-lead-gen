"""Read-only gap-fill scope inspect for the broker-dealer master list.

Prints a per-column NULL report plus a per-sub-pipeline "would fire"
tally that mirrors what the upcoming bulk gap-fill runner will actually
execute. ``resolve-website`` is excluded from the would-fire count
because a separate website backfill owns that pipeline.

No writes, no external API calls. Connects to whichever database
``DATABASE_URL`` points at.
"""
from __future__ import annotations

import asyncio
import os
import selectors
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if sys.platform == "win32" and sys.version_info < (3, 14):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


TARGET_COLUMNS: list[tuple[str, str, str]] = [
    ("latest_net_capital", "latest_net_capital", ""),
    ("yoy_growth", "yoy_growth", ""),
    ("health_status", "health_status", ""),
    ("current_clearing_partner", "current_clearing_partner", ""),
    ("current_clearing_type", "current_clearing_type", ""),
    ("registration_date", "registration_date", ""),
    ("formation_date", "formation_date", ""),
    ("last_filing_date", "last_filing_date", ""),
    ("executive_officers", "executive_officers", ""),
    ("direct_owners", "direct_owners", ""),
    ("types_of_business", "types_of_business", ""),
    ("website", "website", "[owned by separate backfill]"),
]


def _is_empty(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, list) and len(value) == 0:
        return True
    return False


async def main() -> None:
    if not os.environ.get("DATABASE_URL"):
        print("ERROR: DATABASE_URL is not set. Point at staging before running.")
        sys.exit(2)

    from sqlalchemy import select

    from app.db.session import SessionLocal
    from app.models.broker_dealer import BrokerDealer
    from app.models.executive_contact import ExecutiveContact
    from app.services.refresh_all_orchestrator import (
        SUB_ENRICH,
        SUB_HEALTH_CHECK,
        SUB_REFRESH_FILINGS,
        SUB_REFRESH_FINANCIALS,
        SUB_RESOLVE_WEBSITE,
        decide_pipelines,
    )

    async with SessionLocal() as db:
        bd_rows = (await db.execute(select(BrokerDealer))).scalars().all()
        contact_ids: set[int] = set(
            (await db.execute(select(ExecutiveContact.bd_id).distinct())).scalars().all()
        )

    total = len(bd_rows)
    if total == 0:
        print("No broker-dealers found.")
        return

    null_counts: dict[str, int] = {col: 0 for col, _, _ in TARGET_COLUMNS}
    for bd in bd_rows:
        for col, _, _ in TARGET_COLUMNS:
            if _is_empty(getattr(bd, col)):
                null_counts[col] += 1

    contacts_present = sum(1 for bd in bd_rows if bd.id in contact_ids)

    fire_counts: dict[str, int] = defaultdict(int)
    priority_counts: dict[str | None, dict[str, int]] = defaultdict(
        lambda: defaultdict(int)
    )
    hot_missing: list[tuple[int, str, list[str]]] = []

    SUB_LABELS = {
        SUB_REFRESH_FINANCIALS: "refresh-financials",
        SUB_HEALTH_CHECK: "health-check",
        SUB_ENRICH: "enrich (Apollo contacts)",
        SUB_REFRESH_FILINGS: "refresh-filings",
    }

    for bd in bd_rows:
        has_contacts = bd.id in contact_ids
        decision = decide_pipelines(bd, has_contacts, scope="all")
        to_run = tuple(p for p in decision.to_run if p != SUB_RESOLVE_WEBSITE)
        for sub in to_run:
            if sub in SUB_LABELS:
                fire_counts[sub] += 1

        bucket = priority_counts[bd.lead_priority]
        bucket["total"] += 1
        if _is_empty(bd.latest_net_capital):
            bucket["financials"] += 1
        if _is_empty(bd.current_clearing_partner) or _is_empty(bd.current_clearing_type):
            bucket["clearing"] += 1
        if not has_contacts:
            bucket["contacts"] += 1

        if bd.lead_priority == "hot":
            missing: list[str] = []
            if _is_empty(bd.latest_net_capital):
                missing.append("financials")
            if _is_empty(bd.current_clearing_partner) or _is_empty(bd.current_clearing_type):
                missing.append("clearing")
            if not has_contacts:
                missing.append("contacts")
            if _is_empty(bd.last_filing_date) and bd.cik:
                missing.append("filings")
            if _is_empty(bd.registration_date) or _is_empty(bd.formation_date):
                missing.append("dates")
            if missing:
                hot_missing.append((bd.id, bd.name, missing))

    bar = "=" * 70
    print(bar)
    print("  BROKER-DEALER GAP-FILL INSPECT")
    print(bar)
    print(f"Total BDs in master list:               {total:>6,}")
    print()
    print("---- NULL counts (target of gap-fill) ----")
    for col, label, note in TARGET_COLUMNS:
        n = null_counts[col]
        pct = round(100 * n / total)
        suffix = f"   {note}" if note else ""
        print(f"  {label:<32} {n:>6,}  ({pct:>3}%){suffix}")
    pct_present = round(100 * contacts_present / total)
    print(
        f"  {'contacts present?':<32} {contacts_present:>6,}  "
        f"({pct_present:>3}% have >=1 contact)"
    )
    print()
    print("---- Sub-pipelines the bulk gap-fill would fire ----")
    for sub, label in SUB_LABELS.items():
        print(f"  {label:<28} {fire_counts[sub]:>6,} BDs")
    print(f"  {'resolve-website':<28} [excluded - owned by parallel backfill]")
    print()
    print("---- Coverage by lead priority ----")
    for prio in ["hot", "warm", "cold", None]:
        bucket = priority_counts.get(prio)
        if not bucket:
            continue
        label = prio if prio is not None else "(no priority)"
        print(
            f"  {label:<14} ({bucket['total']:>5,} BDs)  "
            f"financials: {bucket['financials']:>5,}, "
            f"clearing: {bucket['clearing']:>5,}, "
            f"contacts: {bucket['contacts']:>5,}"
        )
    print()
    sample = sorted(hot_missing, key=lambda x: -len(x[2]))[:10]
    if sample:
        print("---- Top-10 hot leads with most NULLs (sample) ----")
        for bd_id, name, missing in sample:
            display_name = name[:40]
            print(f"  BD {bd_id:<6} {display_name:<40}  missing: [{', '.join(missing)}]")
    print(bar)


if __name__ == "__main__":
    if sys.platform == "win32" and sys.version_info >= (3, 14):
        with asyncio.Runner(
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())
        ) as runner:
            runner.run(main())
    else:
        asyncio.run(main())
