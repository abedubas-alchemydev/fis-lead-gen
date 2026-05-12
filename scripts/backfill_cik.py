"""One-shot backfill for ``broker_dealers.cik``.

The live ingestion pipeline already populates ``cik`` on new BDs going
forward, but a long tail of legacy rows still carries ``cik IS NULL``
because they were inserted before the EDGAR company-search fast-path was
wired. This script catches those rows up: for every broker_dealer where
``cik IS NULL``, it asks ``EdgarService.lookup_cik_for_bd`` to resolve
the CIK via two paths in priority order:

  1. ``_fetch_browse_record(sec_file_number)`` — direct file-number hit
  2. ``_fetch_browse_record_by_name(name, state)`` — fallback company-
     name search on ``browse-edgar``, disambiguated by ``state``

Rows representing firms with no SEC registration (a legitimate state
for some FINRA-only filers) stay NULL and are reported as ``failed``.

Usage::

    python -m scripts.backfill_cik              # full run
    python -m scripts.backfill_cik --dry-run    # log only, no writes
    python -m scripts.backfill_cik --limit 50   # smoke test (50 firms)

The summary log line at the end is the single line the brief asks for
in the PR description, in the form::

    total_null=X, attempted=Y, found=Z, failed=W, ambiguous=V
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
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
from sqlalchemy import func, select, update  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.broker_dealer import BrokerDealer  # noqa: E402
from app.services.edgar import EdgarService  # noqa: E402


logger = logging.getLogger("backfill_cik")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


_PROGRESS_EVERY = 100


async def _select_targets(
    limit: int | None,
) -> list[tuple[int, str, str | None, str | None]]:
    """Return ``(id, name, state, sec_file_number)`` tuples for firms
    missing a CIK, ordered by id so a partial run + resumption walks the
    same firms in the same order."""
    stmt = (
        select(
            BrokerDealer.id,
            BrokerDealer.name,
            BrokerDealer.state,
            BrokerDealer.sec_file_number,
        )
        .where(BrokerDealer.cik.is_(None))
        .order_by(BrokerDealer.id.asc())
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    async with SessionLocal() as db:
        rows = (await db.execute(stmt)).all()
    return [
        (int(row.id), row.name, row.state, row.sec_file_number)
        for row in rows
    ]


async def _persist(bd_id: int, cik: str) -> None:
    """Stamp ``cik`` on a single broker_dealer row."""
    async with SessionLocal() as db:
        await db.execute(
            update(BrokerDealer)
            .where(BrokerDealer.id == bd_id)
            .values(cik=cik)
        )
        await db.commit()


async def _count_null_ciks() -> int:
    stmt = select(func.count()).select_from(BrokerDealer).where(
        BrokerDealer.cik.is_(None)
    )
    async with SessionLocal() as db:
        return int((await db.execute(stmt)).scalar_one() or 0)


class _Target:
    """Adapter wrapping the row tuple so it satisfies the
    ``CikLookupTarget`` Protocol expected by ``EdgarService``."""

    __slots__ = ("name", "state", "sec_file_number")

    def __init__(
        self, name: str, state: str | None, sec_file_number: str | None,
    ) -> None:
        self.name = name
        self.state = state
        self.sec_file_number = sec_file_number


async def run(*, limit: int | None, dry_run: bool) -> None:
    total_null = await _count_null_ciks()
    targets = await _select_targets(limit=limit)
    target_count = len(targets)
    if target_count == 0:
        logger.info("Nothing to do — no broker_dealers with cik IS NULL.")
        logger.info(
            "total_null=0, attempted=0, found=0, failed=0, ambiguous=0",
        )
        return

    logger.info(
        "Targeting %d firms (total NULL in DB: %d, limit=%s, dry_run=%s).",
        target_count, total_null, limit, dry_run,
    )

    edgar = EdgarService()
    headers = {
        "User-Agent": settings.sec_user_agent,
        "Accept": "text/html,application/xhtml+xml",
        # SEC EDGAR's Cloudflare gateway returns malformed compressed
        # bodies on default Accept-Encoding negotiation. Pin identity
        # to bypass — same fix as services/finra.py.
        "Accept-Encoding": "identity",
    }

    counts = {
        "attempted": 0,
        "found": 0,
        "failed": 0,
        "ambiguous": 0,
        "skipped_already_populated": 0,
    }

    async with httpx.AsyncClient(
        timeout=settings.sec_request_timeout_seconds,
        headers=headers,
        follow_redirects=True,
    ) as client:
        for index, (bd_id, name, state, sec_file_number) in enumerate(targets):
            # Idempotency guard: re-check the row right before doing
            # work — a parallel pipeline run may have populated cik
            # since we selected the worklist.
            async with SessionLocal() as db:
                fresh = await db.get(BrokerDealer, bd_id)
                if fresh is None or fresh.cik:
                    counts["skipped_already_populated"] += 1
                    continue

            counts["attempted"] += 1

            target = _Target(
                name=name, state=state, sec_file_number=sec_file_number,
            )

            try:
                status, cik = await edgar.lookup_cik_for_bd(client, target)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "edgar_lookup_error bd_id=%d name=%r: %s",
                    bd_id, name, exc,
                )
                counts["failed"] += 1
                status, cik = ("not_found", None)

            if status == "found" and cik:
                if dry_run:
                    logger.info(
                        "DRY-RUN would set cik=%s for bd_id=%d (%r)",
                        cik, bd_id, name,
                    )
                else:
                    await _persist(bd_id, cik)
                counts["found"] += 1
            elif status == "ambiguous":
                logger.info(
                    "ambiguous_name_match bd_id=%d name=%r state=%s — skipped",
                    bd_id, name, state,
                )
                counts["ambiguous"] += 1
            else:
                counts["failed"] += 1

            # Honor SEC's published rate ceiling between calls.
            if (
                index < len(targets) - 1
                and settings.edgar_rate_limit_per_second > 0
            ):
                await asyncio.sleep(1 / settings.edgar_rate_limit_per_second)

            if counts["attempted"] % _PROGRESS_EVERY == 0:
                logger.info(
                    "progress %d/%d  found=%d failed=%d ambiguous=%d",
                    counts["attempted"], target_count,
                    counts["found"], counts["failed"], counts["ambiguous"],
                )

    logger.info("──────────── BACKFILL SUMMARY ────────────")
    logger.info(
        "total_null=%d, attempted=%d, found=%d, failed=%d, ambiguous=%d",
        total_null,
        counts["attempted"],
        counts["found"],
        counts["failed"],
        counts["ambiguous"],
    )
    if counts["skipped_already_populated"]:
        logger.info(
            "  skipped_already_populated=%d (concurrent fills)",
            counts["skipped_already_populated"],
        )
    logger.info("──────────────────────────────────────────")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap the number of firms processed (smoke test).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Look up CIKs and log results, but do not write to the DB.",
    )
    args = parser.parse_args()
    asyncio.run(run(limit=args.limit, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
