from __future__ import annotations

import asyncio
import base64
import io
import logging
from datetime import date, datetime, timezone
from pathlib import Path

from app.core.config import settings
from app.services.gemini_responses import (
    GeminiClearingExtraction,
    GeminiConfigurationError,
    GeminiExtractionError,
    GeminiResponsesClient,
)
from app.services.llm_parser import LlmParserService
from app.services.ocr import VisionOCR, VisionOcrConfigurationError, VisionOcrError
from app.services.openai_responses import (
    OpenAIClearingExtraction,
    OpenAIConfigurationError,
    OpenAIExtractionError,
    OpenAIResponsesClient,
)
from app.services.service_models import ClearingExtractionResult, DownloadedPdfRecord

logger = logging.getLogger(__name__)


# Below this length (after stripping whitespace) an X-17A-5 PDF is
# treated as scanned-image and routed through Vision OCR. The threshold
# matches the OCR-cost cap rationale in the BE-2 prompt: ~5% of filings
# trip this on a Fresh Regen, keeping Vision spend at ~$0.23 per regen.
_PDFPLUMBER_MIN_TEXT_CHARS = 50


def _extract_pdfplumber_text(pdf_bytes: bytes) -> str:
    """Return the full pdfplumber text for a PDF, or '' on any failure.

    Returning '' on failure (corrupt PDF, encrypted file, pdfplumber
    parser exception) is intentional: the caller routes < 50 char
    output to Vision OCR, and Vision is robust enough to handle most
    inputs that defeat pdfplumber. Logging stays at info-level so a
    flood of malformed PDFs in a regen does not warn-spam the logs.
    """
    try:
        import pdfplumber
    except ImportError:  # pragma: no cover - pdfplumber is in requirements.txt
        logger.warning("pdfplumber import failed; routing to OCR by default.")
        return ""

    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            return "\n".join((page.extract_text() or "") for page in pdf.pages)
    except Exception as exc:
        logger.info("pdfplumber failed to read PDF (%s); routing to OCR.", exc)
        return ""


class PdfProcessorService:
    def __init__(self) -> None:
        self.llm_parser = LlmParserService()
        # Lazy provider clients only used under ``settings.llm_use_files_api``.
        # Constructing them here keeps the flag-on path in this layer without
        # touching ``llm_parser`` (out of scope for this PR per ADR-0001
        # phase 2 — the legacy flag-off path stays delegating to llm_parser).
        self._gemini_client = GeminiResponsesClient()
        self._openai_client = OpenAIResponsesClient()
        # Vision client is lazily authenticated on first OCR call so a
        # regen with zero scanned-image filings pays no SDK auth cost.
        # One instance per service lifetime so the SHA-256 cache amortizes
        # across the fan-out for a single Fresh Regen.
        self._vision_ocr = VisionOCR()

    async def process_downloaded_pdf(
        self, pdf_record: DownloadedPdfRecord
    ) -> ClearingExtractionResult:
        # OCR pre-pass: scanned-image PDFs (pdfplumber < 50 chars) get
        # routed through Cloud Vision before the LLM call; everything
        # else stays on the existing path byte-for-byte. The pdfplumber
        # read is ~100 ms per filing and is run in a worker thread so
        # the event loop is never blocked. ``pdf_bytes is None`` falls
        # through to the existing handler so the legacy "no bytes"
        # error semantics in ``LlmParserService.extract_structured_data``
        # (and ``_extract_via_files_api``) are preserved.
        pdf_bytes = self._read_pdf_bytes(pdf_record)
        if pdf_bytes:
            plumber_text = await asyncio.to_thread(_extract_pdfplumber_text, pdf_bytes)
            if len(plumber_text.strip()) < _PDFPLUMBER_MIN_TEXT_CHARS:
                return await self._extract_via_ocr(pdf_record, pdf_bytes)

        if not settings.llm_use_files_api:
            # Legacy default-off path: byte-for-byte unchanged from today.
            # The LLM parser consumes ``pdf_record.bytes_base64`` and inlines
            # the PDF into the provider call.
            return await self.llm_parser.extract_structured_data(pdf_record)

        # Files-API path (ADR-0001 phase 2). The PDF is on disk at
        # ``local_document_path`` (streamed there by ``PdfDownloaderService``)
        # and ``bytes_base64`` is empty by construction. The LLM provider
        # uploads the PDF via its Files API and references it by file_id —
        # no inline bytes cross the wire.
        return await self._extract_via_files_api(pdf_record)

    @staticmethod
    def _read_pdf_bytes(pdf_record: DownloadedPdfRecord) -> bytes | None:
        """Materialize PDF bytes from inline base64 or disk, or None.

        The legacy default-off path populates ``bytes_base64`` and the
        Files-API path populates ``local_document_path`` — exactly one
        is available per record by construction, but both sources are
        checked so the OCR pre-pass works on either path.
        """
        if pdf_record.bytes_base64:
            try:
                return base64.b64decode(pdf_record.bytes_base64)
            except (ValueError, TypeError) as exc:
                logger.warning(
                    "Invalid bytes_base64 on PDF record (bd_id=%s): %s",
                    pdf_record.bd_id,
                    exc,
                )
                return None
        if pdf_record.local_document_path:
            local = Path(pdf_record.local_document_path)
            if local.exists():
                try:
                    return local.read_bytes()
                except OSError as exc:
                    logger.warning("Failed to read PDF from %s: %s", local, exc)
                    return None
        return None

    async def _extract_via_ocr(
        self, pdf_record: DownloadedPdfRecord, pdf_bytes: bytes
    ) -> ClearingExtractionResult:
        """Run Vision OCR then dispatch to the OCR-aware LLM extractor.

        Failure modes:

        * Vision SDK / API failure → ``provider_error`` with note
          ``vision_ocr_failed: …``. Preserves the review-queue contract
          per CLAUDE.md — no silent drops.
        * Vision returned text but still under the 50-char floor →
          ``pipeline_error`` (the ``pdf_unparseable`` Unknown-reasons
          category). Vision did its job; the filing genuinely contains
          no machine-readable content.
        """
        try:
            ocr_text = await asyncio.to_thread(self._vision_ocr.ocr_pdf, pdf_bytes)
        except (VisionOcrError, VisionOcrConfigurationError) as exc:
            return self._error_result(
                pdf_record,
                status="provider_error",
                note=f"vision_ocr_failed: {exc}",
            )

        if len(ocr_text.strip()) < _PDFPLUMBER_MIN_TEXT_CHARS:
            return self._error_result(
                pdf_record,
                status="pipeline_error",
                note="OCR returned no usable text — PDF is unparseable.",
            )

        return await self.llm_parser.extract_structured_data_with_ocr_text(
            pdf_record,
            ocr_text=ocr_text,
            pdf_bytes_base64=base64.b64encode(pdf_bytes).decode("ascii"),
        )

    async def _extract_via_files_api(
        self, pdf_record: DownloadedPdfRecord
    ) -> ClearingExtractionResult:
        accession_number = pdf_record.accession_number
        local_path = (
            Path(pdf_record.local_document_path)
            if pdf_record.local_document_path
            else None
        )
        if accession_number is None or local_path is None or not local_path.exists():
            return self._error_result(
                pdf_record,
                status="provider_error",
                note=(
                    "Files API path requires a streamed local PDF and an "
                    "accession_number. Either was missing on this record."
                ),
            )

        prompt = self.llm_parser.build_prompt()
        provider = settings.llm_provider

        if provider == "gemini":
            try:
                gemini_extraction = await self._gemini_client.extract_clearing_data_from_path(
                    local_path=local_path,
                    accession_number=accession_number,
                    prompt=prompt,
                )
            except (GeminiConfigurationError, GeminiExtractionError) as exc:
                return self._error_result(
                    pdf_record, status="provider_error", note=str(exc)
                )
            return self._build_result(pdf_record, gemini_extraction)

        if provider == "openai":
            try:
                openai_extraction = await self._openai_client.extract_clearing_data_from_path(
                    local_path=local_path,
                    accession_number=accession_number,
                    filename=self._build_filename(pdf_record),
                    prompt=prompt,
                )
            except (OpenAIConfigurationError, OpenAIExtractionError) as exc:
                return self._error_result(
                    pdf_record, status="provider_error", note=str(exc)
                )
            return self._build_result(pdf_record, openai_extraction)

        return self._error_result(
            pdf_record,
            status="provider_error",
            note=(
                f"Unsupported LLM provider '{provider}'. Configure "
                f"LLM_PROVIDER=gemini or openai."
            ),
        )

    def _build_result(
        self,
        pdf_record: DownloadedPdfRecord,
        extraction: GeminiClearingExtraction | OpenAIClearingExtraction,
    ) -> ClearingExtractionResult:
        agreement_date = self._parse_optional_date(extraction.agreement_date)
        notes = extraction.rationale
        if extraction.evidence_excerpt:
            notes = f"{notes} Evidence: {extraction.evidence_excerpt}"

        # Mirrors the review-queue thresholding in
        # ``LlmParserService.extract_structured_data``: low-confidence,
        # missing-partner-when-required, and unknown rows land tagged
        # ``needs_review`` rather than silently bypassing the queue.
        status = "parsed"
        partner_required = extraction.clearing_type != "self_clearing"
        if (
            extraction.confidence_score < settings.clearing_extraction_min_confidence
            or (partner_required and not extraction.clearing_partner)
            or extraction.clearing_type == "unknown"
        ):
            status = "needs_review"

        return ClearingExtractionResult(
            bd_id=pdf_record.bd_id,
            filing_year=pdf_record.filing_year,
            report_date=pdf_record.report_date,
            source_filing_url=pdf_record.source_filing_url,
            source_pdf_url=pdf_record.source_pdf_url,
            local_document_path=pdf_record.local_document_path,
            clearing_partner=extraction.clearing_partner,
            clearing_type=extraction.clearing_type,
            agreement_date=agreement_date,
            extraction_confidence=extraction.confidence_score,
            extraction_status=status,
            extraction_notes=notes,
            extracted_at=datetime.now(timezone.utc),
        )

    def _error_result(
        self, pdf_record: DownloadedPdfRecord, *, status: str, note: str
    ) -> ClearingExtractionResult:
        return ClearingExtractionResult(
            bd_id=pdf_record.bd_id,
            filing_year=pdf_record.filing_year,
            report_date=pdf_record.report_date,
            source_filing_url=pdf_record.source_filing_url,
            source_pdf_url=pdf_record.source_pdf_url,
            local_document_path=pdf_record.local_document_path,
            clearing_partner=None,
            clearing_type="unknown",
            agreement_date=None,
            extraction_confidence=0.0,
            extraction_status=status,
            extraction_notes=note,
            extracted_at=datetime.now(timezone.utc),
        )

    def _build_filename(self, pdf_record: DownloadedPdfRecord) -> str:
        return f"broker-dealer-{pdf_record.bd_id}-{pdf_record.filing_year}.pdf"

    def _parse_optional_date(self, value: str | None) -> date | None:
        if not value:
            return None
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
