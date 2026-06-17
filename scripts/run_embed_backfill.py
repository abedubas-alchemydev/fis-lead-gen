"""Standalone runner for the Doxie semantic-index embed backfill.

Why this exists. Doxie's semantic firm search reads the
``chatbot_firm_embedding`` pgvector table, which is refreshed by
``ChatbotSemanticService.backfill_broker_dealers`` /
``.backfill_investment_advisors`` (incremental — a SHA-256 content hash
lets re-runs skip rows whose summary text didn't change). Today the only
automatic refresh is ``run_post_pipeline_embed_backfill()`` chained onto
the populate-all pipeline, but populate-all almost never runs on staging,
so the index drifts stale while other pipelines (nightly net-new BD
extractor, refresh-all, gap-fills) keep mutating firm rows.

This script is the manual / scheduled entry point for that same backfill:
run it whenever the index should catch up, or wire it into a Cloud Run
Job (it reuses the backend image like ``gap_fill_investment_advisors.py``
— same lazy ``app.*`` import pattern, only needs DATABASE_URL +
GEMINI_API_KEY).

Behaviour. Runs the broker-dealer backfill then the investment-advisor
backfill (filter with ``--entity``), logging each entity's
``BackfillResult`` counts. Each entity runs in its own session and its
own try/except, so a hard failure in one never blocks the other.

Exit codes (mirrors ``standalone_extract_new_bds.py``):
  0 — at least one selected backfill completed (row-level ``failed``
      counts are logged but stay exit 0: the next run self-heals via
      hash-skip);
  1 — every selected backfill hard-failed (raised);
  2 — configuration error (no DB URL / no GEMINI_API_KEY).

Usage::

    # embed everything that's new or changed (BDs then IAs)
    python scripts/run_embed_backfill.py

    # one entity only
    python scripts/run_embed_backfill.py --entity broker_dealer
    python scripts/run_embed_backfill.py --entity investment_advisor

    # override DB URL
    python scripts/run_embed_backfill.py --db-url <URL>

Dependencies: the backend app package (``app.services.chatbot_semantic``)
plus its requirements; nothing beyond what the backend image already has.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if sys.platform == "win32" and sys.version_info < (3, 14):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
sys.stderr.reconfigure(line_buffering=True)  # type: ignore[attr-defined]

logger = logging.getLogger("run_embed_backfill")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

ENTITY_BROKER_DEALER = "broker_dealer"
ENTITY_INVESTMENT_ADVISOR = "investment_advisor"
ENTITY_ALL = "all"


def _normalize_db_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Doxie semantic-index embed backfill (chatbot_firm_embedding).",
    )
    parser.add_argument("--db-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument(
        "--entity",
        choices=[ENTITY_BROKER_DEALER, ENTITY_INVESTMENT_ADVISOR, ENTITY_ALL],
        default=ENTITY_ALL,
        help="Which entity table(s) to backfill (default: all).",
    )
    args = parser.parse_args(argv)

    if not args.db_url:
        logger.error("no DATABASE_URL env var and no --db-url; aborting")
        return 2

    # Lazy app imports -- after arg parsing so ``--help`` stays instant and
    # the sys.path bootstrap above is guaranteed to have run. Same pattern
    # as scripts/gap_fill_investment_advisors.py.
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    from app.core.config import settings
    from app.services.chatbot_semantic import ChatbotSemanticService

    if not settings.gemini_api_key:
        logger.error("GEMINI_API_KEY not configured; cannot embed. aborting")
        return 2

    service = ChatbotSemanticService()
    backfills = []
    if args.entity in (ENTITY_BROKER_DEALER, ENTITY_ALL):
        backfills.append((ENTITY_BROKER_DEALER, service.backfill_broker_dealers))
    if args.entity in (ENTITY_INVESTMENT_ADVISOR, ENTITY_ALL):
        backfills.append(
            (ENTITY_INVESTMENT_ADVISOR, service.backfill_investment_advisors)
        )

    # Own engine/sessionmaker (rather than app.db.session.SessionLocal) so
    # ``--db-url`` works without env mutation -- the service only needs an
    # AsyncSession bound to the right database.
    engine = create_async_engine(_normalize_db_url(args.db_url), pool_pre_ping=True)
    session_factory = async_sessionmaker(
        bind=engine, expire_on_commit=False, class_=AsyncSession
    )

    hard_failed = 0
    try:
        for entity, backfill in backfills:
            try:
                # Fresh session per entity: a hard failure mid-transaction
                # in one backfill must not poison the other's session.
                async with session_factory() as db:
                    result = await backfill(db)
            except Exception:  # noqa: BLE001
                hard_failed += 1
                logger.exception("%s embed backfill hard-failed", entity)
                continue
            logger.info(
                "%s embed backfill: embedded=%d skipped=%d failed=%d",
                entity,
                result.embedded,
                result.skipped,
                result.failed,
            )
            if result.failed:
                logger.warning(
                    "%s embed backfill left %d row(s) unembedded; the next "
                    "run retries them via hash-skip",
                    entity,
                    result.failed,
                )
    finally:
        await engine.dispose()

    if hard_failed == len(backfills):
        logger.error(
            "all %d selected embed backfill(s) hard-failed", len(backfills)
        )
        return 1
    if hard_failed:
        logger.warning(
            "%d/%d embed backfill(s) hard-failed; exiting 0 -- the next run "
            "self-heals via hash-skip",
            hard_failed,
            len(backfills),
        )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
