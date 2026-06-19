"""Standalone refresh: re-fetch the FINRA Form BD for EXISTING broker-dealers
and UPDATE their ``registration_date`` / ``formation_date`` in place.

Why this exists. A firm gets its CRD when it first *applies* to FINRA,
then gets SEC-*approved* months later. So a firm registering now usually
already has a CRD <= our max and is **already in our DB** -- but the
net-new extractor (``standalone_extract_new_bds.py``) only ever INSERTs
brand-new CRDs above the max; it never UPDATEs an existing firm's
``registration_date`` when that firm is finally approved. On top of that
``registration_date`` is NULL for most rows (``data_merge.py`` sets it
EDGAR-only, and Form BD is filed via FINRA Web CRD, never EDGAR). The
result: newly-approved firms stay invisible to the "New BDs" window.

This script closes that gap. It walks EXISTING firms on a rolling
cursor, re-fetches each one's FINRA Form BD, re-parses the "SEC Approved"
date and the formation date, and UPDATEs the columns when a value is
present. It is **UPDATE-only** -- it never INSERTs a row -- and it only
ever overwrites a column when it actually parsed a value (COALESCE), so a
parse miss never null-outs an existing date.

Two callers, one script (see plans/new-bds-ingestion-fix-2026-06-19.md):
  * **Nightly rolling refresh** -- a small ``--limit`` (default 250) per
    night cycles the whole book over ~2 weeks, catching new approvals
    within that window.
  * **One-time backfill** -- a large ``--limit`` (or repeated runs) drains
    every NULL-``registration_date`` firm in one pass so the dashboard is
    accurate immediately.

Standalone -- like ``standalone_extract_new_bds.py``, the fetch + parse
imports nothing from ``app.*``. The FINRA JSON/PDF fetchers, the
SEC/formation date regexes, and ``_extract_dates_from_pdf`` are
**copied verbatim** from that extractor (and ultimately from the in-tree
parser at ``backend/app/services/brokercheck_pdf.py:549-570``) so the
parsed semantics match every row already in those columns. The copy is
deliberate: this file can be deleted after the catch-up without breaking
anything else, and it pulls in **no paid provider** (no Gemini, no
Apollo, no embed hook) -- FINRA's free public API only.

Selection (rolling cursor). SELECT a batch of EXISTING firms with a
numeric ``crd_number``, ordered to prioritize refresh need:
  1. ``registration_date IS NULL`` first  (firms with no date at all),
  2. then ``registration_checked_at NULLS FIRST``  (never refreshed),
  3. then oldest ``registration_checked_at``  (least-recently refreshed).
``--limit`` caps the batch. Because ``registration_checked_at`` is
stamped on EVERY touched firm (even a parse miss), repeated runs keep
advancing the cursor instead of re-probing the same stuck firms.

Per-firm flow:
  1. GET ``https://api.brokercheck.finra.org/search/firm/{crd}?wt=json``
     (only to confirm the firm resolves; dates come from the PDF).
  2. Fetch Form BD PDF from ``files.brokercheck.finra.org``.
  3. Regex out SEC ``registration_date`` and ``formation_date``.
  4. On ``--apply``: ``UPDATE broker_dealers SET
     registration_date = COALESCE(:new_reg, registration_date),
     formation_date    = COALESCE(:new_formation, formation_date),
     registration_checked_at = now() WHERE id = :id``.

Usage::

    # dry-run a nightly-sized batch (250 firms), no writes
    python scripts/refresh_registration_dates.py

    # nightly rolling refresh (actually UPDATE)
    python scripts/refresh_registration_dates.py --apply

    # one-time backfill: drain a big batch
    python scripts/refresh_registration_dates.py --apply --limit 3000

    # smoke-test against staging
    python scripts/refresh_registration_dates.py --db-url <URL> --limit 5

Dependencies (already in project requirements): httpx, pdfplumber,
pypdf, sqlalchemy[asyncio], psycopg (v3).
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

import httpx
import pdfplumber
import pypdf
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


if sys.platform == "win32" and sys.version_info < (3, 14):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
sys.stderr.reconfigure(line_buffering=True)  # type: ignore[attr-defined]

logger = logging.getLogger("refresh_registration_dates")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# pdfplumber's underlying pdfminer emits "Data-loss while decompressing
# corrupted data" warnings on the slightly-mangled Flate streams FINRA's
# Cloudflare gateway returns. Extraction still succeeds; silence the noise.
logging.getLogger("pdfminer").setLevel(logging.ERROR)


# ---------------------------------------------------------------------------
# Constants -- mirror the in-tree parser semantics
# (copied verbatim from scripts/standalone_extract_new_bds.py)
# ---------------------------------------------------------------------------

FINRA_SEARCH_URL = (
    "https://api.brokercheck.finra.org/search/firm/{crd}"
    "?hl=true&nrows=12&start=0&r=25&sort=score+desc&wt=json"
)
FINRA_PDF_URL = "https://files.brokercheck.finra.org/firm/firm_{crd}.pdf"
PAGE_HARD_CAP = 80  # matches the in-tree parser at brokercheck_pdf.py:122
HTTP_TIMEOUT = 30.0

# Inter-request sleep so a big batch doesn't 429 FINRA from the shared
# Cloud Run egress IP (FINRA tolerates ~2/s). Matches the extractor's pace.
REQUEST_SLEEP_SECONDS = 0.5

# Verbatim from backend/app/services/brokercheck_pdf.py:549-552 / 567-570
SEC_REG_DATE_RE = re.compile(
    r"(?im)^\s*SEC\s+(?:Approved|Active|Registered|Effective)\s+"
    r"(\d{1,2}/\d{1,2}/\d{4})"
)
FORMATION_DATE_RE = re.compile(
    r"(?im)This\s*firm\s*was\s*formed\s*in\s*[\w\s,&.\-]*?\s*on\s*"
    r"(\d{1,2}/\d{1,2}/\d{4})"
)

HTTP_HEADERS_JSON = {
    "User-Agent": os.environ.get(
        "SEC_USER_AGENT",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    ),
    "Accept": "application/json",
}
HTTP_HEADERS_PDF = {
    "User-Agent": HTTP_HEADERS_JSON["User-Agent"],
    "Accept": "application/pdf",
    "Accept-Encoding": "identity",
}


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RefreshTarget:
    """An existing BD row we may refresh."""

    id: int
    crd_number: str


@dataclass(frozen=True)
class RefreshResult:
    """Outcome of probing one firm's Form BD."""

    registration_date: Optional[date]
    formation_date: Optional[date]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_db_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def _parse_us_date(value: str) -> Optional[date]:
    try:
        return datetime.strptime(value, "%m/%d/%Y").date()
    except ValueError:
        return None


def _slice_first_pages(pdf_bytes: bytes, max_pages: int) -> bytes:
    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    if len(reader.pages) <= max_pages:
        return pdf_bytes
    writer = pypdf.PdfWriter()
    for i in range(max_pages):
        writer.add_page(reader.pages[i])
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def _extract_dates_from_pdf(pdf_bytes: bytes) -> tuple[Optional[date], Optional[date]]:
    """Extract (registration_date, formation_date) from Form BD PDF bytes."""
    sliced = _slice_first_pages(pdf_bytes, PAGE_HARD_CAP)
    pages: list[str] = []
    with pdfplumber.open(io.BytesIO(sliced)) as pdf:
        for page in pdf.pages:
            pages.append(page.extract_text() or "")
    full_text = "\n".join(pages)
    reg_match = SEC_REG_DATE_RE.search(full_text)
    formation_match = FORMATION_DATE_RE.search(full_text)
    reg = _parse_us_date(reg_match.group(1)) if reg_match else None
    formation = _parse_us_date(formation_match.group(1)) if formation_match else None
    return reg, formation


# ---------------------------------------------------------------------------
# FINRA fetchers (copied verbatim from standalone_extract_new_bds.py)
# ---------------------------------------------------------------------------

async def _fetch_firm_json(client: httpx.AsyncClient, crd: int) -> Optional[dict]:
    """Return parsed ``basicInformation`` + ``firmAddressDetails`` for a CRD,
    or None when FINRA has no firm at that CRD (or returns malformed data).
    """
    url = FINRA_SEARCH_URL.format(crd=crd)
    try:
        resp = await client.get(url, headers=HTTP_HEADERS_JSON)
    except httpx.HTTPError as exc:
        logger.warning("CRD %s: JSON fetch network error: %s", crd, exc)
        return None
    if resp.status_code != 200:
        logger.warning("CRD %s: JSON fetch HTTP %s", crd, resp.status_code)
        return None
    try:
        envelope = resp.json()
        hits = envelope.get("hits", {}).get("hits", [])
        if not hits:
            return None
        # Outer hit wraps a JSON-string ``content`` field; parse it back.
        content_raw = hits[0].get("_source", {}).get("content")
        if not content_raw:
            return None
        return json.loads(content_raw)
    except (json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
        logger.warning("CRD %s: JSON parse error: %s", crd, exc)
        return None


async def _fetch_pdf(client: httpx.AsyncClient, crd: int) -> Optional[bytes]:
    url = FINRA_PDF_URL.format(crd=crd)
    try:
        async with client.stream("GET", url, headers=HTTP_HEADERS_PDF) as response:
            if response.status_code == 404:
                return None
            if response.status_code != 200:
                logger.warning("CRD %s: PDF HTTP %s", crd, response.status_code)
                return None
            content_type = response.headers.get("content-type", "").lower()
            chunks: list[bytes] = []
            async for chunk in response.aiter_raw():
                if chunk:
                    chunks.append(chunk)
            pdf_bytes = b"".join(chunks)
    except httpx.HTTPError as exc:
        logger.warning("CRD %s: PDF network error: %s", crd, exc)
        return None
    if "pdf" not in content_type and not pdf_bytes.startswith(b"%PDF"):
        logger.warning("CRD %s: PDF content-type %r unexpected", crd, content_type)
        return None
    return pdf_bytes


# ---------------------------------------------------------------------------
# Per-firm probe
# ---------------------------------------------------------------------------

async def _probe_dates(client: httpx.AsyncClient, crd: int) -> Optional[RefreshResult]:
    """Re-fetch a firm's Form BD and parse its dates.

    Returns a RefreshResult (either date may be None when the PDF didn't
    carry it) on a successful fetch, or None when the firm doesn't resolve
    at FINRA / the PDF is unavailable. A None return is treated as a parse
    miss by the caller -- we still stamp ``registration_checked_at`` so the
    cursor advances, but we touch no date column.
    """
    firm = await _fetch_firm_json(client, crd)
    if firm is None:
        return None

    pdf_bytes = await _fetch_pdf(client, crd)
    if pdf_bytes is None:
        return None

    try:
        reg_date, formation_date = await asyncio.to_thread(
            _extract_dates_from_pdf, pdf_bytes
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("CRD %s: PDF parse error: %s", crd, exc)
        return None

    return RefreshResult(registration_date=reg_date, formation_date=formation_date)


# ---------------------------------------------------------------------------
# Selection (rolling cursor)
# ---------------------------------------------------------------------------

# Prioritize refresh need:
#   1. firms with no registration_date at all (NULL),
#   2. then never-refreshed firms (registration_checked_at NULLS FIRST),
#   3. then the least-recently-refreshed firms.
# Only firms with a numeric crd_number -- the per-CRD FINRA probe needs an
# integer CRD, and a non-numeric legacy value would blow up the int cast.
# ``id`` breaks ties so the cursor is deterministic across runs.
SELECT_TARGETS_SQL = text(
    "SELECT id, crd_number "
    "FROM broker_dealers "
    "WHERE crd_number ~ '^[0-9]+$' "
    "ORDER BY "
    "  (registration_date IS NOT NULL), "
    "  registration_checked_at NULLS FIRST, "
    "  registration_checked_at ASC, "
    "  id ASC "
    "LIMIT :limit"
)

# UPDATE-only, COALESCE-guarded: only overwrite a column when we parsed a
# value (never null-out an existing date), and ALWAYS stamp
# registration_checked_at so the cursor advances even on a parse miss.
UPDATE_DATES_SQL = text(
    "UPDATE broker_dealers SET "
    "  registration_date = COALESCE(:new_reg, registration_date), "
    "  formation_date = COALESCE(:new_formation, formation_date), "
    "  registration_checked_at = now() "
    "WHERE id = :id"
)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Standalone FINRA registration-date refresh for existing "
            "broker-dealers (UPDATE-only, FINRA-free-API only)."
        ),
    )
    parser.add_argument("--db-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually UPDATE rows. Without this, the script runs dry.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=250,
        help=(
            "Max firms to refresh in one run (default 250 = nightly rolling "
            "batch). A large value -- or repeated runs -- drains every "
            "NULL-date firm = the one-time backfill."
        ),
    )
    args = parser.parse_args(argv)

    if not args.db_url:
        logger.error("no DATABASE_URL env var and no --db-url; aborting")
        return 2
    if args.limit <= 0:
        logger.error("--limit must be positive; got %d", args.limit)
        return 2

    db_url = _normalize_db_url(args.db_url)
    engine = create_async_engine(db_url, pool_pre_ping=True)

    try:
        async with engine.connect() as conn:
            rows = (
                await conn.execute(SELECT_TARGETS_SQL, {"limit": args.limit})
            ).all()
        targets = [RefreshTarget(id=row[0], crd_number=str(row[1])) for row in rows]
        logger.info(
            "selected %d firm(s) to refresh (limit=%d, apply=%s)",
            len(targets), args.limit, args.apply,
        )
        if not targets:
            logger.info("no firms to refresh; nothing to do")
            return 0

        scanned = 0
        dates_updated = 0  # firms where we parsed at least one date
        checked_stamped = 0  # firms whose registration_checked_at advanced
        parse_misses = 0  # firms that resolved no date this pass

        async with httpx.AsyncClient(
            timeout=HTTP_TIMEOUT, follow_redirects=True
        ) as client:
            for target in targets:
                scanned += 1
                try:
                    crd_int = int(target.crd_number)
                except ValueError:
                    # The SQL filter already excludes non-numeric CRDs, but
                    # guard anyway so one odd row can't abort the batch.
                    logger.warning(
                        "id=%d: non-numeric crd_number %r; skipping",
                        target.id, target.crd_number,
                    )
                    continue

                result = await _probe_dates(client, crd_int)
                has_date = result is not None and (
                    result.registration_date is not None
                    or result.formation_date is not None
                )
                if has_date:
                    dates_updated += 1
                else:
                    parse_misses += 1

                new_reg = result.registration_date if result else None
                new_formation = result.formation_date if result else None

                if args.apply:
                    # Always UPDATE -- even on a parse miss -- so
                    # registration_checked_at advances and the cursor moves
                    # off this firm. COALESCE keeps existing dates intact when
                    # new_* is None.
                    async with engine.begin() as conn:
                        await conn.execute(
                            UPDATE_DATES_SQL,
                            {
                                "id": target.id,
                                "new_reg": new_reg,
                                "new_formation": new_formation,
                            },
                        )
                    checked_stamped += 1
                    logger.info(
                        "id=%d crd=%s UPDATED reg=%s formation=%s",
                        target.id,
                        target.crd_number,
                        new_reg.isoformat() if new_reg else "-",
                        new_formation.isoformat() if new_formation else "-",
                    )
                else:
                    logger.info(
                        "id=%d crd=%s dry-run reg=%s formation=%s "
                        "(would stamp registration_checked_at)",
                        target.id,
                        target.crd_number,
                        new_reg.isoformat() if new_reg else "-",
                        new_formation.isoformat() if new_formation else "-",
                    )

                # Pace requests so a big backfill doesn't 429 the shared IP.
                await asyncio.sleep(REQUEST_SLEEP_SECONDS)

        logger.info(
            "summary: scanned=%d dates_updated=%d checked_stamped=%d "
            "parse_misses=%d%s",
            scanned,
            dates_updated,
            checked_stamped,
            parse_misses,
            "" if args.apply else " (dry-run -- no writes)",
        )
        return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
