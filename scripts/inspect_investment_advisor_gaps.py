"""Read-only gap-fill scope inspect for the investment-advisor master list.

Prints a per-column NULL report plus a per-sub-pipeline "would fire"
tally that mirrors what the upcoming bulk gap-fill runner will actually
execute. Scoped to ``files_13f = TRUE`` — the default the master-list
endpoint uses (``backend/app/api/v1/endpoints/investment_advisors.py``),
so this reports the population the client actually sees.

No writes, no external API calls. Connects to whichever database
``DATABASE_URL`` points at.

IA analog of ``scripts/inspect_broker_dealer_gaps.py``.
"""
from __future__ import annotations

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


# (attr_name, display_label, optional_note).
TARGET_COLUMNS: list[tuple[str, str, str]] = [
    ("executive_officers", "executive_officers", ""),
    ("direct_owners", "direct_owners", ""),
    ("indirect_owners", "indirect_owners", ""),
    ("firm_operations_text", "firm_operations_text", ""),
    ("client_types", "client_types", ""),
    ("client_counts", "client_counts", ""),
    ("advisory_activities", "advisory_activities", ""),
    ("website", "website", ""),
    ("website_source", "website_source", ""),
    ("registration_date", "registration_date", ""),
    ("formation_date", "formation_date", "(often unavailable in ADV)"),
    ("total_clients", "total_clients", ""),
    ("regulatory_aum", "regulatory_aum", ""),
]


def _is_empty(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, (list, dict, str)) and len(value) == 0:
        return True
    return False


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


async def main() -> None:
    if not os.environ.get("DATABASE_URL"):
        print("ERROR: DATABASE_URL is not set. Point at staging before running.")
        sys.exit(2)

    from sqlalchemy import select

    from app.db.session import SessionLocal
    from app.models.advisor_contact import AdvisorContact
    from app.models.advisor_filing import AdvisorFiling
    from app.models.investment_advisor import InvestmentAdvisor
    from app.services.advisor_refresh_orchestrator import (
        SUB_ENRICH_CONTACTS,
        SUB_REFRESH_FILINGS,
        SUB_REFRESH_IAPD_SUMMARY,
        SUB_REFRESH_OWNERS_OFFICERS,
        SUB_RESOLVE_ADVISOR_WEBSITE,
        decide_pipelines,
    )

    async with SessionLocal() as db:
        ia_rows = (
            await db.execute(
                select(InvestmentAdvisor).where(InvestmentAdvisor.files_13f.is_(True))
            )
        ).scalars().all()
        contact_ids: set[int] = set(
            (
                await db.execute(select(AdvisorContact.advisor_id).distinct())
            ).scalars().all()
        )
        filing_ids: set[int] = set(
            (
                await db.execute(select(AdvisorFiling.advisor_id).distinct())
            ).scalars().all()
        )

    total = len(ia_rows)
    if total == 0:
        print("No 13F-filer investment advisors found.")
        print("Hint: run `python -m scripts.initial_load_advisors` first.")
        return

    null_counts: dict[str, int] = {col: 0 for col, _, _ in TARGET_COLUMNS}
    for ia in ia_rows:
        for col, _, _ in TARGET_COLUMNS:
            if _is_empty(getattr(ia, col)):
                null_counts[col] += 1

    contacts_present = sum(1 for ia in ia_rows if ia.id in contact_ids)
    filings_present = sum(1 for ia in ia_rows if ia.id in filing_ids)

    SUB_LABELS = {
        SUB_REFRESH_OWNERS_OFFICERS: "owners (Gemini ADV)",
        SUB_RESOLVE_ADVISOR_WEBSITE: "resolve-website",
        SUB_REFRESH_FILINGS: "refresh-filings",
        SUB_ENRICH_CONTACTS: "enrich-contacts",
        SUB_REFRESH_IAPD_SUMMARY: "refresh-iapd-summary",
    }

    fire_counts: dict[str, int] = defaultdict(int)
    tier_counts: dict[str, dict[str, int]] = {
        tier: defaultdict(int) for tier in _TIER_LABELS
    }
    biggest_missing: list[tuple[int, str, list[str]]] = []

    for ia in ia_rows:
        decision = decide_pipelines(ia)
        for sub in decision.to_run:
            if sub in SUB_LABELS:
                fire_counts[sub] += 1

        tier = _aum_tier(ia.regulatory_aum)
        bucket = tier_counts[tier]
        bucket["total"] += 1
        if _is_empty(ia.executive_officers):
            bucket["owners"] += 1
        if _is_empty(ia.website):
            bucket["website"] += 1
        if ia.id not in contact_ids:
            bucket["contacts"] += 1
        if ia.id not in filing_ids:
            bucket["filings"] += 1

        missing: list[str] = []
        if _is_empty(ia.executive_officers):
            missing.append("owners")
        if _is_empty(ia.website):
            missing.append("website")
        if ia.id not in contact_ids:
            missing.append("contacts")
        if ia.id not in filing_ids and ia.cik:
            missing.append("filings")
        if _is_empty(ia.registration_date):
            missing.append("registration_date")
        if missing and tier in (">=$10B", "$1B-$10B"):
            biggest_missing.append((ia.id, ia.name, missing))

    bar = "=" * 72
    print(bar)
    print("  INVESTMENT-ADVISOR GAP-FILL INSPECT (scope: files_13f = TRUE)")
    print(bar)
    print(f"Total 13F-filer advisors:               {total:>6,}")
    print()
    print("---- NULL counts (target of gap-fill) ----")
    for col, label, note in TARGET_COLUMNS:
        n = null_counts[col]
        pct = round(100 * n / total)
        suffix = f"   {note}" if note else ""
        print(f"  {label:<28} {n:>6,}  ({pct:>3}%){suffix}")
    pct_contacts = round(100 * contacts_present / total)
    pct_filings = round(100 * filings_present / total)
    print(
        f"  {'advisor_contacts rows?':<28} {contacts_present:>6,}  "
        f"({pct_contacts:>3}% have >=1 contact)"
    )
    print(
        f"  {'advisor_filings rows?':<28} {filings_present:>6,}  "
        f"({pct_filings:>3}% have >=1 filing)"
    )
    print()
    print("---- Sub-pipelines the bulk gap-fill would fire ----")
    for sub, label in SUB_LABELS.items():
        print(f"  {label:<24} {fire_counts[sub]:>6,} advisors")
    print()
    print("---- Coverage by regulatory_aum tier ----")
    for tier in _TIER_LABELS:
        bucket = tier_counts[tier]
        if not bucket.get("total"):
            continue
        print(
            f"  {tier:<12} ({bucket['total']:>5,} advisors)  "
            f"owners: {bucket['owners']:>5,}, "
            f"website: {bucket['website']:>5,}, "
            f"contacts: {bucket['contacts']:>5,}, "
            f"filings: {bucket['filings']:>5,}"
        )
    print()
    sample = sorted(biggest_missing, key=lambda x: -len(x[2]))[:10]
    if sample:
        print("---- Top-10 large-AUM advisors with most NULLs (sample) ----")
        for ia_id, name, missing in sample:
            display_name = (name or "")[:40]
            print(
                f"  IA {ia_id:<6} {display_name:<40}  missing: [{', '.join(missing)}]"
            )
    print(bar)


if __name__ == "__main__":
    if sys.platform == "win32" and sys.version_info >= (3, 14):
        with asyncio.Runner(
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())
        ) as runner:
            runner.run(main())
    else:
        asyncio.run(main())
