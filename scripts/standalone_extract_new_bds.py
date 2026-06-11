"""Standalone extractor: discover and ingest NEW broker-dealers above the
current MAX(crd_number) in our master list.

Why this exists. The ``broker_dealers`` table is populated in bulk by
``scripts/initial_load.py``. Between initial_load runs, FINRA assigns
fresh CRDs to newly-registered firms but our DB doesn't pick them up.
This script catches up the gap without re-running the full initial_load:
it probes FINRA's per-CRD search endpoint starting just above
``MAX(crd_number)``, and INSERTs each new BD it finds.

Standalone -- the extraction itself imports nothing from ``app.*``,
``brokercheck_extractor/``, or any other project module -- so the file
can be removed after the catchup without breaking anything else. The one
exception is the post-apply Doxie freshness hook
(``_embed_backfill_after_apply``): after new BDs are committed it lazily
imports ``app.services.chatbot_semantic`` to embed them into the
``chatbot_firm_embedding`` semantic-search index. That import is
best-effort and failure-isolated -- when ``app.*`` or GEMINI_API_KEY is
unavailable it logs and moves on, never touching the extractor's exit
code or its committed rows.

Discovery strategy. FINRA assigns CRDs sequentially. Start at
``MAX(crd_number) + 1`` and probe upward. Stop when we see
``--max-misses`` consecutive empty/non-BD responses (default 50) or
hit ``--probe-limit`` (default 500). Sequential CRDs aren't always
populated -- FINRA leaves gaps where firms are IA-only, terminated,
or were never approved -- hence the gap tolerance.

Per-CRD flow:
  1. GET ``https://api.brokercheck.finra.org/search/firm/{crd}?wt=json``
  2. Parse JSON; if no hit OR ``bcScope`` is missing -> miss
     (firm doesn't exist or is not a broker-dealer; could be IA-only).
  3. Else extract: ``firmName``, ``bcScope``, ``bdSECNumber``,
     ``firmAddressDetails.officeAddress.city/state``.
  4. Fetch Form BD PDF from ``files.brokercheck.finra.org``.
  5. Regex out SEC ``registration_date`` and ``formation_date`` using
     the same patterns as the in-tree parser at
     ``backend/app/services/brokercheck_pdf.py:549-570`` so the parsed
     semantics match every other row already in those columns.
  6. Buffer a row; INSERT on ``--apply`` (dry-run by default).

Inserted row scope. Minimal viable -- the columns the Master List and
"New BDs / 30 days" KPI need to display the firm:
  crd_number, name, sec_file_number, city, state, status,
  matched_source ("finra_only"), registration_date, formation_date.
Everything else (branch_count, business_type, owners, officers, website,
clearing fields, lead_score, ...) stays at column defaults and gets
filled by the per-firm refresh-all path later.

Usage::

    # dry-run probe starting at MAX(crd_number)+1
    python scripts/standalone_extract_new_bds.py

    # actually insert
    python scripts/standalone_extract_new_bds.py --apply

    # smoke-test a single CRD
    python scripts/standalone_extract_new_bds.py --crd-start 339697 --probe-limit 1

    # override DB URL
    python scripts/standalone_extract_new_bds.py --db-url <URL>

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

logger = logging.getLogger("standalone_extract_new_bds")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# pdfplumber's underlying pdfminer emits "Data-loss while decompressing
# corrupted data" warnings on the slightly-mangled Flate streams FINRA's
# Cloudflare gateway returns. Extraction still succeeds; silence the noise.
logging.getLogger("pdfminer").setLevel(logging.ERROR)


# ---------------------------------------------------------------------------
# Constants -- mirror the in-tree parser semantics
# ---------------------------------------------------------------------------

FINRA_SEARCH_URL = (
    "https://api.brokercheck.finra.org/search/firm/{crd}"
    "?hl=true&nrows=12&start=0&r=25&sort=score+desc&wt=json"
)
FINRA_PDF_URL = "https://files.brokercheck.finra.org/firm/firm_{crd}.pdf"
PAGE_HARD_CAP = 80  # matches the in-tree parser at brokercheck_pdf.py:122
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
# Data shape buffered per discovered firm
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class NewBd:
    crd_number: str
    name: str
    sec_file_number: Optional[str]
    city: Optional[str]
    state: Optional[str]
    status: str  # "Active" or "Inactive"
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


def _normalize_sec_file_number(raw: Optional[str]) -> Optional[str]:
    """FINRA's JSON returns the SEC# without the ``8-`` prefix. Existing
    rows in our DB carry it with the prefix (e.g. ``8-17103``), so add it
    when missing so the new rows match the prevailing format.
    """
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None
    if raw.startswith("8-"):
        return raw
    return f"8-{raw}"


def _normalize_status(bc_scope: Optional[str]) -> Optional[str]:
    if not bc_scope:
        return None
    bc_scope = bc_scope.strip().upper()
    if bc_scope == "ACTIVE":
        return "Active"
    if bc_scope == "INACTIVE":
        return "Inactive"
    return None


# ---------------------------------------------------------------------------
# FINRA fetchers
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
# Per-CRD probe
# ---------------------------------------------------------------------------

async def _probe_one(client: httpx.AsyncClient, crd: int) -> Optional[NewBd]:
    """Probe a single CRD. Returns a NewBd on success, None on miss
    (firm doesn't exist, isn't a broker-dealer, or required fields missing).
    """
    firm = await _fetch_firm_json(client, crd)
    if firm is None:
        return None

    basic = firm.get("basicInformation", {}) or {}
    status = _normalize_status(basic.get("bcScope"))
    if status is None:
        # IA-only firm or missing bcScope -- not a broker-dealer for our purposes
        return None
    name = basic.get("firmName")
    if not name:
        return None

    address = (firm.get("firmAddressDetails", {}) or {}).get("officeAddress", {}) or {}
    sec_file_number = _normalize_sec_file_number(basic.get("bdSECNumber"))

    pdf_bytes = await _fetch_pdf(client, crd)
    reg_date: Optional[date] = None
    formation_date: Optional[date] = None
    if pdf_bytes is not None:
        try:
            reg_date, formation_date = await asyncio.to_thread(_extract_dates_from_pdf, pdf_bytes)
        except Exception as exc:
            logger.warning("CRD %s: PDF parse error: %s", crd, exc)

    return NewBd(
        crd_number=str(crd),
        name=name,
        sec_file_number=sec_file_number,
        city=address.get("city"),
        state=address.get("state"),
        status=status,
        registration_date=reg_date,
        formation_date=formation_date,
    )


# ---------------------------------------------------------------------------
# Doxie semantic-index freshness hook (best-effort, post-apply only)
# ---------------------------------------------------------------------------

async def _embed_backfill_after_apply() -> None:
    """Embed newly-committed BDs into Doxie's semantic-search index.

    The nightly Cloud Run Job is this script's main caller, and the BDs
    it inserts would otherwise sit unembedded until someone runs the
    populate-all pipeline (which almost never happens on staging). The
    backfill is incremental -- content-hash skip means already-embedded
    firms cost a hash lookup, not a Gemini call -- so chaining it here
    keeps ``chatbot_firm_embedding`` fresh for cheap.

    Failure-isolated by contract: lazy ``app.*`` imports (preserving the
    standalone property of every path that doesn't reach a successful
    ``--apply`` write), and every exception is caught and logged. This
    function must never change the extractor's exit code; the inserts it
    runs after are already committed, so there is nothing to roll back.
    In the backend image PYTHONPATH=/app makes ``app.*`` importable and
    the app reads the same DATABASE_URL env var this script defaults to;
    GEMINI_API_KEY missing (it isn't mounted on the extract job yet)
    downgrades to a logged skip.
    """
    try:
        # Local checkouts run this script without PYTHONPATH set up; the
        # backend package lives at <repo>/backend (same bootstrap as
        # scripts/gap_fill_investment_advisors.py). In the image the dir
        # doesn't exist and PYTHONPATH=/app already covers ``app.*``.
        from pathlib import Path

        backend_root = Path(__file__).resolve().parents[1] / "backend"
        if backend_root.is_dir() and str(backend_root) not in sys.path:
            sys.path.insert(0, str(backend_root))

        from app.core.config import settings
        from app.db.session import SessionLocal
        from app.services.chatbot_semantic import ChatbotSemanticService
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "doxie embed backfill after extract skipped: app modules "
            "unavailable (%s)", exc,
        )
        return
    try:
        if not settings.gemini_api_key:
            logger.info(
                "doxie embed backfill after extract skipped: GEMINI_API_KEY "
                "not configured"
            )
            return
        service = ChatbotSemanticService()
        async with SessionLocal() as db:
            bd = await service.backfill_broker_dealers(db)
            ia = await service.backfill_investment_advisors(db)
        logger.info(
            "doxie embed backfill after extract: bd_embedded=%d bd_skipped=%d "
            "bd_failed=%d ia_embedded=%d ia_skipped=%d ia_failed=%d",
            bd.embedded, bd.skipped, bd.failed,
            ia.embedded, ia.skipped, ia.failed,
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "doxie embed backfill after extract failed; extractor result "
            "unaffected"
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Standalone CRD-probe extractor for net-new broker-dealers.",
    )
    parser.add_argument("--db-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually INSERT rows. Without this, the script runs dry.",
    )
    parser.add_argument(
        "--crd-start",
        type=int,
        default=None,
        help="Override the probe start (default: MAX(crd_number) + 1).",
    )
    parser.add_argument(
        "--max-misses",
        type=int,
        default=50,
        help="Stop after N consecutive non-BD/empty CRDs (default 50).",
    )
    parser.add_argument(
        "--probe-limit",
        type=int,
        default=500,
        help="Hard cap on total CRDs probed in one run (default 500).",
    )
    args = parser.parse_args(argv)

    if not args.db_url:
        logger.error("no DATABASE_URL env var and no --db-url; aborting")
        return 2

    db_url = _normalize_db_url(args.db_url)
    engine = create_async_engine(db_url, pool_pre_ping=True)

    try:
        async with engine.connect() as conn:
            if args.crd_start is not None:
                start_crd = args.crd_start
                logger.info("probe start overridden via --crd-start=%d", start_crd)
            else:
                # crd_number is String(32) in the model; cast to int where it
                # looks numeric. ``~ '^[0-9]+$'`` filters out any non-numeric
                # legacy rows that would otherwise blow up the cast.
                row = (
                    await conn.execute(
                        text(
                            "SELECT MAX(CAST(crd_number AS INTEGER)) "
                            "FROM broker_dealers "
                            "WHERE crd_number ~ '^[0-9]+$'"
                        )
                    )
                ).first()
                current_max = row[0] if row and row[0] is not None else 0
                start_crd = current_max + 1
                logger.info("current MAX(crd_number) = %d; probing from %d", current_max, start_crd)

        end_crd = start_crd + args.probe_limit - 1
        logger.info(
            "probing CRDs %d..%d (max-misses=%d)",
            start_crd, end_crd, args.max_misses,
        )

        found: list[NewBd] = []
        consecutive_misses = 0
        probed = 0

        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
            for crd in range(start_crd, end_crd + 1):
                probed += 1
                bd = await _probe_one(client, crd)
                if bd is None:
                    consecutive_misses += 1
                    if consecutive_misses >= args.max_misses:
                        logger.info(
                            "hit %d consecutive misses at CRD %d; stopping",
                            args.max_misses, crd,
                        )
                        break
                    continue
                consecutive_misses = 0
                found.append(bd)
                logger.info(
                    "CRD %d HIT: %s | status=%s | reg=%s | formation=%s | %s, %s",
                    crd,
                    bd.name,
                    bd.status,
                    bd.registration_date.isoformat() if bd.registration_date else "-",
                    bd.formation_date.isoformat() if bd.formation_date else "-",
                    bd.city or "-",
                    bd.state or "-",
                )

        logger.info(
            "summary: probed=%d found=%d consecutive_misses_at_stop=%d",
            probed, len(found), consecutive_misses,
        )

        if not found:
            logger.info("no new BDs; nothing to write")
            return 0

        # Defensive dedup. The default discovery path starts at
        # MAX(crd_number)+1 so collisions are impossible, but --crd-start
        # can bypass that guarantee. crd_number isn't UNIQUE in the
        # schema, so we can't lean on the DB to reject a duplicate.
        async with engine.connect() as conn:
            existing_rows = (
                await conn.execute(
                    text(
                        "SELECT crd_number FROM broker_dealers "
                        "WHERE crd_number = ANY(:crds)"
                    ),
                    {"crds": [bd.crd_number for bd in found]},
                )
            ).all()
        existing_crds = {row[0] for row in existing_rows}
        skipped = [bd for bd in found if bd.crd_number in existing_crds]
        to_insert = [bd for bd in found if bd.crd_number not in existing_crds]
        if skipped:
            logger.info(
                "skipping %d CRD(s) already present in broker_dealers: %s",
                len(skipped),
                ", ".join(bd.crd_number for bd in skipped),
            )
        if not to_insert:
            logger.info("all found CRDs already exist; nothing to write")
            return 0

        if not args.apply:
            logger.info(
                "dry-run: would INSERT %d row(s) (skipping %d duplicate(s)). "
                "Re-run with --apply to write.",
                len(to_insert), len(skipped),
            )
            for bd in to_insert:
                logger.info(
                    "  crd=%s sec=%s name=%r status=%s reg=%s formation=%s city=%r state=%r",
                    bd.crd_number,
                    bd.sec_file_number or "-",
                    bd.name,
                    bd.status,
                    bd.registration_date.isoformat() if bd.registration_date else None,
                    bd.formation_date.isoformat() if bd.formation_date else None,
                    bd.city,
                    bd.state,
                )
            return 0

        # ``matched_source`` is NOT NULL in the schema. ``finra_only`` is the
        # value the in-tree merge writes for FINRA-discovered rows that
        # weren't matched to an EDGAR record -- the same provenance these
        # CRD-probe finds have, since we don't query EDGAR here.
        # Bool columns (is_deficient, current_clearing_is_competitor,
        # is_niche_restricted) and ``status`` defaults are intentionally
        # omitted from the column list so Postgres uses each column's DEFAULT.
        insert_stmt = text(
            "INSERT INTO broker_dealers "
            "(crd_number, name, sec_file_number, city, state, status, "
            " matched_source, registration_date, formation_date) "
            "VALUES "
            "(:crd_number, :name, :sec_file_number, :city, :state, :status, "
            " 'finra_only', :registration_date, :formation_date) "
            "RETURNING id"
        )
        async with engine.begin() as conn:
            written = 0
            for bd in to_insert:
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
                written += 1
                logger.info("inserted id=%d crd=%s %s", new_id, bd.crd_number, bd.name)
        logger.info("wrote %d row(s) (skipped %d duplicate(s))", written, len(skipped))
        # New BDs are committed (engine.begin() above) -- chain the Doxie
        # embed pass so they become semantically searchable tonight, not
        # whenever populate-all next runs. Best-effort: the hook swallows
        # its own errors and never affects this script's exit code.
        await _embed_backfill_after_apply()
        return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
