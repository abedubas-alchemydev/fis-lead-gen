"""Tier-2 Apollo enrichment for bank_contacts — fill email/phone/title.

The bank-charter watcher's contact extractor
(``scripts/watch_bank_charters.py --extract-contacts``) lands people from
OCC charter-application PDFs into ``bank_contacts`` — but the public
portion of a filing rarely prints channels, so most rows have NULL
email/phone (and often a NULL title). This script is the PAID second
tier: it looks each such person up on Apollo ``/people/match`` (~1 credit
per lookup) anchored to their bank's name/domain, and fills email / phone
/ title ONLY where currently NULL. Extracted values are never
overwritten. Conservative acceptance (close name match + plausible org
association) — everything else is rejected and logged; see
``backend/app/services/bank_contact_enrichment.py`` and
``docs/runbooks/bank-contact-enrichment.md``.

Cost discipline (same conventions as every paid job in this repo):

- **Dry-run is the default** and makes ZERO Apollo calls — the plan
  (which contacts qualify, in spend order) is pure SQL.
- ``--apply`` spends credits. Each decided lookup stamps
  ``enriched_at`` + ``enrich_status`` ('matched' | 'no_match'), so
  re-runs skip attempted rows and never re-spend a credit. Provider
  errors do NOT stamp (retried next run).
- ``--limit`` (default 50) hard-caps Apollo person lookups per run.

Environment (never argv): ``DATABASE_URL`` (required),
``APOLLO_API_KEY`` (required with ``--apply``; unused in dry-run).

Usage::

    # Default: dry-run — print the enrichment plan, spend nothing.
    python scripts/enrich_bank_contacts.py

    # Execute against the first 50 eligible contacts.
    python scripts/enrich_bank_contacts.py --apply

    # Cost-bounded smoke test.
    python scripts/enrich_bank_contacts.py --apply --limit 5
"""
from __future__ import annotations

import argparse
import asyncio
import os
import selectors
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if sys.platform == "win32" and sys.version_info < (3, 14):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


DEFAULT_LIMIT = 50


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Execute the plan (spends Apollo credits). Without this flag "
             "the script is a read-only dry-run that prints the plan and "
             "makes zero Apollo calls.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"Hard cap on Apollo person lookups this run "
             f"(default {DEFAULT_LIMIT}). The plan is truncated to this "
             f"many contacts, highest-value roles first.",
    )
    return parser.parse_args()


def _print_plan(plan, *, limit: int) -> None:
    bar = "=" * 100
    print(bar)
    print(
        f"BANK-CONTACT ENRICHMENT PLAN  --  {plan.eligible} eligible contact(s), "
        f"{len(plan.planned)} planned lookup(s) (limit={limit}, "
        f"unparseable_names={plan.unparseable})"
    )
    print(bar)
    if not plan.planned:
        print("  Nothing to do: every bank contact already has an email or "
              "has been attempted (enriched_at set).")
        print(bar)
        return
    header = (
        f"  {'contact':>7}  {'role':<16} {'name':<28} "
        f"{'bank':<30} {'org query / domain':<34} missing"
    )
    print(header)
    print("  " + "-" * (len(header) + 8))
    for item in plan.planned:
        org = item.org_query
        if item.domain:
            org = f"{org} / {item.domain}"
        print(
            f"  {item.contact_id:>7}  {item.role_context:<16} "
            f"{item.contact_name[:27]:<28} {item.bank_name[:29]:<30} "
            f"{org[:33]:<34} {','.join(item.missing) or '-'}"
        )
    print(bar)


async def main() -> None:
    args = _parse_args()

    if not os.environ.get("DATABASE_URL"):
        print("ERROR: DATABASE_URL is not set.", file=sys.stderr)
        sys.exit(2)

    # Imports deferred until after the env check so a missing DSN fails
    # fast with a clear message instead of a settings traceback.
    from app.core.config import settings
    from app.db.session import SessionLocal
    from app.services.bank_contact_enrichment import (
        BankContactApolloClient,
        BankContactEnrichmentStats,
        execute_enrichment,
        plan_enrichment,
    )

    api_key = os.environ.get("APOLLO_API_KEY") or settings.apollo_api_key
    if args.apply and not api_key:
        print(
            "ERROR: APOLLO_API_KEY is not set (required with --apply; "
            "dry-run needs no key).",
            file=sys.stderr,
        )
        sys.exit(2)

    limit = max(0, args.limit)

    # ── Phase 1 — read-only plan (zero Apollo calls) ────────────────────
    async with SessionLocal() as db:
        plan = await plan_enrichment(db, limit=limit)

    _print_plan(plan, limit=limit)

    if not args.apply:
        print()
        print("Dry-run (no --apply): no Apollo calls made, no rows written.")
        stats = BankContactEnrichmentStats(eligible=plan.eligible)
        print(stats.summary_line())
        return

    if not plan.planned:
        stats = BankContactEnrichmentStats(eligible=plan.eligible)
        print(stats.summary_line())
        return

    # ── Phase 2 — execute (paid; per-row commits) ───────────────────────
    print()
    print(
        f"Executing {len(plan.planned)} Apollo lookup(s) "
        f"(~1 credit each; cap={limit})..."
    )
    client = BankContactApolloClient(api_key)
    async with SessionLocal() as db:
        stats = await execute_enrichment(db, client, plan.planned)
    stats.eligible = plan.eligible

    print()
    print(
        f"Done: looked_up={stats.looked_up} matched={stats.matched} "
        f"no_match={stats.no_match} provider_errors={stats.provider_errors} "
        f"skipped_stale={stats.skipped_stale} "
        f"(http_calls={client.http_calls})"
    )
    print(stats.summary_line())


if __name__ == "__main__":
    if sys.platform == "win32" and sys.version_info >= (3, 14):
        with asyncio.Runner(
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())
        ) as runner:
            runner.run(main())
    else:
        asyncio.run(main())
