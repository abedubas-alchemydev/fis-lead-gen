"""Standalone label fix: for active broker-dealers whose latest clearing
arrangement is ``No X-17A-5 PDF available``, NULL out the rolled-up
``current_clearing_*`` columns so the Master List grid shows the
"Not on file" badge instead of the misleading
"Extraction needs review - value pending" tooltip.

Why this exists. The Master List grid renders three different states
for the Clearing Arrangement column:

  - ``current_clearing_type IS NULL`` -> "Not on file" (correct: no
    clearing data is available)
  - ``current_clearing_type = 'unknown'`` -> "Extraction needs review"
    (implies the LLM actually parsed something but couldn't decide)
  - any concrete type -> the partner name

The pipeline writes ``clearing_type='unknown'`` on every arrangement
where it couldn't find a PDF to parse -- including the 325 active BDs
on staging whose ``extraction_notes`` literally say "No X-17A-5 PDF
available for this broker-dealer." That's data-source absence, not
LLM uncertainty. Those firms should display "Not on file" alongside
the 727 firms that have no clearing_arrangements row at all.

Diagnostic on staging (after PRs #471-#479):

  current_clearing_type distribution for status='Active':
    self_clearing:    1,392  (PR #479 displays them correctly)
    NULL:               727  ("Not on file" - correct)
    unknown:            436  <- this PR fixes most of these
    fully_disclosed:    395
    omnibus:              7

  Of the 436 "unknown" rows:
    325 have notes "No X-17A-5 PDF available"  <- this PR
     ~8 have transient errors (network / vision OCR)
    ~13 are genuine multi-partner ambiguity (Susquehanna et al.)
    ~90 are genuine LLM uncertainty (partner not named in PDF, etc.)

What this script does. Single bulk UPDATE that NULLs the rolled-up
clearing columns for BDs whose latest clearing_arrangement explicitly
indicates the source PDF was unavailable. Matches the column shape the
rollup at backend/app/services/broker_dealers.py:628-634 writes when
``latest is None`` (no arrangements at all) -- so the UI treats both
cases identically.

The ~13 multi-partner cases and the ~90 genuine LLM-uncertainty cases
are deliberately left alone. The tooltip text on those rows is
actually informative for human reviewers (e.g. Susquehanna's notes
explain "BofA, Merrill, Goldman, Barclays are all named primarily").

Standalone -- no imports from ``app.*``, ``brokercheck_extractor/``,
or any other project module. Mirrors the file shape of
``scripts/standalone_label_self_clearing_partners.py``.

Idempotency caveat. Same as PR #479: a subsequent
``refresh_clearing_rollups()`` will copy the arrangement's
``clearing_type='unknown'`` back up to the BD row, undoing this fix.
The proper backend follow-up is to teach the rollup to treat
``extraction_status='missing_pdf'`` as equivalent to "no arrangement"
when deciding whether to NULL the BD's clearing columns.

Usage::

    # dry-run (default)
    python scripts/standalone_relabel_no_pdf_clearing_as_not_on_file.py

    # apply
    python scripts/standalone_relabel_no_pdf_clearing_as_not_on_file.py --apply

    # smoke-test a single BD
    python scripts/standalone_relabel_no_pdf_clearing_as_not_on_file.py --bd-id 23000

    # override DB
    python scripts/standalone_relabel_no_pdf_clearing_as_not_on_file.py --db-url <URL>

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

logger = logging.getLogger("standalone_relabel_no_pdf_clearing_as_not_on_file")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


# Phrase the pipeline writes into ``clearing_arrangements.extraction_notes``
# when there's no X-17A-5 PDF to parse. Verified on staging: 325 active BDs
# match this exact prefix.
NO_PDF_PHRASE = "No X-17A-5 PDF available"


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
        description="NULL clearing rollup columns on BDs whose latest arrangement is 'No X-17A-5 PDF available'.",
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
    # 'unknown' AND whose latest (by filing_year DESC) clearing_arrangements
    # row has extraction_notes starting with the no-PDF phrase. Using a
    # correlated subquery for "latest" keeps the matching honest -- some
    # BDs have multiple arrangements (different filing years) and we
    # only care about the one the rollup actually picked.
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
            AND ca.extraction_notes ILIKE :phrase
        )
    """
    base_params = {"phrase": f"{NO_PDF_PHRASE}%"}

    try:
        async with engine.connect() as conn:
            if args.bd_id is not None:
                rows = (
                    await conn.execute(
                        text(
                            "SELECT bd.id, bd.crd_number, bd.name FROM broker_dealers bd "
                            "WHERE bd.id = :id AND " + where_clause
                        ),
                        {**base_params, "id": args.bd_id},
                    )
                ).all()
            else:
                rows = (
                    await conn.execute(
                        text(
                            "SELECT bd.id, bd.crd_number, bd.name FROM broker_dealers bd "
                            "WHERE bd.status = 'Active' AND " + where_clause + " "
                            "ORDER BY bd.id"
                        ),
                        base_params,
                    )
                ).all()

        candidates = [Candidate(bd_id=r.id, crd_number=r.crd_number, name=r.name) for r in rows]
        if not candidates:
            logger.info("no candidate rows; nothing to do")
            return 0
        logger.info(
            "found %d active BD row(s) where current_clearing_type='unknown' AND latest arrangement is 'No X-17A-5 PDF available'",
            len(candidates),
        )

        if not args.apply:
            logger.info(
                "dry-run: would NULL out current_clearing_type / current_clearing_partner / "
                "current_clearing_extraction_confidence / current_clearing_source_filing_url "
                "on %d row(s). Re-run with --apply to write.",
                len(candidates),
            )
            for c in candidates[:10]:
                logger.info("  bd_id=%d crd=%s name=%r", c.bd_id, c.crd_number, c.name)
            if len(candidates) > 10:
                logger.info("  ... and %d more", len(candidates) - 10)
            return 0

        async with engine.begin() as conn:
            result = await conn.execute(
                text(
                    "UPDATE broker_dealers SET "
                    "  current_clearing_type = NULL, "
                    "  current_clearing_partner = NULL, "
                    "  current_clearing_extraction_confidence = NULL, "
                    "  current_clearing_source_filing_url = NULL "
                    "WHERE id = ANY(:ids)"
                ),
                {"ids": [c.bd_id for c in candidates]},
            )
        logger.info(
            "wrote NULL to clearing rollup columns on %d row(s)",
            result.rowcount if result.rowcount is not None else len(candidates),
        )
        return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
