"""Backfill firm websites by re-running the resolver chain on rows where
``website IS NULL``.

The orchestrator's ``resolve-website`` sub-pipeline runs Apollo first;
firms that fail Apollo's company match silently skip serper.dev +
SerpAPI when those keys are missing from the local ``backend/.env``.
This script targets the leftover NULL-website rows and re-runs the full
chain (Apollo → serper.dev → SerpAPI) so firms whose domain Apollo
doesn't know but a search-tier hit lands cleanly.

Idempotent: any row whose ``website`` is already set is skipped.

Usage (from repo root):
    python -m scripts.backfill_websites                  # all NULL rows DB-wide
    python -m scripts.backfill_websites --top 775        # cap to master-list display top-N
    python -m scripts.backfill_websites --top 775 --dry-run
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

from app.core.config import settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.broker_dealer import BrokerDealer  # noqa: E402
from app.services.apollo import ApolloClient  # noqa: E402
from app.services.serpapi import SerpAPIClient  # noqa: E402
from app.services.serper import SerperClient  # noqa: E402
from app.services.website_resolver import resolve_website  # noqa: E402

logger = logging.getLogger(__name__)


def _build_clients() -> tuple[
    ApolloClient | None,
    SerperClient | None,
    SerpAPIClient | None,
]:
    """Construct each provider client when its key is configured.

    Mirrors the resolver chain's optional-key contract — pass ``None``
    when the key is missing so the resolver skips the tier silently
    instead of crashing on instantiation.
    """
    apollo = ApolloClient(settings.apollo_api_key) if settings.apollo_api_key else None
    serper = SerperClient(settings.serper_api_key) if settings.serper_api_key else None
    serpapi = SerpAPIClient(settings.serpapi_api_key) if settings.serpapi_api_key else None
    return apollo, serper, serpapi


async def _select_targets(top: int | None) -> list[BrokerDealer]:
    """Return the BDs we should re-resolve.

    Filters to ``website IS NULL`` and orders by master-list display
    sequence (name ASC NULLS LAST, id ASC). When ``top`` is set, only
    the first ``top`` rows by display order are considered — the
    remaining cohort beyond that is left for a separate pass.
    """
    async with SessionLocal() as db:
        stmt = (
            select(BrokerDealer)
            .where(BrokerDealer.is_deficient.is_(False))
            .where(BrokerDealer.website.is_(None))
            .order_by(BrokerDealer.name.asc().nullslast(), BrokerDealer.id.asc())
        )
        if top is not None:
            ids_stmt = (
                select(BrokerDealer.id)
                .where(BrokerDealer.is_deficient.is_(False))
                .order_by(BrokerDealer.name.asc().nullslast(), BrokerDealer.id.asc())
                .limit(top)
            )
            top_ids = (await db.execute(ids_stmt)).scalars().all()
            stmt = stmt.where(BrokerDealer.id.in_(top_ids))
        return (await db.execute(stmt)).scalars().all()


async def _persist(bd_id: int, url: str, source: str) -> None:
    """Update the BD row's ``website`` + ``website_source`` columns.

    Each persistence happens in its own short-lived session so a long
    run doesn't accumulate session state.
    """
    async with SessionLocal() as db:
        bd = await db.get(BrokerDealer, bd_id)
        if bd is None:
            return
        bd.website = url
        bd.website_source = source
        await db.commit()


async def main(*, top: int | None, dry_run: bool) -> None:
    started_at = time.monotonic()
    apollo, serper, serpapi = _build_clients()
    enabled = [
        name for name, client in (
            ("apollo", apollo),
            ("serper", serper),
            ("serpapi", serpapi),
        )
        if client is not None
    ]
    print(f"backfill_websites: top={top} dry_run={dry_run} chain={enabled}")

    targets = await _select_targets(top)
    print(f"selected {len(targets)} BDs with NULL website")
    if not targets:
        return

    counts = {"apollo": 0, "serper": 0, "serpapi": 0, "miss": 0, "error": 0}

    for idx, bd in enumerate(targets, 1):
        print(f"[{idx}/{len(targets)}] BD {bd.id} {bd.name!r} ...", flush=True)
        if dry_run:
            print("  -> dry_run (no chain call, no DB write)")
            continue
        try:
            url, source, reason = await resolve_website(
                bd.name,
                bd.crd_number,
                apollo,
                serpapi=serpapi,
                serper=serper,
                dba_names=bd.dba_names,
                resolver_aliases=bd.resolver_aliases,
            )
        except Exception as exc:
            counts["error"] += 1
            print(f"  -> error: {type(exc).__name__}: {exc}")
            continue

        if url and source:
            await _persist(bd.id, url, source)
            counts[source] = counts.get(source, 0) + 1
            print(f"  -> {source}: {url}")
        else:
            counts["miss"] += 1
            print(f"  -> miss: {reason}")

    elapsed = time.monotonic() - started_at
    print()
    print("=== outcome tally ===")
    for k in ("apollo", "serper", "serpapi", "miss", "error"):
        print(f"  {k}: {counts.get(k, 0)}")
    print(f"total elapsed: {elapsed:.1f}s")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Re-run the website resolver chain (Apollo -> Hunter -> SerpAPI) "
            "on broker-dealers whose ``website`` column is NULL. Useful after "
            "configuring missing provider keys (e.g., Hunter + SerpAPI) so "
            "firms that Apollo missed get a second look."
        )
    )
    parser.add_argument(
        "--top",
        type=int,
        default=None,
        help="Cap to the first N rows in master-list display order. Default: all NULL-website firms.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List the target rows without firing the chain or writing to the DB.",
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
