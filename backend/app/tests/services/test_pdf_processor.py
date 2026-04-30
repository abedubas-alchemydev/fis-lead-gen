"""Tests for the OCR pre-pass in PdfProcessorService.

Covers the four routing branches added by the BE-2 OCR layer:

* Text-rich PDF (pdfplumber returns >= 50 chars) → existing
  ``LlmParserService.extract_structured_data`` path runs unchanged;
  Vision OCR is **never** called.
* Scanned PDF (pdfplumber returns < 50 chars) → Vision called once
  and the OCR text is forwarded to
  ``LlmParserService.extract_structured_data_with_ocr_text``.
* Scanned PDF + Vision returns empty (or under-the-floor) text →
  result is tagged ``extraction_status='pipeline_error'`` (i.e. the
  ``pdf_unparseable`` Unknown-reasons category). LLM never called.
* Scanned PDF + Vision raises (5xx / quota / config error) → result
  is tagged ``extraction_status='provider_error'`` with note prefix
  ``vision_ocr_failed:``. LLM never called.

All external dependencies (pdfplumber, Vision SDK, Gemini/OpenAI HTTP)
are mocked. The asserts focus on which downstream path was selected,
not on the LLM payload itself — that is the responsibility of
``test_llm_parser`` / ``test_gemini_responses``.
"""

from __future__ import annotations

import base64
from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import pdf_processor as pdf_processor_module
from app.services.ocr import VisionOcrError
from app.services.pdf_processor import PdfProcessorService
from app.services.service_models import (
    ClearingExtractionResult,
    DownloadedPdfRecord,
)


@pytest.fixture
def pdf_record() -> DownloadedPdfRecord:
    """A minimal DownloadedPdfRecord with non-empty inline bytes.

    The actual bytes content is irrelevant — pdfplumber/Vision are
    mocked — but ``bytes_base64`` must be non-empty so
    ``_read_pdf_bytes`` returns a value rather than falling through.
    """
    return DownloadedPdfRecord(
        bd_id=42,
        filing_year=2024,
        report_date=date(2024, 12, 31),
        source_filing_url="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0001234567",
        source_pdf_url="https://www.sec.gov/Archives/edgar/data/1234567/file.pdf",
        local_document_path=None,
        bytes_base64=base64.b64encode(b"%PDF-1.7 fake bytes").decode("ascii"),
        accession_number="0001234567-24-000001",
    )


@pytest.fixture
def text_rich_extraction() -> ClearingExtractionResult:
    """Synthetic result returned by the legacy LLM path."""
    return ClearingExtractionResult(
        bd_id=42,
        filing_year=2024,
        report_date=date(2024, 12, 31),
        source_filing_url=None,
        source_pdf_url=None,
        local_document_path=None,
        clearing_partner="Pershing LLC",
        clearing_type="fully_disclosed",
        agreement_date=None,
        extraction_confidence=0.92,
        extraction_status="parsed",
        extraction_notes="text-rich path",
        extracted_at=None,
    )


@pytest.fixture
def ocr_extraction() -> ClearingExtractionResult:
    """Synthetic result returned by the OCR-aware LLM path."""
    return ClearingExtractionResult(
        bd_id=42,
        filing_year=2024,
        report_date=date(2024, 12, 31),
        source_filing_url=None,
        source_pdf_url=None,
        local_document_path=None,
        clearing_partner="Apex Clearing Corporation",
        clearing_type="fully_disclosed",
        agreement_date=None,
        extraction_confidence=0.81,
        extraction_status="parsed",
        extraction_notes="[text_source=ocr] OCR-routed",
        extracted_at=None,
    )


def _patch_pdfplumber(monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    """Force ``_extract_pdfplumber_text`` to a deterministic string.

    Patching the helper avoids the cost of installing pdfplumber
    fixture PDFs and makes it easy to flip between the < 50 char
    (scanned) and >= 50 char (text-rich) branches per test.
    """
    monkeypatch.setattr(
        pdf_processor_module,
        "_extract_pdfplumber_text",
        lambda _bytes: text,
    )


# ───────────────── text-rich PDF: existing path runs ──────────────────


@pytest.mark.asyncio
async def test_text_rich_pdf_skips_ocr_and_runs_legacy_path(
    monkeypatch: pytest.MonkeyPatch,
    pdf_record: DownloadedPdfRecord,
    text_rich_extraction: ClearingExtractionResult,
) -> None:
    # 100 chars of text — comfortably above the 50-char floor.
    _patch_pdfplumber(monkeypatch, "x" * 100)

    service = PdfProcessorService()

    legacy_call = AsyncMock(return_value=text_rich_extraction)
    ocr_call = AsyncMock()
    vision_call = MagicMock()

    monkeypatch.setattr(service.llm_parser, "extract_structured_data", legacy_call)
    monkeypatch.setattr(
        service.llm_parser, "extract_structured_data_with_ocr_text", ocr_call
    )
    monkeypatch.setattr(service._vision_ocr, "ocr_pdf", vision_call)

    result = await service.process_downloaded_pdf(pdf_record)

    assert result is text_rich_extraction
    legacy_call.assert_awaited_once_with(pdf_record)
    ocr_call.assert_not_awaited()
    vision_call.assert_not_called()


# ───────────────── scanned PDF: OCR called + LLM-with-OCR ─────────────


@pytest.mark.asyncio
async def test_scanned_pdf_routes_through_vision_then_ocr_aware_llm(
    monkeypatch: pytest.MonkeyPatch,
    pdf_record: DownloadedPdfRecord,
    ocr_extraction: ClearingExtractionResult,
) -> None:
    # pdfplumber returns "" — well below the 50-char gate.
    _patch_pdfplumber(monkeypatch, "")

    service = PdfProcessorService()

    long_ocr_text = (
        "The Company has a clearing agreement with Apex Clearing Corporation on a "
        "fully disclosed basis."
    )
    vision_call = MagicMock(return_value=long_ocr_text)
    ocr_llm_call = AsyncMock(return_value=ocr_extraction)
    legacy_call = AsyncMock()

    monkeypatch.setattr(service._vision_ocr, "ocr_pdf", vision_call)
    monkeypatch.setattr(
        service.llm_parser, "extract_structured_data_with_ocr_text", ocr_llm_call
    )
    monkeypatch.setattr(service.llm_parser, "extract_structured_data", legacy_call)

    result = await service.process_downloaded_pdf(pdf_record)

    assert result is ocr_extraction
    vision_call.assert_called_once()
    ocr_llm_call.assert_awaited_once()
    # OCR'd text and base64-encoded PDF bytes are forwarded to the LLM.
    kwargs: dict[str, Any] = ocr_llm_call.await_args.kwargs
    assert kwargs["ocr_text"] == long_ocr_text
    assert isinstance(kwargs["pdf_bytes_base64"], str) and kwargs["pdf_bytes_base64"]
    legacy_call.assert_not_awaited()


# ───────────────── scanned PDF + Vision empty -> pipeline_error ──────


@pytest.mark.asyncio
async def test_scanned_pdf_vision_returns_empty_marks_pipeline_error(
    monkeypatch: pytest.MonkeyPatch,
    pdf_record: DownloadedPdfRecord,
) -> None:
    _patch_pdfplumber(monkeypatch, "")

    service = PdfProcessorService()
    vision_call = MagicMock(return_value="")
    ocr_llm_call = AsyncMock()

    monkeypatch.setattr(service._vision_ocr, "ocr_pdf", vision_call)
    monkeypatch.setattr(
        service.llm_parser, "extract_structured_data_with_ocr_text", ocr_llm_call
    )

    result = await service.process_downloaded_pdf(pdf_record)

    assert result.extraction_status == "pipeline_error"
    assert result.clearing_partner is None
    assert result.clearing_type == "unknown"
    assert "OCR returned no usable text" in (result.extraction_notes or "")
    vision_call.assert_called_once()
    ocr_llm_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_scanned_pdf_vision_short_text_marks_pipeline_error(
    monkeypatch: pytest.MonkeyPatch,
    pdf_record: DownloadedPdfRecord,
) -> None:
    """Vision returned non-empty but still under the 50-char floor."""
    _patch_pdfplumber(monkeypatch, "")

    service = PdfProcessorService()
    monkeypatch.setattr(service._vision_ocr, "ocr_pdf", MagicMock(return_value="x" * 49))
    ocr_llm_call = AsyncMock()
    monkeypatch.setattr(
        service.llm_parser, "extract_structured_data_with_ocr_text", ocr_llm_call
    )

    result = await service.process_downloaded_pdf(pdf_record)

    assert result.extraction_status == "pipeline_error"
    ocr_llm_call.assert_not_awaited()


# ───────────────── scanned PDF + Vision 5xx -> provider_error ────────


@pytest.mark.asyncio
async def test_scanned_pdf_vision_5xx_marks_provider_error(
    monkeypatch: pytest.MonkeyPatch,
    pdf_record: DownloadedPdfRecord,
) -> None:
    _patch_pdfplumber(monkeypatch, "")

    service = PdfProcessorService()

    def _raise_5xx(_pdf_bytes: bytes) -> str:
        raise VisionOcrError("503 Service Unavailable")

    monkeypatch.setattr(service._vision_ocr, "ocr_pdf", _raise_5xx)
    ocr_llm_call = AsyncMock()
    monkeypatch.setattr(
        service.llm_parser, "extract_structured_data_with_ocr_text", ocr_llm_call
    )

    result = await service.process_downloaded_pdf(pdf_record)

    assert result.extraction_status == "provider_error"
    assert result.clearing_partner is None
    assert result.clearing_type == "unknown"
    assert (result.extraction_notes or "").startswith("vision_ocr_failed:")
    ocr_llm_call.assert_not_awaited()


# ───────────── pdf_record without bytes falls through to legacy ──────


@pytest.mark.asyncio
async def test_pdf_record_without_bytes_falls_through_to_legacy_handler(
    monkeypatch: pytest.MonkeyPatch,
    text_rich_extraction: ClearingExtractionResult,
) -> None:
    """No bytes_base64 and no local_document_path → ``_read_pdf_bytes``
    returns None and the existing handler decides the outcome."""
    record = DownloadedPdfRecord(
        bd_id=42,
        filing_year=2024,
        report_date=date(2024, 12, 31),
        source_filing_url=None,
        source_pdf_url=None,
        local_document_path=None,
        bytes_base64="",
        accession_number=None,
    )

    service = PdfProcessorService()
    legacy_call = AsyncMock(return_value=text_rich_extraction)
    vision_call = MagicMock()
    monkeypatch.setattr(service.llm_parser, "extract_structured_data", legacy_call)
    monkeypatch.setattr(service._vision_ocr, "ocr_pdf", vision_call)

    result = await service.process_downloaded_pdf(record)

    assert result is text_rich_extraction
    legacy_call.assert_awaited_once_with(record)
    vision_call.assert_not_called()
