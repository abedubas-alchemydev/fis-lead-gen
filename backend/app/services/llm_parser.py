from __future__ import annotations

from datetime import date, datetime, timezone

from app.core.config import settings
from app.services.gemini_responses import (
    GeminiConfigurationError,
    GeminiExtractionError,
    GeminiResponsesClient,
)
from app.services.openai_responses import (
    OpenAIConfigurationError,
    OpenAIExtractionError,
    OpenAIResponsesClient,
)
from app.services.service_models import ClearingExtractionResult, DownloadedPdfRecord


class LlmParserService:
    def __init__(self) -> None:
        self.gemini_client = GeminiResponsesClient()
        self.openai_client = OpenAIResponsesClient()

    # Marker the OCR path stamps onto ``extraction_notes`` so the review
    # queue can spot Vision-derived rows without re-parsing the rationale.
    # Read by tooling that filters scanned-image extractions; do not rename
    # without updating downstream filters.
    OCR_TEXT_SOURCE_MARKER = "[text_source=ocr]"

    def build_prompt(self) -> str:
        return (
            "Read the broker-dealer annual audit PDF (X-17A-5 Part III filing) and extract the firm's "
            "current clearing arrangement. Focus on the Notes to Financial Statements section, which "
            "typically contains a sentence such as 'The Company has a clearing agreement with [Partner Name] "
            "on a fully disclosed basis.'\n\n"
            "Return a JSON object with these fields:\n"
            "- clearing_partner: The name of the clearing firm (e.g. 'Pershing LLC', 'Apex Clearing Corporation'). "
            "Use null only if no clearing partner is mentioned or the firm is self-clearing.\n"
            "- clearing_type: One of 'fully_disclosed', 'self_clearing', 'omnibus', or 'unknown'.\n"
            "- agreement_date: The date of the clearing agreement in YYYY-MM-DD format, only if explicitly stated. "
            "Use null if not explicitly present.\n"
            "- confidence_score: A number between 0 and 1 reflecting your certainty.\n"
            "- rationale: A brief explanation of how you determined the clearing arrangement.\n"
            "- evidence_excerpt: The exact sentence(s) from the document that support your answer.\n\n"
            "## Examples\n\n"
            "Example 1 — Fully Disclosed:\n"
            'Document says: "The Company clears all transactions through Pershing LLC on a fully disclosed basis."\n'
            "Expected output:\n"
            '{"clearing_partner": "Pershing LLC", "clearing_type": "fully_disclosed", "agreement_date": null, '
            '"confidence_score": 0.95, "rationale": "Notes to Financial Statements explicitly name Pershing LLC '
            'as the clearing firm on a fully disclosed basis.", "evidence_excerpt": "The Company clears all '
            'transactions through Pershing LLC on a fully disclosed basis."}\n\n'
            "Example 2 — Self-Clearing:\n"
            'Document says: "The Company is a self-clearing broker-dealer and carries customer accounts."\n'
            "Expected output:\n"
            '{"clearing_partner": null, "clearing_type": "self_clearing", "agreement_date": null, '
            '"confidence_score": 0.95, "rationale": "The firm explicitly states it is self-clearing and '
            'carries its own customer accounts.", "evidence_excerpt": "The Company is a self-clearing '
            'broker-dealer and carries customer accounts."}\n\n'
            "Example 3 — Unknown/Ambiguous:\n"
            'Document mentions multiple clearing brokers but does not name them specifically.\n'
            "Expected output:\n"
            '{"clearing_partner": null, "clearing_type": "unknown", "agreement_date": null, '
            '"confidence_score": 0.4, "rationale": "The document references clearing brokers but does not '
            'name them.", "evidence_excerpt": "The Company conducts business through several clearing brokers."}\n\n'
            "Now analyze the attached PDF and extract the clearing arrangement data. Use only evidence from "
            "the document. Treat uncertain or conflicting references conservatively."
        )

    def build_prompt_with_ocr_text(self, ocr_text: str) -> str:
        """Wrap the standard clearing-extraction prompt with OCR'd text up
        front, so the model has a text-rich source on scanned-image PDFs.

        The provider call still receives the PDF bytes alongside this
        prompt — Gemini/OpenAI will fall back to the OCR text when the
        embedded layer is empty, instead of returning ``unknown`` from
        pixel-only input. The marker line tells the model that the
        attached PDF and the inline text describe the same filing, so
        it does not split confidence across two evidence sources.
        """
        cleaned = " ".join(ocr_text.split()) if ocr_text else ""
        base_prompt = self.build_prompt()
        return (
            "The attached PDF is a scanned image with no embedded text layer. "
            "The text below was extracted by Google Cloud Vision's "
            "documentTextDetection from the same filing — treat it as the "
            "authoritative source of evidence. The PDF bytes are attached "
            "only so you can cross-reference page layout if helpful.\n\n"
            "── BEGIN OCR TEXT ──\n"
            f"{cleaned}\n"
            "── END OCR TEXT ──\n\n"
            f"{base_prompt}"
        )

    async def extract_structured_data_with_ocr_text(
        self,
        pdf_record: DownloadedPdfRecord,
        *,
        ocr_text: str,
        pdf_bytes_base64: str,
    ) -> ClearingExtractionResult:
        """Run clearing extraction with OCR'd text augmenting the prompt.

        Mirrors :meth:`extract_structured_data` but builds the prompt via
        :meth:`build_prompt_with_ocr_text` and stamps
        :attr:`OCR_TEXT_SOURCE_MARKER` onto ``extraction_notes`` so the
        review queue can spot Vision-derived rows.

        ``pdf_bytes_base64`` is taken as a parameter rather than read off
        ``pdf_record`` because the Files-API path constructs records with
        an empty ``bytes_base64``; the caller in ``pdf_processor`` reads
        the bytes off ``local_document_path`` once for both the OCR call
        and this LLM call, so the disk hit is paid only once per filing.
        """
        prompt = self.build_prompt_with_ocr_text(ocr_text)

        if settings.llm_provider == "gemini":
            try:
                extraction = await self.gemini_client.extract_clearing_data(
                    pdf_bytes_base64=pdf_bytes_base64,
                    prompt=prompt,
                )
            except (GeminiConfigurationError, GeminiExtractionError) as exc:
                return self._error_result(pdf_record, status="provider_error", note=str(exc))
        elif settings.llm_provider == "openai":
            try:
                extraction = await self.openai_client.extract_clearing_data(
                    pdf_bytes_base64=pdf_bytes_base64,
                    filename=self._build_filename(pdf_record),
                    prompt=prompt,
                )
            except (OpenAIConfigurationError, OpenAIExtractionError) as exc:
                return self._error_result(pdf_record, status="provider_error", note=str(exc))
        else:
            return self._error_result(
                pdf_record,
                status="provider_error",
                note=f"Unsupported LLM provider '{settings.llm_provider}'. Configure LLM_PROVIDER=gemini or openai.",
            )

        agreement_date = self._parse_optional_date(extraction.agreement_date)
        notes = f"{self.OCR_TEXT_SOURCE_MARKER} {extraction.rationale}".strip()
        if extraction.evidence_excerpt:
            notes = f"{notes} Evidence: {extraction.evidence_excerpt}"

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

    async def extract_structured_data(self, pdf_record: DownloadedPdfRecord) -> ClearingExtractionResult:
        if settings.llm_provider == "gemini":
            try:
                extraction = await self.gemini_client.extract_clearing_data(
                    pdf_bytes_base64=pdf_record.bytes_base64,
                    prompt=self.build_prompt(),
                )
            except (GeminiConfigurationError, GeminiExtractionError) as exc:
                return self._error_result(pdf_record, status="provider_error", note=str(exc))
        elif settings.llm_provider == "openai":
            try:
                extraction = await self.openai_client.extract_clearing_data(
                    pdf_bytes_base64=pdf_record.bytes_base64,
                    filename=self._build_filename(pdf_record),
                    prompt=self.build_prompt(),
                )
            except (OpenAIConfigurationError, OpenAIExtractionError) as exc:
                return self._error_result(pdf_record, status="provider_error", note=str(exc))
        else:
            return self._error_result(
                pdf_record,
                status="provider_error",
                note=f"Unsupported LLM provider '{settings.llm_provider}'. Configure LLM_PROVIDER=gemini or openai.",
            )

        agreement_date = self._parse_optional_date(extraction.agreement_date)
        notes = extraction.rationale
        if extraction.evidence_excerpt:
            notes = f"{notes} Evidence: {extraction.evidence_excerpt}"

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

    def _build_filename(self, pdf_record: DownloadedPdfRecord) -> str:
        return f"broker-dealer-{pdf_record.bd_id}-{pdf_record.filing_year}.pdf"

    def _parse_optional_date(self, value: str | None) -> date | None:
        if not value:
            return None
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None

    def _error_result(self, pdf_record: DownloadedPdfRecord, *, status: str, note: str) -> ClearingExtractionResult:
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

