"""Backfill ``investment_advisors.other_business_names`` from the IAPD
per-firm JSON (Form ADV Schedule D Section 1.B "Other Business Names").

The column was added on 2026-06-03 (migration 20260603_0001). Going
forward the refresh orchestrator's IAPD-summary sub-pipeline populates it
for free (it already fetches the same per-firm JSON for
``registration_date``), so the nightly gap-fill runner fills it over its
30-day cadence. This script gives an immediate one-shot fill of the
~3,107 ``files_13f=TRUE`` advisors without waiting for that cadence.

It re-hits ``api.adviserinfo.sec.gov/search/firm/{crd}`` for every 13F
advisor with ``other_business_names IS NULL``, parses the
``basicInformation.otherNames`` array (dropping the firm's own primary +
legal name), and writes the result. Idempotent: rows whose IAPD payload
yields no usable other-names land back as NULL, so a future re-run
retries them.

Usage (from repo root):
    python -m scripts.backfill_other_business_names                  # all NULL 13F rows
    python -m scripts.backfill_other_business_names --top 200        # cap to first N firms
    python -m scripts.backfill_other_business_names --top 5 --dry-run

Cost: one IAPD fetch per advisor. ~3,100 firms ≈ 10-15 min runtime.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import selectors
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
sys.stderr.reconfigure(line_buffering=True)  # type: ignore[attr-defined]

ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

if sys.platform == "win32" and sys.version_info < (3, 14):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy import select  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models.investment_advisor import InvestmentAdvisor  # noqa: E402
from app.services.advisor_refresh_orchestrator import (  # noqa: E402
    _fetch_iapd_firm_summary,
    extract_other_business_names,
)

logger = logging.getLogger(__name__)


async def _select_targets(top: int | None) -> list[InvestmentAdvisor]:
    async with SessionLocal() as db:
        stmt = (
            select(InvestmentAdvisor)
            .where(InvestmentAdvisor.files_13f.is_(True))
            .where(InvestmentAdvisor.other_business_names.is_(None))
            .where(InvestmentAdvisor.crd_number.is_not(None))
            .order_by(InvestmentAdvisor.name.asc().nullslast(), InvestmentAdvisor.id.asc())
        )
        if top is not None:
            stmt = stmt.limit(top)
        return (await db.execute(stmt)).scalars().all()


async def _persist(advisor_id: int, names: list[str] | None) -> None:
    async with SessionLocal() as db:
        advisor = await db.get(InvestmentAdvisor, advisor_id)
        if advisor is None:
            return
        advisor.other_business_names = names
        await db.commit()


async def main(*, top: int | None, dry_run: bool) -> None:
    started_at = time.monotonic()
    print(f"backfill_other_business_names: top={top} dry_run={dry_run}")

    targets = await _select_targets(top)
    print(f"selected {len(targets)} 13F advisors with NULL other_business_names")
    if not targets:
        return

    counts = {"populated": 0, "no_names": 0, "fetch_error": 0}

    # ``_fetch_iapd_firm_summary`` builds + tears down its own httpx client
    # per call (it sets IAPD-specific headers), so there's no shared client to
    # wrap the loop in — unlike the BD dba backfill. The sequential loop keeps
    # us comfortably under SEC's ~10 req/sec per-IP ceiling.
    for idx, adv in enumerate(targets, 1):
        print(
            f"[{idx}/{len(targets)}] IA {adv.id} CRD={adv.crd_number} {adv.name!r} ...",
            flush=True,
        )
        iacontent = await _fetch_iapd_firm_summary(adv.crd_number)
        if iacontent is None:
            counts["fetch_error"] += 1
            print("  -> fetch_error")
            continue

        names = extract_other_business_names(
            iacontent, primary_name=adv.name, legal_name=adv.legal_name
        )
        if names:
            counts["populated"] += 1
            print(f"  -> populated: {names!r}")
            if not dry_run:
                await _persist(adv.id, names)
        else:
            counts["no_names"] += 1
            print("  -> no_names")

    elapsed = time.monotonic() - started_at
    print()
    print("=== outcome tally ===")
    for k in ("populated", "no_names", "fetch_error"):
        print(f"  {k}: {counts.get(k, 0)}")
    print(f"total elapsed: {elapsed:.1f}s")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill investment_advisors.other_business_names from the IAPD "
            "per-firm JSON (Form ADV Schedule D 1.B). Scoped to files_13f=TRUE. "
            "Idempotent — rows that yield no names stay NULL and can be retried "
            "on a future run."
        )
    )
    parser.add_argument(
        "--top",
        type=int,
        default=None,
        help="Cap to the first N rows in alphabetical order. Default: all NULL 13F rows.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch + parse but don't write to the DB.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    coro = main(top=args.top, dry_run=args.dry_run)
    if sys.platform == "win32" and sys.version_info >= (3, 14):
        with asyncio.Runner(
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())
        ) as runner:
            runner.run(coro)
    else:
        asyncio.run(coro)
