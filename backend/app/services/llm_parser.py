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

