"""One-shot backfill: NULL ``broker_dealer.website`` rows poisoned with
a FINRA/SEC self-reference URL.

Why this script exists. The FINRA enumeration writer used to fall through
to ``firm_bc_scope_url`` when every Form-BD-canonical key was empty, and
that field is FINRA's pointer to the firm's own BrokerCheck profile/PDF
(e.g. ``https://files.brokercheck.finra.org/firm/firm_<CRD>.pdf``), not a
website the firm filed. The companion code change drops that fallback and
adds a defensive blocklist on writes — but rows already in the DB are
stuck with the bad URL. Setting ``website`` back to NULL lets the lazy
on-demand resolver (Apollo → Hunter → SerpAPI) source a real website on
the user's next visit to ``/master-list/{id}``.

What it touches. Any row where ``urlparse(website).hostname`` matches the
``DOMAIN_BLOCKLIST`` from ``website_resolver`` — primarily
``brokercheck.finra.org`` / ``files.brokercheck.finra.org`` /
``finra.org`` / ``sec.gov`` and the social/news hosts. Both ``website``
and ``website_source`` are cleared so the resolver re-runs cleanly.

Idempotent. A second run finds zero matches because the first run
NULL'd them. Safe to re-run after a re-ingestion that re-introduces
poisoned data (the application-level guard added alongside this script
prevents that, but the script remains the recovery tool).

Usage::

    python -m scripts.backfill_null_brokercheck_websites              # full run
    python -m scripts.backfill_null_brokercheck_websites --limit 50   # smoke test
    python -m scripts.backfill_null_brokercheck_websites --dry-run    # show, no writes
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

from sqlalchemy import select, update  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models.broker_dealer import BrokerDealer  # noqa: E402
from app.services.website_resolver import is_blocklisted_host  # noqa: E402


logger = logging.getLogger("backfill_null_brokercheck_websites")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


_PROGRESS_EVERY = 100


async def _select_candidates(
    limit: int | None,
) -> list[tuple[int, str, str, str | None]]:
    """Return (id, name, website, website_source) for rows with a non-null website.

    The blocklist filter runs in Python (not SQL) so the script and the
    application-level writer share one source of truth — the
    ``DOMAIN_BLOCKLIST`` set. For ~3.5k rows this is well within budget.
    """
    stmt = (
        select(
            BrokerDealer.id,
            BrokerDealer.name,
            BrokerDealer.website,
            BrokerDealer.website_source,
        )
        .where(BrokerDealer.website.is_not(None))
        .order_by(BrokerDealer.id.asc())
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    async with SessionLocal() as db:
        rows = (await db.execute(stmt)).all()
    return [
        (int(row.id), row.name, row.website, row.website_source)
        for row in rows
    ]


async def _null_website(bd_id: int) -> None:
    """Clear ``website`` and ``website_source`` on a single row."""
    async with SessionLocal() as db:
        await db.execute(
            update(BrokerDealer)
            .where(BrokerDealer.id == bd_id)
            .values(website=None, website_source=None)
        )
        await db.commit()


async def run(*, limit: int | None, dry_run: bool) -> None:
    candidates = await _select_candidates(limit=limit)
    total = len(candidates)
    if total == 0:
        logger.info("Nothing to scan — no broker_dealers with a non-null website.")
        return

    logger.info(
        "Scanning %d firms with non-null website (limit=%s, dry_run=%s).",
        total, limit, dry_run,
    )

    counts = {
        "scanned": 0,
        "nulled": 0,
        "kept": 0,
    }

    for bd_id, name, website, website_source in candidates:
        counts["scanned"] += 1
        if is_blocklisted_host(website):
            logger.info(
                "POISONED bd=%d name=%r source=%s url=%s",
                bd_id, name, website_source, website,
            )
            if not dry_run:
                await _null_website(bd_id)
            counts["nulled"] += 1
        else:
            counts["kept"] += 1

        if counts["scanned"] % _PROGRESS_EVERY == 0:
            logger.info(
                "Progress %d/%d  nulled=%d kept=%d",
                counts["scanned"], total, counts["nulled"], counts["kept"],
            )

    logger.info("──────────── BACKFILL SUMMARY ────────────")
    logger.info("  dry_run                       %s", dry_run)
    for key, value in counts.items():
        logger.info("  %-28s %d", key, value)
    logger.info("──────────────────────────────────────────")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap the number of firms scanned (smoke test).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log poisoned rows without writing — preview before committing.",
    )
    args = parser.parse_args()
    asyncio.run(run(limit=args.limit, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
