"""Initial-load script for the Investment Advisor master list.

Pulls the SEC IAPD monthly Compilation Report, cross-references the
EDGAR EFTS 13F-HR filer set, and upserts the merged rows into
``investment_advisors``. Sibling of ``scripts/initial_load.py`` for
the broker-dealer pipeline.

Usage:
    python -m scripts.initial_load_advisors                # full run
    python -m scripts.initial_load_advisors --limit 50     # smoke run
    python -m scripts.initial_load_advisors --dry-run      # rollback at end
    python -m scripts.initial_load_advisors --refresh-cache  # bypass 7d ZIP cache
    python -m scripts.initial_load_advisors --skip-13f     # IAPD-only, no 13F flag
    python -m scripts.initial_load_advisors --lookback-days 180  # widen 13F window

Environment:
    DATABASE_URL must point at the target Neon DB. Per the project's
    "staging and prod each have their own Neon DB" memory, ``run on
    staging`` is safe; for prod, double-check the URL before
    --refresh-cache + full run.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

if sys.platform == "win32" and sys.version_info < (3, 14):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.core.config import settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.services.advisor_merge import AdvisorMergeService  # noqa: E402
from app.services.iapd import IapdService  # noqa: E402
from app.services.investment_advisors import InvestmentAdvisorRepository  # noqa: E402
from app.services.thirteen_f_filter import ThirteenFFilterService  # noqa: E402


def _print_section(title: str) -> None:
    # Plain ASCII bar — Windows cp1252 console encoding chokes on the
    # box-drawing chars BD's initial_load.py uses, and this script also
    # runs on dev workstations.
    bar = "-" * 55
    print(f"\n{bar}\n  {title}\n{bar}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Truncate IAPD parser to first N records (smoke runs).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Rollback the DB transaction at end — fetch + parse + merge but no writes.",
    )
    parser.add_argument(
        "--refresh-cache",
        action="store_true",
        help="Bypass the 7-day ZIP cache and force a fresh download from SEC.",
    )
    parser.add_argument(
        "--skip-13f",
        action="store_true",
        help="Skip the EDGAR EFTS 13F-HR pass; all advisors land with files_13f=False.",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=settings.thirteen_f_lookback_days,
        help=(
            f"13F-HR EFTS lookback window in days (default {settings.thirteen_f_lookback_days})."
        ),
    )
    return parser.parse_args()


async def main(args: argparse.Namespace) -> None:
    iapd_service = IapdService()
    thirteen_f_service = ThirteenFFilterService()
    merge_service = AdvisorMergeService()
    repository = InvestmentAdvisorRepository()

    # ── Step 1: Pull the IAPD bulk feed ──
    _print_section("Step 1: Fetching SEC IAPD Compilation Report")
    iapd_records = await iapd_service.fetch_compilation_records(
        limit=args.limit, force_refresh=args.refresh_cache
    )
    print(f"  Parsed {len(iapd_records):,} IAPD advisor records.")

    # ── Step 2: Enumerate Form 13F-HR filers from EDGAR EFTS ──
    if args.skip_13f:
        _print_section("Step 2: Skipping 13F-HR enumeration (--skip-13f)")
        thirteen_f_ciks: dict[str, date] = {}
    else:
        _print_section(
            f"Step 2: Enumerating Form 13F-HR filers (last {args.lookback_days}d)"
        )
        thirteen_f_ciks = await thirteen_f_service.fetch_recent_filer_ciks(
            lookback_days=args.lookback_days
        )
        print(f"  Found {len(thirteen_f_ciks):,} distinct 13F-HR filer CIKs.")

    # ── Step 3: Merge ──
    _print_section("Step 3: Merging IAPD + 13F filer set")
    merged, report = merge_service.merge(iapd_records, thirteen_f_ciks)
    print(
        f"  Merged {report.merged_count:,} rows "
        f"({report.files_13f_count:,} flagged 13F; "
        f"{report.cik_match_count:,} CIK matches; "
        f"{report.dropped_no_crd + report.dropped_no_name:,} dropped)."
    )
    if report.dropped_examples:
        print("  Sample dropped rows:")
        for reason, identifier in report.dropped_examples:
            print(f"    - [{reason}] {identifier}")

    # ── Step 4: Upsert ──
    _print_section(
        "Step 4: Upserting into investment_advisors"
        + (" (DRY-RUN — will rollback)" if args.dry_run else "")
    )
    async with SessionLocal() as db:
        upserted = await repository.upsert_many(db, merged)
        if args.dry_run:
            await db.rollback()
            print(f"  Dry-run: prepared {upserted:,} upserts, then rolled back.")
        else:
            await db.commit()
            print(f"  Committed {upserted:,} upserts.")

    _print_section("Initial-load advisors complete")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(main(_parse_args()))
