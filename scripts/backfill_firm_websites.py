"""One-shot backfill for ``broker_dealer.website``.

PR #114 added FE rendering for a website link under firm name on
``/master-list/{id}``, but ``broker_dealer.website`` was null for almost
every firm because the BrokerCheck Form BD Web Address field wasn't
being plucked on the keys we read and no Apollo fallback was wired. The
companion PR widens the FINRA pluck and adds an Apollo organizations
fallback to the live merge service. This script catches up existing
rows: for every broker_dealer where ``website IS NULL``, it tries the
BrokerCheck Form BD Web Address first, then falls back to Apollo
``/v1/organizations/search``.

Idempotent: rows where ``website`` is already populated are skipped, so
a second run is effectively free (selects + skips). Apollo errors
(5xx / 429-after-retries / network) leave the row's ``website`` NULL
and log a structured warning — the next run picks the firm up again,
which matches the executive Apollo enrichment convention.

Rate-limit hygiene: a 0.25s sleep between Apollo calls keeps the burst
under Apollo's 429 line; FINRA detail uses the same delay the live
``FinraService.enrich_with_detail`` enforces. Progress is printed every
100 firms so a long run is visible from a tee'd log file.

Usage::

    python -m scripts.backfill_firm_websites              # full run
    python -m scripts.backfill_firm_websites --limit 50   # smoke test
    python -m scripts.backfill_firm_websites --skip-finra # Apollo only
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
from sqlalchemy import select, update  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.broker_dealer import BrokerDealer  # noqa: E402
from app.services.apollo import ApolloClient, ApolloError  # noqa: E402
from app.services.finra import FinraService  # noqa: E402


logger = logging.getLogger("backfill_firm_websites")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


_BATCH_SIZE = 50
_PROGRESS_EVERY = 100
_APOLLO_DELAY_S = 0.25


async def _select_targets(
    limit: int | None,
) -> list[tuple[int, str, str | None]]:
    """Return (id, name, crd_number) tuples for firms missing a website.

    Ordered by id so a partial run + resumption walks the same firms in
    the same order. ``crd_number`` may be None on legacy ``finra_only``
    rows that never had one — those firms still get the Apollo fallback,
    they just skip the FINRA detail re-fetch.
    """
    stmt = (
        select(BrokerDealer.id, BrokerDealer.name, BrokerDealer.crd_number)
        .where(BrokerDealer.website.is_(None))
        .order_by(BrokerDealer.id.asc())
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    async with SessionLocal() as db:
        rows = (await db.execute(stmt)).all()
    return [(int(row.id), row.name, row.crd_number) for row in rows]


async def _persist(bd_id: int, website: str, source: str) -> None:
    """Stamp ``website`` + ``website_source`` on a single broker_dealer row."""
    async with SessionLocal() as db:
        await db.execute(
            update(BrokerDealer)
            .where(BrokerDealer.id == bd_id)
            .values(website=website, website_source=source)
        )
        await db.commit()


async def run(*, limit: int | None, skip_finra: bool) -> None:
    targets = await _select_targets(limit=limit)
    total = len(targets)
    if total == 0:
        logger.info("Nothing to do — no broker_dealers with website IS NULL.")
        return

    logger.info(
        "Targeting %d firms (limit=%s, skip_finra=%s).",
        total,
        limit,
        skip_finra,
    )

    apollo_key = settings.apollo_api_key
    apollo_client: ApolloClient | None = None
    if apollo_key:
        apollo_client = ApolloClient(apollo_key)
    else:
        logger.warning(
            "APOLLO_API_KEY missing — running with FINRA-only path. "
            "Firms without a FINRA Web Address will stay NULL."
        )

    counts = {
        "scanned": 0,
        "skipped_already_populated": 0,
        "filled_from_finra": 0,
        "filled_from_apollo": 0,
        "apollo_no_match": 0,
        "apollo_error": 0,
        "still_null": 0,
    }

    finra = FinraService()

    finra_headers = {
        "Accept": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (compatible; AlchemyDev/1.0; "
            "compliance@alchemy.dev)"
        ),
    }

    async with httpx.AsyncClient(
        timeout=settings.finra_request_timeout_seconds,
        follow_redirects=True,
        headers=finra_headers,
    ) as finra_http:
        for batch_start in range(0, total, _BATCH_SIZE):
            batch = targets[batch_start : batch_start + _BATCH_SIZE]
            for bd_id, name, crd_number in batch:
                counts["scanned"] += 1

                # Idempotency guard: re-check the latest column state per
                # row so a concurrent backfill (or a resumed run) doesn't
                # overwrite a website that just landed.
                async with SessionLocal() as db:
                    fresh = await db.get(BrokerDealer, bd_id)
                    if fresh is None or fresh.website:
                        counts["skipped_already_populated"] += 1
                        continue

                website: str | None = None
                source: str | None = None

                # Step 1: FINRA Form BD Web Address.
                if not skip_finra and crd_number:
                    try:
                        website = await finra.fetch_website_by_crd(
                            finra_http, crd_number
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "FINRA detail lookup failed for BD %d (CRD %s): %s",
                            bd_id,
                            crd_number,
                            exc,
                        )
                        website = None
                    if website:
                        source = "finra"

                # Step 2: Apollo organizations fallback.
                if website is None and apollo_client is not None:
                    apollo_call_succeeded = False
                    try:
                        org = await apollo_client.search_organization(
                            name, crd_number
                        )
                        apollo_call_succeeded = True
                    except ApolloError as exc:
                        logger.warning(
                            "apollo_org_lookup_failed for BD %d ('%s'): %s",
                            bd_id,
                            name,
                            exc,
                        )
                        counts["apollo_error"] += 1
                        org = None
                    await asyncio.sleep(_APOLLO_DELAY_S)

                    if org is not None and org.website_url:
                        website = org.website_url
                        source = "apollo"
                    elif apollo_call_succeeded:
                        counts["apollo_no_match"] += 1

                if website and source:
                    await _persist(bd_id, website, source)
                    if source == "finra":
                        counts["filled_from_finra"] += 1
                    else:
                        counts["filled_from_apollo"] += 1
                else:
                    counts["still_null"] += 1

                if counts["scanned"] % _PROGRESS_EVERY == 0:
                    logger.info(
                        "Progress %d/%d  finra=%d apollo=%d no_match=%d "
                        "errors=%d still_null=%d",
                        counts["scanned"],
                        total,
                        counts["filled_from_finra"],
                        counts["filled_from_apollo"],
                        counts["apollo_no_match"],
                        counts["apollo_error"],
                        counts["still_null"],
                    )

    logger.info("──────────── BACKFILL SUMMARY ────────────")
    for key, value in counts.items():
        logger.info("  %-28s %d", key, value)
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
        "--skip-finra",
        action="store_true",
        help="Skip the FINRA detail re-fetch and go straight to Apollo.",
    )
    args = parser.parse_args()
    asyncio.run(run(limit=args.limit, skip_finra=args.skip_finra))


if __name__ == "__main__":
    main()
