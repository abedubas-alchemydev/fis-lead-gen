"""A/B test Gemini 2.5 Pro vs Flash on clearing or financial extraction.

Tier-1 of the Gemini paid plan caps ``gemini-2.5-pro`` at 1,000 requests/day
but allows ``gemini-2.5-flash`` at 10,000 requests/day. Switching the default
extraction model to Flash unblocks the full-universe backfill at 10x quota
and ~5x lower cost -- IF Flash matches Pro's accuracy on the load-bearing
fields. This script answers that question on a representative 10-firm sample
without touching the production model setting or the database.

For each sampled broker-dealer we download the X-17A-5 PDF once and call
BOTH models with the SAME prompt. The output is a side-by-side table plus
per-model rollups (status counts, mean confidence, agreement rate on key
fields, estimated total cost) and a regression signal (count of firms where
Flash flips a Pro ``parsed`` row to ``needs_review``).

No DB writes. Pure read-sample-firms + dual-model call + print.

Usage:
    python -m scripts.ab_test_gemini_models                 # 10 firms, clearing
    python -m scripts.ab_test_gemini_models --type financial
    python -m scripts.ab_test_gemini_models --limit 5
    python -m scripts.ab_test_gemini_models --bd-ids 1,2,3
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import selectors
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
sys.stderr.reconfigure(line_buffering=True)  # type: ignore[attr-defined]

if sys.platform == "win32" and sys.version_info < (3, 14):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy import exists, select  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.broker_dealer import BrokerDealer  # noqa: E402
from app.models.clearing_arrangement import ClearingArrangement  # noqa: E402
from app.services.extraction_status import (  # noqa: E402
    STATUS_NEEDS_REVIEW,
    STATUS_PARSED,
    classify_financial_extraction_status,
)
from app.services.focus_reports import FocusReportService  # noqa: E402
from app.services.gemini_responses import (  # noqa: E402
    GeminiClearingExtraction,
    GeminiConfigurationError,
    GeminiExtractionError,
    GeminiFinancialExtraction,
    GeminiResponsesClient,
)
from app.services.llm_parser import LlmParserService  # noqa: E402
from app.services.pdf_downloader import PdfDownloaderService, pdf_tempdir  # noqa: E402

logger = logging.getLogger(__name__)

PRO_MODEL = "gemini-2.5-pro"
FLASH_MODEL = "gemini-2.5-flash"

# USD per million tokens, public Gemini paid-tier pricing.
COST_INPUT_PER_M: dict[str, float] = {PRO_MODEL: 1.25, FLASH_MODEL: 0.075}
COST_OUTPUT_PER_M: dict[str, float] = {PRO_MODEL: 5.00, FLASH_MODEL: 0.30}

# Per-call token estimate for an X-17A-5 audit. The Gemini response payload
# we surface today drops ``usageMetadata`` (see gemini_responses.py
# ``_extract_response_text``), so this script can't read actual token counts
# without modifying the production client. The numbers below are deliberate
# back-of-envelope figures: prompt + audit PDF body lands around ~50k input
# tokens, structured-JSON response stays under ~250 output tokens.
APPROX_INPUT_TOKENS_PER_CALL = 50_000
APPROX_OUTPUT_TOKENS_PER_CALL = 250

EXTRACTION_TYPES = ("clearing", "financial")


@dataclass(slots=True)
class CallStats:
    model: str
    bd_id: int
    bd_name: str
    extraction_status: str  # parsed | needs_review | error
    confidence: float | None = None
    clearing_partner: str | None = None
    clearing_type: str | None = None
    report_date: str | None = None
    net_capital: float | None = None
    rationale: str = ""
    error: str | None = None


@dataclass(slots=True)
class FirmRow:
    bd_id: int
    bd_name: str
    pro: CallStats | None = None
    flash: CallStats | None = None
    skip_reason: str | None = None


@dataclass(slots=True)
class Rollup:
    total: int = 0
    parsed: int = 0
    needs_review: int = 0
    error: int = 0
    confidences: list[float] = field(default_factory=list)


@contextlib.contextmanager
def _override_gemini_model(model: str):
    """Diagnostic-only swap of ``settings.gemini_pdf_model``.

    ``GeminiResponsesClient._post_with_retries`` reads the model name out of
    settings at call time, so flipping it for the duration of one call
    re-targets the same client at a different model. Restored in ``finally``
    so other coroutines and follow-up code see the original value.
    """
    original = settings.gemini_pdf_model
    settings.gemini_pdf_model = model
    try:
        yield
    finally:
        settings.gemini_pdf_model = original


async def _select_target_bds(
    db, *, extraction_type: str, limit: int, bd_ids: list[int] | None
) -> list[BrokerDealer]:
    stmt = select(BrokerDealer).where(BrokerDealer.filings_index_url.is_not(None))
    if bd_ids:
        stmt = stmt.where(BrokerDealer.id.in_(bd_ids))
    elif extraction_type == "clearing":
        clearing_baseline = (
            select(ClearingArrangement.id)
            .where(ClearingArrangement.bd_id == BrokerDealer.id)
        )
        stmt = stmt.where(exists(clearing_baseline)).order_by(BrokerDealer.id.asc()).limit(limit)
    else:
        stmt = (
            stmt.where(BrokerDealer.latest_net_capital.is_not(None))
            .order_by(BrokerDealer.id.asc())
            .limit(limit)
        )
    return list((await db.execute(stmt)).scalars().all())


def _classify_clearing_status(extraction: GeminiClearingExtraction) -> str:
    """Mirror the production tagger in ``LlmParserService.extract_structured_data``."""
    partner_required = extraction.clearing_type != "self_clearing"
    if (
        extraction.confidence_score < settings.clearing_extraction_min_confidence
        or (partner_required and not extraction.clearing_partner)
        or extraction.clearing_type == "unknown"
    ):
        return STATUS_NEEDS_REVIEW
    return STATUS_PARSED


def _classify_financial_status(extraction: GeminiFinancialExtraction) -> str:
    return classify_financial_extraction_status(
        confidence_score=extraction.confidence_score,
        min_confidence=settings.financial_extraction_min_confidence,
        has_required_fields=extraction.net_capital is not None,
    )


async def _call_clearing(
    client: GeminiResponsesClient,
    *,
    pdf_b64: str,
    prompt: str,
    model: str,
    bd: BrokerDealer,
) -> CallStats:
    try:
        with _override_gemini_model(model):
            extraction = await client.extract_clearing_data(
                pdf_bytes_base64=pdf_b64, prompt=prompt
            )
    except (GeminiConfigurationError, GeminiExtractionError) as exc:
        return CallStats(
            model=model, bd_id=bd.id, bd_name=bd.name,
            extraction_status="error", error=str(exc)[:160],
        )
    return CallStats(
        model=model,
        bd_id=bd.id,
        bd_name=bd.name,
        extraction_status=_classify_clearing_status(extraction),
        confidence=extraction.confidence_score,
        clearing_partner=extraction.clearing_partner,
        clearing_type=extraction.clearing_type,
        rationale=extraction.rationale[:120],
    )


async def _call_financial(
    client: GeminiResponsesClient,
    *,
    pdf_b64: str,
    prompt: str,
    model: str,
    bd: BrokerDealer,
) -> CallStats:
    try:
        with _override_gemini_model(model):
            extraction = await client.extract_financial_data(
                pdf_bytes_base64=pdf_b64, prompt=prompt
            )
    except (GeminiConfigurationError, GeminiExtractionError) as exc:
        return CallStats(
            model=model, bd_id=bd.id, bd_name=bd.name,
            extraction_status="error", error=str(exc)[:160],
        )
    return CallStats(
        model=model,
        bd_id=bd.id,
        bd_name=bd.name,
        extraction_status=_classify_financial_status(extraction),
        confidence=extraction.confidence_score,
        report_date=extraction.report_date,
        net_capital=extraction.net_capital,
        rationale=extraction.rationale[:120],
    )


def _trunc(value: object, width: int) -> str:
    text = "" if value is None else str(value)
    return text if len(text) <= width else text[: width - 1] + "…"


def _print_clearing_rows(rows: list[FirmRow]) -> None:
    header = (
        f"{'BD':>5} {'Name':<30} {'Model':<8} {'Status':<13} "
        f"{'Conf':>5} {'Type':<16} {'Partner':<28}"
    )
    print(header)
    print("-" * len(header))
    for row in rows:
        if row.skip_reason:
            print(f"{row.bd_id:>5} {_trunc(row.bd_name, 30):<30} -- skipped: {row.skip_reason}")
            continue
        for tag, stats in (("pro", row.pro), ("flash", row.flash)):
            if stats is None:
                continue
            conf = "" if stats.confidence is None else f"{stats.confidence:0.2f}"
            print(
                f"{row.bd_id:>5} "
                f"{_trunc(row.bd_name, 30):<30} "
                f"{tag:<8} "
                f"{_trunc(stats.extraction_status, 13):<13} "
                f"{conf:>5} "
                f"{_trunc(stats.clearing_type or '-', 16):<16} "
                f"{_trunc(stats.clearing_partner or '-', 28):<28}"
            )


def _print_financial_rows(rows: list[FirmRow]) -> None:
    header = (
        f"{'BD':>5} {'Name':<30} {'Model':<8} {'Status':<13} "
        f"{'Conf':>5} {'ReportDate':<11} {'NetCapital':>16}"
    )
    print(header)
    print("-" * len(header))
    for row in rows:
        if row.skip_reason:
            print(f"{row.bd_id:>5} {_trunc(row.bd_name, 30):<30} -- skipped: {row.skip_reason}")
            continue
        for tag, stats in (("pro", row.pro), ("flash", row.flash)):
            if stats is None:
                continue
            conf = "" if stats.confidence is None else f"{stats.confidence:0.2f}"
            net_cap = "" if stats.net_capital is None else f"{stats.net_capital:>16,.0f}"
            print(
                f"{row.bd_id:>5} "
                f"{_trunc(row.bd_name, 30):<30} "
                f"{tag:<8} "
                f"{_trunc(stats.extraction_status, 13):<13} "
                f"{conf:>5} "
                f"{_trunc(stats.report_date or '-', 11):<11} "
                f"{net_cap:>16}"
            )


def _rollup_for(rows: list[FirmRow], picker) -> Rollup:
    rollup = Rollup()
    for row in rows:
        stats = picker(row)
        if stats is None:
            continue
        rollup.total += 1
        if stats.extraction_status == STATUS_PARSED:
            rollup.parsed += 1
        elif stats.extraction_status == STATUS_NEEDS_REVIEW:
            rollup.needs_review += 1
        else:
            rollup.error += 1
        if stats.confidence is not None:
            rollup.confidences.append(stats.confidence)
    return rollup


def _print_rollups(rows: list[FirmRow], extraction_type: str) -> None:
    pro = _rollup_for(rows, lambda r: r.pro)
    flash = _rollup_for(rows, lambda r: r.flash)

    def _summary(model: str, r: Rollup) -> str:
        mean_conf = sum(r.confidences) / len(r.confidences) if r.confidences else 0.0
        return (
            f"  {model:<22} total={r.total:>3}  parsed={r.parsed:>3}  "
            f"needs_review={r.needs_review:>3}  error={r.error:>3}  "
            f"mean_conf={mean_conf:0.3f}"
        )

    print()
    print("Per-model rollups")
    print(_summary(PRO_MODEL, pro))
    print(_summary(FLASH_MODEL, flash))

    if extraction_type == "clearing":
        key_fields = ("clearing_partner", "clearing_type")
    else:
        key_fields = ("report_date", "net_capital")

    paired = [
        r for r in rows
        if r.pro is not None and r.flash is not None
        and r.pro.extraction_status != "error"
        and r.flash.extraction_status != "error"
    ]
    agree_count = sum(
        1 for r in paired if all(getattr(r.pro, f) == getattr(r.flash, f) for f in key_fields)
    )
    agreement = f"{agree_count}/{len(paired)}" if paired else "0/0"
    print(f"  agreement on {key_fields}: {agreement}")

    # Per CLAUDE.md "review-queue semantics in LLM extraction must be
    # preserved" -- a non-zero count here is the yellow flag for the
    # follow-up brief that flips GEMINI_PDF_MODEL to Flash.
    regressions = [
        r.bd_id for r in paired
        if r.pro.extraction_status == STATUS_PARSED
        and r.flash.extraction_status == STATUS_NEEDS_REVIEW
    ]
    print(
        f"  Flash regressions (Pro=parsed -> Flash=needs_review): "
        f"{len(regressions)} firms {regressions}"
    )

    pro_cost = (
        pro.total
        * (
            APPROX_INPUT_TOKENS_PER_CALL * COST_INPUT_PER_M[PRO_MODEL]
            + APPROX_OUTPUT_TOKENS_PER_CALL * COST_OUTPUT_PER_M[PRO_MODEL]
        )
        / 1_000_000.0
    )
    flash_cost = (
        flash.total
        * (
            APPROX_INPUT_TOKENS_PER_CALL * COST_INPUT_PER_M[FLASH_MODEL]
            + APPROX_OUTPUT_TOKENS_PER_CALL * COST_OUTPUT_PER_M[FLASH_MODEL]
        )
        / 1_000_000.0
    )
    if pro_cost > 0:
        savings_pct = (1 - flash_cost / pro_cost) * 100
        print(
            f"  estimated cost (assuming ~{APPROX_INPUT_TOKENS_PER_CALL:,} in / "
            f"~{APPROX_OUTPUT_TOKENS_PER_CALL} out tokens per call): "
            f"Pro=${pro_cost:0.4f}  Flash=${flash_cost:0.4f}  "
            f"savings=${pro_cost - flash_cost:0.4f} ({savings_pct:0.0f}% lower)"
        )
    else:
        print("  estimated cost: 0 calls")


async def _process_firm(
    bd: BrokerDealer,
    *,
    downloader: PdfDownloaderService,
    client: GeminiResponsesClient,
    prompt: str,
    extraction_type: str,
) -> FirmRow:
    row = FirmRow(bd_id=bd.id, bd_name=bd.name)
    with pdf_tempdir(prefix="ab_test_") as tmp_dir:
        try:
            record = await downloader.download_latest_x17a5_pdf(bd, tmp_dir)
        except Exception as exc:  # noqa: BLE001
            row.skip_reason = f"download_error: {exc}"
            return row
        if record is None or not record.bytes_base64:
            row.skip_reason = "no_pdf_or_files_api_path"
            return row

        caller = _call_clearing if extraction_type == "clearing" else _call_financial
        row.pro = await caller(
            client, pdf_b64=record.bytes_base64, prompt=prompt, model=PRO_MODEL, bd=bd
        )
        row.flash = await caller(
            client, pdf_b64=record.bytes_base64, prompt=prompt, model=FLASH_MODEL, bd=bd
        )
    return row


async def main(*, extraction_type: str, limit: int, bd_ids: list[int] | None) -> None:
    if not settings.gemini_api_key:
        print("ERROR: GEMINI_API_KEY is not set. Cannot run A/B test.", file=sys.stderr)
        sys.exit(1)
    if settings.llm_use_files_api:
        # The Files-API streaming path leaves bytes_base64 empty and routes
        # uploads through extract_clearing_data_from_path with a different
        # signature. Surface the constraint loudly rather than silently
        # producing 'no_pdf' rows for every firm.
        print(
            "ERROR: LLM_USE_FILES_API=true is set; this A/B script needs the "
            "inline-base64 download path. Re-run with LLM_USE_FILES_API=false.",
            file=sys.stderr,
        )
        sys.exit(2)

    print(f"A/B test: {extraction_type} extraction, Pro vs Flash")
    print(f"  Sample size: {len(bd_ids) if bd_ids else limit}")
    print(f"  BD IDs override: {bd_ids if bd_ids else '(auto-selected)'}")
    print()

    async with SessionLocal() as db:
        bds = await _select_target_bds(
            db, extraction_type=extraction_type, limit=limit, bd_ids=bd_ids,
        )

    if not bds:
        print("No matching broker-dealers found. Check filters or DB state.")
        return

    downloader = PdfDownloaderService()
    client = GeminiResponsesClient()
    if extraction_type == "clearing":
        prompt = LlmParserService().build_prompt()
    else:
        # Financial prompt lives on FocusReportService as a private method.
        # Reaching past the underscore is fine for diagnostic tooling and
        # avoids copy-paste drift between this script and the production
        # extraction path.
        prompt = FocusReportService()._build_financial_prompt()

    rows: list[FirmRow] = []
    for idx, bd in enumerate(bds, start=1):
        print(f"[{idx:>2}/{len(bds)}] BD {bd.id} {bd.name}: downloading + dual-extraction...")
        row = await _process_firm(
            bd,
            downloader=downloader,
            client=client,
            prompt=prompt,
            extraction_type=extraction_type,
        )
        rows.append(row)

    print()
    print(f"Side-by-side comparison ({extraction_type})")
    if extraction_type == "clearing":
        _print_clearing_rows(rows)
    else:
        _print_financial_rows(rows)
    _print_rollups(rows, extraction_type)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="A/B test Gemini Pro vs Flash on clearing or financial extraction.",
    )
    parser.add_argument(
        "--type", choices=EXTRACTION_TYPES, default="clearing",
        help="Extraction type (default: clearing)",
    )
    parser.add_argument(
        "--limit", type=int, default=10,
        help="Sample size when --bd-ids is not set (default: 10)",
    )
    parser.add_argument(
        "--bd-ids", type=str, default=None,
        help="Comma-separated BD IDs to override the auto-sampled set, e.g. 1,2,3",
    )
    return parser.parse_args()


def _coerce_bd_ids(raw: str | None) -> list[int] | None:
    if not raw:
        return None
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts:
        return None
    try:
        return [int(p) for p in parts]
    except ValueError as exc:
        raise SystemExit(
            f"--bd-ids must be a comma-separated list of integers: {exc}"
        ) from exc


if __name__ == "__main__":
    args = _parse_args()
    bd_ids = _coerce_bd_ids(args.bd_ids)

    if sys.platform == "win32" and sys.version_info >= (3, 14):
        with asyncio.Runner(
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())
        ) as runner:
            runner.run(main(extraction_type=args.type, limit=args.limit, bd_ids=bd_ids))
    else:
        asyncio.run(main(extraction_type=args.type, limit=args.limit, bd_ids=bd_ids))
