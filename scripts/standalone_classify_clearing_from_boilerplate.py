"""Standalone deterministic clearing classifier: fix the
``clearing_classification = 'needs_review'`` epidemic on broker_dealers by
applying a binary rule on the ``firm_operations_text`` we already have.

Why this exists. Phase 1 diagnostic on staging found that 2,654 of 2,957
active BDs (90%) were stuck on ``clearing_classification = 'needs_review'``.
The cause was NOT a parsing bug -- the parser at
``backend/app/services/brokercheck_pdf.py:_parse_firm_operations`` correctly
extracts the "Clearing Arrangements" body from Form BD PDFs. The cause was
that the LLM classifier consistently failed to make a high-confidence
(>= 0.7) call on the short two-variant text FINRA actually puts there.

The text is deterministic and binary. Across all 2,957 active BDs the
``firm_operations_text`` column held exactly two unique values:

  Variant 1 (2,712 firms): "This firm does not hold or maintain funds or
    securities or provide clearing services for other broker-dealer(s)."
    -> Firm doesn't self-clear and doesn't carry for others.
    -> Must be a fully_disclosed introducing firm.

  Variant 2 (99 firms): "This firm does hold or maintain funds or
    securities or provide clearing services for other broker-dealer(s)."
    -> Firm self-clears OR carries for others. The text cannot distinguish
       self_clearing from omnibus -- that requires the X-17A-5 clearing
       statement (which the in-tree pipeline currently never populates
       into ``clearing_raw_text`` on the BD row). So this script does NOT
       touch Variant 2 firms.

What this script does:

  1. SELECT every BD whose ``firm_operations_text`` matches Variant 1
     (case-insensitive, fragment-tolerant).
  2. If ``clearing_classification`` is not already ``fully_disclosed``,
     UPDATE it to ``fully_disclosed`` -- including the 4 firms that the
     LLM wrongly stamped ``self_clearing`` despite the text saying "does
     NOT hold". The text is regulated self-disclosure on a Form BD filing;
     the LLM was second-guessing it.

  3. Variant 2 rows are left alone -- their existing classification (which
     may be ``self_clearing``, ``omnibus``, or ``needs_review``) reflects
     the data we can extract today; refining it is out of scope here and
     belongs in the parser-improvement work (Phase 3 of the plan).

Standalone -- no imports from ``app.*``, ``brokercheck_extractor/``, or
any other project module. Mirrors the file shape of
``scripts/standalone_gap_fill_master_list.py``.

After running this script with ``--apply``, follow with::

    python -m scripts.run_scoring --all

so the new ``fully_disclosed`` classifications flow into ``lead_score`` /
``lead_priority``. Lead scoring weights ``clearing_classification``
heavily -- ``fully_disclosed`` firms are the primary prospect category.

Usage::

    # dry-run (default): list proposed updates, no writes
    python scripts/standalone_classify_clearing_from_boilerplate.py

    # apply
    python scripts/standalone_classify_clearing_from_boilerplate.py --apply

    # smoke-test a single row
    python scripts/standalone_classify_clearing_from_boilerplate.py --bd-id 21314

    # override DB
    python scripts/standalone_classify_clearing_from_boilerplate.py --db-url <URL>

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

logger = logging.getLogger("standalone_classify_clearing_from_boilerplate")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


# ---------------------------------------------------------------------------
# Rule
# ---------------------------------------------------------------------------

# Case-insensitive fragment that uniquely identifies Variant 1 ("does not
# hold...") without false-matching Variant 2 ("does hold...").
VARIANT_1_FRAGMENT = "does not hold or maintain funds"

# What we write when Variant 1 matches.
TARGET_CLASSIFICATION = "fully_disclosed"


@dataclass(frozen=True)
class Candidate:
    bd_id: int
    crd_number: Optional[str]
    name: str
    current_class: Optional[str]


def _normalize_db_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Deterministic clearing_classification fix from firm_operations_text boilerplate.",
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
        # Select Variant-1 rows whose current classification is NOT already
        # fully_disclosed. Active-only -- inactive firms aren't shown on the
        # Master List and aren't worth churning.
        async with engine.connect() as conn:
            if args.bd_id is not None:
                rows = (
                    await conn.execute(
                        text(
                            "SELECT id, crd_number, name, clearing_classification "
                            "FROM broker_dealers "
                            "WHERE id = :id "
                            "AND firm_operations_text ILIKE :pat "
                            "AND (clearing_classification IS DISTINCT FROM :target)"
                        ),
                        {"id": args.bd_id, "pat": f"%{VARIANT_1_FRAGMENT}%", "target": TARGET_CLASSIFICATION},
                    )
                ).all()
            else:
                rows = (
                    await conn.execute(
                        text(
                            "SELECT id, crd_number, name, clearing_classification "
                            "FROM broker_dealers "
                            "WHERE status = 'Active' "
                            "AND firm_operations_text ILIKE :pat "
                            "AND (clearing_classification IS DISTINCT FROM :target) "
                            "ORDER BY id"
                        ),
                        {"pat": f"%{VARIANT_1_FRAGMENT}%", "target": TARGET_CLASSIFICATION},
                    )
                ).all()

        candidates = [
            Candidate(
                bd_id=r.id,
                crd_number=r.crd_number,
                name=r.name,
                current_class=r.clearing_classification,
            )
            for r in rows
        ]
        if not candidates:
            logger.info("no candidate rows; nothing to do (existing classifications already correct)")
            return 0
        logger.info(
            "found %d candidate row(s) with Variant-1 text whose classification != %r",
            len(candidates), TARGET_CLASSIFICATION,
        )

        # Breakdown by current classification -- useful as a sanity check
        # before applying.
        breakdown: dict[Optional[str], int] = {}
        for c in candidates:
            breakdown[c.current_class] = breakdown.get(c.current_class, 0) + 1
        for cur, n in sorted(breakdown.items(), key=lambda kv: -kv[1]):
            logger.info("  current='%s'  count=%d", cur, n)

        if not args.apply:
            logger.info(
                "dry-run: would update %d row(s) to clearing_classification='%s'. "
                "Re-run with --apply to write.",
                len(candidates), TARGET_CLASSIFICATION,
            )
            # Show a few samples
            for c in candidates[:10]:
                logger.info(
                    "  bd_id=%d crd=%s name=%r  %s -> %s",
                    c.bd_id, c.crd_number, c.name, c.current_class, TARGET_CLASSIFICATION,
                )
            if len(candidates) > 10:
                logger.info("  ... and %d more", len(candidates) - 10)
            return 0

        # Single bulk UPDATE. The candidate list is the source of truth -- we
        # already filtered to exactly the rows that need to change.
        async with engine.begin() as conn:
            result = await conn.execute(
                text(
                    "UPDATE broker_dealers SET clearing_classification = :target "
                    "WHERE id = ANY(:ids)"
                ),
                {"target": TARGET_CLASSIFICATION, "ids": [c.bd_id for c in candidates]},
            )
        logger.info(
            "wrote %d row(s) to clearing_classification='%s'",
            result.rowcount if result.rowcount is not None else len(candidates),
            TARGET_CLASSIFICATION,
        )
        return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
