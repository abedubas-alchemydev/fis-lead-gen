"""
Base parsing utilities shared by FINRA and FOCUS parsers.

Extraction strategy (layered, cheap-to-expensive):
  1. pdfplumber.extract_text() + extract_tables()   — covers born-digital PDFs
  2. PyMuPDF (fitz) fallback for layout edge cases
  3. OCR (pdf2image -> pytesseract) only if a page's text length < threshold
"""
from __future__ import annotations

import hashlib
import io
import logging
import re
from dataclasses import dataclass
from typing import Optional

import pdfplumber

from ..config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Extracted representations
# ---------------------------------------------------------------------------

@dataclass
class PageText:
    page_no: int
    text: str
    ocr_used: bool = False


@dataclass
class ExtractedPdf:
    pages: list[PageText]
    full_text: str
    sha256: str
    tables_by_page: dict[int, list[list[list[str]]]]


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _ocr_page(pdf_bytes: bytes, page_index: int) -> str:
    """Render one page to image and OCR it. Imported lazily so the rest of the
    pipeline doesn't require tesseract+poppler unless a scanned page is hit."""
    try:
        from pdf2image import convert_from_bytes
        import pytesseract
    except ImportError:  # pragma: no cover
        logger.warning("OCR deps missing (pdf2image/pytesseract); returning empty text")
        return ""

    try:
        images = convert_from_bytes(
            pdf_bytes,
            dpi=settings.ocr_dpi,
            first_page=page_index + 1,
            last_page=page_index + 1,
        )
        if not images:
            return ""
        return pytesseract.image_to_string(images[0])
    except Exception as exc:  # noqa: BLE001
        logger.warning("OCR failed on page %d: %s", page_index, exc)
        return ""


def _pymupdf_page_text(pdf_bytes: bytes, page_index: int) -> str:
    """PyMuPDF fallback — better on multi-column layouts than pdfplumber."""
    try:
        import fitz  # PyMuPDF
    except ImportError:  # pragma: no cover
        return ""
    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            return doc[page_index].get_text("text") or ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("PyMuPDF failed on page %d: %s", page_index, exc)
        return ""


def extract_pdf(pdf_bytes: bytes) -> ExtractedPdf:
    """Extract text + tables from a PDF using the layered strategy."""
    pages: list[PageText] = []
    tables_by_page: dict[int, list[list[list[str]]]] = {}

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for i, page in enumerate(pdf.pages):
            text = (page.extract_text() or "").strip()

            if len(text) < settings.ocr_text_threshold:
                alt = _pymupdf_page_text(pdf_bytes, i).strip()
                if len(alt) > len(text):
                    text = alt

            ocr_used = False
            if len(text) < settings.ocr_text_threshold and settings.enable_ocr:
                ocr_text = _ocr_page(pdf_bytes, i).strip()
                if ocr_text:
                    text = ocr_text
                    ocr_used = True

            pages.append(PageText(page_no=i + 1, text=text, ocr_used=ocr_used))

            try:
                t = page.extract_tables() or []
                if t:
                    tables_by_page[i + 1] = t
            except Exception as exc:  # noqa: BLE001
                logger.debug("Table extraction failed on page %d: %s", i + 1, exc)

    full_text = "\n\n".join(p.text for p in pages)
    return ExtractedPdf(
        pages=pages,
        full_text=full_text,
        sha256=sha256_bytes(pdf_bytes),
        tables_by_page=tables_by_page,
    )


# ---------------------------------------------------------------------------
# Section splitting
# ---------------------------------------------------------------------------

def split_sections(full_text: str, headers: list[str]) -> dict[str, str]:
    """
    Split document text into named sections by exact header anchors.

    `headers` is an ordered list of headers as they appear in the PDF. A section
    is the text between header[i] and header[i+1] (or EOF). Headers are matched
    in order — later ones only looked for after earlier ones are found so we
    don't get confused by header text appearing in a TOC.
    """
    results: dict[str, str] = {}
    cursor = 0
    # Find each header's position sequentially
    positions: list[tuple[str, int]] = []
    for hdr in headers:
        pattern = re.compile(rf"(?m)^\s*{re.escape(hdr)}\b")
        m = pattern.search(full_text, cursor)
        if m:
            positions.append((hdr, m.start()))
            cursor = m.end()

    for idx, (hdr, start) in enumerate(positions):
        end = positions[idx + 1][1] if idx + 1 < len(positions) else len(full_text)
        results[hdr] = full_text[start:end].strip()

    return results


def find_first_match(patterns: list[str], text: str, flags: int = re.IGNORECASE) -> Optional[str]:
    """Return the first capturing-group match across the given patterns."""
    for pat in patterns:
        m = re.search(pat, text, flags)
        if m:
            return (m.group(1) if m.groups() else m.group(0)).strip()
    return None


def parse_money(s: Optional[str]) -> Optional[str]:
    """Normalize a money string to a bare numeric string (Decimal-friendly)."""
    if s is None:
        return None
    cleaned = re.sub(r"[^\d.\-()]", "", s)
    if not cleaned:
        return None
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = "-" + cleaned[1:-1]
    return cleaned
