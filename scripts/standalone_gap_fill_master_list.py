"""Standalone gap-fill: probe every CRD in the existing range, INSERT active
broker-dealers not yet in the master list.

Why this exists. Companion to ``scripts/standalone_extract_new_bds.py``
which probes forward above ``MAX(crd_number)``. This script walks the
entire CRD space from ``MIN(crd_number)`` upward, skipping CRDs already
in our DB (no FINRA call for those) and INSERTing any active BD it
finds in the gap. Use this to backfill firms that initial_load missed,
or to audit the master list for completeness.

Standalone -- no imports from ``app.*``, ``brokercheck_extractor/``, or
any other project module -- so the file can be deleted after the
catchup without breaking anything else.

Discovery strategy.

  1. Pre-load existing crd_number values from broker_dealers into a
     Python set. Saves the FINRA round-trip for the ~2,800 we already
     know about.
  2. For every CRD in [start, end] NOT in that set:
       a. GET /search/firm/{crd}?wt=json
       b. Skip if empty, ``firm_scope`` != "ACTIVE", or ``firm_bd_sec_number``
          is None (IA-only firms have a CRD but no BD scope).
       c. Buffer (crd, name, sec#, city, state).
  3. For each buffered candidate, fetch Form BD PDF and regex out
     registration_date + formation_date (same patterns as
     backend/app/services/brokercheck_pdf.py:549-570).
  4. Dry-run prints; ``--apply`` INSERTs (one transaction per row so
     progress survives a mid-run interrupt).

Concurrency. The FINRA per-CRD probe is parallelized via an asyncio
Semaphore (``--concurrency``, default 20). Sequential per-firm would
take >40 hours over the full range; 20-way concurrent brings it to
~2-3 hours. Higher concurrency risks FINRA rate-limit responses --
keep it modest.

Realistic runtime estimate for the full range (~340k CRDs minus ~2,800
known = ~337k probes):

    concurrency=20  ->  ~2.5-3.5 hours
    concurrency=50  ->  ~1-1.5 hours (risk of 429s)

Use ``--crd-end`` to bound the work, or ``--crd-start`` to resume a
partial run.

Logs / visibility. Every chunk (roughly every 5 seconds) emits a single
INFO line with cumulative progress, finds-in-chunk, total finds, chunk
wall-clock, and ETA. Per-find INSERT/FOUND lines fire as they happen.
``sys.stdout`` is line-buffered, so a long-running invocation can be
``tee``'d to a file and tailed live -- the file updates row-by-row,
not in big blocks.

Usage::

    # Dry-run a small range to verify behavior
    python scripts/standalone_gap_fill_master_list.py --crd-start 1500 --crd-end 1600

    # Full gap-fill, dry-run first
    python scripts/standalone_gap_fill_master_list.py

    # Apply
    python scripts/standalone_gap_fill_master_list.py --apply

    # Long run with live tail of progress in another terminal:
    python scripts/standalone_gap_fill_master_list.py --apply 2>&1 | tee gap_fill.log
    # then in another shell:  tail -f gap_fill.log

    # Resume from a midpoint after a previous interrupted run
    python scripts/standalone_gap_fill_master_list.py --crd-start 200000 --apply

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

logger = logging.getLogger("standalone_gap_fill_master_list")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logging.getLogger("pdfminer").setLevel(logging.ERROR)
logging.getLogger("httpx").setLevel(logging.WARNING)  # one-line-per-probe is too noisy at scale


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FINRA_SEARCH_URL = (
    "https://api.brokercheck.finra.org/search/firm/{crd}"
    "?hl=true&nrows=12&start=0&r=25&sort=score+desc&wt=json"
)
FINRA_PDF_URL = "https://files.brokercheck.finra.org/firm/firm_{crd}.pdf"
PAGE_HARD_CAP = 80  # matches backend/app/services/brokercheck_pdf.py:122
HTTP_TIMEOUT = 30.0

# Verbatim from backend/app/services/brokercheck_pdf.py:549-552 / 567-570
SEC_REG_DATE_RE = re.compile(
    r"(?im)^\s*SEC\s+(?:Approved|Active|Registered|Effective)\s+"
    r"(\d{1,2}/\d{1,2}/\d{4})"
)
FORMATION_DATE_RE = re.compile(
    r"(?im)This\s*firm\s*was\s*formed\s*in\s*[\w\s,&.\-]*?\s*on\s*"
    r"(\d{1,2}/\d{1,2}/\d{4})"
)

# Full browser-fingerprint headers. FINRA's Cloudflare gateway 403s
# requests with partial fingerprints; this set was captured from a
# real browser session and mirrors what
# backend/app/services/finra.py:30-53 uses. The PDF endpoint needs
# Accept: application/pdf instead of JSON; everything else is shared.
_COMMON_HEADERS = {
    "Accept-Encoding": "identity",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://brokercheck.finra.org",
    "Referer": "https://brokercheck.finra.org/",
    "Sec-Ch-Ua": '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/147.0.0.0 Safari/537.36"
    ),
}
HTTP_HEADERS_JSON = dict(_COMMON_HEADERS, **{"Accept": "application/json, text/plain, */*"})
HTTP_HEADERS_PDF = dict(_COMMON_HEADERS, **{"Accept": "application/pdf"})


# ---------------------------------------------------------------------------
# Data shape buffered per discovered firm
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GapBd:
    crd_number: str
    name: str
    sec_file_number: Optional[str]
    city: Optional[str]
    state: Optional[str]
    status: str  # "Active" (we filter to ACTIVE only here)
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


def _normalize_sec_file_number(raw: Optional[str | int]) -> Optional[str]:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if s.startswith("8-"):
        return s
    return f"8-{s}"


# ---------------------------------------------------------------------------
# FINRA fetchers
# ---------------------------------------------------------------------------

async def _get_with_retry(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    crd: int,
    max_retries: int = 4,
) -> Optional[httpx.Response]:
    """Single-shot GET with exponential-backoff retry on both 429 responses
    AND transient network errors (DNS failures, connection resets, timeouts).

    Why retry both. FINRA's Cloudflare gateway returns 429 once sustained
    request rates climb past ~5-10 RPS. Independently, the local network
    stack / DNS resolver can briefly fall over under heavy concurrent load
    (we've observed bursts of ``[Errno 11001] getaddrinfo failed`` mid-run).
    Without retry, either failure mode silently treats the CRD as "no firm
    here" -- corrupting the gap-fill result. Retry naturally throttles the
    script: each failure inserts a backoff (1s, 2s, 4s, 8s, max 30s) before
    retrying. After ``max_retries`` failures in a row, gives up and logs.

    Non-200 non-429 responses are NOT retried -- they're real errors
    (auth, 5xx) the caller should treat as misses.
    """
    delay = 1.0
    for attempt in range(max_retries + 1):
        try:
            resp = await client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            if attempt < max_retries:
                logger.info(
                    "CRD %s: network error (%s); backing off %.1fs (attempt %d/%d)",
                    crd, type(exc).__name__, delay, attempt + 1, max_retries,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)
                continue
            logger.error(
                "CRD %s: persistent network error after %d retries; giving up: %s",
                crd, max_retries, exc,
            )
            return None
        if resp.status_code != 429:
            return resp
        if attempt < max_retries:
            logger.info(
                "CRD %s: 429; backing off %.1fs (attempt %d/%d)",
                crd, delay, attempt + 1, max_retries,
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30.0)
            continue
        logger.error("CRD %s: still 429 after %d retries; giving up", crd, max_retries)
        return None
    return None


async def _fetch_firm_json(client: httpx.AsyncClient, crd: int) -> Optional[dict]:
    """Return the parsed firm content blob (basicInformation + addressDetails),
    or None when FINRA has no firm at that CRD or returns malformed data.
    """
    url = FINRA_SEARCH_URL.format(crd=crd)
    resp = await _get_with_retry(client, url, HTTP_HEADERS_JSON, crd)
    if resp is None:
        return None
    if resp.status_code != 200:
        logger.warning("CRD %s: JSON HTTP %s", crd, resp.status_code)
        return None
    try:
        envelope = resp.json()
        hits = envelope.get("hits", {}).get("hits", [])
        if not hits:
            return None
        content_raw = hits[0].get("_source", {}).get("content")
        if not content_raw:
            return None
        return json.loads(content_raw)
    except (json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
        logger.warning("CRD %s: JSON parse error: %s", crd, exc)
        return None


async def _fetch_pdf(client: httpx.AsyncClient, crd: int) -> Optional[bytes]:
    """Fetch the Form BD PDF for ``crd`` with backoff on both 429 and
    network errors. Returns None on 404 (no PDF on file) or persistent
    failure after retries are exhausted.
    """
    url = FINRA_PDF_URL.format(crd=crd)
    delay = 1.0
    max_retries = 4
    for attempt in range(max_retries + 1):
        try:
            async with client.stream("GET", url, headers=HTTP_HEADERS_PDF) as response:
                if response.status_code == 429:
                    if attempt < max_retries:
                        logger.info(
                            "CRD %s: PDF 429; backing off %.1fs (attempt %d/%d)",
                            crd, delay, attempt + 1, max_retries,
                        )
                        await asyncio.sleep(delay)
                        delay = min(delay * 2, 30.0)
                        continue
                    logger.error("CRD %s: PDF still 429 after %d retries; giving up", crd, max_retries)
                    return None
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
                if "pdf" not in content_type and not pdf_bytes.startswith(b"%PDF"):
                    logger.warning("CRD %s: PDF content-type %r unexpected", crd, content_type)
                    return None
                return pdf_bytes
        except httpx.HTTPError as exc:
            if attempt < max_retries:
                logger.info(
                    "CRD %s: PDF network error (%s); backing off %.1fs (attempt %d/%d)",
                    crd, type(exc).__name__, delay, attempt + 1, max_retries,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)
                continue
            logger.error(
                "CRD %s: PDF persistent network error after %d retries; giving up: %s",
                crd, max_retries, exc,
            )
            return None
    return None


# ---------------------------------------------------------------------------
# Per-CRD probe + (conditional) PDF parse
# ---------------------------------------------------------------------------

async def _probe_one(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    crd: int,
) -> Optional[GapBd]:
    """Return a GapBd when CRD ``crd`` is an active broker-dealer not yet
    in the DB; None on any miss (empty, IA-only, inactive, malformed)."""
    async with sem:
        firm = await _fetch_firm_json(client, crd)
        if firm is None:
            return None

        basic = firm.get("basicInformation", {}) or {}
        bc_scope = basic.get("bcScope")
        if (bc_scope or "").strip().upper() != "ACTIVE":
            return None
        name = basic.get("firmName")
        if not name:
            return None
        sec_file_number = _normalize_sec_file_number(basic.get("bdSECNumber"))
        if sec_file_number is None:
            # Firm has bcScope but no BD SEC# -- shouldn't happen for true
            # active BDs. Skip to avoid inserting a malformed row.
            return None

        address = (firm.get("firmAddressDetails", {}) or {}).get("officeAddress", {}) or {}

        pdf_bytes = await _fetch_pdf(client, crd)
        reg_date: Optional[date] = None
        formation_date: Optional[date] = None
        if pdf_bytes is not None:
            try:
                reg_date, formation_date = await asyncio.to_thread(_extract_dates_from_pdf, pdf_bytes)
            except Exception as exc:
                logger.warning("CRD %s: PDF parse error: %s", crd, exc)

        return GapBd(
            crd_number=str(crd),
            name=name,
            sec_file_number=sec_file_number,
            city=address.get("city"),
            state=address.get("state"),
            status="Active",
            registration_date=reg_date,
            formation_date=formation_date,
        )


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def _load_existing_crds(engine) -> set[int]:
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT crd_number FROM broker_dealers "
                    "WHERE crd_number ~ '^[0-9]+$'"
                )
            )
        ).all()
    return {int(r[0]) for r in rows}


async def _get_min_max(engine) -> tuple[int, int]:
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT MIN(CAST(crd_number AS INTEGER)), "
                    "       MAX(CAST(crd_number AS INTEGER)) "
                    "FROM broker_dealers "
                    "WHERE crd_number ~ '^[0-9]+$'"
                )
            )
        ).first()
    return int(row[0] or 0), int(row[1] or 0)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Standalone CRD-probe gap-fill for the broker-dealer master list.",
    )
    parser.add_argument("--db-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--apply", action="store_true", help="actually INSERT rows; default is dry-run")
    parser.add_argument("--crd-start", type=int, default=None, help="probe start CRD (default: MIN(crd_number))")
    parser.add_argument("--crd-end", type=int, default=None, help="probe end CRD inclusive (default: MAX(crd_number))")
    parser.add_argument(
        "--crds-from-file",
        type=str,
        default=None,
        help="Probe only the CRDs listed in this file (one CRD per line, "
        "leading/trailing whitespace ignored, blanks and non-numeric lines "
        "skipped). When set, --crd-start and --crd-end are ignored. DB "
        "dedup still applies -- known CRDs are filtered out before probing. "
        "Use this to re-probe a targeted recovery set (e.g. CRDs that "
        "gave up after retries in a prior run).",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=5,
        help="parallel FINRA fetches (default 5). Higher values risk 429 "
        "rate-limits from FINRA's Cloudflare gateway; the script retries "
        "with exponential backoff so transient 429s are absorbed, but "
        "sustained pressure above ~10 RPS will degrade throughput as "
        "retries multiply.",
    )
    args = parser.parse_args()

    if not args.db_url:
        logger.error("no DATABASE_URL env var and no --db-url; aborting")
        return 2

    db_url = _normalize_db_url(args.db_url)
    engine = create_async_engine(db_url, pool_pre_ping=True)

    try:
        existing = await _load_existing_crds(engine)
        min_db, max_db = await _get_min_max(engine)
        logger.info("loaded %d existing CRDs (DB range %d..%d)", len(existing), min_db, max_db)

        if args.crds_from_file:
            with open(args.crds_from_file, encoding="utf-8") as fh:
                raw_lines = fh.readlines()
            file_crds: list[int] = []
            for line in raw_lines:
                s = line.strip()
                if not s or not s.isdigit():
                    continue
                file_crds.append(int(s))
            file_crds = sorted(set(file_crds))
            to_probe = [crd for crd in file_crds if crd not in existing]
            total_probes = len(to_probe)
            logger.info(
                "loaded %d CRDs from %s; probing %d at concurrency=%d "
                "(%d already in DB are skipped)",
                len(file_crds), args.crds_from_file,
                total_probes, args.concurrency,
                len(file_crds) - total_probes,
            )
        else:
            start = args.crd_start if args.crd_start is not None else min_db
            end = args.crd_end if args.crd_end is not None else max_db
            if end < start:
                logger.error("end (%d) < start (%d); aborting", end, start)
                return 2

            to_probe = [crd for crd in range(start, end + 1) if crd not in existing]
            total_probes = len(to_probe)
            logger.info(
                "probing %d unknown CRDs in [%d..%d] at concurrency=%d "
                "(%d already in DB are skipped)",
                total_probes, start, end, args.concurrency,
                (end - start + 1) - total_probes,
            )

        # Prepare INSERT (only used in --apply path)
        insert_stmt = text(
            "INSERT INTO broker_dealers "
            "(crd_number, name, sec_file_number, city, state, status, "
            " matched_source, registration_date, formation_date) "
            "VALUES "
            "(:crd_number, :name, :sec_file_number, :city, :state, :status, "
            " 'finra_only', :registration_date, :formation_date) "
            "RETURNING id"
        )

        sem = asyncio.Semaphore(args.concurrency)
        found_count = 0
        processed = 0

        async def _process(crd: int, client: httpx.AsyncClient) -> Optional[GapBd]:
            return await _probe_one(client, sem, crd)

        # Process in chunks so we can stream output and INSERT incrementally
        # without holding 100k+ tasks in memory. Sized to ~5s of wall clock
        # at typical FINRA latency so the operator sees a chunk-summary
        # heartbeat every few seconds even when no firms are found in a
        # quiet window of empty CRDs.
        chunk_size = max(args.concurrency * 10, 50)
        total_chunks = (total_probes + chunk_size - 1) // chunk_size
        logger.info(
            "splitting into %d chunks of up to %d CRDs each; "
            "expect a chunk-summary line every few seconds.",
            total_chunks, chunk_size,
        )
        import time
        run_start = time.time()
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
            for chunk_idx, chunk_start in enumerate(range(0, total_probes, chunk_size), start=1):
                chunk = to_probe[chunk_start:chunk_start + chunk_size]
                chunk_t0 = time.time()
                results = await asyncio.gather(*(_process(crd, client) for crd in chunk))
                chunk_dt = time.time() - chunk_t0
                processed += len(chunk)
                chunk_finds = [r for r in results if r is not None]
                found_count += len(chunk_finds)

                # Per-chunk heartbeat. Fires even when no firms are found
                # in this batch, so the operator always knows the script
                # is alive. ETA assumes remaining chunks take the same
                # avg wall clock as the elapsed avg (smoothed across
                # variable FINRA latency).
                elapsed = time.time() - run_start
                avg_per_chunk = elapsed / chunk_idx
                eta_sec = avg_per_chunk * (total_chunks - chunk_idx)
                logger.info(
                    "chunk %d/%d: probed %d (cum=%d/%d, %.1f%%)  "
                    "found_in_chunk=%d  total_finds=%d  chunk_time=%.1fs  ETA=%dm%02ds",
                    chunk_idx, total_chunks,
                    len(chunk), processed, total_probes,
                    processed * 100 / total_probes if total_probes else 100,
                    len(chunk_finds), found_count,
                    chunk_dt,
                    int(eta_sec // 60), int(eta_sec % 60),
                )

                for bd in chunk_finds:
                    if args.apply:
                        # One transaction per row keeps progress durable
                        # against mid-run interrupts. The volume is low
                        # (~hundreds of finds across hours) so per-row
                        # commit overhead is negligible compared to the
                        # FINRA round-trip dominating each step.
                        async with engine.begin() as conn:
                            result = await conn.execute(
                                insert_stmt,
                                {
                                    "crd_number": bd.crd_number,
                                    "name": bd.name,
                                    "sec_file_number": bd.sec_file_number,
                                    "city": bd.city,
                                    "state": bd.state,
                                    "status": bd.status,
                                    "registration_date": bd.registration_date,
                                    "formation_date": bd.formation_date,
                                },
                            )
                            new_id = result.scalar_one()
                        logger.info(
                            "INSERTED id=%d crd=%s %r reg=%s formation=%s %s, %s",
                            new_id, bd.crd_number, bd.name,
                            bd.registration_date.isoformat() if bd.registration_date else "-",
                            bd.formation_date.isoformat() if bd.formation_date else "-",
                            bd.city or "-", bd.state or "-",
                        )
                    else:
                        logger.info(
                            "FOUND crd=%s sec=%s %r status=%s reg=%s formation=%s city=%r state=%r",
                            bd.crd_number,
                            bd.sec_file_number or "-",
                            bd.name,
                            bd.status,
                            bd.registration_date.isoformat() if bd.registration_date else None,
                            bd.formation_date.isoformat() if bd.formation_date else None,
                            bd.city,
                            bd.state,
                        )

        action = "INSERTED" if args.apply else "would INSERT"
        logger.info(
            "done. probed=%d  %s=%d  %s",
            processed,
            action, found_count,
            "(dry-run; re-run with --apply to write)" if not args.apply else "",
        )
        return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
