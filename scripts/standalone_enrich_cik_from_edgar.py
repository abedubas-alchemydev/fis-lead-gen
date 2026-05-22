"""Standalone enrichment: look up SEC EDGAR CIKs by sec_file_number and
backfill ``broker_dealers.cik`` + ``filings_index_url``.

Why this exists. The standalone gap-fill (PR #473) added 144 broker-dealer
rows to staging with ``matched_source = 'finra_only'`` and ``cik = NULL``.
Every EDGAR-dependent enrichment pipeline keys off CIK -- without it,
``last_filing_date``, ``latest_net_capital``, ``health_status``,
``clearing_classification``, and ``lead_priority`` can never be populated
for those rows. Some older rows from initial_load also have ``cik = NULL``
for the same merge-failure reason.

This script reads SEC EDGAR's ``cgi-bin/browse-edgar`` Atom feed and
maps each NULL-cik row's ``sec_file_number`` (e.g. ``"8-129"``) to the
firm's CIK. On success it UPDATEs both ``cik`` and the canonical
``filings_index_url`` (``data.sec.gov/submissions/CIK{padded}.json``) so
downstream pipelines (``EdgarService.fetch_last_filing_for_cik``,
``FocusReportService.load_financial_metrics``) can run cleanly.

Standalone -- no imports from ``app.*``, ``brokercheck_extractor/``, or
any other project module. Mirrors the file shape of
``scripts/standalone_gap_fill_master_list.py``.

Usage::

    # dry-run (default): print proposed updates
    python scripts/standalone_enrich_cik_from_edgar.py

    # apply
    python scripts/standalone_enrich_cik_from_edgar.py --apply

    # smoke-test a single firm by id
    python scripts/standalone_enrich_cik_from_edgar.py --bd-id 22655

    # override DB
    python scripts/standalone_enrich_cik_from_edgar.py --db-url <URL>

Dependencies (already in project requirements): httpx, sqlalchemy[asyncio],
psycopg (v3). Uses ``xml.etree`` from stdlib for the Atom feed.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from dataclasses import dataclass
from typing import Optional
from xml.etree import ElementTree as ET

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


if sys.platform == "win32" and sys.version_info < (3, 14):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
sys.stderr.reconfigure(line_buffering=True)  # type: ignore[attr-defined]

logger = logging.getLogger("standalone_enrich_cik_from_edgar")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# SEC EDGAR's Atom feed for company lookup by filer-number. Returns XML with
# a <cik>0000NNNNNN</cik> element when the file number resolves.
EDGAR_BROWSE_URL = (
    "https://www.sec.gov/cgi-bin/browse-edgar"
    "?action=getcompany&filenum={filenum}&output=atom"
)

# Canonical submissions endpoint our downstream EDGAR pipeline expects.
SUBMISSIONS_URL_TEMPLATE = "https://data.sec.gov/submissions/CIK{padded}.json"

HTTP_TIMEOUT = 30.0

# SEC.gov's Edgar Fair Access policy requires a User-Agent identifying the
# tool/contact. Plain browser UA gets a "Your Request Originates from an
# Undeclared Automated Tool" 403-equivalent response page.
HTTP_HEADERS = {
    "User-Agent": os.environ.get(
        "SEC_USER_AGENT",
        "AlchemyDev BD-Catchup Tool contact@alchemydev.io",
    ),
    "Accept": "application/atom+xml, application/xml, text/xml",
    "Accept-Encoding": "identity",
}


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CikHit:
    bd_id: int
    crd_number: Optional[str]
    sec_file_number: str
    name_from_db: str
    cik: str            # zero-padded 10-digit string, e.g. "0000042352"
    name_from_edgar: Optional[str]
    filings_index_url: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_db_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def _pad_cik(cik_raw: str) -> str:
    """SEC submissions JSON keys off the 10-digit zero-padded CIK form."""
    digits = "".join(ch for ch in cik_raw if ch.isdigit())
    if not digits:
        return ""
    return digits.zfill(10)


def _parse_browse_edgar_response(body: str) -> tuple[Optional[str], Optional[str]]:
    """Pull (cik_padded, conformed_name) from a browse-edgar Atom feed body.

    EDGAR's response is an Atom feed (default namespace
    ``http://www.w3.org/2005/Atom``) into which a non-standard
    ``<company-info>`` subtree is injected. The injected children
    (``<cik>``, ``<conformed-name>``, etc.) lack their own xmlns
    declaration, so they INHERIT the default Atom namespace via XML
    namespace-default-inheritance rules. ElementTree then sees them as
    ``{http://www.w3.org/2005/Atom}cik`` etc., and a naked ``.//cik``
    XPath returns nothing.

    Rather than hardcode the Atom namespace (and re-break if EDGAR ever
    changes it), iterate all elements and match on local-name. Robust
    to namespace shifts.
    """
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        logger.debug("XML parse error: %s; body preview=%r", exc, body[:200])
        return None, None
    cik_raw: Optional[str] = None
    name_raw: Optional[str] = None
    for el in root.iter():
        local = el.tag.split("}", 1)[-1]  # strip "{ns}" prefix if present
        if local == "cik" and el.text and cik_raw is None:
            cik_raw = el.text
        elif local == "conformed-name" and el.text and name_raw is None:
            name_raw = el.text
        if cik_raw is not None and name_raw is not None:
            break
    cik = _pad_cik(cik_raw) if cik_raw else None
    if not cik:
        return None, None
    name = name_raw.strip() if name_raw else None
    return cik, name


async def _lookup_cik(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    bd_id: int,
    crd_number: Optional[str],
    sec_file_number: str,
    db_name: str,
) -> Optional[CikHit]:
    """Resolve one BD row's CIK via the EDGAR browse-edgar Atom feed.

    Returns None on miss (firm not in EDGAR, malformed response, network
    error after retries).
    """
    url = EDGAR_BROWSE_URL.format(filenum=sec_file_number)
    delay = 1.0
    max_retries = 3
    async with sem:
        for attempt in range(max_retries + 1):
            try:
                resp = await client.get(url, headers=HTTP_HEADERS, timeout=HTTP_TIMEOUT)
            except httpx.HTTPError as exc:
                if attempt < max_retries:
                    logger.info(
                        "bd_id=%s sec=%s: network error (%s); backoff %.1fs (attempt %d/%d)",
                        bd_id, sec_file_number, type(exc).__name__, delay,
                        attempt + 1, max_retries,
                    )
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 16.0)
                    continue
                logger.warning("bd_id=%s sec=%s: network give-up: %s", bd_id, sec_file_number, exc)
                return None
            if resp.status_code == 429:
                if attempt < max_retries:
                    logger.info(
                        "bd_id=%s sec=%s: 429; backoff %.1fs (attempt %d/%d)",
                        bd_id, sec_file_number, delay, attempt + 1, max_retries,
                    )
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 16.0)
                    continue
                logger.warning("bd_id=%s sec=%s: still 429 after %d retries", bd_id, sec_file_number, max_retries)
                return None
            if resp.status_code != 200:
                logger.warning("bd_id=%s sec=%s: HTTP %s", bd_id, sec_file_number, resp.status_code)
                return None
            cik, name = _parse_browse_edgar_response(resp.text)
            if not cik:
                # EDGAR returned 200 but no <cik> -- firm not registered with
                # SEC, or wrong file_num format. Common for FINRA-only firms.
                logger.info("bd_id=%s sec=%s: no CIK in EDGAR feed", bd_id, sec_file_number)
                return None
            return CikHit(
                bd_id=bd_id,
                crd_number=crd_number,
                sec_file_number=sec_file_number,
                name_from_db=db_name,
                cik=cik,
                name_from_edgar=name,
                filings_index_url=SUBMISSIONS_URL_TEMPLATE.format(padded=cik),
            )
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Standalone CIK enrichment for broker_dealers via SEC EDGAR.",
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
    parser.add_argument(
        "--concurrency",
        type=int,
        default=2,
        help="Parallel EDGAR fetches (default 2). SEC tolerates a few RPS "
        "from a properly-identified UA; higher values risk throttling.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap the number of candidate rows to process.",
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
                            "SELECT id, crd_number, sec_file_number, name "
                            "FROM broker_dealers WHERE id = :id"
                        ),
                        {"id": args.bd_id},
                    )
                ).all()
            else:
                lim = "" if args.limit is None else f" LIMIT {int(args.limit)}"
                rows = (
                    await conn.execute(
                        text(
                            "SELECT id, crd_number, sec_file_number, name "
                            "FROM broker_dealers "
                            "WHERE cik IS NULL AND sec_file_number IS NOT NULL "
                            "ORDER BY id" + lim
                        )
                    )
                ).all()

        if not rows:
            logger.info("no candidate rows; nothing to do")
            return 0
        logger.info("found %d candidate row(s) with NULL cik + non-NULL sec_file_number", len(rows))

        sem = asyncio.Semaphore(args.concurrency)
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
            results = await asyncio.gather(
                *(
                    _lookup_cik(client, sem, r.id, r.crd_number, r.sec_file_number, r.name)
                    for r in rows
                )
            )

        hits = [r for r in results if r is not None]
        misses = len(results) - len(hits)
        logger.info("summary: hits=%d  misses=%d  total_probed=%d", len(hits), misses, len(results))

        if not hits:
            logger.info("no CIK matches; nothing to write")
            return 0

        if not args.apply:
            logger.info(
                "dry-run: would update %d row(s). Re-run with --apply to write.", len(hits),
            )
            for h in hits:
                logger.info(
                    "  bd_id=%d crd=%s sec=%s name_db=%r -> cik=%s name_edgar=%r",
                    h.bd_id, h.crd_number, h.sec_file_number,
                    h.name_from_db, h.cik, h.name_from_edgar,
                )
            return 0

        async with engine.begin() as conn:
            for h in hits:
                await conn.execute(
                    text(
                        "UPDATE broker_dealers "
                        "SET cik = :cik, filings_index_url = :url "
                        "WHERE id = :id"
                    ),
                    {"cik": h.cik, "url": h.filings_index_url, "id": h.bd_id},
                )
        logger.info("wrote %d row(s)", len(hits))
        return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
