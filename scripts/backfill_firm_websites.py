"""One-shot backfill for ``broker_dealer.website``.

For every broker_dealer where ``website IS NULL``, runs the on-demand
website-resolver chain (Apollo -> SerpAPI) with the firm's legal name
plus DBA tokens, and stamps the first validated URL it finds.

Why this script delegates to ``website_resolver.resolve_website``:
the validator there enforces HEAD reachability, a domain blocklist
(linkedin / sec.gov / finra.org / news / social), a content-page guard,
and a domain-anchor check that loops over every firm token (legal name
+ DBAs from ``broker_dealers.dba_names``). That last gate is what lets
firms registered as ``303 ALTERNATIVES, LLC`` land at
``303capitalmarkets.com`` (DBA "303Capital Markets") — neither the
legal-name token nor a raw Apollo lookup would surface that without
the DBA-aware anchor.

serper.dev (the cheaper Google-search tier in the on-demand chain) is
intentionally skipped by passing ``serper=None``: this backfill walks
``Apollo -> SerpAPI`` only. Drop ``--no-serpapi`` if you want
Apollo-only.

Idempotent: rows where ``website`` is already populated are skipped, so
a second run is effectively free (selects + skips). Provider errors
(``all_providers_errored``) leave the row's ``website`` NULL so the
next run picks the firm up again. ``no_valid_candidate`` is a clean
miss (Apollo + SerpAPI ran, validator rejected everything) and stays
NULL until a future re-run can do better.

Rate-limit hygiene: a 0.25s sleep between firms keeps Apollo bursts
under their 429 line. SerpAPI fires only when Apollo misses, so its
call rate is naturally capped by the miss cohort.

Usage::

    python -m scripts.backfill_firm_websites              # full run (Apollo -> SerpAPI)
    python -m scripts.backfill_firm_websites --limit 50   # smoke test
    python -m scripts.backfill_firm_websites --no-serpapi # Apollo only
    python -m scripts.backfill_firm_websites --no-apollo  # SerpAPI only
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
from sqlalchemy.exc import DBAPIError, OperationalError  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.broker_dealer import BrokerDealer  # noqa: E402
from app.services.apollo import ApolloClient  # noqa: E402
from app.services.serpapi import SerpAPIClient  # noqa: E402
from app.services.website_resolver import resolve_website  # noqa: E402


logger = logging.getLogger("backfill_firm_websites")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
# Quiet the noisy per-HTTP-request logs from httpx and the per-candidate
# "Website validate failed" lines from the resolver. WARNING+ still shows.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("app.services.website_resolver").setLevel(logging.WARNING)


_BATCH_SIZE = 50
_PROGRESS_EVERY = 25
_PER_FIRM_DELAY_S = 0.25
# Retry config for transient DB errors. Neon's serverless compute
# auto-suspends + scales: when it does, every active connection gets
# a Postgres ``AdminShutdown`` (SQLSTATE 57P01) → SQLAlchemy
# ``OperationalError``. ``pool_pre_ping`` catches DEAD connections
# before they're handed out, but mid-query kills bypass it. Retry the
# DB op on a fresh connection (the pool replaces the dead one).
_DB_RETRY_ATTEMPTS = 4
_DB_RETRY_BACKOFF_S = (2, 5, 10)  # waits before attempts 2/3/4


async def _db_retry(coro_factory, *, what: str):
    """Execute ``coro_factory()`` with retries on ``OperationalError``.

    ``coro_factory`` is a zero-arg callable that returns a fresh
    coroutine each call (we can't await the same coroutine twice).
    Retries on ``OperationalError`` and ``DBAPIError`` with
    ``connection_invalidated`` set — both signal a dead/killed
    connection. Other DBAPIErrors (constraint violations, etc.) are
    re-raised immediately.
    """
    last_exc: Exception | None = None
    for attempt in range(_DB_RETRY_ATTEMPTS):
        if attempt > 0:
            sleep_s = _DB_RETRY_BACKOFF_S[
                min(attempt - 1, len(_DB_RETRY_BACKOFF_S) - 1)
            ]
            print(
                f"  -> db retry {attempt}/{_DB_RETRY_ATTEMPTS - 1} "
                f"for {what} (sleeping {sleep_s}s)",
                flush=True,
            )
            await asyncio.sleep(sleep_s)
        try:
            return await coro_factory()
        except OperationalError as exc:
            last_exc = exc
            continue
        except DBAPIError as exc:
            # Some flavors of pool-killed connection surface as a
            # generic DBAPIError with ``connection_invalidated``
            # rather than ``OperationalError``. Retry those too.
            if getattr(exc, "connection_invalidated", False):
                last_exc = exc
                continue
            raise
    assert last_exc is not None
    raise last_exc


async def _select_targets(
    limit: int | None,
) -> list[tuple[int, str, str | None, list[str] | None, list[str] | None]]:
    """Return (id, name, crd_number, dba_names, resolver_aliases) tuples
    for firms missing a website.

    Ordered by id so a partial run + resumption walks the same firms in
    the same order. ``crd_number`` may be None on legacy ``finra_only``
    rows that never had one — those firms still get the Apollo lookup,
    just without the CRD hint passed to Apollo. ``resolver_aliases``
    is the LLM-augmented alternate-name pool; pre-populate it via
    ``scripts/enrich_firm_aliases.py`` before this run for the widest
    recall on short-acronym brand names (BOFA / TD / RBC etc.).
    """
    stmt = (
        select(
            BrokerDealer.id,
            BrokerDealer.name,
            BrokerDealer.crd_number,
            BrokerDealer.dba_names,
            BrokerDealer.resolver_aliases,
        )
        .where(BrokerDealer.website.is_(None))
        .order_by(BrokerDealer.id.asc())
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    async with SessionLocal() as db:
        rows = (await db.execute(stmt)).all()
    return [
        (int(row.id), row.name, row.crd_number, row.dba_names, row.resolver_aliases)
        for row in rows
    ]


async def _persist(bd_id: int, website: str, source: str) -> None:
    """Stamp ``website`` + ``website_source`` on a single broker_dealer row."""
    async with SessionLocal() as db:
        await db.execute(
            update(BrokerDealer)
            .where(BrokerDealer.id == bd_id)
            .values(website=website, website_source=source)
        )
        await db.commit()


async def _fetch_fresh(bd_id: int) -> BrokerDealer | None:
    """Re-load the BD row inside its own short-lived session for the
    idempotency check. Factored out so it can be called via
    ``_db_retry`` without leaking session lifetime across retry
    attempts."""
    async with SessionLocal() as db:
        return await db.get(BrokerDealer, bd_id)


async def run(
    *, limit: int | None, no_serpapi: bool, no_apollo: bool
) -> None:
    targets = await _select_targets(limit=limit)
    total = len(targets)
    if total == 0:
        logger.info("Nothing to do — no broker_dealers with website IS NULL.")
        return

    if no_apollo and no_serpapi:
        logger.error(
            "Both --no-apollo and --no-serpapi set; nothing to run."
        )
        return

    logger.info(
        "Targeting %d firms (limit=%s, no_serpapi=%s, no_apollo=%s).",
        total,
        limit,
        no_serpapi,
        no_apollo,
    )

    apollo: ApolloClient | None = None
    if no_apollo:
        logger.info("--no-apollo flag set; skipping Apollo tier (SerpAPI-only).")
    else:
        apollo_key = settings.apollo_api_key
        if not apollo_key:
            logger.error(
                "APOLLO_API_KEY missing — Apollo is the primary tier; "
                "aborting. Set the env var or pass --no-apollo."
            )
            return
        apollo = ApolloClient(apollo_key)

    serpapi_key = settings.serpapi_api_key
    serpapi: SerpAPIClient | None = None
    if no_serpapi:
        logger.info("--no-serpapi flag set; running without SerpAPI.")
    elif serpapi_key:
        serpapi = SerpAPIClient(serpapi_key)
    else:
        logger.warning(
            "SERPAPI_API_KEY missing — running without SerpAPI fallback. "
            "Firms without an Apollo match will stay NULL."
        )

    counts = {
        "scanned": 0,
        "skipped_already_populated": 0,
        "filled_from_apollo": 0,
        "filled_from_serpapi": 0,
        "no_valid_candidate": 0,
        "all_providers_errored": 0,
        "db_error": 0,
    }

    for batch_start in range(0, total, _BATCH_SIZE):
        batch = targets[batch_start : batch_start + _BATCH_SIZE]
        for bd_id, name, crd_number, dba_names, resolver_aliases in batch:
            counts["scanned"] += 1
            print(
                f"[{counts['scanned']}/{total}] BD {bd_id} {name!r} ...",
                flush=True,
            )

            # Idempotency guard with retry: re-check the latest column
            # state per row so a concurrent backfill (or a resumed run)
            # doesn't overwrite a website that just landed. Wrapped in
            # ``_db_retry`` because Neon's serverless compute can drop
            # active connections during auto-suspend; the next attempt
            # gets a fresh connection from the pool.
            try:
                fresh = await _db_retry(
                    lambda: _fetch_fresh(bd_id),
                    what=f"idempotency check BD {bd_id}",
                )
            except (OperationalError, DBAPIError) as exc:
                counts["db_error"] += 1
                print(
                    f"  -> db_error (idempotency): {type(exc).__name__}",
                    flush=True,
                )
                continue
            if fresh is None or fresh.website:
                counts["skipped_already_populated"] += 1
                print("  -> skipped (already populated)", flush=True)
                continue

            website, source, reason = await resolve_website(
                name,
                crd_number,
                apollo,
                serpapi,
                None,  # serper.dev intentionally skipped — Apollo -> SerpAPI only
                dba_names=dba_names,
                resolver_aliases=resolver_aliases,
            )

            if website and source:
                try:
                    await _db_retry(
                        lambda u=website, s=source: _persist(bd_id, u, s),
                        what=f"persist BD {bd_id}",
                    )
                except (OperationalError, DBAPIError) as exc:
                    counts["db_error"] += 1
                    print(
                        f"  -> db_error (persist): {type(exc).__name__}",
                        flush=True,
                    )
                    continue
                if source == "apollo":
                    counts["filled_from_apollo"] += 1
                elif source == "serpapi":
                    counts["filled_from_serpapi"] += 1
                else:
                    # Defensive: serper is disabled but the resolver could
                    # in principle return another source string; count it
                    # so the summary still totals correctly.
                    counts.setdefault(f"filled_from_{source}", 0)
                    counts[f"filled_from_{source}"] += 1
                print(f"  -> {source}: {website}", flush=True)
            elif reason and reason.startswith("all_providers_errored"):
                counts["all_providers_errored"] += 1
                print(f"  -> all_providers_errored: {reason}", flush=True)
            else:
                counts["no_valid_candidate"] += 1
                print("  -> no_valid_candidate", flush=True)

            await asyncio.sleep(_PER_FIRM_DELAY_S)

            if counts["scanned"] % _PROGRESS_EVERY == 0:
                logger.info(
                    "Progress %d/%d  apollo=%d serpapi=%d "
                    "no_candidate=%d errored=%d skipped=%d db_err=%d",
                    counts["scanned"],
                    total,
                    counts["filled_from_apollo"],
                    counts["filled_from_serpapi"],
                    counts["no_valid_candidate"],
                    counts["all_providers_errored"],
                    counts["skipped_already_populated"],
                    counts["db_error"],
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
        "--no-serpapi",
        action="store_true",
        help="Skip the SerpAPI fallback (Apollo-only run).",
    )
    parser.add_argument(
        "--no-apollo",
        action="store_true",
        help="Skip the Apollo tier (SerpAPI-only run).",
    )
    args = parser.parse_args()
    asyncio.run(
        run(
            limit=args.limit,
            no_serpapi=args.no_serpapi,
            no_apollo=args.no_apollo,
        )
    )


if __name__ == "__main__":
    main()
