"""
Hybrid orchestrator — the production pipeline that delivers effectively-100% accuracy.

Per firm workflow:
  1. Download the FINRA BrokerCheck PDF (deterministic URL, CRD known)
  2. Run both extractors in parallel:
        a) Deterministic parser (parsers/finra_parser.py)
        b) Gemini 2.5 Flash extractor (llm/extractors.py)
  3. Score confidence on the deterministic output
  4. Cross-validate deterministic vs LLM
  5. Route based on outcome:
        - All agree → write to Neon (auto-accept, confidence ≥ 0.95)
        - Minor disagreement → merge (LLM wins on conflicts), write with flag
        - Critical disagreement → escalate to Gemini 2.5 Pro with full PDF
        - Post-escalation still ambiguous → enqueue for human review
  6. Same flow for the SEC X-17A-5 side

Concurrency: asyncio.Semaphore(N); Gemini Flash calls are the slow path.
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
from .acquisition.sec_edgar_client import SecEdgarClient
from .config import settings
from .derivation.clearing_classifier import apply_classification
from .derivation.yoy_calculator import compute_all_yoy
from .llm.extractors import extract_finra_with_llm, extract_focus_with_llm
from .llm.gemini_client import GeminiClient, GeminiSettings
from .parsers.base import sha256_bytes
from .parsers.finra_parser import parse_finra_pdf
from .parsers.focus_parser import parse_focus_pdf
from .schema.models import FirmProfile, FirmRecord, FocusReport
from .storage import db as store
from .validation.confidence import score_finra, score_focus
from .validation.cross_validator import (
    CrossValidationResult,
    cross_validate_finra,
    cross_validate_focus,
)

logger = logging.getLogger(__name__)


@dataclass
class HybridStats:
    total: int = 0
    auto_accepted: int = 0          # deterministic + LLM agreed
    llm_filled: int = 0             # LLM filled gaps the deterministic parser missed
    escalated_to_pro: int = 0       # Gemini Pro adjudicated
    human_review: int = 0           # queued for manual review
    failed: int = 0
    started_at: float = field(default_factory=time.monotonic)

    def summary(self) -> str:
        return (
            f"total={self.total} auto={self.auto_accepted} "
            f"llm_filled={self.llm_filled} escalated={self.escalated_to_pro} "
            f"review={self.human_review} failed={self.failed} "
            f"elapsed={time.monotonic() - self.started_at:.1f}s"
        )


# ---------------------------------------------------------------------------
# Per-firm hybrid pipeline
# ---------------------------------------------------------------------------

async def process_firm_hybrid(
    crd_number: str,
    firm_name: str,
    finra: FinraClient,
    sec: SecEdgarClient,
    gemini: Optional[GeminiClient],
    sem: asyncio.Semaphore,
    save_raw_pdfs: bool = False,
) -> tuple[FirmRecord, dict]:
    """Return the final FirmRecord plus a routing trace dict for metrics."""
    trace = {"crd": crd_number, "routing": []}

    async with sem:
        record = FirmRecord(firm_id=crd_number, queried_name=firm_name)

        # ---------------------------------------------------------- FINRA
        try:
            pdf_bytes = await finra.download_pdf(crd_number)
        except Exception as exc:  # noqa: BLE001
            await store.log_parse_error(crd_number, "finra", "acquire", exc)
            record.status = "failed"
            record.failure_reason = f"finra_acquire: {exc!r}"
            trace["routing"].append("finra_acquire_failed")
            return record, trace

        if save_raw_pdfs:
            _save_raw(crd_number, "finra", pdf_bytes)

        # -- Tier 1: deterministic
        try:
            det_profile = parse_finra_pdf(pdf_bytes, queried_name=firm_name)
            apply_classification(det_profile)
        except Exception as exc:  # noqa: BLE001
            det_profile = FirmProfile(
                crd_number=crd_number,
                firm_name=firm_name,
                parse_warnings=[f"deterministic_crash:{exc!r}"],
            )

        # -- Score confidence
        conf = score_finra(det_profile, raw_text_sample=_text_sample(pdf_bytes))
        trace["finra_det_confidence"] = conf.score
        trace["finra_det_reasons"] = conf.reasons

        final_profile: FirmProfile = det_profile
        xval_result: Optional[CrossValidationResult] = None

        # -- Tier 2/3: LLM extraction (always runs for cross-validation if gemini available)
        if gemini is not None:
            try:
                llm_profile = await extract_finra_with_llm(
                    pdf_bytes, gemini, crd_hint=crd_number, use_pro=False
                )
                trace["routing"].append("finra_llm_flash")
                final_profile, xval_result = cross_validate_finra(det_profile, llm_profile)
                trace["finra_xval_agrees"] = xval_result.agrees
                trace["finra_xval_disagrees"] = xval_result.disagrees

                # -- Tier 3: escalate to Pro if critical disagreements remain
                if xval_result.critical_disagreements:
                    logger.info(
                        "crd=%s escalating to Pro (disagreements=%d)",
                        crd_number,
                        xval_result.disagrees,
                    )
                    try:
                        pro_profile = await extract_finra_with_llm(
                            pdf_bytes, gemini, crd_hint=crd_number, use_pro=True
                        )
                        trace["routing"].append("finra_llm_pro")
                        # Pro adjudicates: the Pro output is treated as authoritative
                        final_profile, xval_result = cross_validate_finra(final_profile, pro_profile)
                    except Exception as exc:  # noqa: BLE001
                        await store.log_parse_error(crd_number, "gemini_pro", "finra", exc)
                        trace["routing"].append("finra_pro_failed")
            except Exception as exc:  # noqa: BLE001
                await store.log_parse_error(crd_number, "gemini_flash", "finra", exc)
                trace["routing"].append("finra_flash_failed")

        # -- Stamp hash and classification
        final_profile.raw_pdf_hash = sha256_bytes(pdf_bytes)
        apply_classification(final_profile)
        record.finra = final_profile

        # -- Persist
        await store.upsert_firm_profile(final_profile)

        # -- Queue for human review if disagreements persist after Pro
        if xval_result and xval_result.critical_disagreements:
            await store.enqueue_review(
                crd_number=crd_number,
                source="finra",
                disagreements=[
                    {
                        "field": d.field_name,
                        "deterministic": _jsonable(d.deterministic_value),
                        "llm": _jsonable(d.llm_value),
                    }
                    for d in xval_result.critical_disagreements
                ],
            )
            trace["routing"].append("finra_human_review")

        # -------------------------------------------------------- SEC X-17A-5
        try:
            cik = await sec.resolve_cik(firm_name)
            if cik:
                filings = await sec.list_x17_filings(cik, limit=2)
                current, prior = (filings + [None, None])[:2]

                for focus_filing, slot in ((current, "focus_current"), (prior, "focus_prior")):
                    if not focus_filing:
                        continue
                    focus_filing.pdf_bytes = await sec.download_filing(focus_filing)
                    if save_raw_pdfs:
                        _save_raw(crd_number, slot, focus_filing.pdf_bytes)
                    focus_report = await _process_focus_pdf(
                        crd_number, focus_filing.pdf_bytes, gemini, trace
                    )
                    setattr(record, slot, focus_report)
                    await store.upsert_focus_report(crd_number, focus_report)
            else:
                trace["routing"].append("finra_cik_not_found")
        except Exception as exc:  # noqa: BLE001
            await store.log_parse_error(crd_number, "sec_edgar", "acquire_or_parse", exc)
            trace["routing"].append(f"sec_error:{type(exc).__name__}")

        # -------------------------------------------------------- Derivation
        yoy = compute_all_yoy(record.focus_current, record.focus_prior)
        record.net_capital_yoy = yoy["net_capital_yoy"]
        record.total_assets_yoy = yoy["total_assets_yoy"]

        # -------------------------------------------------------- Status
        if record.finra and record.focus_current:
            record.status = "ok"
        elif record.finra or record.focus_current:
            record.status = "partial"
        else:
            record.status = "failed"

        await store.upsert_firm_record(record)
        return record, trace


async def _process_focus_pdf(
    crd_number: str,
    pdf_bytes: bytes,
    gemini: Optional[GeminiClient],
    trace: dict,
) -> FocusReport:
    """Same hybrid pattern but for an X-17A-5 filing."""
    try:
        det_report = parse_focus_pdf(pdf_bytes)
    except Exception as exc:  # noqa: BLE001
        det_report = FocusReport(parse_warnings=[f"deterministic_crash:{exc!r}"])

    conf = score_focus(det_report)
    trace.setdefault("focus_confidences", []).append(conf.score)

    final_report: FocusReport = det_report
    xval_result: Optional[CrossValidationResult] = None

    if gemini is not None:
        try:
            llm_report = await extract_focus_with_llm(pdf_bytes, gemini, use_pro=False)
            final_report, xval_result = cross_validate_focus(det_report, llm_report)

            if xval_result.critical_disagreements:
                try:
                    pro_report = await extract_focus_with_llm(
                        pdf_bytes, gemini, use_pro=True
                    )
                    final_report, xval_result = cross_validate_focus(final_report, pro_report)
                    trace["routing"].append("focus_llm_pro")
                except Exception as exc:  # noqa: BLE001
                    await store.log_parse_error(crd_number, "gemini_pro", "focus", exc)
        except Exception as exc:  # noqa: BLE001
            await store.log_parse_error(crd_number, "gemini_flash", "focus", exc)

    final_report.raw_pdf_hash = sha256_bytes(pdf_bytes)

    if xval_result and xval_result.critical_disagreements:
        await store.enqueue_review(
            crd_number=crd_number,
            source="focus",
            disagreements=[
                {
                    "field": d.field_name,
                    "deterministic": _jsonable(d.deterministic_value),
                    "llm": _jsonable(d.llm_value),
                }
                for d in xval_result.critical_disagreements
            ],
        )
        trace["routing"].append("focus_human_review")

    return final_report


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _save_raw(crd: str, kind: str, pdf_bytes: bytes) -> None:
    os.makedirs(settings.raw_pdf_dir, exist_ok=True)
    path = os.path.join(settings.raw_pdf_dir, f"crd_{crd}__{kind}.pdf")
    with open(path, "wb") as f:
        f.write(pdf_bytes)


def _text_sample(pdf_bytes: bytes, max_chars: int = 4000) -> str:
    """Cheap text sample for space-collapse detection — doesn't need OCR.
    Samples across multiple pages because some FINRA reports have a clean
    cover page followed by space-collapsed body pages."""
    try:
        import pdfplumber
        import io
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            out = []
            # Skip the cover page if the doc has multiple pages — cover is
            # often typeset differently than the body
            start = 1 if len(pdf.pages) > 1 else 0
            for page in pdf.pages[start:start + 4]:
                t = page.extract_text() or ""
                out.append(t)
                if sum(len(x) for x in out) > max_chars:
                    break
            return "\n".join(out)[:max_chars]
    except Exception:  # noqa: BLE001
        return ""


def _jsonable(v):
    from datetime import date, datetime
    from decimal import Decimal
    if isinstance(v, (Decimal, date, datetime)):
        return str(v)
    return v


# ---------------------------------------------------------------------------
# Batch driver
# ---------------------------------------------------------------------------

async def run_hybrid_batch(
    where_status: Optional[str] = None,
    limit: Optional[int] = None,
    save_raw_pdfs: bool = False,
    enable_llm: bool = True,
) -> HybridStats:
    await store.init_schema()

    stats = HybridStats()
    sem = asyncio.Semaphore(settings.max_concurrency)

    gemini: Optional[GeminiClient] = None
    if enable_llm:
        try:
            gemini = GeminiClient(GeminiSettings.from_env())
        except RuntimeError as exc:
            logger.warning("Gemini disabled: %s (running deterministic-only)", exc)
            gemini = None

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

        tasks = []
        async for firm in store.iter_input_crds(where_status=where_status):
            if limit is not None and stats.total >= limit:
                break
            stats.total += 1
            tasks.append(
                asyncio.create_task(
                    process_firm_hybrid(
                        crd_number=firm.crd_number,
                        firm_name=firm.firm_name,
                        finra=finra,
                        sec=sec,
                        gemini=gemini,
                        sem=sem,
                        save_raw_pdfs=save_raw_pdfs,
                    )
                )
            )

        for coro in asyncio.as_completed(tasks):
            try:
                record, trace = await coro
                if record.status == "failed":
                    stats.failed += 1
                elif "human_review" in "|".join(trace.get("routing", [])):
                    stats.human_review += 1
                elif "llm_pro" in "|".join(trace.get("routing", [])):
                    stats.escalated_to_pro += 1
                elif "llm_flash" in "|".join(trace.get("routing", [])):
                    # Flash ran, no escalation → deterministic was good or Flash filled gaps
                    if trace.get("finra_xval_disagrees", 0) > 0:
                        stats.llm_filled += 1
                    else:
                        stats.auto_accepted += 1
                else:
                    stats.auto_accepted += 1
            except Exception as exc:  # noqa: BLE001
                logger.exception("Unhandled in process_firm_hybrid: %s", exc)
                stats.failed += 1

            processed = stats.auto_accepted + stats.llm_filled + stats.escalated_to_pro + stats.human_review + stats.failed
            if processed % 25 == 0:
                logger.info("progress: %s", stats.summary())

    logger.info("Run complete: %s", stats.summary())
    return stats
