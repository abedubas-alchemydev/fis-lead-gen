"""Backfill ``broker_dealers.dba_names`` from FINRA's
``firm_other_names`` payload.

The DBA column was added on 2026-05-08 (migration 20260508_0032). New
firms loaded after that date pick up DBA values via the standard
initial-load / data-merge pipeline. Existing rows still have
``dba_names = NULL`` because their FINRA payloads were processed by
the pre-fix parser that conflated ``firm_other_names`` with
``business_type``.

This script re-hits FINRA's BrokerCheck firm-search endpoint for every
BD with ``dba_names IS NULL``, parses the alternate-trade-name field,
and writes the result. Idempotent: rows whose FINRA payload yields no
DBAs land back as NULL, which means a future re-run will retry them.

Usage (from repo root):
    python -m scripts.backfill_dba_names                     # all NULL rows DB-wide
    python -m scripts.backfill_dba_names --top 200           # cap to first N firms
    python -m scripts.backfill_dba_names --top 200 --dry-run

Cost: one FINRA fetch per BD. ~2,800 firms ≈ 10-15 min runtime.
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

import httpx  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.broker_dealer import BrokerDealer  # noqa: E402
from app.services.finra import (  # noqa: E402
    BROKERCHECK_HEADERS,
    FinraService,
)

logger = logging.getLogger(__name__)


async def _select_targets(top: int | None) -> list[BrokerDealer]:
    async with SessionLocal() as db:
        stmt = (
            select(BrokerDealer)
            .where(BrokerDealer.dba_names.is_(None))
            .where(BrokerDealer.crd_number.is_not(None))
            .order_by(BrokerDealer.name.asc().nullslast(), BrokerDealer.id.asc())
        )
        if top is not None:
            stmt = stmt.limit(top)
        return (await db.execute(stmt)).scalars().all()


async def _persist(bd_id: int, dba_names: list[str] | None) -> None:
    async with SessionLocal() as db:
        bd = await db.get(BrokerDealer, bd_id)
        if bd is None:
            return
        bd.dba_names = dba_names
        await db.commit()


async def main(*, top: int | None, dry_run: bool) -> None:
    started_at = time.monotonic()
    print(f"backfill_dba_names: top={top} dry_run={dry_run}")

    targets = await _select_targets(top)
    print(f"selected {len(targets)} BDs with NULL dba_names")
    if not targets:
        return

    service = FinraService()
    counts = {"populated": 0, "no_dba": 0, "fetch_error": 0}

    async with httpx.AsyncClient(
        timeout=settings.finra_request_timeout_seconds,
        follow_redirects=True,
        headers=BROKERCHECK_HEADERS,
    ) as client:
        for idx, bd in enumerate(targets, 1):
            print(f"[{idx}/{len(targets)}] BD {bd.id} CRD={bd.crd_number} {bd.name!r} ...", flush=True)
            try:
                detail = await service._fetch_firm_detail(client, bd.crd_number)
            except Exception as exc:
                counts["fetch_error"] += 1
                print(f"  -> fetch_error: {type(exc).__name__}: {exc}")
                continue

            source = service._extract_detail_source(detail) if detail else None
            raw = source.get("firm_other_names") if isinstance(source, dict) else None
            dba = service._parse_dba_names(raw, legal_name=bd.name)

            if dba:
                counts["populated"] += 1
                print(f"  -> populated: {dba!r}")
                if not dry_run:
                    await _persist(bd.id, dba)
            else:
                counts["no_dba"] += 1
                print("  -> no_dba")

    elapsed = time.monotonic() - started_at
    print()
    print("=== outcome tally ===")
    for k in ("populated", "no_dba", "fetch_error"):
        print(f"  {k}: {counts.get(k, 0)}")
    print(f"total elapsed: {elapsed:.1f}s")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill broker_dealers.dba_names from FINRA's "
            "firm_other_names payload. Idempotent — rows that yield "
            "no DBAs stay NULL and can be retried on a future run."
        )
    )
    parser.add_argument(
        "--top",
        type=int,
        default=None,
        help="Cap to the first N rows in alphabetical order. Default: all NULL-dba rows.",
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
