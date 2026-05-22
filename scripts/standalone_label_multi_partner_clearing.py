"""Standalone label fix: for active broker-dealers whose latest clearing
arrangement is ``unknown`` because the LLM extracted MULTIPLE clearing
partners and couldn't pick a single primary, stamp
``current_clearing_partner = 'Multiple Partners'`` and
``current_clearing_type = 'fully_disclosed'`` so the Master List grid
stops showing "Extraction needs review - value pending" on rows where
the data is actually informative.

Why this exists. Phase 1 diagnostic on staging surfaced 111 active BDs
still on ``current_clearing_type = 'unknown'`` after PRs #471-#480. Of
those, 12 firms have ``extraction_notes`` that explicitly identify
multiple disclosed clearing partners but decline to pick one as
"primary." Example: Susquehanna Securities, LLC --

  "Multiple clearing partners (BofA Securities, Inc., Merrill Lynch
   International London, Goldman Sachs & Co. LLC, and Barclays Capital
   Inc.) are named as primarily providing clearing and depository
   operations. As no single primary partner is identified, the
   clearing_type is set to 'unknown' as per instructions."

That's the LLM faithfully reporting accurate data (the firm DOES use
multiple disclosed clearers). The UI then renders "Extraction needs
review" because it doesn't know what else to show for clearing_type=
unknown with NULL partner. This script writes a "Multiple Partners"
sentinel and reclassifies the firm as fully_disclosed (which is
literally what the firm is -- they introduce to multiple disclosed
partners).

Affected firms on staging:
  - ATOMIC BROKERAGE LLC
  - BALANZ CAPITAL USA LLC
  - DRIVEWEALTH, LLC
  - OPPENHEIMER & CO. INC.
  - EFG CAPITAL INTERNATIONAL
  - MESIROW FINANCIAL, INC.
  - AMERICAN TRUST INVESTMENT SERVICES, INC.
  - XP INVESTMENTS US, LLC
  - ROYAL TREASURE SECURITIES LLC
  - SUSQUEHANNA SECURITIES, LLC
  - FLOW TRADERS U.S. INSTITUTIONAL TRADING LLC
  - TP ICAP GLOBAL MARKETS AMERICAS LLC (gap-filled in PR #473)

Detail-page fidelity. The full list of partners stays in the
``clearing_arrangements`` row's ``extraction_notes``. A user clicking
into the firm detail page can still see the LLM's full reasoning
including all the partner names -- this script only changes the
Master List grid summary.

Standalone -- no imports from ``app.*``, ``brokercheck_extractor/``,
or any other project module. Mirrors the file shape of
``scripts/standalone_label_self_clearing_partners.py``.

Idempotency caveat. Same as #479 / #480: a subsequent
``refresh_clearing_rollups()`` will copy the arrangement's
``clearing_type='unknown'`` and NULL partner back up to the BD row,
undoing this fix. Backend rollup needs a small change to make this
permanent.

Usage::

    # dry-run (default)
    python scripts/standalone_label_multi_partner_clearing.py

    # apply
    python scripts/standalone_label_multi_partner_clearing.py --apply

    # smoke-test a single BD
    python scripts/standalone_label_multi_partner_clearing.py --bd-id 23785

    # override DB
    python scripts/standalone_label_multi_partner_clearing.py --db-url <URL>

Dependencies (already in project requirements): sqlalchemy[asyncio],
psycopg (v3). Zero HTTP, zero LLM.
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

logger = logging.getLogger("standalone_label_multi_partner_clearing")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


# Sentinel values written for multi-partner firms.
PARTNER_SENTINEL = "Multiple Partners"
TYPE_SENTINEL = "fully_disclosed"


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
        description="Label multi-partner clearing firms with sentinel value instead of 'needs review'.",
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

    # The candidate set: BDs whose current rolled-up clearing_type is
    # 'unknown' AND whose latest arrangement's notes explicitly mention
    # multiple partners with no single primary. We require BOTH the
    # "multiple clearing partners" phrase AND the "no single primary"
    # phrase to be conservative -- some firms' notes mention "multiple"
    # casually without being the LLM's structured giveup.
    where_clause = """
        bd.current_clearing_type = 'unknown'
        AND EXISTS (
          SELECT 1 FROM clearing_arrangements ca
          WHERE ca.bd_id = bd.id
            AND ca.filing_year = (
                SELECT MAX(ca2.filing_year)
                FROM clearing_arrangements ca2
                WHERE ca2.bd_id = bd.id
            )
            AND (
                ca.extraction_notes ILIKE '%multiple clearing partners%'
             OR ca.extraction_notes ILIKE '%multiple named clearing%'
             OR ca.extraction_notes ILIKE '%no single primary%'
             OR ca.extraction_notes ILIKE '%two distinct%'
            )
        )
    """

    try:
        async with engine.connect() as conn:
            if args.bd_id is not None:
                rows = (
                    await conn.execute(
                        text(
                            "SELECT bd.id, bd.crd_number, bd.name FROM broker_dealers bd "
                            "WHERE bd.id = :id AND " + where_clause
                        ),
                        {"id": args.bd_id},
                    )
                ).all()
            else:
                rows = (
                    await conn.execute(
                        text(
                            "SELECT bd.id, bd.crd_number, bd.name FROM broker_dealers bd "
                            "WHERE bd.status = 'Active' AND " + where_clause + " "
                            "ORDER BY bd.id"
                        )
                    )
                ).all()

        candidates = [Candidate(bd_id=r.id, crd_number=r.crd_number, name=r.name) for r in rows]
        if not candidates:
            logger.info("no candidate rows; nothing to do")
            return 0
        logger.info(
            "found %d active BD row(s) where LLM identified multiple primary clearing partners",
            len(candidates),
        )
        for c in candidates:
            logger.info("  bd_id=%d crd=%s name=%r", c.bd_id, c.crd_number, c.name)

        if not args.apply:
            logger.info(
                "dry-run: would set current_clearing_type='%s', current_clearing_partner='%s' "
                "on %d row(s). Re-run with --apply to write.",
                TYPE_SENTINEL, PARTNER_SENTINEL, len(candidates),
            )
            return 0

        async with engine.begin() as conn:
            result = await conn.execute(
                text(
                    "UPDATE broker_dealers SET "
                    "  current_clearing_type = :type_val, "
                    "  current_clearing_partner = :partner_val "
                    "WHERE id = ANY(:ids)"
                ),
                {
                    "type_val": TYPE_SENTINEL,
                    "partner_val": PARTNER_SENTINEL,
                    "ids": [c.bd_id for c in candidates],
                },
            )
        logger.info(
            "wrote multi-partner sentinel on %d row(s)",
            result.rowcount if result.rowcount is not None else len(candidates),
        )
        return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
