"""Standalone backfill: fill NULL ``broker_dealers.registration_date`` from the
FINRA Form BD Detailed Report PDF.

Why this exists. A subset of broker-dealer rows ended up with NULL
``registration_date`` (typically the ``matched_source = "finra_only"`` path in
``backend/app/services/data_merge.py``, which writes None when EDGAR has no
match). The schema's ``registration_date`` mirrors the
"SEC Approved/Active/Registered/Effective <date>" line of the firm's Form BD
Detailed Report PDF (the same field
``backend/app/services/brokercheck_pdf.py:_parse_registration_date`` reads on
the per-firm refresh path). This one-off script reproduces that exact
extraction in isolation -- no imports from ``app.*``,
``brokercheck_extractor/``, or any other project module -- so it can be
deleted after the backfill without breaking anything else.

What it does NOT do. No LLM enrichment, no Form BD field updates beyond
``registration_date``, no PipelineRun rows, no audit-log entries. Pure
read-FINRA-PDF + write-one-column.

Usage::

    # dry-run (default): print proposed updates without writing
    python scripts/standalone_backfill_null_registration_dates.py

    # actually write
    python scripts/standalone_backfill_null_registration_dates.py --apply

    # smoke test one firm (e.g. Goldman Sachs CRD 361)
    python scripts/standalone_backfill_null_registration_dates.py --crd 361

    # override the DB URL (otherwise read from DATABASE_URL env)
    python scripts/standalone_backfill_null_registration_dates.py --db-url <URL>

Dependencies (already in project requirements): httpx, pdfplumber, pypdf,
sqlalchemy[asyncio], psycopg (v3).
"""

from __future__ import annotations

import argparse
import asyncio
import io
import logging
import os
import re
import sys
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

logger = logging.getLogger("standalone_backfill_null_registration_dates")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# pdfminer emits a "Data-loss while decompressing corrupted data" WARNING on
# every Flate stream FINRA's Cloudflare gateway slightly mangles. The text
# extraction still succeeds (the warnings are advisory), so silence them to
# keep the run output readable.
logging.getLogger("pdfminer").setLevel(logging.ERROR)


FINRA_PDF_URL = "https://files.brokercheck.finra.org/firm/firm_{crd}.pdf"
# The in-tree parser's ``_PAGE_HARD_CAP = 30`` (see
# ``backend/app/services/brokercheck_pdf.py:122``) was tuned for Form-BD
# fields that live on the cover-page Firm Profile section. The SEC
# registration date, however, sits in the Registrations section, which
# for large firms (Davenport, R.J. O'Brien, etc.) gets pushed past page 30
# by long Direct Owners / Firm History / Disclosure Events blocks.
# Davenport's "SEC Approved 05/26/1972" line is on page 36; raising the cap
# to 80 covers everything we've observed.
PAGE_HARD_CAP = 80
HTTP_TIMEOUT = 30.0

# Verbatim from backend/app/services/brokercheck_pdf.py:549-552 so the parsed
# date carries the same semantic as every other row populated by the in-tree
# parser ("SEC Approved/Active/Registered/Effective <date>" on the Form BD
# Detailed Report cover page).
SEC_REG_DATE_RE = re.compile(
    r"(?im)^\s*SEC\s+(?:Approved|Active|Registered|Effective)\s+"
    r"(\d{1,2}/\d{1,2}/\d{4})"
)

# FINRA's Cloudflare gateway 403s requests with no UA or a python-shaped one.
# Accept-Encoding: identity + reading via aiter_raw is the same trick
# backend/app/services/finra_pdf_service.py uses to dodge the gateway's
# malformed-gzip-on-already-application-compressed-PDF behaviour.
HTTP_HEADERS = {
    "User-Agent": os.environ.get(
        "SEC_USER_AGENT",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    ),
    "Accept": "application/pdf",
    "Accept-Encoding": "identity",
}


def _normalize_db_url(url: str) -> str:
    """Accept ``postgresql://`` for convenience; psycopg (v3) supports asyncio
    via the ``postgresql+psycopg://`` driver string used by ``create_async_engine``.
    """
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


def _extract_registration_date(pdf_bytes: bytes) -> Optional[date]:
    sliced = _slice_first_pages(pdf_bytes, PAGE_HARD_CAP)
    pages: list[str] = []
    with pdfplumber.open(io.BytesIO(sliced)) as pdf:
        for page in pdf.pages:
            pages.append(page.extract_text() or "")
    full_text = "\n".join(pages)
    match = SEC_REG_DATE_RE.search(full_text)
    if match is None:
        return None
    return _parse_us_date(match.group(1))


async def _fetch_pdf(client: httpx.AsyncClient, crd: str) -> Optional[bytes]:
    url = FINRA_PDF_URL.format(crd=crd)
    try:
        async with client.stream("GET", url, headers=HTTP_HEADERS) as response:
            if response.status_code == 404:
                return None
            if response.status_code != 200:
                logger.warning("CRD %s: unexpected HTTP %s", crd, response.status_code)
                return None
            content_type = response.headers.get("content-type", "").lower()
            chunks: list[bytes] = []
            async for chunk in response.aiter_raw():
                if chunk:
                    chunks.append(chunk)
            pdf_bytes = b"".join(chunks)
    except httpx.HTTPError as exc:
        logger.warning("CRD %s: network error: %s: %s", crd, type(exc).__name__, exc)
        return None

    if "pdf" not in content_type and not pdf_bytes.startswith(b"%PDF"):
        logger.warning("CRD %s: unexpected content-type %r", crd, content_type)
        return None
    return pdf_bytes


async def _process_one(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    bd_id: int,
    crd: str,
    name: str,
) -> tuple[int, Optional[date], str]:
    """Returns ``(id, parsed_date, status)``.

    status is one of: ``"ok"``, ``"no_pdf"``, ``"no_date"``, ``"error"``.
    """
    async with sem:
        pdf_bytes = await _fetch_pdf(client, crd)
        if pdf_bytes is None:
            logger.info("CRD %s (%s): no PDF / fetch failure", crd, name)
            return (bd_id, None, "no_pdf")
        try:
            parsed = await asyncio.to_thread(_extract_registration_date, pdf_bytes)
        except Exception as exc:
            logger.warning("CRD %s (%s): PDF parse error: %s", crd, name, exc)
            return (bd_id, None, "error")
        if parsed is None:
            logger.info("CRD %s (%s): no SEC registration date in PDF text", crd, name)
            return (bd_id, None, "no_date")
        logger.info(
            "CRD %s (%s): parsed registration_date=%s", crd, name, parsed.isoformat()
        )
        return (bd_id, parsed, "ok")


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Standalone backfill of broker_dealers.registration_date from FINRA Form BD PDFs.",
    )
    parser.add_argument(
        "--db-url",
        default=os.environ.get("DATABASE_URL"),
        help="SQLAlchemy DB URL. Defaults to DATABASE_URL env.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write to the DB. Without this, the script runs in dry-run mode.",
    )
    parser.add_argument(
        "--crd",
        type=str,
        default=None,
        help="Run for a single CRD (useful for smoke-testing one row).",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=4,
        help="Max concurrent PDF fetches (default 4).",
    )
    args = parser.parse_args()

    if not args.db_url:
        logger.error("no DATABASE_URL env var and no --db-url passed; aborting")
        return 2

    db_url = _normalize_db_url(args.db_url)
    engine = create_async_engine(db_url, pool_pre_ping=True)

    try:
        async with engine.connect() as conn:
            if args.crd is not None:
                rows = (
                    await conn.execute(
                        text(
                            "SELECT id, crd_number, name FROM broker_dealers "
                            "WHERE crd_number = :crd"
                        ),
                        {"crd": args.crd},
                    )
                ).all()
            else:
                rows = (
                    await conn.execute(
                        text(
                            "SELECT id, crd_number, name FROM broker_dealers "
                            "WHERE registration_date IS NULL "
                            "AND crd_number IS NOT NULL "
                            "ORDER BY name"
                        )
                    )
                ).all()

        if not rows:
            logger.info("no candidate rows; nothing to do")
            return 0
        logger.info("found %d candidate row(s)", len(rows))

        sem = asyncio.Semaphore(args.concurrency)
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
            results = await asyncio.gather(
                *(_process_one(client, sem, r.id, r.crd_number, r.name) for r in rows)
            )

        counts = {"ok": 0, "no_pdf": 0, "no_date": 0, "error": 0}
        for _, _, status in results:
            counts[status] += 1
        logger.info(
            "summary: ok=%d  no_pdf=%d  no_date=%d  error=%d",
            counts["ok"], counts["no_pdf"], counts["no_date"], counts["error"],
        )

        ok = [(bd_id, d) for (bd_id, d, status) in results if status == "ok" and d is not None]
        if not ok:
            logger.info("no successful parses; nothing to write")
            return 0

        if not args.apply:
            logger.info(
                "dry-run: would update %d row(s). Re-run with --apply to write.",
                len(ok),
            )
            for bd_id, d in ok:
                logger.info("  id=%d  registration_date=%s", bd_id, d.isoformat())
            return 0

        async with engine.begin() as conn:
            for bd_id, d in ok:
                await conn.execute(
                    text(
                        "UPDATE broker_dealers SET registration_date = :d WHERE id = :id"
                    ),
                    {"d": d, "id": bd_id},
                )
        logger.info("wrote %d row(s)", len(ok))
        return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
