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
            "on a fully disclosed basis.' Clearing language can also appear in the Statement of Financial "
            "Condition narrative, the description-of-business section, or supplementary schedules — scan "
            "those if the Notes are silent.\n\n"
            "## Search hints — scan for these literal phrases first\n"
            "Before reading the document end-to-end, search for any of the following anchor phrases. "
            "When one matches, the surrounding sentence is almost always the evidence_excerpt:\n"
            '- "clearing agreement with"\n'
            '- "clears all transactions through"\n'
            '- "introducing broker"\n'
            '- "carries customer accounts on a fully disclosed basis"\n'
            '- "self-clearing"\n'
            '- "executes and clears"\n'
            '- "prime broker"\n'
            '- "omnibus account"\n\n'
            "## Special-case rule — firms that do not carry customer accounts\n"
            "If the firm explicitly states it does not carry customer accounts, does not hold customer "
            "funds or securities, is limited to M&A advisory or other non-custodial activity, or operates "
            "under a Section 15(c)(3)-1 (or (k)(2)(i) / (k)(2)(ii)) exemption, return "
            "clearing_type='self_clearing' with confidence_score >= 0.85. Do NOT return 'unknown' and do "
            "NOT set clearing_partner to null with low confidence in this case — the firm's regulatory "
            "status (no customer accounts → no clearing relationship needed) IS the answer. "
            "clearing_partner should be null because there is no third-party clearing firm to name.\n\n"
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
            "Example 4 — Introducing Broker (named partner):\n"
            'Document says: "The Company operates as an introducing broker and clears its securities '
            'transactions through National Financial Services LLC on a fully disclosed basis."\n'
            "Expected output:\n"
            '{"clearing_partner": "National Financial Services LLC", "clearing_type": "fully_disclosed", '
            '"agreement_date": null, "confidence_score": 0.95, "rationale": "Firm describes itself as an '
            'introducing broker with NFS named as the clearing partner on a fully disclosed basis.", '
            '"evidence_excerpt": "The Company operates as an introducing broker and clears its securities '
            'transactions through National Financial Services LLC on a fully disclosed basis."}\n\n'
            "Example 5 — Omnibus / multiple clearing relationships:\n"
            'Document says: "The Company maintains an omnibus account with BNY Mellon Capital Markets, LLC '
            'through which it clears institutional fixed-income transactions."\n'
            "Expected output:\n"
            '{"clearing_partner": "BNY Mellon Capital Markets, LLC", "clearing_type": "omnibus", '
            '"agreement_date": null, "confidence_score": 0.9, "rationale": "Firm explicitly maintains an '
            'omnibus account with BNY Mellon Capital Markets for clearing.", "evidence_excerpt": "The Company '
            'maintains an omnibus account with BNY Mellon Capital Markets, LLC through which it clears '
            'institutional fixed-income transactions."}\n\n'
            "Example 6 — Foreign clearing partner:\n"
            'Document says: "Pursuant to a clearing agreement with TD Securities (USA) LLC, the Company '
            'introduces its customer accounts on a fully disclosed basis."\n'
            "Expected output:\n"
            '{"clearing_partner": "TD Securities (USA) LLC", "clearing_type": "fully_disclosed", '
            '"agreement_date": null, "confidence_score": 0.95, "rationale": "Clearing agreement explicitly '
            'with TD Securities (USA) LLC on a fully disclosed basis.", "evidence_excerpt": "Pursuant to a '
            'clearing agreement with TD Securities (USA) LLC, the Company introduces its customer accounts '
            'on a fully disclosed basis."}\n\n'
            "Example 7 — Prime brokerage relationship:\n"
            'Document says: "The Company\'s prime broker is Goldman Sachs & Co. LLC, which provides custody, '
            'clearance and financing services for the Company\'s proprietary trading activities."\n'
            "Expected output:\n"
            '{"clearing_partner": "Goldman Sachs & Co. LLC", "clearing_type": "fully_disclosed", '
            '"agreement_date": null, "confidence_score": 0.85, "rationale": "Goldman Sachs & Co. LLC is '
            "named as the firm's prime broker providing custody, clearance, and financing — treat as "
            'fully_disclosed since the prime broker carries the accounts.", "evidence_excerpt": "The '
            "Company's prime broker is Goldman Sachs & Co. LLC, which provides custody, clearance and "
            'financing services for the Company\'s proprietary trading activities."}\n\n'
            'Example 8 — "Executes and clears" phrasing (often outside Notes):\n'
            'Document says: "RBC Capital Markets, LLC executes and clears all securities transactions for '
            "the Company's customers under a written clearing agreement.\"\n"
            "Expected output:\n"
            '{"clearing_partner": "RBC Capital Markets, LLC", "clearing_type": "fully_disclosed", '
            '"agreement_date": null, "confidence_score": 0.95, "rationale": "RBC Capital Markets executes '
            'and clears under a written clearing agreement — fully disclosed arrangement.", '
            '"evidence_excerpt": "RBC Capital Markets, LLC executes and clears all securities transactions '
            "for the Company's customers under a written clearing agreement.\"}\n\n"
            "Example 9 — M&A advisory only (no customer accounts):\n"
            'Document says: "The Company\'s business is limited to mergers-and-acquisitions advisory '
            "services. The Company does not carry accounts of or for customers and does not hold customer "
            'funds or securities."\n'
            "Expected output:\n"
            '{"clearing_partner": null, "clearing_type": "self_clearing", "agreement_date": null, '
            '"confidence_score": 0.95, "rationale": "Firm is limited to M&A advisory and explicitly does '
            'not carry customer accounts, so no clearing relationship is required — treat as '
            'self_clearing with null partner per the special-case rule.", "evidence_excerpt": "The '
            "Company's business is limited to mergers-and-acquisitions advisory services. The Company "
            'does not carry accounts of or for customers and does not hold customer funds or securities."}\n\n'
            "Example 10 — Section 15(c)(3)-1 / (k)(2)(i) exemption report:\n"
            'Document says: "The Company claims exemption from Rule 15c3-3 under paragraph (k)(2)(i) of '
            'the Rule, as it does not carry customer accounts and all customer transactions are cleared '
            'through another broker-dealer on a fully disclosed basis."\n'
            "Expected output:\n"
            '{"clearing_partner": null, "clearing_type": "self_clearing", "agreement_date": null, '
            '"confidence_score": 0.9, "rationale": "Exemption Report cites Rule 15c3-3(k)(2)(i) and the '
            'firm does not carry customer accounts. The (k)(2)(i) exemption itself IS the regulatory '
            'answer — return self_clearing with null partner per the special-case rule, even though the '
            'document mentions transactions are cleared through an unnamed broker-dealer.", '
            '"evidence_excerpt": "The Company claims exemption from Rule 15c3-3 under paragraph (k)(2)(i) '
            'of the Rule, as it does not carry customer accounts and all customer transactions are '
            'cleared through another broker-dealer on a fully disclosed basis."}\n\n'
            'Example 11 — "Does not carry customer accounts" boilerplate:\n'
            'Document says: "The Notes to Financial Statements explicitly state that the Company does not '
            'carry customer accounts or hold customer funds or securities. No clearing partner or clearing '
            'agreement is mentioned anywhere in the filing."\n'
            "Expected output:\n"
            '{"clearing_partner": null, "clearing_type": "self_clearing", "agreement_date": null, '
            '"confidence_score": 0.85, "rationale": "Firm explicitly does not carry customer accounts and '
            'does not hold customer funds — no clearing relationship is required. Return self_clearing '
            'with null partner per the special-case rule rather than unknown.", "evidence_excerpt": "The '
            'Company does not carry customer accounts or hold customer funds or securities."}\n\n'
            "Now analyze the attached PDF and extract the clearing arrangement data. Use only evidence from "
            "the document. Treat uncertain or conflicting references conservatively. If the firm names "
            "multiple clearing relationships (e.g. an introducing partner plus a separate omnibus or prime "
            "broker), pick the one that carries the firm's customer accounts; if no single relationship is "
            "primary, return clearing_type 'unknown' with the lower-confidence rationale."
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
            clearing_statement_text=extraction.evidence_excerpt,
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
            clearing_statement_text=extraction.evidence_excerpt,
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

