from __future__ import annotations

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
from app.services.openai_responses import (
    OpenAIClearingExtraction,
    OpenAIConfigurationError,
    OpenAIExtractionError,
    OpenAIResponsesClient,
)
from app.services.service_models import ClearingExtractionResult, DownloadedPdfRecord


class PdfProcessorService:
    def __init__(self) -> None:
        self.llm_parser = LlmParserService()
        # Lazy provider clients only used under ``settings.llm_use_files_api``.
        # Constructing them here keeps the flag-on path in this layer without
        # touching ``llm_parser`` (out of scope for this PR per ADR-0001
        # phase 2 — the legacy flag-off path stays delegating to llm_parser).
        self._gemini_client = GeminiResponsesClient()
        self._openai_client = OpenAIResponsesClient()

    async def process_downloaded_pdf(
        self, pdf_record: DownloadedPdfRecord
    ) -> ClearingExtractionResult:
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
