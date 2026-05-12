"""Backfill ``broker_dealers.resolver_aliases`` via the Gemini-driven
firm-alias enricher (``app.services.firm_alias_enricher``).

The ``resolver_aliases`` column was added on 2026-05-09 (migration
20260509_0036). The lazy-on-mount path in
``broker_dealers.resolve_broker_dealer_website`` populates the column
on demand when a user visits a firm-detail page, but bulk runs of
``backfill_firm_websites.py`` / ``backfill_websites.py`` only consume
whatever happens to be on the row already. Pre-populating via this
script ahead of a bulk website-resolution pass is the recommended
flow:

    python -m scripts.enrich_firm_aliases               # all NULL rows
    python -m scripts.backfill_firm_websites            # actual website work

The split keeps LLM cost observable: this script is the only place
that fires Gemini for alias generation in bulk, and its output tally
prints exact populated/empty/error counts so you can diff before/after.

Idempotent: rows whose Gemini call returns no useful aliases land
back as ``[]`` (not NULL) so they don't re-fire on subsequent runs.
A future re-run only re-tries firms that had a Gemini failure (column
stays NULL on error).

Usage:
    python -m scripts.enrich_firm_aliases                     # all NULL rows
    python -m scripts.enrich_firm_aliases --top 200           # cap to first N
    python -m scripts.enrich_firm_aliases --top 200 --dry-run

Cost: one Gemini Flash call per BD (~$0.0001 per call, ~$0.30 for
~3,000 firms). Runtime depends on the API latency; expect ~1-2s per
firm so a full backfill is ~1-2 hours.
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
from app.models.broker_dealer import BrokerDealer  # noqa: E402
from app.services.firm_alias_enricher import (  # noqa: E402
    enrich_firm_aliases,
)

logger = logging.getLogger(__name__)


async def _select_targets(top: int | None) -> list[BrokerDealer]:
    """Return BDs with NULL ``resolver_aliases`` in (name, id) order.

    Skips firms with no name (a defensive guard — every BD should have
    one but legacy migrations occasionally land partial rows). NULL
    ``crd_number`` is tolerated; the enricher passes it through to
    Gemini as "(not provided)" without aborting.
    """
    async with SessionLocal() as db:
        stmt = (
            select(BrokerDealer)
            .where(BrokerDealer.resolver_aliases.is_(None))
            .where(BrokerDealer.name.is_not(None))
            .order_by(BrokerDealer.name.asc().nullslast(), BrokerDealer.id.asc())
        )
        if top is not None:
            stmt = stmt.limit(top)
        return (await db.execute(stmt)).scalars().all()


async def _persist(bd_id: int, aliases: list[str]) -> None:
    """Write the cleaned alias list to the BD row.

    Each call opens its own short-lived session so a long backfill
    doesn't accumulate identity-map state. Persists ``[]`` (not NULL)
    on a successful-but-empty Gemini response so the firm doesn't
    get re-tried on the next run.
    """
    async with SessionLocal() as db:
        bd = await db.get(BrokerDealer, bd_id)
        if bd is None:
            return
        bd.resolver_aliases = aliases
        await db.commit()


async def main(*, top: int | None, dry_run: bool) -> None:
    started_at = time.monotonic()
    print(f"enrich_firm_aliases: top={top} dry_run={dry_run}")

    targets = await _select_targets(top)
    print(f"selected {len(targets)} BDs with NULL resolver_aliases")
    if not targets:
        return

    counts = {"populated": 0, "empty": 0, "gemini_error": 0}

    for idx, bd in enumerate(targets, 1):
        print(
            f"[{idx}/{len(targets)}] BD {bd.id} CRD={bd.crd_number} {bd.name!r} ...",
            flush=True,
        )
        result = await enrich_firm_aliases(bd.name, bd.crd_number)
        if result is None:
            counts["gemini_error"] += 1
            print("  -> gemini_error (column stays NULL for retry)")
            continue

        if result.aliases:
            counts["populated"] += 1
            print(
                f"  -> populated ({len(result.aliases)}, "
                f"conf={result.confidence:.2f}): {result.aliases!r}"
            )
        else:
            counts["empty"] += 1
            print(f"  -> empty (conf={result.confidence:.2f}); persisting [] to skip retry")

        if not dry_run:
            await _persist(bd.id, result.aliases)

    elapsed = time.monotonic() - started_at
    print()
    print("=== outcome tally ===")
    for k in ("populated", "empty", "gemini_error"):
        print(f"  {k}: {counts.get(k, 0)}")
    print(f"total elapsed: {elapsed:.1f}s")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill broker_dealers.resolver_aliases via Gemini. "
            "Idempotent — empty results land as [] (not NULL) so "
            "successful-but-empty rows don't re-fire; only Gemini "
            "failures stay NULL for retry on a future run."
        )
    )
    parser.add_argument(
        "--top",
        type=int,
        default=None,
        help="Cap to the first N rows in name order. Default: all NULL-alias rows.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Call Gemini but don't write to the DB.",
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
