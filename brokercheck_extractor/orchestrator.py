"""
Batch orchestrator for the BrokerCheck + X-17A-5 pipeline.

Per firm workflow:
  1. Read the next CRD from the input table (your existing 3K-firm Neon table).
  2. Fetch the FINRA Detailed Report PDF directly from the deterministic URL
     (no search call — we already have the CRD).
  3. Parse the FINRA PDF → FirmProfile.
  4. Resolve the firm's CIK on EDGAR (by name) and pull the latest two
     X-17A-5 filings → FocusReport x2.
  5. Run derivations (clearing classifier, YoY growth).
  6. UPSERT everything into Neon. Failures go to the parse_errors DLQ.

Concurrency: asyncio.Semaphore(N) limits simultaneous in-flight firms.
Default N=5 which keeps us polite to FINRA/SEC. Tune via MAX_CONCURRENCY env.

Delta detection: if the newly downloaded FINRA PDF hash matches the previously
stored hash, we skip parsing entirely and just refresh the focus reports.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx

from .acquisition.finra_client import FinraClient
from .acquisition.sec_edgar_client import SecEdgarClient, X17Filing
from .config import settings
from .derivation.clearing_classifier import apply_classification
from .derivation.yoy_calculator import compute_all_yoy
from .parsers.base import sha256_bytes
from .parsers.finra_parser import parse_finra_pdf
from .parsers.focus_parser import parse_focus_pdf
from .schema.models import FirmProfile, FirmRecord, FocusReport
from .storage import db as store

logger = logging.getLogger(__name__)


@dataclass
class RunStats:
    total: int = 0
    ok: int = 0
    partial: int = 0
    failed: int = 0
    skipped_unchanged: int = 0
    started_at: float = field(default_factory=time.monotonic)

    def elapsed(self) -> float:
        return time.monotonic() - self.started_at

    def summary(self) -> str:
        return (
            f"total={self.total} ok={self.ok} partial={self.partial} "
            f"failed={self.failed} skipped_unchanged={self.skipped_unchanged} "
            f"elapsed={self.elapsed():.1f}s"
        )


# ---------------------------------------------------------------------------
# Per-firm pipeline
# ---------------------------------------------------------------------------

async def process_firm(
    crd_number: str,
    firm_name: str,
    finra: FinraClient,
    sec: SecEdgarClient,
    sem: asyncio.Semaphore,
    save_raw_pdfs: bool = False,
) -> FirmRecord:
    """Process one firm end-to-end. Exceptions are caught and DLQ'd."""
    async with sem:
        record = FirmRecord(firm_id=crd_number, queried_name=firm_name)

        # ---- FINRA -------------------------------------------------------
        try:
            pdf_bytes = await finra.download_pdf(crd_number)
        except Exception as exc:  # noqa: BLE001
            await store.log_parse_error(crd_number, "finra", "acquire", exc)
            record.status = "failed"
            record.failure_reason = f"finra_acquire: {exc!r}"
            await store.upsert_firm_record(record)
            return record

        new_hash = sha256_bytes(pdf_bytes)
        existing_hash = await store.get_existing_pdf_hash(crd_number)
        delta_skip_finra = existing_hash == new_hash

        if save_raw_pdfs:
            _save_raw_pdf(crd_number, "finra", pdf_bytes)

        if not delta_skip_finra:
            try:
                profile = parse_finra_pdf(pdf_bytes, queried_name=firm_name)
                apply_classification(profile)
                record.finra = profile
                await store.upsert_firm_profile(profile)
            except Exception as exc:  # noqa: BLE001
                await store.log_parse_error(crd_number, "finra", "parse", exc)
                record.status = "partial"
                record.failure_reason = (record.failure_reason or "") + f" finra_parse: {exc!r};"
        else:
            logger.info("crd=%s FINRA PDF unchanged — skipping parse", crd_number)

        # ---- SEC X-17A-5 -------------------------------------------------
        try:
            cik = await sec.resolve_cik(firm_name)
            if cik:
                filings = await sec.list_x17_filings(cik, limit=2)
                current, prior = (filings + [None, None])[:2]

                if current:
                    current.pdf_bytes = await sec.download_filing(current)
                    if save_raw_pdfs:
                        _save_raw_pdf(crd_number, "focus_current", current.pdf_bytes)
                    current_report = parse_focus_pdf(current.pdf_bytes)
                    record.focus_current = current_report
                    await store.upsert_focus_report(crd_number, current_report)

                if prior:
                    prior.pdf_bytes = await sec.download_filing(prior)
                    if save_raw_pdfs:
                        _save_raw_pdf(crd_number, "focus_prior", prior.pdf_bytes)
                    prior_report = parse_focus_pdf(prior.pdf_bytes)
                    record.focus_prior = prior_report
                    await store.upsert_focus_report(crd_number, prior_report)
            else:
                logger.warning("crd=%s: no CIK match on EDGAR", crd_number)
        except Exception as exc:  # noqa: BLE001
            await store.log_parse_error(crd_number, "sec_edgar", "acquire_or_parse", exc)
            record.status = "partial"
            record.failure_reason = (record.failure_reason or "") + f" sec_edgar: {exc!r};"

        # ---- Derivation --------------------------------------------------
        yoy = compute_all_yoy(record.focus_current, record.focus_prior)
        record.net_capital_yoy = yoy["net_capital_yoy"]
        record.total_assets_yoy = yoy["total_assets_yoy"]

        # ---- Final status ------------------------------------------------
        if record.finra and record.focus_current:
            record.status = "ok"
        elif record.finra or record.focus_current:
            record.status = "partial"
        else:
            record.status = "failed"

        await store.upsert_firm_record(record)
        return record


def _save_raw_pdf(crd: str, kind: str, pdf_bytes: bytes) -> None:
    path = os.path.join(settings.raw_pdf_dir, f"crd_{crd}__{kind}.pdf")
    os.makedirs(settings.raw_pdf_dir, exist_ok=True)
    with open(path, "wb") as f:
        f.write(pdf_bytes)


# ---------------------------------------------------------------------------
# Batch driver
# ---------------------------------------------------------------------------

async def run_batch(
    where_status: Optional[str] = None,
    limit: Optional[int] = None,
    save_raw_pdfs: bool = False,
) -> RunStats:
    """Drive the pipeline over all firms (or a filtered subset)."""
    await store.init_schema()

    stats = RunStats()
    sem = asyncio.Semaphore(settings.max_concurrency)

    async with (
        httpx.AsyncClient(
            timeout=settings.per_request_timeout_s,
            headers={"User-Agent": settings.user_agent},
            follow_redirects=True,
        ) as finra_http,
        httpx.AsyncClient(
            timeout=settings.per_request_timeout_s,
            headers={"User-Agent": settings.user_agent},
            follow_redirects=True,
        ) as sec_http,
    ):
        finra = FinraClient(client=finra_http)
        sec = SecEdgarClient(client=sec_http)

        tasks: list[asyncio.Task] = []
        async for firm in store.iter_input_crds(where_status=where_status):
            if limit is not None and stats.total >= limit:
                break
            stats.total += 1
            tasks.append(
                asyncio.create_task(
                    process_firm(
                        crd_number=firm.crd_number,
                        firm_name=firm.firm_name,
                        finra=finra,
                        sec=sec,
                        sem=sem,
                        save_raw_pdfs=save_raw_pdfs,
                    )
                )
            )

        for coro in asyncio.as_completed(tasks):
            try:
                record = await coro
                if record.status == "ok":
                    stats.ok += 1
                elif record.status == "partial":
                    stats.partial += 1
                else:
                    stats.failed += 1
            except Exception as exc:  # noqa: BLE001
                logger.exception("Unhandled in process_firm: %s", exc)
                stats.failed += 1

            # Progress log every 25 firms
            if (stats.ok + stats.partial + stats.failed) % 25 == 0:
                logger.info("progress: %s", stats.summary())

    logger.info("Run complete: %s", stats.summary())
    return stats
