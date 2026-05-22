"""Standalone label fix: set ``current_clearing_partner = 'Self-Clearing'``
for active broker-dealers whose latest clearing arrangement is itself a
self-clearing arrangement (and therefore correctly has no partner name).

Why this exists. The Master List grid renders an "Extraction needs review
- value pending" badge whenever ``current_clearing_partner`` is NULL on
the BD row. For self-clearing firms that is misleading -- they don't HAVE
a partner because they clear their own trades. The data is correct
(``clearing_type = 'self_clearing'``, ``clearing_partner = NULL``); the
rollup at ``backend/app/services/broker_dealers.py:637`` blindly copies
the NULL partner up to the BD row, and the UI treats NULL as a parsing
failure.

Diagnostic on staging: 1,788 active BDs in this state. Examples include
Susquehanna Financial Group (id=24222, exempt under Rule 15c3-3(k)(2)(i))
and most of the well-known self-clearing wirehouses (J.P. Morgan, Goldman,
Morgan Stanley) whose arrangements record ``self_clearing`` with high
confidence (~0.90) but the partner column is correctly empty.

What this script does:

  UPDATE broker_dealers
     SET current_clearing_partner = 'Self-Clearing'
   WHERE status = 'Active'
     AND current_clearing_type = 'self_clearing'
     AND (current_clearing_partner IS NULL OR current_clearing_partner = '')

A sentinel string ('Self-Clearing') is the simplest way to remove the
misleading "Extraction needs review" badge without a backend/frontend
deploy. Long-term the proper fix is in the rollup (or in the FE
renderer) -- see the companion plan -- but this gets the UI honest
immediately.

Standalone -- no imports from ``app.*``, ``brokercheck_extractor/``, or
any other project module. Mirrors the file shape of
``scripts/standalone_classify_clearing_from_boilerplate.py``.

Idempotency note: a subsequent ``refresh_clearing_rollups()`` run will
overwrite our sentinel back to NULL (the rollup just copies the latest
arrangement's NULL partner). Until the backend rollup is patched, this
script needs to be re-run after any clearing pipeline run that touches
self-clearing firms. The companion PR (Option A) will fix that.

Usage::

    # dry-run (default)
    python scripts/standalone_label_self_clearing_partners.py

    # apply
    python scripts/standalone_label_self_clearing_partners.py --apply

    # smoke-test a single firm
    python scripts/standalone_label_self_clearing_partners.py --bd-id 24222

    # override DB
    python scripts/standalone_label_self_clearing_partners.py --db-url <URL>

Dependencies (already in project requirements):
sqlalchemy[asyncio], psycopg (v3). Zero HTTP, zero LLM.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


if sys.platform == "win32" and sys.version_info < (3, 14):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
sys.stderr.reconfigure(line_buffering=True)  # type: ignore[attr-defined]

logger = logging.getLogger("standalone_label_self_clearing_partners")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


SENTINEL = "Self-Clearing"


@dataclass(frozen=True)
class Candidate:
    bd_id: int
    crd_number: Optional[str]
    name: str


def _normalize_db_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Stamp current_clearing_partner='Self-Clearing' on active BDs whose latest arrangement is self_clearing with NULL partner.",
    )
    parser.add_argument("--db-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write to the DB. Without this, the script runs dry.",
    )
    parser.add_argument(
        "--bd-id",
        type=int,
        default=None,
        help="Restrict to a single broker_dealers.id (smoke-test mode).",
    )
    args = parser.parse_args()

    if not args.db_url:
        logger.error("no DATABASE_URL env var and no --db-url; aborting")
        return 2

    db_url = _normalize_db_url(args.db_url)
    engine = create_async_engine(db_url, pool_pre_ping=True)

    try:
        async with engine.connect() as conn:
            if args.bd_id is not None:
                rows = (
                    await conn.execute(
                        text(
                            "SELECT id, crd_number, name "
                            "FROM broker_dealers "
                            "WHERE id = :id "
                            "AND current_clearing_type = 'self_clearing' "
                            "AND (current_clearing_partner IS NULL OR current_clearing_partner = '')"
                        ),
                        {"id": args.bd_id},
                    )
                ).all()
            else:
                rows = (
                    await conn.execute(
                        text(
                            "SELECT id, crd_number, name "
                            "FROM broker_dealers "
                            "WHERE status = 'Active' "
                            "AND current_clearing_type = 'self_clearing' "
                            "AND (current_clearing_partner IS NULL OR current_clearing_partner = '') "
                            "ORDER BY id"
                        )
                    )
                ).all()

        candidates = [Candidate(bd_id=r.id, crd_number=r.crd_number, name=r.name) for r in rows]
        if not candidates:
            logger.info("no candidate rows; nothing to do")
            return 0
        logger.info(
            "found %d active BD row(s) with clearing_type=self_clearing and NULL partner",
            len(candidates),
        )

        if not args.apply:
            logger.info(
                "dry-run: would update %d row(s) to current_clearing_partner='%s'. "
                "Re-run with --apply to write.",
                len(candidates), SENTINEL,
            )
            for c in candidates[:10]:
                logger.info("  bd_id=%d crd=%s name=%r", c.bd_id, c.crd_number, c.name)
            if len(candidates) > 10:
                logger.info("  ... and %d more", len(candidates) - 10)
            return 0

        async with engine.begin() as conn:
            result = await conn.execute(
                text(
                    "UPDATE broker_dealers SET current_clearing_partner = :sentinel "
                    "WHERE id = ANY(:ids)"
                ),
                {"sentinel": SENTINEL, "ids": [c.bd_id for c in candidates]},
            )
        logger.info(
            "wrote %d row(s) to current_clearing_partner='%s'",
            result.rowcount if result.rowcount is not None else len(candidates),
            SENTINEL,
        )
        return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
